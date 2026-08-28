"""Message classification and event handling for the Shoe game."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Iterable
import logging
import re
from typing import TypeVar
import unicodedata

import discord

from .database import (
    DatabaseError,
    GuildConfig,
    MatchingMode,
    ShoeDatabase,
    UserDeletion,
)


LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

# Official footwear and skate emoji. Feet, socks, footprints, skateboards, and
# unrelated clothing are deliberately excluded to keep matching predictable.
FOOTWEAR_EMOJIS = frozenset(
    {
        "👞",  # man's shoe
        "👟",  # running shoe
        "👠",  # high-heeled shoe
        "👡",  # woman's sandal
        "👢",  # woman's boot
        "🥾",  # hiking boot
        "🥿",  # flat shoe
        "🩰",  # ballet shoes
        "🩴",  # thong sandal
        "⛸",   # ice skate (with or without variation selector)
        "⛸️",
        "🛼",  # roller skate
    }
)

# Creative mode accepts repeated letters, zero as O, and separators between
# letters. ASCII boundaries prevent arbitrary letter sequences inside unrelated
# words from becoming surprising matches. The ordinary substring check still
# means words such as "horseshoe" and "shoelace" count.
_CREATIVE_SHOE_PATTERN = re.compile(
    r"(?<![a-z0-9])s+[\s._\-]*h+[\s._\-]*[o0]+[\s._\-]*e+(?![a-z0-9])"
)


def _normalise_creative_text(value: str) -> str:
    normalised = unicodedata.normalize("NFKC", value).casefold()
    # Format controls such as zero-width joiners are ignored for the text test.
    # They remain in the original string used for exact emoji detection.
    return "".join(
        character
        for character in normalised
        if unicodedata.category(character) != "Cf"
    )


def _creative_text_match(value: str) -> bool:
    normalised = _normalise_creative_text(value)
    return "shoe" in normalised or _CREATIVE_SHOE_PATTERN.search(normalised) is not None


def message_matches_shoe(
    content: str,
    mode: MatchingMode,
    sticker_names: Iterable[str] = (),
) -> bool:
    """Return whether one message satisfies the selected fixed ruleset.

    The function is pure and stores nothing. A message can match several forms
    but is still processed as one contribution by the database.
    """
    if "shoe" in content.casefold():
        return True
    if mode == "classic":
        return False
    if mode != "creative":
        raise ValueError("mode must be 'classic' or 'creative'")
    if any(emoji in content for emoji in FOOTWEAR_EMOJIS):
        return True
    if _creative_text_match(content):
        return True
    return any(_creative_text_match(name) for name in sticker_names)


class RecentMessageCache:
    """Bounded, in-memory message ID cache for one-runtime deduplication."""

    def __init__(self, capacity: int = 10_000) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._ids: OrderedDict[int, None] = OrderedDict()

    def add_if_new(self, message_id: int) -> bool:
        if message_id in self._ids:
            return False
        self._ids[message_id] = None
        if len(self._ids) > self._capacity:
            self._ids.popitem(last=False)
        return True

    def discard(self, message_id: int) -> None:
        self._ids.pop(message_id, None)


class ShoeGame:
    def __init__(self, database: ShoeDatabase) -> None:
        self._database = database
        self._guild_configs: dict[int, GuildConfig] = {}
        self._recent_messages = RecentMessageCache()
        self._state_lock = asyncio.Lock()
        self._closing = False

    @staticmethod
    async def _finish_transition(
        operation: Awaitable[T],
    ) -> tuple[T, asyncio.CancelledError | None]:
        """Finish a submitted state transition even if its caller is cancelled."""
        task = asyncio.create_task(operation)
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                result = await asyncio.shield(task)
                return result, cancellation
            except asyncio.CancelledError as exc:
                cancellation = exc
                if task.done():
                    return task.result(), cancellation

    async def load_configuration(self) -> None:
        async with self._state_lock:
            if self._closing:
                raise DatabaseError("Shoe game is shutting down")
            configs, cancellation = await self._finish_transition(
                self._database.run(self._database.load_guild_configs)
            )
            self._guild_configs = configs
            if cancellation is not None:
                raise cancellation

    async def configure_guild(
        self,
        guild_id: int,
        channel_id: int,
        matching_mode: str = "creative",
        gameplay_mode: str = "relay",
    ) -> GuildConfig:
        async with self._state_lock:
            if self._closing:
                raise DatabaseError("Shoe game is shutting down")
            config, cancellation = await self._finish_transition(
                self._database.run(
                    self._database.configure_guild,
                    guild_id,
                    channel_id,
                    matching_mode,
                    gameplay_mode,
                )
            )
            # The cache changes only after the database commit succeeds.
            self._guild_configs[guild_id] = config
            if cancellation is not None:
                raise cancellation
        return config

    async def configure_channel(self, guild_id: int, channel_id: int) -> None:
        existing = self._guild_configs.get(guild_id)
        await self.configure_guild(
            guild_id,
            channel_id,
            existing.matching_mode if existing else "creative",
            existing.gameplay_mode if existing else "relay",
        )

    async def configure_random_shoe(
        self, guild_id: int, enabled: bool, channel_ids: tuple[int, ...]
    ) -> None:
        async with self._state_lock:
            if self._closing:
                raise DatabaseError("Shoe game is shutting down")
            _, cancellation = await self._finish_transition(
                self._database.run(
                    self._database.configure_random_shoe,
                    guild_id,
                    enabled,
                    channel_ids,
                    None,
                )
            )
            if cancellation is not None:
                raise cancellation

    def configured_channel_id(self, guild_id: int) -> int | None:
        config = self._guild_configs.get(guild_id)
        return config.channel_id if config is not None else None

    def configured_guild_ids(self) -> set[int]:
        return set(self._guild_configs)

    async def remove_guild(self, guild_id: int) -> None:
        async with self._state_lock:
            if self._closing:
                raise DatabaseError("Shoe game is shutting down")
            _, cancellation = await self._finish_transition(
                self._database.run(self._database.delete_guild, guild_id)
            )
            self._guild_configs.pop(guild_id, None)
            if cancellation is not None:
                raise cancellation

    async def reset_guild_stats(self, guild_id: int) -> None:
        async with self._state_lock:
            if self._closing:
                raise DatabaseError("Shoe game is shutting down")
            _, cancellation = await self._finish_transition(
                self._database.run(self._database.reset_guild_stats, guild_id)
            )
            if cancellation is not None:
                raise cancellation

    async def delete_user_stats(self, guild_id: int, user_id: int) -> UserDeletion:
        async with self._state_lock:
            if self._closing:
                raise DatabaseError("Shoe game is shutting down")
            result, cancellation = await self._finish_transition(
                self._database.run(
                    self._database.delete_user_stats,
                    guild_id,
                    user_id,
                )
            )
            if cancellation is not None:
                raise cancellation
            return result

    async def aclose(self) -> None:
        """Drain transitions already waiting ahead of shutdown, then close SQLite."""
        async with self._state_lock:
            self._closing = True
            await self._database.aclose()

    async def handle_message(self, message: discord.Message) -> None:
        # All routing and author checks happen before message.content is read.
        if message.guild is None:
            return
        if message.author.bot or message.webhook_id is not None:
            return
        if getattr(message, "type", discord.MessageType.default) not in {
            discord.MessageType.default,
            discord.MessageType.reply,
        }:
            return
        async with self._state_lock:
            if self._closing:
                return
            config = self._guild_configs.get(message.guild.id)
            if config is None or config.channel_id != message.channel.id:
                return
            if not self._recent_messages.add_if_new(message.id):
                return

            sticker_names = (
                str(sticker.name)
                for sticker in getattr(message, "stickers", ())
                if getattr(sticker, "name", None)
            )
            content_matches = message_matches_shoe(
                message.content,
                config.matching_mode,
                sticker_names,
            )
            try:
                update = await self._database.run(
                    self._database.record_message,
                    guild_id=message.guild.id,
                    user_id=message.author.id if content_matches else None,
                    content_matches=content_matches,
                )
            except DatabaseError as exc:
                # A duplicate gateway event may safely retry if the write failed.
                self._recent_messages.discard(message.id)
                LOGGER.error("Could not update Shoe statistics (%s)", type(exc).__name__)
                return

        try:
            await message.add_reaction("✅" if update.accepted else "❌")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            LOGGER.warning("Could not add a Shoe reaction (%s)", type(exc).__name__)

        if update.break_reason is None or update.previous_streak == 0:
            return

        if update.break_reason == "relay":
            text = (
                f"{message.author.mention} broke a "
                f"{update.previous_streak:,}-message shoe streak by posting "
                "twice in a row."
            )
        else:
            text = (
                f"{message.author.mention} broke a "
                f"{update.previous_streak:,}-message shoe streak."
            )
        if update.hall_of_fame_rank is not None:
            text += (
                " It entered the Hall of Fame at rank "
                f"#{update.hall_of_fame_rank:,}."
            )

        try:
            await message.channel.send(
                text,
                allowed_mentions=discord.AllowedMentions(
                    users=[message.author],
                    roles=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            LOGGER.warning(
                "Could not send the streak-break message (%s)", type(exc).__name__
            )
