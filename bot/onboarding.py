"""One-time, non-pinging setup welcome with a persistent administrator button."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

import discord

from .commands import _private_error
from .database import DatabaseError, ShoeDatabase

LOGGER = logging.getLogger(__name__)
WELCOME_TEXT = (
    "👟 **Thanks for adding Shoe Bot!**\n\n"
    "Get your server's Shoe streak started:\n"
    "1. Create or choose a channel, such as **#shoe**.\n"
    "2. Have a server administrator click **Set up Shoe Bot** below or run `/setup`.\n"
    "3. Choose your game modes and save—you're ready!\n\n"
    "Need help? Use `/shoehelp`. You can change your settings later with `/shoesettings`."
)


class WelcomeView(discord.ui.View):
    def __init__(self, open_setup: Callable[[discord.Interaction], Awaitable[None]]) -> None:
        super().__init__(timeout=None)
        self._open_setup = open_setup

    @discord.ui.button(
        label="Set up Shoe Bot", style=discord.ButtonStyle.primary,
        custom_id="shoe:welcome:setup:v1",
    )
    async def setup(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._open_setup(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception,
                       _item: discord.ui.Item) -> None:
        LOGGER.error("Welcome setup button failed (%s)", type(error).__name__)
        await _private_error(interaction, "Setup is temporarily unavailable. Try `/setup` again shortly.")


class WelcomeService:
    def __init__(self, database: ShoeDatabase, view: WelcomeView) -> None:
        self.database = database
        self.view = view

    async def send(self, guild: discord.Guild, *, include_existing: bool = False) -> bool:
        if guild.unavailable or guild.me is None:
            return False
        try:
            if not await self.database.run(self.database.claim_welcome, guild.id, include_existing):
                return False
        except DatabaseError as exc:
            LOGGER.error("Could not claim server welcome (%s)", type(exc).__name__)
            return False
        candidates = list(guild.text_channels)
        if guild.system_channel is not None:
            candidates = [guild.system_channel] + [c for c in candidates if c.id != guild.system_channel.id]
        for channel in candidates:
            permissions = channel.permissions_for(guild.me)
            if not (permissions.view_channel and permissions.send_messages):
                continue
            try:
                await channel.send(
                    WELCOME_TEXT, view=self.view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                LOGGER.info("Sent setup welcome in server %s", guild.id)
                return True
            except (discord.Forbidden, discord.NotFound):
                # These definite rejections permit trying another allowed channel.
                continue
            except Exception as exc:
                # A timeout/5xx may have delivered: don't try another channel.
                LOGGER.warning("Server %s welcome delivery uncertain (%s)", guild.id, type(exc).__name__)
                return False
        LOGGER.info("No writable channel for server %s welcome; /setup remains available", guild.id)
        return False

    async def reconcile(
        self, guilds: list[discord.Guild], *, include_existing: bool = False,
    ) -> bool:
        try:
            await self.database.run(self.database.prepare_welcomes, [g.id for g in guilds])
        except DatabaseError as exc:
            LOGGER.error("Could not reconcile welcome history (%s)", type(exc).__name__)
            return False
        sent = 0
        for guild in guilds:
            sent += await self.send(guild, include_existing=include_existing)
        LOGGER.info("Welcome check complete: %d message(s) sent", sent)
        return True
