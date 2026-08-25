"""Message handling for the Shoe game."""

from __future__ import annotations

from collections import OrderedDict
import logging

import discord

from .database import DatabaseError, ShoeDatabase


LOGGER = logging.getLogger(__name__)


class RecentMessageCache:
    """Bounded, in-memory message ID cache used only for runtime deduplication."""

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
        self._configured_channels: dict[int, int] = {}
        self._recent_messages = RecentMessageCache()

    def load_configuration(self) -> None:
        self._configured_channels = self._database.load_configured_channels()

    def configure_channel(self, guild_id: int, channel_id: int) -> None:
        self._database.set_shoe_channel(guild_id, channel_id)
        self._configured_channels[guild_id] = channel_id

    def configured_channel_id(self, guild_id: int) -> int | None:
        return self._configured_channels.get(guild_id)

    def configured_guild_ids(self) -> set[int]:
        return set(self._configured_channels)

    def remove_guild(self, guild_id: int) -> None:
        self._database.delete_guild(guild_id)
        self._configured_channels.pop(guild_id, None)

    async def handle_message(self, message: discord.Message) -> None:
        # These checks happen before message.content is inspected.
        if message.guild is None:
            return
        if message.author.bot or message.webhook_id is not None:
            return
        if self._configured_channels.get(message.guild.id) != message.channel.id:
            return
        if not self._recent_messages.add_if_new(message.id):
            return

        is_valid = "shoe" in message.content.casefold()
        try:
            update = self._database.record_message(
                guild_id=message.guild.id,
                user_id=message.author.id if is_valid else None,
                is_valid=is_valid,
            )
        except DatabaseError as exc:
            # A duplicate gateway event may safely retry if this write failed.
            self._recent_messages.discard(message.id)
            LOGGER.error(
                "Could not update Shoe statistics (%s)", type(exc).__name__
            )
            return

        reaction = "✅" if is_valid else "❌"
        try:
            await message.add_reaction(reaction)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            LOGGER.warning("Could not add a Shoe reaction (%s)", type(exc).__name__)

        if not is_valid and update.previous_streak > 0:
            try:
                await message.channel.send(
                    f"💥 {message.author.mention} broke the shoe streak at "
                    f"{update.previous_streak:,}!",
                    allowed_mentions=discord.AllowedMentions(
                        users=[message.author],
                        roles=False,
                        everyone=False,
                        replied_user=False,
                    ),
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                LOGGER.warning(
                    "Could not send the streak-break message (%s)",
                    type(exc).__name__,
                )
