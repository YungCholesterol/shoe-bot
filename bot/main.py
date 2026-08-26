"""Shoe Bot entry point."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from .commands import ShoeCommands, _private_error
from .database import DatabaseError, ShoeDatabase
from .shoe_game import ShoeGame


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger(__name__)


class ConfigurationError(ValueError):
    """Raised when required application configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class BotConfig:
    token: str
    application_id: int
    development_guild_id: int | None
    database_path: Path


def _optional_snowflake(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    if not raw.isdecimal() or int(raw) <= 0:
        raise ConfigurationError(f"{name} must be a positive Discord ID")
    return int(raw)


def load_config() -> BotConfig:
    load_dotenv(PROJECT_ROOT / ".env")

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ConfigurationError("DISCORD_TOKEN is required")

    application_id = _optional_snowflake("APPLICATION_ID")
    if application_id is None:
        raise ConfigurationError("APPLICATION_ID is required")

    database_value = os.getenv("DATABASE_PATH", "data/shoe_bot.sqlite3").strip()
    if not database_value:
        raise ConfigurationError("DATABASE_PATH cannot be empty")
    database_path = Path(database_value)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    return BotConfig(
        token=token,
        application_id=application_id,
        development_guild_id=_optional_snowflake("DEV_GUILD_ID"),
        database_path=database_path,
    )


class ShoeBot(commands.Bot):
    def __init__(self, config: BotConfig, database: ShoeDatabase) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            application_id=config.application_id,
            allowed_installs=app_commands.AppInstallationType(
                guild=True,
                user=False,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
            max_messages=None,
            member_cache_flags=discord.MemberCacheFlags.none(),
        )
        self.config = config
        self.database = database
        self.game = ShoeGame(database)
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        await self.game.load_configuration()
        await self.add_cog(ShoeCommands(self.database, self.game))

        try:
            if self.config.development_guild_id is not None:
                guild = discord.Object(id=self.config.development_guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                LOGGER.info("Synced %d development-guild commands", len(synced))
            else:
                synced = await self.tree.sync()
                LOGGER.info("Synced %d global commands", len(synced))
        except discord.HTTPException as exc:
            LOGGER.error("Could not sync application commands (%s)", type(exc).__name__)

    async def on_ready(self) -> None:
        # Reconcile removals that happened while this process was offline. Guilds
        # marked temporarily unavailable remain in self.guilds and are retained.
        installed_guild_ids = {guild.id for guild in self.guilds}
        stale_guild_ids = self.game.configured_guild_ids() - installed_guild_ids
        for guild_id in stale_guild_ids:
            try:
                await self.game.remove_guild(guild_id)
            except DatabaseError as exc:
                LOGGER.error(
                    "Could not purge stale server data (%s)", type(exc).__name__
                )
        LOGGER.info("Shoe Bot is ready in %d server(s)", len(self.guilds))

    async def on_message(self, message: discord.Message) -> None:
        # Prefix commands are intentionally unsupported; only slash commands are used.
        await self.game.handle_message(message)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        try:
            await self.game.remove_guild(guild.id)
        except DatabaseError as exc:
            LOGGER.error(
                "Could not purge data for a removed server (%s)", type(exc).__name__
            )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await _private_error(
                interaction,
                "You need the Administrator permission to use that command.",
            )
            return
        if isinstance(error, app_commands.NoPrivateMessage):
            await _private_error(
                interaction, "Shoe Bot commands can only be used in a server."
            )
            return

        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        LOGGER.error(
            "Application command %s failed (%s)",
            command_name,
            type(error).__name__,
        )
        await _private_error(
            interaction, "That command could not be completed. Please try again."
        )

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self.game.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Never enable Discord gateway DEBUG logging: raw gateway payloads can
    # contain message content. WARNING keeps operational errors without payloads.
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    try:
        config = load_config()
        database = ShoeDatabase(config.database_path)
    except (ConfigurationError, DatabaseError, OSError) as exc:
        LOGGER.error("Shoe Bot could not start: %s", exc)
        raise SystemExit(2) from exc

    bot = ShoeBot(config, database)
    try:
        bot.run(config.token, log_handler=None)
    finally:
        database.close()


if __name__ == "__main__":
    main()
