"""Guild-scoped slash commands and administrator configuration views."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .database import (
    DatabaseError,
    GuildStats,
    HallOfFameEntry,
    LeaderboardEntry,
    ShoeDatabase,
)
from .shoe_game import FOOTWEAR_EMOJIS, ShoeGame


LOGGER = logging.getLogger(__name__)
EMBED_COLOUR = discord.Colour.from_rgb(43, 45, 49)
ACHIEVEMENT_THRESHOLDS = (1, 10, 25, 50, 100, 250, 500, 1_000, 2_500, 5_000)
PRIVACY_URL = "https://github.com/YungCholesterol/shoe-bot/blob/main/PRIVACY.md"
TERMS_URL = "https://github.com/YungCholesterol/shoe-bot/blob/main/TERMS.md"
SUPPORT_EMAIL = "yungcholesterol@gmail.com"
RANDOM_SHOE_IMAGE = Path(__file__).resolve().parents[1] / "assets" / "random-shoe-obama.jpg"
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


async def _replace_with_terminal_view(
    interaction: discord.Interaction,
    *,
    text: str,
    view: discord.ui.View,
) -> None:
    """Acknowledge a failed component and leave no enabled-looking controls."""
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(
                content=text,
                embed=None,
                view=view,
            )
        else:
            await interaction.response.edit_message(
                content=text,
                embed=None,
                view=view,
            )
    except discord.HTTPException as exc:
        LOGGER.warning(
            "Could not replace a failed component panel (%s)",
            type(exc).__name__,
        )
        await _private_error(interaction, text)


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


def _rules_text(stats: GuildStats | None) -> tuple[str, str]:
    matching_mode = stats.matching_mode if stats is not None else "creative"
    gameplay_mode = stats.gameplay_mode if stats is not None else "relay"
    matching = (
        "Creative matching accepts `shoe` anywhere, fixed creative spellings "
        "such as `s-h-o-e`, `sh0e`, styled Unicode text, footwear or skate "
        "emoji (`👞 👟 👠 👡 👢 🥾 🥿 🩰 🩴 ⛸️ 🛼`), and shoe-named custom "
        "emoji or stickers. Attachments and reactions are not analyzed."
        if matching_mode == "creative"
        else "Classic matching accepts messages containing `shoe`, case-insensitively."
    )
    gameplay = (
        "Relay gameplay requires different users on consecutive accepted "
        "messages. Repeating twice in a row breaks the streak."
        if gameplay_mode == "relay"
        else "Standard gameplay allows consecutive accepted messages from the same user."
    )
    return matching, gameplay


def _contributors_embed(entries: Sequence[LeaderboardEntry]) -> discord.Embed:
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
    embed.set_footer(text="Use /profile user:@user to check a user's full profile.")
    return embed


def _hall_of_fame_embed(
    stats: GuildStats,
    entries: Sequence[HallOfFameEntry],
) -> discord.Embed:
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
    return embed


class LeaderboardView(discord.ui.View):
    """Requester-bound switcher between contributors and completed streaks."""

    def __init__(
        self,
        *,
        requester_id: int,
        guild_id: int,
        contributors: discord.Embed,
        hall_of_fame: discord.Embed,
    ) -> None:
        super().__init__(timeout=120.0)
        self._requester_id = requester_id
        self._guild_id = guild_id
        self._contributors_embed = contributors
        self._hall_of_fame_embed = hall_of_fame
        self._callback_lock = asyncio.Lock()
        self._message: discord.InteractionMessage | None = None
        self.contributors.disabled = True

    def bind_message(self, message: discord.InteractionMessage) -> None:
        self._message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        valid = bool(
            interaction.user.id == self._requester_id
            and interaction.guild_id == self._guild_id
            and not self.is_finished()
        )
        if not valid:
            await _private_error(
                interaction,
                "Run `/leaderboard` yourself to use these controls.",
            )
        return valid

    async def _show(
        self,
        interaction: discord.Interaction,
        *,
        hall_of_fame: bool,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if self.is_finished():
                await _private_error(interaction, "This leaderboard has expired.")
                return
            self.contributors.disabled = not hall_of_fame
            self.hall_of_fame.disabled = hall_of_fame
            embed = (
                self._hall_of_fame_embed
                if hall_of_fame
                else self._contributors_embed
            )
            await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Contributors", style=discord.ButtonStyle.secondary)
    async def contributors(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self._show(interaction, hall_of_fame=False)

    @discord.ui.button(label="Hall of Fame", style=discord.ButtonStyle.secondary)
    async def hall_of_fame(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self._show(interaction, hall_of_fame=True)

    async def on_timeout(self) -> None:
        async with self._callback_lock:
            _disable_view(self)
            self.stop()
            if self._message is None:
                return
            try:
                await self._message.edit(view=self)
            except discord.HTTPException as exc:
                LOGGER.warning(
                    "Could not disable an expired leaderboard (%s)",
                    type(exc).__name__,
                )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item,
    ) -> None:
        LOGGER.error("Leaderboard interaction failed (%s)", type(error).__name__)
        async with self._callback_lock:
            _disable_view(self)
            self.stop()
        await _replace_with_terminal_view(
            interaction,
            text="The leaderboard panel could not be updated. Run `/leaderboard` again.",
            view=self,
        )


def _settings_embed(
    *,
    title: str,
    channel_id: int | None,
    matching_mode: str,
    gameplay_mode: str,
    random_shoe_enabled: bool,
    random_shoe_channel_ids: Sequence[int],
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
    random_channels = " ".join(f"<#{channel_id}>" for channel_id in random_shoe_channel_ids)
    embed.add_field(
        name="Random Shoe posts",
        value=(
            ("Enabled · " + (random_channels or "no channels selected"))
            if random_shoe_enabled else
            "Off (default). Select channels, choose `/shoelog`, optionally use `/shoetiming`, then turn it on."
        ),
        inline=False,
    )
    embed.add_field(name="Permission check", value=permission_text, inline=False)
    embed.add_field(
        name="Portal check",
        value=(
            "Message Content Intent must be enabled in the Discord Developer "
            "Portal. Shoe Bot cannot inspect that portal setting."
        ),
        inline=False,
    )
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
        initial_random_shoe_enabled: bool = False,
        initial_random_shoe_channel_ids: Sequence[int] = (),
        initial_random_shoe_min_minutes: int = 50,
        initial_random_shoe_max_minutes: int = 103,
        initial_quiet_start_hour: int | None = None,
        initial_quiet_end_hour: int | None = None,
        initial_log_channel_id: int | None = None,
        start_reset: Callable[[discord.Interaction], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(timeout=180.0)
        self._game = game
        self._guild = guild
        self._guild_id = guild.id
        self._requester_id = requester_id
        self._channel_id = initial_channel_id
        self._matching_mode = initial_matching_mode
        self._gameplay_mode = initial_gameplay_mode
        self._random_shoe_enabled = initial_random_shoe_enabled
        self._random_shoe_channel_ids = tuple(initial_random_shoe_channel_ids)
        self._random_shoe_min_minutes = initial_random_shoe_min_minutes
        self._random_shoe_max_minutes = initial_random_shoe_max_minutes
        self._quiet_start_hour = initial_quiet_start_hour
        self._quiet_end_hour = initial_quiet_end_hour
        self._log_channel_id = initial_log_channel_id
        self._is_current = is_current
        self._finished = finished
        self._title = title
        self._start_reset = start_reset
        self._consumed = False
        self._saved = False
        self._callback_lock = asyncio.Lock()
        self._message: discord.InteractionMessage | None = None

        for option in self.matching_select.options:
            option.default = option.value == initial_matching_mode
        for option in self.gameplay_select.options:
            option.default = option.value == initial_gameplay_mode
        if start_reset is None:
            self.remove_item(self.reset_data)
        self.toggle_random_posts.label = (
            "Random posts: ON" if initial_random_shoe_enabled else "Random posts: OFF"
        )
        self.toggle_random_posts.style = (
            discord.ButtonStyle.success
            if initial_random_shoe_enabled else discord.ButtonStyle.secondary
        )

    def bind_message(self, message: discord.InteractionMessage) -> None:
        self._message = message

    def build_embed(self) -> discord.Embed:
        return _settings_embed(
            title=self._title,
            channel_id=self._channel_id,
            matching_mode=self._matching_mode,
            gameplay_mode=self._gameplay_mode,
            random_shoe_enabled=self._random_shoe_enabled,
            random_shoe_channel_ids=self._random_shoe_channel_ids,
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

    @discord.ui.button(label="Run diagnostic", style=discord.ButtonStyle.secondary, row=4)
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
            await interaction.edit_original_response(embed=self.build_embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Random Shoe channels (optional)",
        min_values=0,
        max_values=25,
        row=3,
    )
    async def random_channels_select(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            self._random_shoe_channel_ids = tuple(channel.id for channel in select.values)
            await interaction.edit_original_response(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Random posts: OFF", style=discord.ButtonStyle.secondary, row=4)
    async def toggle_random_posts(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            self._random_shoe_enabled = not self._random_shoe_enabled
            button.label = "Random posts: ON" if self._random_shoe_enabled else "Random posts: OFF"
            button.style = discord.ButtonStyle.success if self._random_shoe_enabled else discord.ButtonStyle.secondary
            await interaction.edit_original_response(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Save settings", style=discord.ButtonStyle.primary, row=4)
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
                    + ". Fix them, then select Run diagnostic.",
                )
                return
            if self._random_shoe_enabled and not self._random_shoe_channel_ids:
                await _private_error(interaction, "Select at least one Random Shoe channel before turning it on.")
                return
            if self._random_shoe_enabled and self._log_channel_id is None:
                await _private_error(interaction, "Choose an audit-log channel with `/shoelog` before turning Random Shoe posts on.")
                return
            for random_channel_id in self._random_shoe_channel_ids:
                random_channel = self._guild.get_channel(random_channel_id)
                if not isinstance(random_channel, discord.TextChannel):
                    await _private_error(interaction, "Every Random Shoe destination must be an existing text channel.")
                    return
                random_missing = _missing_permissions(self._guild, random_channel)
                bot_member = self._guild.me
                if bot_member is None or not random_channel.permissions_for(bot_member).attach_files:
                    random_missing.append("Attach Files")
                if random_missing:
                    await _private_error(interaction, f"I am missing permissions in {random_channel.mention}: " + ", ".join(random_missing))
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
                await self._game.configure_random_shoe(
                    self._guild_id,
                    self._random_shoe_enabled,
                    tuple(self._random_shoe_channel_ids),
                    self._random_shoe_min_minutes,
                    self._random_shoe_max_minutes,
                    self._quiet_start_hour,
                    self._quiet_end_hour,
                    self._log_channel_id,
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
                    await _private_error(
                        interaction,
                        "I could not verify that the settings were saved. "
                        "Open `/shoesettings` and check the current values.",
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
                    f"Random Shoe posts: {'On' if self._random_shoe_enabled else 'Off'}\n"
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
            log_channel = self._guild.get_channel(self._log_channel_id) if self._log_channel_id else None
            if isinstance(log_channel, discord.TextChannel):
                try:
                    await log_channel.send(
                        f"Shoe Bot audit · Settings saved by <@{self._requester_id}>.",
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=4)
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

    @discord.ui.button(
        label="Reset server data",
        style=discord.ButtonStyle.danger,
        row=4,
    )
    async def reset_data(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer()
        async with self._callback_lock:
            if self._consumed or not self._is_current():
                await _private_error(interaction, "These settings are no longer valid.")
                return
            if self._start_reset is None:
                await _private_error(interaction, "Reset is not available during setup.")
                return
            if not self._consume():
                await _private_error(interaction, "These settings have already been used.")
                return
            _disable_view(self)
            self.stop()
            await self._start_reset(interaction)

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
        await _replace_with_terminal_view(interaction, text=text, view=self)


class TimingModal(discord.ui.Modal, title="Random Shoe timing"):
    minimum = discord.ui.TextInput(
        label="Minimum delay (minutes)", min_length=1, max_length=4,
        placeholder="50",
    )
    maximum = discord.ui.TextInput(
        label="Maximum delay (minutes)", min_length=1, max_length=4,
        placeholder="103",
    )
    quiet_start = discord.ui.TextInput(
        label="Quiet start hour UTC (optional)", required=False,
        min_length=1, max_length=2, placeholder="0–23",
    )
    quiet_end = discord.ui.TextInput(
        label="Quiet end hour UTC (optional)", required=False,
        min_length=1, max_length=2, placeholder="0–23",
    )

    def __init__(self, parent: "SettingsHubView") -> None:
        super().__init__()
        self._parent = parent
        self.minimum.default = str(parent.random_min_minutes)
        self.maximum.default = str(parent.random_max_minutes)
        self.quiet_start.default = (
            "" if parent.quiet_start_hour is None else str(parent.quiet_start_hour)
        )
        self.quiet_end.default = (
            "" if parent.quiet_end_hour is None else str(parent.quiet_end_hour)
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self._parent.interaction_check(interaction):
            return
        try:
            minimum = int(self.minimum.value)
            maximum = int(self.maximum.value)
            quiet_start = int(self.quiet_start.value) if self.quiet_start.value else None
            quiet_end = int(self.quiet_end.value) if self.quiet_end.value else None
        except ValueError:
            await interaction.response.send_message(
                "Use whole numbers for timing and quiet hours.", ephemeral=True
            )
            return
        if not 5 <= minimum <= maximum <= 1440:
            await interaction.response.send_message(
                "Timing must be 5–1440 minutes, and minimum cannot exceed maximum.",
                ephemeral=True,
            )
            return
        if (quiet_start is None) != (quiet_end is None) or any(
            value is not None and not 0 <= value <= 23
            for value in (quiet_start, quiet_end)
        ):
            await interaction.response.send_message(
                "Quiet hours need both a start and end from 0–23 UTC, or both blank.",
                ephemeral=True,
            )
            return
        await self._parent.save_timing(
            interaction, minimum, maximum, quiet_start, quiet_end
        )


class SettingsHubView(discord.ui.View):
    """Compact, status-first administrator control center for `/shoesettings`."""

    SECTIONS = ("Game", "Schedule", "Timing", "Logs", "More")

    def __init__(
        self,
        *,
        game: ShoeGame,
        guild: discord.Guild,
        requester_id: int,
        stats: GuildStats,
        is_current: Callable[[], bool],
        finished: Callable[[], None],
        start_reset: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=300.0)
        self._game = game
        self._guild = guild
        self._guild_id = guild.id
        self._requester_id = requester_id
        self._is_current = is_current
        self._finished = finished
        self._start_reset = start_reset
        self._message: discord.InteractionMessage | None = None
        self._section = "Schedule"
        self._closed = False
        self._callback_lock = asyncio.Lock()

        self.channel_id = stats.channel_id
        self.matching_mode = stats.matching_mode
        self.gameplay_mode = stats.gameplay_mode
        self.random_enabled = stats.random_shoe_enabled
        self.random_channel_ids = tuple(stats.random_shoe_channel_ids)
        self.random_min_minutes = stats.random_shoe_min_minutes
        self.random_max_minutes = stats.random_shoe_max_minutes
        self.quiet_start_hour = stats.quiet_start_hour
        self.quiet_end_hour = stats.quiet_end_hour
        self.log_channel_id = stats.log_channel_id
        self.next_send_at = stats.random_shoe_next_at
        self._render()

    def bind_message(self, message: discord.InteractionMessage) -> None:
        self._message = message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._requester_id:
            await _private_error(
                interaction,
                "Only the administrator who opened this control center can use it.",
            )
            return False
        permissions = getattr(interaction.user, "guild_permissions", None)
        valid = bool(
            interaction.guild_id == self._guild_id
            and permissions
            and permissions.administrator
            and self._is_current()
            and not self._closed
        )
        if not valid:
            await _private_error(
                interaction,
                "This control center expired. Run `/shoesettings` again.",
            )
        return valid

    def _health(self) -> tuple[bool, list[str]]:
        problems: list[str] = []
        game_channel = self._guild.get_channel(self.channel_id)
        if not isinstance(game_channel, discord.TextChannel):
            problems.append("game channel is missing")
        elif _missing_permissions(self._guild, game_channel):
            problems.append("game channel permissions need attention")
        if not RANDOM_SHOE_IMAGE.is_file():
            problems.append("Shoe image is missing")
        if self.random_enabled and not self.random_channel_ids:
            problems.append("scheduled posts need a destination")
        if self.random_enabled and self.log_channel_id is None:
            problems.append("scheduled posts need an audit-log channel")
        return not problems, problems

    def build_embed(self) -> discord.Embed:
        healthy, problems = self._health()
        status = "All systems healthy" if healthy else "Needs attention"
        colour = discord.Colour.green() if healthy else discord.Colour.orange()
        if not self.random_enabled:
            next_post = "Paused"
        elif self.next_send_at is not None:
            next_post = f"<t:{self.next_send_at}:R>"
        else:
            next_post = "Scheduling now"
        embed = discord.Embed(
            title="Shoe Bot control center",
            description=(
                f"**{status}** · Next scheduled Shoe: **{next_post}**\n"
                "Use the tabs below. Only the controls for the open section are shown."
            ),
            colour=colour,
        )
        embed.add_field(
            name="Overview",
            value=(
                f"Game <#{self.channel_id}> · {self.matching_mode.title()} / {self.gameplay_mode.title()}\n"
                f"Scheduled posts {'On' if self.random_enabled else 'Off'} · "
                f"{len(self.random_channel_ids)} destination(s) · "
                f"{self.random_min_minutes}–{self.random_max_minutes} min"
            ),
            inline=False,
        )
        if self._section == "Game":
            embed.add_field(
                name="Game",
                value=(
                    "Choose the dedicated channel and the two game rules, then save. "
                    "Changing them completes any active streak but keeps all records."
                ),
                inline=False,
            )
        elif self._section == "Schedule":
            destinations = " ".join(f"<#{item}>" for item in self.random_channel_ids)
            embed.add_field(
                name="Scheduled Shoe posts",
                value=(
                    f"State: **{'On' if self.random_enabled else 'Off'}**\n"
                    f"Destinations: {destinations or 'None selected'}\n"
                    "Select one or more channels, toggle the state if needed, then save."
                ),
                inline=False,
            )
        elif self._section == "Timing":
            quiet = (
                "Off" if self.quiet_start_hour is None
                else f"{self.quiet_start_hour:02d}:00–{self.quiet_end_hour:02d}:00 UTC"
            )
            embed.add_field(
                name="Timing & quiet hours",
                value=(
                    f"Random delay: **{self.random_min_minutes}–{self.random_max_minutes} minutes**\n"
                    f"Quiet hours: **{quiet}**\n"
                    "The timer is stored in the database; the bot does not burn usage counting every second."
                ),
                inline=False,
            )
        elif self._section == "Logs":
            embed.add_field(
                name="Admin audit log",
                value=(
                    f"Channel: {f'<#{self.log_channel_id}>' if self.log_channel_id else 'Not selected'}\n"
                    "Receives settings changes and scheduler notices."
                ),
                inline=False,
            )
        else:
            detail = "No problems detected." if healthy else "Fix: " + "; ".join(problems) + "."
            embed.add_field(
                name="Health & maintenance",
                value=(
                    detail
                    + "\n`/forceshoe` can send one post immediately without changing this schedule."
                ),
                inline=False,
            )
        embed.set_footer(text=f"{self._section} · Private admin panel · Expires after 5 minutes")
        return embed

    def _add_button(
        self, label: str, style: discord.ButtonStyle, row: int,
        callback: Callable[[discord.Interaction], Awaitable[None]],
    ) -> None:
        button = discord.ui.Button(label=label, style=style, row=row)

        async def wrapped(interaction: discord.Interaction) -> None:
            await callback(interaction)

        button.callback = wrapped
        self.add_item(button)

    def _render(self) -> None:
        self.clear_items()
        for label in self.SECTIONS:
            async def open_section(
                interaction: discord.Interaction, section: str = label
            ) -> None:
                await interaction.response.defer()
                async with self._callback_lock:
                    self._section = section
                    self._render()
                    await interaction.edit_original_response(
                        content=None, embed=self.build_embed(), view=self
                    )

            self._add_button(
                label,
                discord.ButtonStyle.primary if label == self._section else discord.ButtonStyle.secondary,
                0,
                open_section,
            )

        if self._section == "Game":
            channel = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                placeholder="Game channel",
                min_values=1, max_values=1, row=1,
            )
            async def choose_game(interaction: discord.Interaction) -> None:
                self.channel_id = channel.values[0].id
                await self._refresh(interaction)
            channel.callback = choose_game
            self.add_item(channel)
            matching = discord.ui.Select(
                placeholder="Matching mode",
                options=[
                    discord.SelectOption(label="Creative (recommended)", value="creative", default=self.matching_mode == "creative"),
                    discord.SelectOption(label="Classic", value="classic", default=self.matching_mode == "classic"),
                ], row=2,
            )
            async def choose_matching(interaction: discord.Interaction) -> None:
                self.matching_mode = matching.values[0]
                self._render()
                await self._refresh(interaction)
            matching.callback = choose_matching
            self.add_item(matching)
            gameplay = discord.ui.Select(
                placeholder="Gameplay mode",
                options=[
                    discord.SelectOption(label="Relay (recommended)", value="relay", default=self.gameplay_mode == "relay"),
                    discord.SelectOption(label="Standard", value="standard", default=self.gameplay_mode == "standard"),
                ], row=3,
            )
            async def choose_gameplay(interaction: discord.Interaction) -> None:
                self.gameplay_mode = gameplay.values[0]
                self._render()
                await self._refresh(interaction)
            gameplay.callback = choose_gameplay
            self.add_item(gameplay)
            self._add_button("Save game", discord.ButtonStyle.success, 4, self._save_game)
        elif self._section == "Schedule":
            destinations = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                placeholder="Scheduled-post destinations",
                min_values=0, max_values=25, row=1,
            )
            async def choose_destinations(interaction: discord.Interaction) -> None:
                self.random_channel_ids = tuple(item.id for item in destinations.values)
                await self._refresh(interaction)
            destinations.callback = choose_destinations
            self.add_item(destinations)
            self._add_button(
                "Turn off" if self.random_enabled else "Turn on",
                discord.ButtonStyle.danger if self.random_enabled else discord.ButtonStyle.success,
                2, self._toggle_schedule,
            )
            self._add_button("Save schedule", discord.ButtonStyle.primary, 2, self._save_schedule)
        elif self._section == "Timing":
            self._add_button("Edit timing", discord.ButtonStyle.primary, 1, self._open_timing)
        elif self._section == "Logs":
            logs = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text], placeholder="Audit-log channel",
                min_values=1, max_values=1, row=1,
            )
            async def choose_logs(interaction: discord.Interaction) -> None:
                self.log_channel_id = logs.values[0].id
                await self._refresh(interaction)
            logs.callback = choose_logs
            self.add_item(logs)
            self._add_button("Save log channel", discord.ButtonStyle.primary, 2, self._save_logs)
            self._add_button("Send test log", discord.ButtonStyle.secondary, 2, self._test_log)
        else:
            self._add_button("Refresh health", discord.ButtonStyle.primary, 1, self._refresh_health)
            self._add_button("Reset server data", discord.ButtonStyle.danger, 1, self._reset)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await interaction.edit_original_response(
            content=None, embed=self.build_embed(), view=self
        )

    async def _save_random(self) -> None:
        await self._game.configure_random_shoe(
            self._guild_id, self.random_enabled, self.random_channel_ids,
            self.random_min_minutes, self.random_max_minutes,
            self.quiet_start_hour, self.quiet_end_hour, self.log_channel_id,
        )
        self.next_send_at = None

    async def _audit(self, text: str) -> None:
        channel = self._guild.get_channel(self.log_channel_id) if self.log_channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    "Shoe Bot audit · " + text,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

    async def _save_game(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        channel = self._guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            await _private_error(interaction, "Choose an existing game channel.")
            return
        missing = _missing_permissions(self._guild, channel)
        if missing:
            await _private_error(interaction, "Missing in that channel: " + ", ".join(missing))
            return
        await self._game.configure_guild(
            self._guild_id, channel.id, self.matching_mode, self.gameplay_mode
        )
        await self._audit(f"Game settings changed by <@{self._requester_id}>.")
        await interaction.edit_original_response(
            content="Game settings saved.", embed=self.build_embed(), view=self
        )

    async def _toggle_schedule(self, interaction: discord.Interaction) -> None:
        self.random_enabled = not self.random_enabled
        self._render()
        await self._refresh(interaction)

    async def _save_schedule(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if self.random_enabled and not self.random_channel_ids:
            await _private_error(interaction, "Choose at least one destination before turning posts on.")
            return
        if self.random_enabled and self.log_channel_id is None:
            await _private_error(interaction, "Choose an audit-log channel in the Logs tab first.")
            return
        for channel_id in self.random_channel_ids:
            channel = self._guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                await _private_error(interaction, "Every destination must be an existing text channel.")
                return
            missing = _missing_permissions(self._guild, channel)
            member = self._guild.me
            if member is None or not channel.permissions_for(member).attach_files:
                missing.append("Attach Files")
            if missing:
                await _private_error(interaction, f"Missing in {channel.mention}: " + ", ".join(missing))
                return
        await self._save_random()
        self._render()
        await self._audit(f"Scheduled posts {'enabled' if self.random_enabled else 'paused'} by <@{self._requester_id}>.")
        await interaction.edit_original_response(
            content="Schedule saved.", embed=self.build_embed(), view=self
        )

    async def _open_timing(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TimingModal(self))

    async def save_timing(
        self, interaction: discord.Interaction, minimum: int, maximum: int,
        quiet_start: int | None, quiet_end: int | None,
    ) -> None:
        self.random_min_minutes = minimum
        self.random_max_minutes = maximum
        self.quiet_start_hour = quiet_start
        self.quiet_end_hour = quiet_end
        await self._save_random()
        await self._audit(f"Timing changed by <@{self._requester_id}>.")
        await interaction.response.edit_message(
            content="Timing saved.", embed=self.build_embed(), view=self
        )

    async def _save_logs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        channel = self._guild.get_channel(self.log_channel_id) if self.log_channel_id else None
        if not isinstance(channel, discord.TextChannel):
            await _private_error(interaction, "Choose an existing audit-log channel.")
            return
        missing = _missing_permissions(self._guild, channel)
        if missing:
            await _private_error(
                interaction, "Missing in that audit channel: " + ", ".join(missing)
            )
            return
        await self._save_random()
        await self._audit(f"Audit channel selected by <@{self._requester_id}>.")
        await interaction.edit_original_response(
            content="Audit-log channel saved.", embed=self.build_embed(), view=self
        )

    async def _test_log(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if self.log_channel_id is None:
            await _private_error(interaction, "Choose and save an audit-log channel first.")
            return
        await self._audit(f"Test log requested by <@{self._requester_id}>.")
        await interaction.edit_original_response(
            content="Test log sent.", embed=self.build_embed(), view=self
        )

    async def _refresh_health(self, interaction: discord.Interaction) -> None:
        await self._refresh(interaction)

    async def _reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self._closed = True
        self._finished()
        _disable_view(self)
        self.stop()
        await self._start_reset(interaction)

    async def on_timeout(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._finished()
        _disable_view(self)
        self.stop()
        if self._message is not None:
            try:
                await self._message.edit(
                    content="Control center expired. Run `/shoesettings` to reopen it.",
                    view=self,
                )
            except discord.HTTPException:
                pass

    async def on_error(
        self, interaction: discord.Interaction, error: Exception,
        _item: discord.ui.Item,
    ) -> None:
        LOGGER.error("Settings control center failed (%s)", type(error).__name__)
        await _private_error(interaction, "That setting could not be updated. Try `/shoesettings` again.")


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
                "This reset is no longer valid. Open `/shoesettings` again as an administrator.",
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
                            "Check `/streak` before trying again."
                        ),
                        view=self,
                    )
                except discord.HTTPException as response_exc:
                    LOGGER.warning(
                        "Could not report a reset failure (%s)",
                        type(response_exc).__name__,
                    )
                    await _private_error(
                        interaction,
                        "I could not verify that the reset completed. "
                        "Check `/streak` before trying again.",
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
                        "Run `/streak` to verify it.",
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
            "The reset completed, but the panel failed to update. Run `/streak` to verify it."
            if self._reset_completed
            else "The reset action failed. Check `/streak` before trying again."
        )
        await _replace_with_terminal_view(interaction, text=text, view=self)


class ShoeCommands(commands.Cog):
    def __init__(self, database: ShoeDatabase, game: ShoeGame) -> None:
        self._database = database
        self._game = game
        self._pending_reset_tokens: dict[int, object] = {}
        self._pending_settings_tokens: dict[int, object] = {}

    async def _audit(self, guild: discord.Guild, channel_id: int | None, text: str) -> None:
        channel = guild.get_channel(channel_id) if channel_id is not None else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send("Shoe Bot audit · " + text, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as exc:
                LOGGER.warning("Could not send Shoe Bot audit log (%s)", type(exc).__name__)

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
        start_reset: Callable[[discord.Interaction], Awaitable[None]] | None = None,
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
                initial_random_shoe_enabled=stats.random_shoe_enabled if stats else False,
                initial_random_shoe_channel_ids=stats.random_shoe_channel_ids if stats else (),
                initial_random_shoe_min_minutes=stats.random_shoe_min_minutes if stats else 50,
                initial_random_shoe_max_minutes=stats.random_shoe_max_minutes if stats else 103,
                initial_quiet_start_hour=stats.quiet_start_hour if stats else None,
                initial_quiet_end_hour=stats.quiet_end_hour if stats else None,
                initial_log_channel_id=stats.log_channel_id if stats else None,
                is_current=is_current,
                finished=finished,
                title=title,
                start_reset=start_reset,
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

    async def _open_reset_confirmation(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild_id is None:
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
                embed=None,
                view=view,
            )
            view.bind_message(message)
        except BaseException:
            finished()
            if "view" in locals():
                view.stop()
            raise

    async def _open_settings_hub(
        self, interaction: discord.Interaction, stats: GuildStats
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
            view = SettingsHubView(
                game=self._game,
                guild=interaction.guild,
                requester_id=interaction.user.id,
                stats=stats,
                is_current=is_current,
                finished=finished,
                start_reset=self._open_reset_confirmation,
            )
            message = await interaction.edit_original_response(
                content=None,
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
        name="shoesettings",
        description="Manage Shoe Bot settings, diagnostics, or server reset",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shoesettings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is not None:
            await self._open_settings_hub(interaction, stats)

    @app_commands.command(name="shoelog", description="Choose the private Shoe Bot audit-log channel")
    @app_commands.describe(channel="Channel for settings changes and scheduler notices")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shoelog(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None or interaction.guild is None:
            return
        await self._game.configure_random_shoe(
            interaction.guild.id, stats.random_shoe_enabled,
            stats.random_shoe_channel_ids, stats.random_shoe_min_minutes,
            stats.random_shoe_max_minutes, stats.quiet_start_hour,
            stats.quiet_end_hour, channel.id,
        )
        await self._audit(interaction.guild, channel.id, f"Audit channel selected by <@{interaction.user.id}>.")
        await _respond(interaction, f"Shoe Bot audit logs will be sent to {channel.mention}.")

    @app_commands.command(name="shoetiming", description="Set Random Shoe timing and optional UTC quiet hours")
    @app_commands.describe(
        minimum_minutes="Minimum delay (5–1440 minutes)",
        maximum_minutes="Maximum delay (minimum–1440 minutes)",
        quiet_start_utc="Quiet-hour start, 0–23 UTC; omit both to disable",
        quiet_end_utc="Quiet-hour end, 0–23 UTC; omit both to disable",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def shoetiming(
        self, interaction: discord.Interaction,
        minimum_minutes: app_commands.Range[int, 5, 1440],
        maximum_minutes: app_commands.Range[int, 5, 1440],
        quiet_start_utc: app_commands.Range[int, 0, 23] | None = None,
        quiet_end_utc: app_commands.Range[int, 0, 23] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None or interaction.guild is None:
            return
        if minimum_minutes > maximum_minutes or ((quiet_start_utc is None) != (quiet_end_utc is None)):
            await _private_error(interaction, "Minimum must not exceed maximum, and quiet hours need both a start and end.")
            return
        await self._game.configure_random_shoe(
            interaction.guild.id, stats.random_shoe_enabled,
            stats.random_shoe_channel_ids, minimum_minutes, maximum_minutes,
            quiet_start_utc, quiet_end_utc, stats.log_channel_id,
        )
        quiet = "off" if quiet_start_utc is None else f"{quiet_start_utc:02d}:00–{quiet_end_utc:02d}:00 UTC"
        await self._audit(interaction.guild, stats.log_channel_id, f"Timing changed by <@{interaction.user.id}> to {minimum_minutes}–{maximum_minutes} minutes; quiet hours {quiet}.")
        await _respond(interaction, f"Random Shoe timing saved: **{minimum_minutes}–{maximum_minutes} minutes**; quiet hours **{quiet}**.")

    @app_commands.command(name="shoestatus", description="Check Shoe Bot health in this server")
    @app_commands.guild_only()
    async def shoestatus(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is None:
            return
        image_ok = RANDOM_SHOE_IMAGE.is_file()
        destinations_ok = bool(stats.random_shoe_channel_ids) if stats.random_shoe_enabled else True
        logs_ok = stats.log_channel_id is not None if stats.random_shoe_enabled else True
        healthy = image_ok and destinations_ok and logs_ok
        embed = discord.Embed(title="Shoe Bot status", colour=discord.Colour.green() if healthy else discord.Colour.orange())
        embed.description = "Healthy and ready." if healthy else "Needs administrator attention."
        embed.add_field(name="Database", value="Connected")
        embed.add_field(name="Image", value="Ready" if image_ok else "Missing")
        embed.add_field(name="Random posts", value="Enabled" if stats.random_shoe_enabled else "Off")
        embed.add_field(name="Timing", value=f"{stats.random_shoe_min_minutes}–{stats.random_shoe_max_minutes} minutes")
        quiet = "Off" if stats.quiet_start_hour is None else f"{stats.quiet_start_hour:02d}:00–{stats.quiet_end_hour:02d}:00 UTC"
        embed.add_field(name="Quiet hours", value=quiet)
        embed.add_field(name="Audit log", value=f"<#{stats.log_channel_id}>" if stats.log_channel_id else "Not selected")
        await _respond(interaction, embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="forceshoe", description="Immediately send one Shoe post to a selected channel")
    @app_commands.describe(channel="The single text channel that should receive the Shoe post")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def forceshoe(
        self, interaction: discord.Interaction, channel: discord.TextChannel,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if interaction.guild is None:
            await _private_error(interaction, "This command can only be used in a server.")
            return
        missing = _missing_permissions(interaction.guild, channel)
        bot_member = interaction.guild.me
        if bot_member is None or not channel.permissions_for(bot_member).attach_files:
            missing.append("Attach Files")
        if missing:
            await _private_error(
                interaction,
                f"I cannot send the Shoe post in {channel.mention}. Missing: " + ", ".join(missing),
            )
            return
        try:
            await channel.send(
                "Shoe",
                file=discord.File(RANDOM_SHOE_IMAGE, filename="shoe.jpg"),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            LOGGER.warning("Could not force a Shoe post (%s)", type(exc).__name__)
            await _private_error(interaction, "I could not send the Shoe post in that channel.")
            return

        try:
            stats = await self._database.run(
                self._database.get_guild_stats, interaction.guild.id
            )
        except DatabaseError:
            stats = None
        if stats is not None:
            await self._audit(
                interaction.guild,
                stats.log_channel_id,
                f"`/forceshoe` sent in {channel.mention} by <@{interaction.user.id}>.",
            )
        await _respond(
            interaction,
            f"Sent `Shoe` with the image in {channel.mention}. The automatic timer was not changed.",
        )

    @app_commands.command(name="streak", description="Show this server's Shoe streak")
    @app_commands.guild_only()
    async def streak(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        stats = await self._guild_stats_or_error(interaction)
        if stats is not None:
            embed = discord.Embed(title="Shoe streak", colour=EMBED_COLOUR)
            embed.add_field(name="Current streak", value=f"{stats.current_streak:,}")
            embed.add_field(name="Best streak", value=f"{stats.best_streak:,}")
            embed.add_field(name="Total accepted", value=f"{stats.total_shoes:,}")
            embed.set_footer(
                text=f"{stats.matching_mode.title()} matching · {stats.gameplay_mode.title()} gameplay"
            )
            await _respond(interaction, embed=embed)

    @app_commands.command(
        name="leaderboard", description="Show Shoe rankings and Hall of Fame"
    )
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        if interaction.guild_id is None:
            return
        try:
            snapshot = await self._database.run(
                self._database.get_leaderboard_snapshot,
                interaction.guild_id,
                limit=10,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read leaderboard (%s)", type(exc).__name__)
            await _private_error(interaction, "The leaderboard is temporarily unavailable.")
            return
        if snapshot is None:
            await _private_error(
                interaction,
                "Shoe Bot is not configured here. Create a dedicated text channel "
                "such as `#shoe`, then have an administrator run `/setup`.",
            )
            return

        contributors_embed = _contributors_embed(snapshot.contributors)
        view = LeaderboardView(
            requester_id=interaction.user.id,
            guild_id=interaction.guild_id,
            contributors=contributors_embed,
            hall_of_fame=_hall_of_fame_embed(
                snapshot.stats,
                snapshot.hall_of_fame,
            ),
        )
        try:
            message = await interaction.edit_original_response(
                embed=contributors_embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            view.bind_message(message)
        except BaseException:
            view.stop()
            raise

    @app_commands.command(
        name="profile", description="Show a user's Shoe count, rank, and milestones"
    )
    @app_commands.describe(user="Optionally show another user's Shoe profile")
    @app_commands.guild_only()
    async def profile(
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
            LOGGER.error("Could not read a Shoe profile (%s)", type(exc).__name__)
            await _private_error(interaction, "That Shoe profile is temporarily unavailable.")
            return
        rank = f"#{user_stats.rank:,}" if user_stats.rank is not None else "Unranked"
        next_value = _next_achievement(user_stats.shoe_count)
        progress = (
            f"{user_stats.shoe_count:,} / {next_value:,}"
            if next_value is not None
            else "All milestones unlocked"
        )
        lines = [
            f"{'Unlocked' if user_stats.shoe_count >= threshold else 'Locked'} · {threshold:,} accepted messages"
            for threshold in ACHIEVEMENT_THRESHOLDS
        ]
        embed = discord.Embed(
            title="Shoe profile",
            description=target.mention,
            colour=EMBED_COLOUR,
        )
        embed.add_field(name="Accepted messages", value=f"{user_stats.shoe_count:,}")
        embed.add_field(name="Leaderboard rank", value=rank)
        embed.add_field(name="Next milestone", value=progress, inline=False)
        embed.add_field(name="Milestones", value="\n".join(lines), inline=False)
        embed.set_footer(text="Milestones are derived from the stored personal count.")
        await _respond(
            interaction,
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        name="shoehelp", description="Show Shoe Bot commands, rules, and support"
    )
    @app_commands.guild_only()
    async def shoehelp(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            stats = await self._database.run(
                self._database.get_guild_stats,
                interaction.guild_id,
            )
        except DatabaseError as exc:
            LOGGER.error("Could not read rules for help (%s)", type(exc).__name__)
            await _private_error(interaction, "Shoe Bot help is temporarily unavailable.")
            return
        matching, gameplay = _rules_text(stats)
        setup_status = (
            "This server is configured."
            if stats is not None
            else "This server is not configured yet. An administrator can run `/setup`."
        )
        embed = discord.Embed(
            title="Shoe Bot help",
            description=(
                "A server game that builds a streak from accepted Shoe messages. "
                + setup_status
            ),
            colour=EMBED_COLOUR,
        )
        embed.add_field(
            name="Game commands",
            value=(
                "`/streak` · `/profile [user]` · `/leaderboard` · "
                "`/shoehelp` · `/forgetme`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Current rules" if stats is not None else "Recommended defaults",
            value=f"{matching}\n\n{gameplay}",
            inline=False,
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
        embed.add_field(
            name="Administrator commands",
            value=(
                "`/setup` · `/shoesettings`\n"
                "`/shoelog` · `/shoetiming` · `/shoestatus` · `/forceshoe`\n"
                "`/shoesettings` is a status-first control center with Game, Schedule, "
                "Timing, Logs, and More tabs. It includes permission diagnostics, the protected "
                "server reset, and optional Random Shoe posts. When enabled, the bot chooses one of "
                "the admin-selected channels and posts `Shoe` with the supplied image "
                "after a fresh configurable random delay, with optional UTC quiet hours. It is off by default."
            ),
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
