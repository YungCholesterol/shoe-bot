"""Guild-scoped slash command behavior for Shoe Bot."""

from __future__ import annotations

from collections.abc import Callable
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .database import DatabaseError, GuildStats, ShoeDatabase
from .shoe_game import ShoeGame


LOGGER = logging.getLogger(__name__)


async def _private_error(interaction: discord.Interaction, text: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException as exc:
        LOGGER.warning("Could not send an interaction response (%s)", type(exc).__name__)


def _disable_view(view: discord.ui.View) -> None:
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            item.disabled = True


class ResetConfirmationView(discord.ui.View):
    def __init__(
        self,
        database: ShoeDatabase,
        guild_id: int,
        requester_id: int,
        is_current: Callable[[], bool],
        finished: Callable[[], None],
    ) -> None:
        super().__init__(timeout=30.0)
        self._database = database
        self._guild_id = guild_id
        self._requester_id = requester_id
        self._is_current = is_current
        self._finished = finished
        self._consumed = False
        self._message: discord.InteractionMessage | None = None

    def bind_message(self, message: discord.InteractionMessage) -> None:
        self._message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self._requester_id:
            permissions = getattr(interaction.user, "guild_permissions", None)
            is_administrator = bool(permissions and permissions.administrator)
            if (
                interaction.guild_id == self._guild_id
                and is_administrator
                and self._is_current()
                and not self._consumed
            ):
                return True
            await _private_error(
                interaction,
                "This reset is no longer valid. Run `/shoereset` again as an administrator.",
            )
            return False
        await _private_error(
            interaction,
            "Only the administrator who started this reset can confirm it.",
        )
        return False

    def _consume(self) -> bool:
        # No await occurs here, so two queued button callbacks cannot both win.
        if self._consumed or not self._is_current():
            return False
        self._consumed = True
        self._finished()
        return True

    @discord.ui.button(label="Reset this server", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if not self._consume():
            await _private_error(
                interaction, "This reset confirmation has already been used."
            )
            return
        try:
            self._database.reset_guild_stats(self._guild_id)
        except DatabaseError as exc:
            LOGGER.error("Could not reset Shoe statistics (%s)", type(exc).__name__)
            _disable_view(self)
            self.stop()
            await _private_error(
                interaction,
                "I could not verify that the reset completed. Check `/shoestats` "
                "before trying again.",
            )
            return

        _disable_view(self)
        self.stop()
        try:
            await interaction.response.edit_message(
                content="✅ This server's Shoe statistics and leaderboard were reset.",
                view=self,
            )
        except discord.HTTPException as exc:
            LOGGER.warning("Could not confirm a completed reset (%s)", type(exc).__name__)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        if not self._consume():
            await _private_error(
                interaction, "This reset confirmation has already been used."
            )
            return
        _disable_view(self)
        self.stop()
        try:
            await interaction.response.edit_message(
                content="Reset canceled. No statistics were changed.",
                view=self,
            )
        except discord.HTTPException as exc:
            LOGGER.warning("Could not confirm reset cancellation (%s)", type(exc).__name__)

    async def on_timeout(self) -> None:
        if not self._consumed:
            self._consumed = True
            self._finished()
        _disable_view(self)
        if self._message is None:
            return
        try:
            await self._message.edit(
                content="Reset canceled because confirmation timed out.",
                view=self,
            )
        except discord.HTTPException as exc:
            LOGGER.warning("Could not update an expired reset prompt (%s)", type(exc).__name__)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item,
    ) -> None:
        LOGGER.error("Reset confirmation failed (%s)", type(error).__name__)
        await _private_error(interaction, "The reset action failed. Please try again.")


class ShoeCommands(commands.Cog):
    def __init__(self, database: ShoeDatabase, game: ShoeGame) -> None:
        self._database = database
        self._game = game
        self._pending_reset_tokens: dict[int, object] = {}

    async def _guild_stats_or_error(
        self, interaction: discord.Interaction
    ) -> GuildStats | None:
        if interaction.guild_id is None:
            await _private_error(
                interaction, "Shoe Bot commands can only be used in a server."
            )
            return None
        try:
            stats = self._database.get_guild_stats(interaction.guild_id)
        except DatabaseError as exc:
            LOGGER.error("Could not read Shoe statistics (%s)", type(exc).__name__)
            await _private_error(interaction, "Shoe statistics are temporarily unavailable.")
            return None
        if stats is None:
            await _private_error(
                interaction,
                "Shoe Bot is not configured here yet. Create a dedicated text "
                "channel such as `#shoe`, then have an administrator run "
                "`/shoesetup` and select it.",
            )
            return None
        return stats

    @app_commands.command(
        name="shoesetup",
        description="Set up Shoe Bot after creating a dedicated text channel",
    )
    @app_commands.describe(
        channel="Select #shoe or another dedicated text channel for the game"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shoesetup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ) -> None:
        if interaction.guild is None:
            await _private_error(interaction, "This command can only be used in a server.")
            return

        bot_member = interaction.guild.me
        if bot_member is None:
            await _private_error(interaction, "I could not verify my channel permissions.")
            return

        permissions = channel.permissions_for(bot_member)
        required = (
            ("view_channel", "View Channel"),
            ("send_messages", "Send Messages"),
            ("add_reactions", "Add Reactions"),
            ("read_message_history", "Read Message History"),
            ("embed_links", "Embed Links"),
        )
        missing = [label for attr, label in required if not getattr(permissions, attr)]
        if missing:
            await _private_error(
                interaction,
                f"I need these permissions in {channel.mention} before it can be used: "
                + ", ".join(missing)
                + ".",
            )
            return

        try:
            self._game.configure_channel(interaction.guild.id, channel.id)
        except DatabaseError as exc:
            LOGGER.error("Could not save Shoe configuration (%s)", type(exc).__name__)
            await _private_error(
                interaction, "The Shoe channel could not be saved. Try again later."
            )
            return

        await interaction.response.send_message(
            "**Shoe Bot is ready**\n\n"
            f"Game channel: {channel.mention}\n"
            "Messages containing `shoe` build the streak. Anything else breaks it.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="shoestats", description="Show server or user Shoe statistics"
    )
    @app_commands.describe(user="Optionally show one user's Shoe count and rank")
    @app_commands.guild_only()
    async def shoestats(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return

        if user is None:
            await interaction.response.send_message(
                "👟 **Shoe Statistics**\n\n"
                f"Total Shoes: {stats.total_shoes:,}\n"
                f"Current Streak: {stats.current_streak:,}\n"
                f"Best Streak: {stats.best_streak:,}"
            )
            return

        try:
            user_stats = self._database.get_user_stats(interaction.guild_id, user.id)
        except DatabaseError as exc:
            LOGGER.error("Could not read user Shoe statistics (%s)", type(exc).__name__)
            await _private_error(
                interaction, "That user's Shoe statistics are temporarily unavailable."
            )
            return

        rank = f"#{user_stats.rank:,}" if user_stats.rank is not None else "Unranked"
        await interaction.response.send_message(
            f"👟 **Shoe Statistics for {user.mention}**\n\n"
            f"Valid Shoes: {user_stats.shoe_count:,}\n"
            f"Leaderboard Rank: {rank}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="shoeleaderboard",
        description="Show this server's top 10 Shoe players",
    )
    @app_commands.guild_only()
    async def shoeleaderboard(self, interaction: discord.Interaction) -> None:
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        try:
            entries = self._database.get_leaderboard(interaction.guild_id, limit=10)
        except DatabaseError as exc:
            LOGGER.error("Could not read the Shoe leaderboard (%s)", type(exc).__name__)
            await _private_error(
                interaction, "The Shoe leaderboard is temporarily unavailable."
            )
            return

        embed = discord.Embed(
            title="SHOE BOT // LEADERBOARD",
            description="SERVER TOP 10",
            colour=discord.Colour.blurple(),
        )
        if not entries:
            embed.add_field(
                name="NO SCORES YET",
                value="Be the first to say `shoe`.",
                inline=False,
            )
        else:
            rankings = "\n".join(
                f"`{entry.rank:>2}`  <@{entry.user_id}>  **{entry.shoe_count:,} shoes**"
                for entry in entries
            )
            embed.add_field(name="RANKINGS", value=rankings, inline=False)
        embed.set_footer(
            text="Use /shoestats user:@user to check any player's rank."
        )
        await interaction.response.send_message(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="shoecount", description="Show this server's total Shoe count"
    )
    @app_commands.guild_only()
    async def shoecount(self, interaction: discord.Interaction) -> None:
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        await interaction.response.send_message(f"👟 Total Shoes: **{stats.total_shoes:,}**")

    @app_commands.command(
        name="shoeconfig",
        description="Show this server's configured Shoe channel",
    )
    @app_commands.guild_only()
    async def shoeconfig(self, interaction: discord.Interaction) -> None:
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        guild = interaction.guild
        channel = guild.get_channel(stats.channel_id) if guild is not None else None
        if channel is None:
            await _private_error(
                interaction,
                "The configured Shoe channel no longer exists or is unavailable. "
                "An administrator should run `/shoesetup` again.",
            )
            return
        await interaction.response.send_message(
            f"👟 This server's Shoe channel is {channel.mention}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="shoeforgetme",
        description="Delete your stored user ID and personal Shoe count",
    )
    @app_commands.describe(confirm="Required confirmation before deletion")
    @app_commands.choices(
        confirm=[
            app_commands.Choice(
                name="Yes, delete my stored user statistics",
                value="delete",
            )
        ]
    )
    @app_commands.guild_only()
    async def shoeforgetme(
        self,
        interaction: discord.Interaction,
        confirm: app_commands.Choice[str],
    ) -> None:
        stats = await self._guild_stats_or_error(interaction)
        if stats is None or interaction.guild_id is None:
            return
        if confirm.value != "delete":
            await _private_error(interaction, "No data was deleted.")
            return

        try:
            deleted = self._database.delete_user_stats(
                interaction.guild_id, interaction.user.id
            )
        except DatabaseError as exc:
            LOGGER.error(
                "Could not delete user Shoe statistics (%s)", type(exc).__name__
            )
            await _private_error(
                interaction,
                "I could not verify that deletion completed. Try again later.",
            )
            return

        if deleted:
            text = (
                "✅ Your stored user ID and personal Shoe count were deleted from "
                "this server's leaderboard. Anonymous server totals and streaks "
                "were not changed. A future valid Shoe message will create a new count."
            )
        else:
            text = "✅ No personal Shoe statistics were stored for you in this server."
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(
        name="shoereset", description="Reset this server's Shoe statistics"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shoereset(self, interaction: discord.Interaction) -> None:
        stats = await self._guild_stats_or_error(interaction)
        if stats is None or interaction.guild_id is None:
            return

        token = object()
        self._pending_reset_tokens[interaction.guild_id] = token

        def is_current() -> bool:
            return self._pending_reset_tokens.get(interaction.guild_id) is token

        def finished() -> None:
            if is_current():
                self._pending_reset_tokens.pop(interaction.guild_id, None)

        view = ResetConfirmationView(
            database=self._database,
            guild_id=interaction.guild_id,
            requester_id=interaction.user.id,
            is_current=is_current,
            finished=finished,
        )
        await interaction.response.send_message(
            "⚠️ Reset this server's total, streaks, and entire user leaderboard? "
            "This cannot be undone.",
            ephemeral=True,
            view=view,
        )
        try:
            view.bind_message(await interaction.original_response())
        except discord.HTTPException as exc:
            LOGGER.warning("Could not track a reset prompt (%s)", type(exc).__name__)
