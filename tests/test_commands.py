from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock

import discord
from discord import app_commands

from bot.commands import ResetConfirmationView, SetupWizardView, ShoeCommands
from bot.database import DatabaseError, ShoeDatabase
from bot.main import BotConfig, ShoeBot


class FakeResponse:
    def __init__(self, *, component: bool = False) -> None:
        self.messages: list[tuple[str | None, dict]] = []
        self.type: discord.InteractionResponseType | None = None
        self.defer_ephemeral: bool | None = None
        self.component = component

    def is_done(self) -> bool:
        return self.type is not None

    async def send_message(self, text: str | None = None, **kwargs) -> None:
        self.type = discord.InteractionResponseType.channel_message
        self.messages.append((text, kwargs))

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self.type = (
            discord.InteractionResponseType.deferred_message_update
            if self.component and not thinking
            else discord.InteractionResponseType.deferred_channel_message
        )
        self.defer_ephemeral = ephemeral

    async def edit_message(self, **kwargs) -> None:
        self.type = discord.InteractionResponseType.message_update
        self.messages.append((kwargs.pop("content", None), kwargs))


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[tuple[str | None, dict]] = []

    async def send(self, text: str | None = None, **kwargs) -> None:
        self.messages.append((text, kwargs))


class FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int,
        guild_id: int,
        administrator: bool,
        fail_edit: bool = False,
        component: bool = False,
    ) -> None:
        self.user = SimpleNamespace(
            id=user_id,
            mention=f"<@{user_id}>",
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        self.guild_id = guild_id
        self.guild = None
        self.response = FakeResponse(component=component)
        self.followup = FakeFollowup()
        self.fail_edit = fail_edit

    async def original_response(self):
        return SimpleNamespace(edit=None)

    async def edit_original_response(self, **kwargs):
        if self.fail_edit:
            raise discord.HTTPException(
                SimpleNamespace(status=500, reason="simulated"),
                "simulated response failure",
            )
        view = kwargs.get("view")
        if view is not None:
            kwargs["_view_disabled_snapshot"] = all(
                getattr(item, "disabled", False) for item in view.children
            )
        self.response.messages.append((kwargs.pop("content", None), kwargs))
        return SimpleNamespace(edit=None)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "commands.sqlite3"
        self.database = ShoeDatabase(database_path)
        config = BotConfig(
            token="not-a-real-token",
            application_id=123,
            development_guild_id=None,
            database_path=database_path,
        )
        self.bot = ShoeBot(config, self.database)

    async def asyncTearDown(self) -> None:
        await self.bot.close()
        self.temporary_directory.cleanup()

    async def test_command_tree_has_only_new_guild_commands(self) -> None:
        await self.bot.add_cog(ShoeCommands(self.database, self.bot.game))
        commands = {command.name: command for command in self.bot.tree.get_commands()}
        self.assertEqual(
            set(commands),
            {
                "setup",
                "shoesettings",
                "stats",
                "leaderboard",
                "count",
                "records",
                "achievements",
                "shoerules",
                "shoehelp",
                "diagnose",
                "forgetme",
                "reset",
            },
        )
        self.assertTrue(all(command.guild_only for command in commands.values()))
        for old_name in (
            "shoesetup",
            "shoestats",
            "shoeleaderboard",
            "shoecount",
            "shoeconfig",
            "shoeforgetme",
            "shoereset",
        ):
            self.assertNotIn(old_name, commands)

    async def test_application_install_context_is_guild_only(self) -> None:
        self.assertTrue(self.bot.tree.allowed_installs.guild)
        self.assertFalse(self.bot.tree.allowed_installs.user)

    async def test_every_mutating_admin_command_has_two_permission_layers(self) -> None:
        await self.bot.add_cog(ShoeCommands(self.database, self.bot.game))
        commands = {command.name: command for command in self.bot.tree.get_commands()}
        admin_commands = {"setup", "shoesettings", "diagnose", "reset"}
        for name in admin_commands:
            self.assertTrue(commands[name].default_permissions.administrator)
            self.assertGreaterEqual(len(commands[name].checks), 1)
        for name in set(commands) - admin_commands:
            permissions = commands[name].default_permissions
            self.assertTrue(permissions is None or not permissions.administrator)

    async def test_bot_uses_only_required_gateway_intents_and_no_message_cache(self) -> None:
        self.assertTrue(self.bot.intents.guilds)
        self.assertTrue(self.bot.intents.guild_messages)
        self.assertFalse(self.bot.intents.dm_messages)
        self.assertTrue(self.bot.intents.message_content)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.presences)
        self.assertFalse(self.bot.intents.guild_reactions)
        self.assertIsNone(self.bot._connection.max_messages)
        self.assertIsNone(self.bot._connection._messages)
        self.assertFalse(self.bot._connection.member_cache_flags.joined)
        self.assertFalse(self.bot._connection.member_cache_flags.voice)

    async def test_reset_confirmation_rechecks_user_guild_admin_and_token(self) -> None:
        current = True
        finished_count = 0

        def is_current() -> bool:
            return current

        def finished() -> None:
            nonlocal finished_count
            finished_count += 1

        view = ResetConfirmationView(
            self.bot.game, 100, 300, is_current, finished
        )
        correct = FakeInteraction(user_id=300, guild_id=100, administrator=True)
        lost_admin = FakeInteraction(user_id=300, guild_id=100, administrator=False)
        wrong_guild = FakeInteraction(user_id=300, guild_id=101, administrator=True)
        wrong_user = FakeInteraction(user_id=301, guild_id=100, administrator=True)
        self.assertTrue(await view.interaction_check(correct))
        self.assertFalse(await view.interaction_check(lost_admin))
        self.assertFalse(await view.interaction_check(wrong_guild))
        self.assertFalse(await view.interaction_check(wrong_user))
        self.assertTrue(view._consume())
        self.assertFalse(view._consume())
        self.assertEqual(finished_count, 1)
        self.assertFalse(await view.interaction_check(correct))

    async def test_settings_view_rechecks_user_guild_admin_and_token(self) -> None:
        fake_guild = SimpleNamespace(id=100)
        view = SetupWizardView(
            game=self.bot.game,
            guild=fake_guild,  # type: ignore[arg-type]
            requester_id=300,
            initial_channel_id=None,
            initial_matching_mode="creative",
            initial_gameplay_mode="relay",
            is_current=lambda: True,
            finished=lambda: None,
            title="Test",
        )
        self.assertTrue(
            await view.interaction_check(
                FakeInteraction(user_id=300, guild_id=100, administrator=True)
            )
        )
        self.assertFalse(
            await view.interaction_check(
                FakeInteraction(user_id=301, guild_id=100, administrator=True)
            )
        )
        self.assertFalse(
            await view.interaction_check(
                FakeInteraction(user_id=300, guild_id=100, administrator=False)
            )
        )
        self.assertFalse(
            await view.interaction_check(
                FakeInteraction(user_id=300, guild_id=101, administrator=True)
            )
        )

    async def test_setup_view_defaults_to_recommended_modes(self) -> None:
        fake_guild = SimpleNamespace(id=100)
        view = SetupWizardView(
            game=self.bot.game,
            guild=fake_guild,  # type: ignore[arg-type]
            requester_id=300,
            initial_channel_id=None,
            initial_matching_mode="creative",
            initial_gameplay_mode="relay",
            is_current=lambda: True,
            finished=lambda: None,
            title="Test",
        )
        self.assertEqual(view._matching_mode, "creative")
        self.assertEqual(view._gameplay_mode, "relay")
        self.assertTrue(
            next(option for option in view.matching_select.options if option.value == "creative").default
        )
        self.assertTrue(
            next(option for option in view.gameplay_select.options if option.value == "relay").default
        )

    async def test_settings_save_defers_before_commit_and_disables_panel(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 200
        channel.mention = "<#200>"
        channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True,
            send_messages=True,
            add_reactions=True,
            read_message_history=True,
        )
        guild = SimpleNamespace(
            id=100,
            me=SimpleNamespace(id=999),
            get_channel=lambda channel_id: channel if channel_id == 200 else None,
        )
        finished = 0

        def mark_finished() -> None:
            nonlocal finished
            finished += 1

        view = SetupWizardView(
            game=self.bot.game,
            guild=guild,  # type: ignore[arg-type]
            requester_id=300,
            initial_channel_id=200,
            initial_matching_mode="creative",
            initial_gameplay_mode="relay",
            is_current=lambda: True,
            finished=mark_finished,
            title="Test",
        )
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            component=True,
        )
        original_configure = self.bot.game.configure_guild
        observed: list[bool] = []

        async def checked_configure(*args, **kwargs):
            observed.append(interaction.response.is_done())
            return await original_configure(*args, **kwargs)

        self.bot.game.configure_guild = checked_configure  # type: ignore[method-assign]
        try:
            await view.save.callback(interaction)
        finally:
            self.bot.game.configure_guild = original_configure  # type: ignore[method-assign]
        self.assertEqual(observed, [True])
        self.assertEqual(finished, 1)
        self.assertTrue(view._saved)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        self.assertEqual(self.database.get_guild_stats(100).channel_id, 200)

    async def test_settings_confirmation_failure_reports_commit_as_success(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 200
        channel.mention = "<#200>"
        channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True,
            send_messages=True,
            add_reactions=True,
            read_message_history=True,
        )
        guild = SimpleNamespace(
            id=100,
            me=SimpleNamespace(id=999),
            get_channel=lambda channel_id: channel if channel_id == 200 else None,
        )
        view = SetupWizardView(
            game=self.bot.game,
            guild=guild,  # type: ignore[arg-type]
            requester_id=300,
            initial_channel_id=200,
            initial_matching_mode="creative",
            initial_gameplay_mode="relay",
            is_current=lambda: True,
            finished=lambda: None,
            title="Test",
        )
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            fail_edit=True,
            component=True,
        )
        await view.save.callback(interaction)
        self.assertEqual(self.database.get_guild_stats(100).channel_id, 200)
        fallback = interaction.followup.messages[0][0]
        self.assertIn("settings were saved", fallback.casefold())
        self.assertNotIn("settings failed", fallback.casefold())

    async def test_settings_database_failure_posts_disabled_terminal_panel(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 200
        channel.mention = "<#200>"
        channel.permissions_for.return_value = SimpleNamespace(
            view_channel=True,
            send_messages=True,
            add_reactions=True,
            read_message_history=True,
        )
        guild = SimpleNamespace(
            id=100,
            me=SimpleNamespace(id=999),
            get_channel=lambda channel_id: channel if channel_id == 200 else None,
        )
        view = SetupWizardView(
            game=self.bot.game,
            guild=guild,  # type: ignore[arg-type]
            requester_id=300,
            initial_channel_id=200,
            initial_matching_mode="creative",
            initial_gameplay_mode="relay",
            is_current=lambda: True,
            finished=lambda: None,
            title="Test",
        )
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            component=True,
        )
        original_configure = self.bot.game.configure_guild

        async def fail_configure(*_args, **_kwargs):
            raise DatabaseError("simulated failure")

        self.bot.game.configure_guild = fail_configure  # type: ignore[method-assign]
        try:
            await view.save.callback(interaction)
        finally:
            self.bot.game.configure_guild = original_configure  # type: ignore[method-assign]
        self.assertTrue(interaction.response.messages[0][1]["_view_disabled_snapshot"])
        self.assertTrue(view.is_finished())

    async def test_new_reset_prompt_invalidates_older_prompt_for_same_guild(self) -> None:
        self.database.set_shoe_channel(100, 200)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        first = FakeInteraction(user_id=300, guild_id=100, administrator=True)
        second = FakeInteraction(user_id=300, guild_id=100, administrator=True)
        await ShoeCommands.reset.callback(cog, first)
        await ShoeCommands.reset.callback(cog, second)
        first_view = first.response.messages[0][1]["view"]
        second_view = second.response.messages[0][1]["view"]
        self.assertFalse(await first_view.interaction_check(first))
        self.assertTrue(await second_view.interaction_check(second))

    async def test_reset_prompt_names_every_deleted_and_preserved_category(self) -> None:
        self.database.set_shoe_channel(100, 200)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(user_id=300, guild_id=100, administrator=True)
        await ShoeCommands.reset.callback(cog, interaction)
        text = interaction.response.messages[0][0]
        for phrase in (
            "total count",
            "current and best streaks",
            "personal counts",
            "Hall of Fame",
            "Relay state",
            "configured channel",
            "game modes",
            "cannot be undone",
        ):
            self.assertIn(phrase.casefold(), text.casefold())
        self.assertTrue(interaction.response.defer_ephemeral)

    async def test_reset_defers_before_database_access(self) -> None:
        self.database.set_shoe_channel(100, 200)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(user_id=300, guild_id=100, administrator=True)
        original_run = self.database.run
        observed: list[bool] = []

        async def checked_run(operation, *args, **kwargs):
            observed.append(interaction.response.is_done())
            return await original_run(operation, *args, **kwargs)

        self.database.run = checked_run  # type: ignore[method-assign]
        try:
            await ShoeCommands.reset.callback(cog, interaction)
        finally:
            self.database.run = original_run  # type: ignore[method-assign]
        self.assertTrue(observed)
        self.assertTrue(all(observed))

    async def test_failed_reset_prompt_publication_clears_pending_token(self) -> None:
        self.database.set_shoe_channel(100, 200)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            fail_edit=True,
        )
        with self.assertRaises(discord.HTTPException):
            await ShoeCommands.reset.callback(cog, interaction)
        self.assertNotIn(100, cog._pending_reset_tokens)

    async def test_failed_settings_prompt_publication_clears_pending_token(self) -> None:
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            fail_edit=True,
        )
        interaction.guild = SimpleNamespace(
            id=100,
            me=None,
            get_channel=lambda _channel_id: None,
        )
        with self.assertRaises(discord.HTTPException):
            await ShoeCommands.setup.callback(cog, interaction)
        self.assertNotIn(100, cog._pending_settings_tokens)

    async def test_reset_confirm_defers_then_commits_once(self) -> None:
        self.database.configure_guild(100, 200, "creative", "standard")
        self.database.record_message(100, 300, True)
        finished = 0

        def mark_finished() -> None:
            nonlocal finished
            finished += 1

        view = ResetConfirmationView(
            self.bot.game,
            100,
            300,
            lambda: True,
            mark_finished,
        )
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            component=True,
        )
        original_run = self.database.run
        observed: list[bool] = []

        async def checked_run(operation, *args, **kwargs):
            observed.append(interaction.response.is_done())
            return await original_run(operation, *args, **kwargs)

        self.database.run = checked_run  # type: ignore[method-assign]
        try:
            await view.confirm.callback(interaction)
        finally:
            self.database.run = original_run  # type: ignore[method-assign]
        self.assertEqual(observed, [True])
        self.assertEqual(finished, 1)
        self.assertTrue(view._reset_completed)
        self.assertTrue(view.is_finished())
        self.assertTrue(all(item.disabled for item in view.children))
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 0)

    async def test_reset_confirmation_failure_reports_commit_as_success(self) -> None:
        self.database.configure_guild(100, 200, "creative", "standard")
        self.database.record_message(100, 300, True)
        view = ResetConfirmationView(
            self.bot.game,
            100,
            300,
            lambda: True,
            lambda: None,
        )
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            fail_edit=True,
            component=True,
        )
        await view.confirm.callback(interaction)
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 0)
        fallback = interaction.followup.messages[0][0]
        self.assertIn("reset completed", fallback.casefold())
        self.assertNotIn("reset failed", fallback.casefold())

    async def test_reset_database_failure_posts_disabled_terminal_panel(self) -> None:
        view = ResetConfirmationView(
            self.bot.game,
            100,
            300,
            lambda: True,
            lambda: None,
        )
        interaction = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            component=True,
        )
        original_reset = self.bot.game.reset_guild_stats

        async def fail_reset(_guild_id):
            raise DatabaseError("simulated failure")

        self.bot.game.reset_guild_stats = fail_reset  # type: ignore[method-assign]
        try:
            await view.confirm.callback(interaction)
        finally:
            self.bot.game.reset_guild_stats = original_reset  # type: ignore[method-assign]
        self.assertTrue(interaction.response.messages[0][1]["_view_disabled_snapshot"])
        self.assertTrue(view.is_finished())

    async def test_overlapping_reset_confirmations_mutate_once(self) -> None:
        self.database.configure_guild(100, 200, "creative", "standard")
        self.database.record_message(100, 300, True)
        view = ResetConfirmationView(
            self.bot.game,
            100,
            300,
            lambda: True,
            lambda: None,
        )
        first = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            component=True,
        )
        second = FakeInteraction(
            user_id=300,
            guild_id=100,
            administrator=True,
            component=True,
        )
        original_run = self.database.run
        reset_calls = 0

        async def counted_run(operation, *args, **kwargs):
            nonlocal reset_calls
            if operation == self.database.reset_guild_stats:
                reset_calls += 1
            return await original_run(operation, *args, **kwargs)

        self.database.run = counted_run  # type: ignore[method-assign]
        try:
            await asyncio.gather(
                view.confirm.callback(first),
                view.confirm.callback(second),
            )
        finally:
            self.database.run = original_run  # type: ignore[method-assign]
        self.assertEqual(reset_calls, 1)
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 0)

    async def test_leaderboard_uses_clean_emoji_free_embed(self) -> None:
        self.database.configure_guild(100, 200, "creative", "standard")
        for user_id in (301, 301, 302):
            self.database.record_message(100, user_id, True)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(user_id=300, guild_id=100, administrator=False)
        await ShoeCommands.leaderboard.callback(cog, interaction)
        text, kwargs = interaction.response.messages[0]
        embed = kwargs["embed"]
        rendered = str(embed.to_dict())
        self.assertIsNone(text)
        self.assertEqual(embed.title, "Shoe leaderboard")
        self.assertIn("<@301>", rendered)
        self.assertIn("<@302>", rendered)
        for emoji in ("🤖", "🥇", "🥈", "🥉", "👟", "💥"):
            self.assertNotIn(emoji, rendered)

    async def test_stats_and_achievements_are_derived_from_user_count(self) -> None:
        self.database.configure_guild(100, 200, "creative", "standard")
        for _ in range(10):
            self.database.record_message(100, 301, True)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        target = SimpleNamespace(id=301, mention="<@301>")

        stats_interaction = FakeInteraction(
            user_id=300, guild_id=100, administrator=False
        )
        await ShoeCommands.stats.callback(cog, stats_interaction, target)
        self.assertFalse(stats_interaction.response.defer_ephemeral)
        stats_embed = stats_interaction.response.messages[0][1]["embed"]
        self.assertIn("10 / 25", str(stats_embed.to_dict()))

        achievement_interaction = FakeInteraction(
            user_id=300, guild_id=100, administrator=False
        )
        await ShoeCommands.achievements.callback(cog, achievement_interaction, target)
        rendered = str(achievement_interaction.response.messages[0][1]["embed"].to_dict())
        self.assertIn("Unlocked · 10 accepted messages", rendered)
        self.assertIn("Locked · 25 accepted messages", rendered)

    async def test_forgetme_can_only_delete_the_callers_row(self) -> None:
        self.database.configure_guild(100, 200, "creative", "standard")
        self.database.record_message(100, 300, True)
        self.database.record_message(100, 301, True)
        await self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(user_id=300, guild_id=100, administrator=False)
        choice = app_commands.Choice(name="Yes", value="delete")
        await ShoeCommands.forgetme.callback(cog, interaction, choice)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 0)
        self.assertEqual(self.database.get_user_stats(100, 301).shoe_count, 1)
        self.assertTrue(interaction.response.defer_ephemeral)
        self.assertIn("deleted", interaction.response.messages[0][0].casefold())

    async def test_help_lists_all_admin_commands_and_current_public_names(self) -> None:
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(user_id=300, guild_id=100, administrator=False)
        await ShoeCommands.shoehelp.callback(cog, interaction)
        rendered = str(interaction.response.messages[0][1]["embed"].to_dict())
        for command in (
            "/count",
            "/stats",
            "/leaderboard",
            "/records",
            "/achievements",
            "/setup",
            "/shoesettings",
            "/diagnose",
            "/reset",
        ):
            self.assertIn(command, rendered)
        self.assertIn("PRIVACY.md", rendered)
        self.assertIn("TERMS.md", rendered)
        self.assertIn("yungcholesterol@gmail.com", rendered)
        self.assertIn("not affiliated", rendered)


if __name__ == "__main__":
    unittest.main()
