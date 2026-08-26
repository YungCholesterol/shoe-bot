"""Guild-scoped slash commands and administrator configuration views."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .database import DatabaseError, GuildStats, ShoeDatabase
from .shoe_game import FOOTWEAR_EMOJIS, ShoeGame


LOGGER = logging.getLogger(__name__)
EMBED_COLOUR = discord.Colour.from_rgb(43, 45, 49)
ACHIEVEMENT_THRESHOLDS = (1, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000)
PRIVACY_URL = "https://github.com/YungCholesterol/shoe-bot/blob/main/PRIVACY.md"
TERMS_URL = "https://github.com/YungCholesterol/shoe-bot/blob/main/TERMS.md"
SUPPORT_EMAIL = "yungcholesterol@gmail.com"
REQUIRED_CHANNEL_PERMISSIONS = (
    ("view_channel", "View Channel"),
    ("send_messages", "Send Messages"),
    ("add_reactions", "Add Reactions"),
    ("read_message_history", "Read Message History"),
)


async def _private_error(interaction: discord.Interaction, text: str) -> None:
    try:
        if (
            interaction.response.type
            is discord.InteractionResponseType.deferred_channel_message
        ):
            await interaction.edit_original_response(
                content=text,
                embed=None,
                view=None,
            )
        elif interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
    except discord.HTTPException as exc:
        LOGGER.warning("Could not send an interaction response (%s)", type(exc).__name__)


async def _respond(
    interaction: discord.Interaction,
    content: str | None = None,
    **kwargs: object,
) -> None:
    """Complete either a fresh or deferred application-command response."""
    if (
        interaction.response.type
        is discord.InteractionResponseType.deferred_channel_message
    ):
        kwargs.pop("ephemeral", None)
        await interaction.edit_original_response(content=content, **kwargs)
    else:
        await interaction.response.send_message(content, **kwargs)


def _disable_view(view: discord.ui.View) -> None:
    for item in view.children:
        if hasattr(item, "disabled"):
            item.disabled = True  # type: ignore[attr-defined]


def _missing_permissions(
    guild: discord.Guild,
    channel: discord.abc.GuildChannel,
) -> list[str]:
    bot_member = guild.me
    if bot_member is None:
        return [label for _attribute, label in REQUIRED_CHANNEL_PERMISSIONS]
    permissions = channel.permissions_for(bot_member)
    return [
        label
        for attribute, label in REQUIRED_CHANNEL_PERMISSIONS
        if not getattr(permissions, attribute, False)
    ]


def _next_achievement(count: int) -> int | None:
    return next((value for value in ACHIEVEMENT_THRESHOLDS if value > count), None)


def _settings_embed(
    *,
    title: str,
    channel_id: int | None,
    matching_mode: str,
    gameplay_mode: str,
    guild: discord.Guild,
) -> discord.Embed:
    channel = guild.get_channel(channel_id) if channel_id is not None else None
    if channel is None:
        channel_text = "Not selected"
        permission_text = "Select a text channel."
    else:
        channel_text = channel.mention
        missing = _missing_permissions(guild, channel)
        permission_text = "Ready" if not missing else "Missing: " + ", ".join(missing)

    matching_text = (
        "Creative (recommended): shoe text, fixed creative spellings, footwear "
        "emoji, and shoe-named custom emoji or stickers."
        if matching_mode == "creative"
        else "Classic: a case-insensitive `shoe` substring only."
    )
    gameplay_text = (
        "Relay (recommended): consecutive accepted messages must come from "
        "different users."
        if gameplay_mode == "relay"
        else "Standard: the same user may contribute consecutive messages."
    )

    embed = discord.Embed(
        title=title,
        description=(
            "Choose the game channel and rules, review permissions, then save. "
            "Nothing changes until you select Save settings. Saving a different "
            "channel or mode completes any active streak; totals, best streak, "
            "personal counts, and existing records are not reset."
        ),
        colour=EMBED_COLOUR,
    )
    embed.add_field(name="Channel", value=channel_text, inline=False)
    embed.add_field(name="Matching", value=matching_text, inline=False)
    embed.add_field(name="Gameplay", value=gameplay_text, inline=False)
    embed.add_field(name="Permission check", value=permission_text, inline=False)
    return embed


class SetupWizardView(discord.ui.View):
    """Ephemeral, requester-bound setup/settings workflow."""

    def __init__(
        self,
        *,
        game: ShoeGame,
        guild: discord.Guild,
        requester_id: int,
        initial_channel_id: int | None,
        initial_matching_mode: str,
        initial_gameplay_mode: str,
        is_current: Callable[[], bool],
        finished: Callable[[], None],
        title: str,
    ) -> None:
        super().__init__(timeout=180.0)
        self._game = game
        self._guild = guild
        self._guild_id = guild.id
        self._requester_id = requester_id
        self._channel_id = initial_channel_id
        self._matching_mode = initial_matching_mode
        self._gameplay_mode = initial_gameplay_mode
        self._is_current = is_current
        self._finished = finished
        self._title = title
        self._consumed = False
        self._saved = False
        self._callback_lock = asyncio.Lock()
        self._message: discord.InteractionMessage | None = None

        for option in self.matching_select.options:
            option.default = option.value == initial_matching_mode
        for option in self.gameplay_select.options:
            option.default = option.value == initial_gameplay_mode

    def bind_message(self, message: discord.InteractionMessage) -> None:
        self._message = message

    def build_embed(self) -> discord.Embed:
        return _settings_embed(
            title=self._title,
            channel_id=self._channel_id,
            matching_mode=self._matching_mode,
            gameplay_mode=self._gameplay_mode,
            guild=self._guild,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._requester_id:
            await _private_error(
                interaction,
                "Only the administrator who opened these settings can use them.",
            )
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        valid = bool(
            interaction.guild_id == self._guild_id
            and permissions
            and permissions.administrator
            and self._is_current()
            and not self._consumed
        )
        if not valid:
            await _private_error(
                interaction,
                "These settings are no longer valid. Open the command again as an administrator.",
            )
        return valid

    def _consume(self) -> bool:
        if self._consumed or not self._is_current():
            return False
        self._consumed = True
        self._finished()
        return True

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Choose the dedicated game channel",
        min_values=1,
        max_values=1,
        row=0,
    )
    async def channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect,
    ) -> None:
        selected = select.values[0]
        await interaction.response.defer()
        async with self._callback_lock:
            if self._consumed or not self._is_current():
                await _private_error(interaction, "These settings are no longer valid.")
                return
            if getattr(selected, "guild", self._guild) != self._guild:
                await _private_error(interaction, "Select a channel from this server.")
                return
            self._channel_id = selected.id
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.select(
        placeholder="Choose matching mode",
        options=[
            discord.SelectOption(
                label="Creative (recommended)",
                value="creative",
                description="Accept fixed creative spellings and footwear emoji",
            ),
            discord.SelectOption(
                label="Classic",
                value="classic",
                description="Accept only text containing shoe",
            ),
        ],
        row=1,
    )
    async def matching_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        selected_mode = select.values[0]
        await interaction.response.defer()
        async with self._callback_lock:
            if self._consumed or not self._is_current():
                await _private_error(interaction, "These settings are no longer valid.")
                return
            self._matching_mode = selected_mode
            for option in select.options:
                option.default = option.value == self._matching_mode
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.select(
        placeholder="Choose gameplay mode",
        options=[
            discord.SelectOption(
                label="Relay (recommended)",
                value="relay",
                description="Different users must alternate accepted messages",
            ),
            discord.SelectOption(
                label="Standard",
                value="standard",
                description="The same user may post consecutive accepted messages",
            ),
        ],
        row=2,
    )
    async def gameplay_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ) -> None:
        selected_mode = select.values[0]
        await interaction.response.defer()
        async with self._callback_lock:
            if self._consumed or not self._is_current():
                await _private_error(interaction, "These settings are no longer valid.")
                return
            self._gameplay_mode = selected_mode
            for option in select.options:
                option.default = option.value == self._gameplay_mode
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(label="Recheck permissions", style=discord.ButtonStyle.secondary, row=3)
    async def recheck(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if self._consumed or not self._is_current():
                await _private_error(interaction, "These settings are no longer valid.")
                return
            await interaction.edit_original_response(
                embed=self.build_embed(),
                view=self,
            )

    @discord.ui.button(label="Save settings", style=discord.ButtonStyle.primary, row=3)
    async def save(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if self._consumed or not self._is_current():
                await _private_error(interaction, "These settings have already been used.")
                return
            channel = (
                self._guild.get_channel(self._channel_id)
                if self._channel_id is not None
                else None
            )
            if not isinstance(channel, discord.TextChannel):
                await _private_error(
                    interaction, "Select an existing text channel before saving."
                )
                return
            missing = _missing_permissions(self._guild, channel)
            if missing:
                await _private_error(
                    interaction,
                    "I still need these permissions in that channel: "
                    + ", ".join(missing)
                    + ". Fix them, then select Recheck permissions.",
                )
                return
            if not self._consume():
                await _private_error(interaction, "These settings have already been used.")
                return

            try:
                config = await self._game.configure_guild(
                    self._guild_id,
                    channel.id,
                    self._matching_mode,
                    self._gameplay_mode,
                )
                self._saved = True
            except DatabaseError as exc:
                LOGGER.error("Could not save Shoe configuration (%s)", type(exc).__name__)
                _disable_view(self)
                self.stop()
                try:
                    await interaction.edit_original_response(
                        content=(
                            "I could not verify that the settings were saved. "
                            "Open `/shoesettings` and check the current values."
                        ),
                        embed=None,
                        view=self,
                    )
                except discord.HTTPException as response_exc:
                    LOGGER.warning(
                        "Could not report a settings failure (%s)",
                        type(response_exc).__name__,
                    )
                return
            finally:
                if self._consumed:
                    _disable_view(self)
                    self.stop()

            embed = discord.Embed(
                title="Settings saved",
                description=(
                    f"Channel: <#{config.channel_id}>\n"
                    f"Matching: {config.matching_mode.title()}\n"
                    f"Gameplay: {config.gameplay_mode.title()}\n\n"
                    "Totals, best streak, personal counts, and existing records "
                    "were not reset. A different channel or mode completes any "
                    "active streak and considers it for the Hall of Fame."
                ),
                colour=EMBED_COLOUR,
            )
            try:
                await interaction.edit_original_response(embed=embed, view=self)
            except discord.HTTPException as exc:
                LOGGER.warning(
                    "Settings commit succeeded; confirmation failed (%s)",
                    type(exc).__name__,
                )
                try:
                    await interaction.followup.send(
                        "Settings were saved, but I could not update the panel. "
                        "Run `/shoesettings` to verify them.",
                        ephemeral=True,
                    )
                except discord.HTTPException as followup_exc:
                    LOGGER.warning(
                        "Could not send saved-settings fallback (%s)",
                        type(followup_exc).__name__,
                    )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if not self._consume():
                await _private_error(interaction, "These settings have already been used.")
                return
            _disable_view(self)
            self.stop()
            try:
                await interaction.edit_original_response(
                    content="Canceled. No settings were changed.", embed=None, view=self
                )
            except discord.HTTPException as exc:
                LOGGER.warning("Could not confirm settings cancellation (%s)", type(exc).__name__)

    async def on_timeout(self) -> None:
        async with self._callback_lock:
            if self._consumed:
                return
            self._consumed = True
            self._finished()
            _disable_view(self)
            self.stop()
            if self._message is None:
                return
            try:
                await self._message.edit(
                    content="Settings expired. No changes were saved.", embed=None, view=self
                )
            except discord.HTTPException as exc:
                LOGGER.warning("Could not update expired settings (%s)", type(exc).__name__)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item,
    ) -> None:
        LOGGER.error("Settings interaction failed (%s)", type(error).__name__)
        async with self._callback_lock:
            if not self._consumed:
                self._consumed = True
                self._finished()
            _disable_view(self)
            self.stop()
        text = (
            "Settings were saved, but the panel failed to update. Run `/shoesettings` to verify them."
            if self._saved
            else "The settings action failed. Open `/shoesettings` and verify the current values."
        )
        await _private_error(interaction, text)


class ResetConfirmationView(discord.ui.View):
    def __init__(
        self,
        game: ShoeGame,
        guild_id: int,
        requester_id: int,
        is_current: Callable[[], bool],
        finished: Callable[[], None],
    ) -> None:
        super().__init__(timeout=30.0)
        self._game = game
        self._guild_id = guild_id
        self._requester_id = requester_id
        self._is_current = is_current
        self._finished = finished
        self._consumed = False
        self._reset_completed = False
        self._callback_lock = asyncio.Lock()
        self._message: discord.InteractionMessage | None = None

    def bind_message(self, message: discord.InteractionMessage) -> None:
        self._message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._requester_id:
            await _private_error(
                interaction,
                "Only the administrator who started this reset can confirm it.",
            )
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        valid = bool(
            interaction.guild_id == self._guild_id
            and permissions
            and permissions.administrator
            and self._is_current()
            and not self._consumed
        )
        if not valid:
            await _private_error(
                interaction,
                "This reset is no longer valid. Run `/reset` again as an administrator.",
            )
        return valid

    def _consume(self) -> bool:
        if self._consumed or not self._is_current():
            return False
        self._consumed = True
        self._finished()
        return True

    @discord.ui.button(label="Reset all counts", style=discord.ButtonStyle.danger)
    async def confirm(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if not self._consume():
                await _private_error(interaction, "This reset has already been used.")
                return
            try:
                await self._game.reset_guild_stats(self._guild_id)
                self._reset_completed = True
            except DatabaseError as exc:
                LOGGER.error("Could not reset Shoe statistics (%s)", type(exc).__name__)
                _disable_view(self)
                self.stop()
                try:
                    await interaction.edit_original_response(
                        content=(
                            "I could not verify that the reset completed. "
                            "Check `/stats` before trying again."
                        ),
                        view=self,
                    )
                except discord.HTTPException as response_exc:
                    LOGGER.warning(
                        "Could not report a reset failure (%s)",
                        type(response_exc).__name__,
                    )
                return
            finally:
                if self._consumed:
                    _disable_view(self)
                    self.stop()

            try:
                await interaction.edit_original_response(
                    content=(
                        "All counts, streaks, personal totals, Hall of Fame records, "
                        "and Relay state for this server were reset. The channel and "
                        "game modes were preserved."
                    ),
                    view=self,
                )
            except discord.HTTPException as exc:
                LOGGER.warning(
                    "Reset commit succeeded; confirmation failed (%s)",
                    type(exc).__name__,
                )
                try:
                    await interaction.followup.send(
                        "The reset completed, but I could not update the panel. "
                        "Run `/stats` to verify it.",
                        ephemeral=True,
                    )
                except discord.HTTPException as followup_exc:
                    LOGGER.warning(
                        "Could not send completed-reset fallback (%s)",
                        type(followup_exc).__name__,
                    )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if not self._consume():
                await _private_error(interaction, "This reset has already been used.")
                return
            _disable_view(self)
            self.stop()
            try:
                await interaction.edit_original_response(
                    content="Reset canceled. Nothing was changed.", view=self
                )
            except discord.HTTPException as exc:
                LOGGER.warning("Could not confirm reset cancellation (%s)", type(exc).__name__)

    async def on_timeout(self) -> None:
        async with self._callback_lock:
            if self._consumed:
                return
            self._consumed = True
            self._finished()
            _disable_view(self)
            self.stop()
            if self._message is None:
                return
            try:
                await self._message.edit(
                    content="Reset canceled because confirmation timed out.", view=self
                )
            except discord.HTTPException as exc:
                LOGGER.warning("Could not update an expired reset (%s)", type(exc).__name__)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item,
    ) -> None:
        LOGGER.error("Reset confirmation failed (%s)", type(error).__name__)
        async with self._callback_lock:
            if not self._consumed:
                self._consumed = True
                self._finished()
            _disable_view(self)
            self.stop()
        text = (
            "The reset completed, but the panel failed to update. Run `/stats` to verify it."
            if self._reset_completed
            else "The reset action failed. Check `/stats` before trying again."
        )
        await _private_error(interaction, text)


class ShoeCommands(commands.Cog):
    def __init__(self, database: ShoeDatabase, game: ShoeGame) -> None:
        self._database = database
        self._game = game
        self._pending_reset_tokens: dict[int, object] = {}
        self._pending_settings_tokens: dict[int, object] = {}

    async def _guild_stats_or_error(
        self, interaction: discord.Interaction
    ) -> GuildStats | None:
        if interaction.guild_id is None:
            await _private_error(interaction, "This command can only be used in a server.")
            return None
        try:
            stats = await self._database.run(
                self._database.get_guild_stats,
                interaction.guild_id,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read Shoe statistics (%s)", type(exc).__name__)
            await _private_error(interaction, "Shoe statistics are temporarily unavailable.")
            return None
        if stats is None:
            await _private_error(
                interaction,
                "Shoe Bot is not configured here. Create a dedicated text channel "
                "such as `#shoe`, then have an administrator run `/setup`.",
            )
            return None
        return stats

    async def _open_settings(
        self,
        interaction: discord.Interaction,
        *,
        stats: GuildStats | None,
        title: str,
    ) -> None:
        if interaction.guild is None or interaction.guild_id is None:
            await _private_error(interaction, "This command can only be used in a server.")
            return

        token = object()
        guild_id = interaction.guild_id
        self._pending_settings_tokens[guild_id] = token

        def is_current() -> bool:
            return self._pending_settings_tokens.get(guild_id) is token

        def finished() -> None:
            if is_current():
                self._pending_settings_tokens.pop(guild_id, None)

        try:
            view = SetupWizardView(
                game=self._game,
                guild=interaction.guild,
                requester_id=interaction.user.id,
                initial_channel_id=stats.channel_id if stats else None,
                initial_matching_mode=stats.matching_mode if stats else "creative",
                initial_gameplay_mode=stats.gameplay_mode if stats else "relay",
                is_current=is_current,
                finished=finished,
                title=title,
            )
            message = await interaction.edit_original_response(
                embed=view.build_embed(),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            view.bind_message(message)
        except BaseException:
            finished()
            if "view" in locals():
                view.stop()
            raise

    @app_commands.command(name="setup", description="Set up Shoe Bot in this server")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = None
        if interaction.guild_id is not None:
            try:
                stats = await self._database.run(
                    self._database.get_guild_stats,
                    interaction.guild_id,
                )
            except DatabaseError as exc:
                LOGGER.error("Could not read setup state (%s)", type(exc).__name__)
                await _private_error(interaction, "Setup is temporarily unavailable.")
                return
        await self._open_settings(interaction, stats=stats, title="Shoe Bot setup")

    @app_commands.command(
        name="shoesettings", description="Change Shoe Bot's channel or game modes"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shoesettings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is not None:
            await self._open_settings(interaction, stats=stats, title="Shoe Bot settings")

    @app_commands.command(name="stats", description="Show server or user Shoe statistics")
    @app_commands.describe(user="Optionally show one user's accepted Shoe total and rank")
    @app_commands.guild_only()
    async def stats(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        if user is None:
            embed = discord.Embed(title="Shoe statistics", colour=EMBED_COLOUR)
            embed.add_field(name="Total", value=f"{stats.total_shoes:,}")
            embed.add_field(name="Current streak", value=f"{stats.current_streak:,}")
            embed.add_field(name="Best streak", value=f"{stats.best_streak:,}")
            embed.set_footer(
                text=f"{stats.matching_mode.title()} matching · {stats.gameplay_mode.title()} gameplay"
            )
            await _respond(interaction, embed=embed)
            return

        try:
            user_stats = await self._database.run(
                self._database.get_user_stats,
                interaction.guild_id,
                user.id,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read user statistics (%s)", type(exc).__name__)
            await _private_error(interaction, "That user's statistics are unavailable.")
            return
        rank = f"#{user_stats.rank:,}" if user_stats.rank is not None else "Unranked"
        next_value = _next_achievement(user_stats.shoe_count)
        progress = (
            f"{user_stats.shoe_count:,} / {next_value:,}"
            if next_value is not None
            else "All milestones unlocked"
        )
        embed = discord.Embed(
            title="Personal Shoe statistics",
            description=user.mention,
            colour=EMBED_COLOUR,
        )
        embed.add_field(name="Accepted messages", value=f"{user_stats.shoe_count:,}")
        embed.add_field(name="Leaderboard rank", value=rank)
        embed.add_field(name="Next achievement", value=progress, inline=False)
        await _respond(
            interaction,
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="leaderboard", description="Show the top 10 Shoe players")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if await self._guild_stats_or_error(interaction) is None:
            return
        try:
            entries = await self._database.run(
                self._database.get_leaderboard,
                interaction.guild_id,
                limit=10,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read leaderboard (%s)", type(exc).__name__)
            await _private_error(interaction, "The leaderboard is temporarily unavailable.")
            return

        embed = discord.Embed(
            title="Shoe leaderboard",
            description="Top 10 by accepted messages",
            colour=EMBED_COLOUR,
        )
        if entries:
            embed.add_field(
                name="Rankings",
                value="\n".join(
                    f"`{entry.rank:>2}`  <@{entry.user_id}>  **{entry.shoe_count:,}**"
                    for entry in entries
                ),
                inline=False,
            )
        else:
            embed.description = "No accepted messages yet."
        embed.set_footer(text="Use /stats user:@user to check any user's rank.")
        await _respond(
            interaction,
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="count", description="Show the server's total Shoe count")
    @app_commands.guild_only()
    async def count(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is not None:
            await _respond(
                interaction,
                f"Total accepted Shoe messages: **{stats.total_shoes:,}**"
            )

    @app_commands.command(name="records", description="Show the server's Hall of Fame")
    @app_commands.guild_only()
    async def records(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        try:
            entries = await self._database.run(
                self._database.get_hall_of_fame,
                interaction.guild_id,
                limit=10,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read Hall of Fame (%s)", type(exc).__name__)
            await _private_error(interaction, "The Hall of Fame is temporarily unavailable.")
            return
        embed = discord.Embed(
            title="Shoe Hall of Fame",
            description=(
                f"Current streak: **{stats.current_streak:,}**\n"
                f"Best streak: **{stats.best_streak:,}**"
            ),
            colour=EMBED_COLOUR,
        )
        if entries:
            lines = []
            for entry in entries:
                date = (
                    "recorded before Hall of Fame"
                    if entry.completed_at is None or entry.is_legacy
                    else f"completed <t:{entry.completed_at}:d>"
                )
                lines.append(
                    f"`{entry.rank:>2}`  **{entry.streak_length:,}** messages · {date}"
                )
            embed.add_field(name="Completed streaks", value="\n".join(lines), inline=False)
        else:
            embed.add_field(
                name="Completed streaks",
                value="No completed streaks have been recorded yet.",
                inline=False,
            )
        await _respond(interaction, embed=embed)

    @app_commands.command(
        name="achievements", description="Show Shoe milestones for a user"
    )
    @app_commands.describe(user="Optionally show another user's milestones")
    @app_commands.guild_only()
    async def achievements(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        if await self._guild_stats_or_error(interaction) is None:
            return
        target = user or interaction.user
        try:
            user_stats = await self._database.run(
                self._database.get_user_stats,
                interaction.guild_id,
                target.id,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read achievements (%s)", type(exc).__name__)
            await _private_error(interaction, "Achievements are temporarily unavailable.")
            return
        lines = [
            f"{'Unlocked' if user_stats.shoe_count >= threshold else 'Locked'} · {threshold:,} accepted messages"
            for threshold in ACHIEVEMENT_THRESHOLDS
        ]
        embed = discord.Embed(
            title="Shoe achievements",
            description=f"{target.mention}\n{user_stats.shoe_count:,} accepted messages",
            colour=EMBED_COLOUR,
        )
        embed.add_field(name="Milestones", value="\n".join(lines), inline=False)
        embed.set_footer(text="Achievements are derived from the stored personal count.")
        await _respond(
            interaction,
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="shoerules", description="Show this server's Shoe rules")
    @app_commands.guild_only()
    async def shoerules(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        matching = (
            "Creative matching accepts `shoe` anywhere, fixed creative spellings "
            "such as `s-h-o-e`, `sh0e`, styled Unicode text, footwear or skate "
            "emoji (`👞 👟 👠 👡 👢 🥾 🥿 🩰 🩴 ⛸️ 🛼`), and shoe-named custom "
            "emoji or stickers. Attachments and reactions are not analyzed."
            if stats.matching_mode == "creative"
            else "Classic matching accepts messages containing `shoe`, case-insensitively."
        )
        gameplay = (
            "Relay gameplay requires different users on consecutive accepted "
            "messages. Repeating twice in a row breaks the streak."
            if stats.gameplay_mode == "relay"
            else "Standard gameplay allows consecutive accepted messages from the same user."
        )
        embed = discord.Embed(
            title="Shoe rules",
            description=f"{matching}\n\n{gameplay}",
            colour=EMBED_COLOUR,
        )
        embed.add_field(
            name="Always",
            value=(
                "One Discord message can add at most one count. Invalid messages "
                "break non-zero streaks. Bots, webhooks, Discord system notices, "
                "edits, and deletions are ignored; ordinary replies count."
            ),
            inline=False,
        )
        await _respond(interaction, embed=embed)

    @app_commands.command(name="shoehelp", description="Show Shoe Bot commands")
    @app_commands.guild_only()
    async def shoehelp(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Shoe Bot help",
            description="A server game that builds a streak from accepted Shoe messages.",
            colour=EMBED_COLOUR,
        )
        embed.add_field(
            name="Game commands",
            value=(
                "`/count` · `/stats [user]` · `/leaderboard` · `/records` · "
                "`/achievements [user]` · `/shoerules` · `/forgetme`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Administrator commands",
            value="`/setup` · `/shoesettings` · `/diagnose` · `/reset`",
            inline=False,
        )
        embed.add_field(
            name="Policies and support",
            value=(
                f"[Privacy]({PRIVACY_URL}) · [Terms]({TERMS_URL})\n"
                f"Support: {SUPPORT_EMAIL}\n"
                "Independent; not affiliated with or endorsed by Barack Obama."
            ),
            inline=False,
        )
        embed.set_footer(text="Administrator commands recheck permission when used.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="diagnose", description="Check Shoe Bot's channel permissions"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def diagnose(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None or interaction.guild is None:
            return
        channel = interaction.guild.get_channel(stats.channel_id)
        if channel is None:
            status = "Configured channel is missing or unavailable. Run `/setup`."
        elif not isinstance(channel, discord.TextChannel):
            status = "Configured channel is not a text channel. Run `/setup`."
        else:
            missing = _missing_permissions(interaction.guild, channel)
            status = "Ready" if not missing else "Missing: " + ", ".join(missing)
        embed = discord.Embed(
            title="Shoe Bot diagnostic",
            description=status,
            colour=EMBED_COLOUR,
        )
        embed.add_field(name="Channel", value=f"<#{stats.channel_id}>", inline=False)
        embed.add_field(
            name="Portal setting",
            value=(
                "Message Content Intent must also be enabled in the Discord Developer "
                "Portal. This command cannot inspect that portal setting."
            ),
            inline=False,
        )
        await _respond(
            interaction,
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="forgetme", description="Delete your stored personal Shoe statistics"
    )
    @app_commands.describe(confirm="Required confirmation before deletion")
    @app_commands.choices(
        confirm=[
            app_commands.Choice(name="Yes, delete my stored statistics", value="delete")
        ]
    )
    @app_commands.guild_only()
    async def forgetme(
        self,
        interaction: discord.Interaction,
        confirm: app_commands.Choice[str],
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self._guild_stats_or_error(interaction) is None:
            return
        if confirm.value != "delete":
            await _private_error(interaction, "Nothing was deleted.")
            return
        try:
            result = await self._game.delete_user_stats(
                interaction.guild_id,
                interaction.user.id,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not delete user statistics (%s)", type(exc).__name__)
            await _private_error(
                interaction,
                "I could not verify that deletion completed. Try again later.",
            )
            return
        text = (
            "Your stored user ID and personal count were deleted."
            if result.deleted
            else "No personal statistics were stored for you in this server."
        )
        if result.ended_relay_streak:
            text += (
                f" The active {result.ended_relay_streak:,}-message Relay streak "
                "was ended so your user ID is no longer retained as its last contributor."
            )
        text += " Aggregate totals and the best streak were preserved."
        try:
            await interaction.edit_original_response(content=text)
        except discord.HTTPException as exc:
            LOGGER.warning(
                "User-data deletion committed; confirmation failed (%s)",
                type(exc).__name__,
            )
            try:
                await interaction.followup.send(
                    "Your deletion request completed, but I could not update the response.",
                    ephemeral=True,
                )
            except discord.HTTPException as followup_exc:
                LOGGER.warning(
                    "Could not send deletion fallback (%s)",
                    type(followup_exc).__name__,
                )

    @app_commands.command(name="reset", description="Reset all Shoe counts in this server")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self._guild_stats_or_error(interaction) is None or interaction.guild_id is None:
            return
        token = object()
        guild_id = interaction.guild_id
        self._pending_reset_tokens[guild_id] = token

        def is_current() -> bool:
            return self._pending_reset_tokens.get(guild_id) is token

        def finished() -> None:
            if is_current():
                self._pending_reset_tokens.pop(guild_id, None)

        try:
            view = ResetConfirmationView(
                self._game,
                guild_id,
                interaction.user.id,
                is_current,
                finished,
            )
            message = await interaction.edit_original_response(
                content=(
                    "Reset this server's total count, current and best streaks, "
                    "personal counts, Hall of Fame records, and Relay state? The "
                    "configured channel and game modes will remain. This cannot be undone."
                ),
                view=view,
            )
            view.bind_message(message)
        except BaseException:
            finished()
            if "view" in locals():
                view.stop()
            raise
