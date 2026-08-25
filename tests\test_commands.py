from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from bot.commands import ResetConfirmationView, ShoeCommands
from bot.database import ShoeDatabase
from bot.main import BotConfig, ShoeBot


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str | None, dict]] = []

    def is_done(self) -> bool:
        return False

    async def send_message(self, text: str | None = None, **kwargs) -> None:
        self.messages.append((text, kwargs))


class FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int,
        guild_id: int,
        administrator: bool,
    ) -> None:
        self.user = SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(administrator=administrator),
        )
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.followup = SimpleNamespace()

    async def original_response(self):
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

    async def test_command_tree_is_guild_only_and_admin_commands_are_enforced(self) -> None:
        await self.bot.add_cog(ShoeCommands(self.database, self.bot.game))
        commands = {command.name: command for command in self.bot.tree.get_commands()}

        self.assertEqual(
            set(commands),
            {
                "shoesetup",
                "shoestats",
                "shoeleaderboard",
                "shoecount",
                "shoeconfig",
                "shoeforgetme",
                "shoereset",
            },
        )
        self.assertTrue(all(command.guild_only for command in commands.values()))
        for name in ("shoesetup", "shoereset"):
            self.assertTrue(commands[name].default_permissions.administrator)
            self.assertGreaterEqual(len(commands[name].checks), 1)

    async def test_bot_uses_only_required_gateway_intents_and_no_message_cache(self) -> None:
        self.assertTrue(self.bot.intents.guilds)
        self.assertTrue(self.bot.intents.guild_messages)
        self.assertFalse(self.bot.intents.dm_messages)
        self.assertTrue(self.bot.intents.message_content)
        self.assertFalse(self.bot.intents.members)
        self.assertFalse(self.bot.intents.presences)
        self.assertIsNone(self.bot._connection.max_messages)
        self.assertIsNone(self.bot._connection._messages)
        self.assertFalse(self.bot._connection.member_cache_flags.joined)
        self.assertFalse(self.bot._connection.member_cache_flags.voice)

    async def test_reset_confirmation_rechecks_requester_guild_and_admin(self) -> None:
        current = True
        finished_count = 0

        def is_current() -> bool:
            return current

        def finished() -> None:
            nonlocal finished_count
            finished_count += 1

        view = ResetConfirmationView(
            self.database,
            guild_id=100,
            requester_id=300,
            is_current=is_current,
            finished=finished,
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

    async def test_new_reset_prompt_invalidates_older_prompt_for_same_guild(self) -> None:
        self.database.set_shoe_channel(100, 200)
        game = self.bot.game
        game.load_configuration()
        cog = ShoeCommands(self.database, game)
        first_interaction = FakeInteraction(
            user_id=300, guild_id=100, administrator=True
        )
        second_interaction = FakeInteraction(
            user_id=300, guild_id=100, administrator=True
        )

        await ShoeCommands.shoereset.callback(cog, first_interaction)
        await ShoeCommands.shoereset.callback(cog, second_interaction)
        first_view = first_interaction.response.messages[0][1]["view"]
        second_view = second_interaction.response.messages[0][1]["view"]

        self.assertFalse(await first_view.interaction_check(first_interaction))
        self.assertTrue(await second_view.interaction_check(second_interaction))

    async def test_leaderboard_uses_an_emoji_free_embed(self) -> None:
        self.database.set_shoe_channel(100, 200)
        self.database.record_message(guild_id=100, user_id=301, is_valid=True)
        self.database.record_message(guild_id=100, user_id=301, is_valid=True)
        self.database.record_message(guild_id=100, user_id=302, is_valid=True)
        self.bot.game.load_configuration()
        cog = ShoeCommands(self.database, self.bot.game)
        interaction = FakeInteraction(
            user_id=300, guild_id=100, administrator=False
        )

        await ShoeCommands.shoeleaderboard.callback(cog, interaction)

        text, kwargs = interaction.response.messages[0]
        embed = kwargs["embed"]
        rendered = str(embed.to_dict())
        self.assertIsNone(text)
        self.assertEqual(embed.title, "SHOE BOT // LEADERBOARD")
        self.assertIn("<@301>", rendered)
        self.assertIn("<@302>", rendered)
        for emoji in ("🤖", "🥇", "🥈", "🥉", "👟"):
            self.assertNotIn(emoji, rendered)


if __name__ == "__main__":
    unittest.main()
