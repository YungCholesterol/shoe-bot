from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import discord

from bot.commands import ShoeCommands
from bot.database import DatabaseError, ShoeDatabase
from bot.shoe_game import ShoeGame
from tests.test_commands import FakeInteraction
from tests.test_shoe_game import FakeChannel, FakeMessage


class RecapDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "recaps.sqlite3"
        self.db = ShoeDatabase(self.path)
        self.db.configure_guild(100, 200, "creative", "standard")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_recaps_measure_distinct_contributors_and_survive_restart(self):
        with patch("bot.database.time.time", return_value=1000):
            for user in (300, 301, 300):
                self.db.record_message(100, user, True)
        self.db.close()
        self.db = ShoeDatabase(self.path)
        with patch("bot.database.time.time", return_value=4665):
            update = self.db.record_message(100, None, False)
        self.assertEqual(update.previous_streak, 3)
        self.assertEqual(update.recap.started_at, 1000)
        self.assertEqual(update.recap.ended_at, 4665)
        self.assertEqual(update.recap.contributors, 2)
        self.assertTrue(update.recap.contributors_complete)
        self.assertEqual(self.db._connection.execute("SELECT COUNT(*) FROM active_streak_contributors").fetchone()[0], 0)
        self.assertEqual(self.db._connection.execute("SELECT COUNT(*) FROM active_streaks").fetchone()[0], 0)
        self.assertIsNone(self.db.record_message(100, None, False).recap)

    def test_relay_break_does_not_add_breaker_to_the_run(self):
        self.db.configure_guild(100, 200, "creative", "relay")
        self.db.record_message(100, 300, True)
        update = self.db.record_message(100, 300, True)
        self.assertEqual(update.recap.contributors, 1)
        self.assertEqual(update.previous_streak, 1)
        self.assertEqual(update.break_reason, "relay")

    def test_old_active_run_has_no_invented_start_or_complete_contributor_count(self):
        self.db.record_message(100, 300, True)
        self.db.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DROP TABLE active_streak_contributors")
            connection.execute("DROP TABLE active_streaks")
            connection.execute("PRAGMA user_version = 4")
            connection.execute("UPDATE schema_metadata SET metadata_value = '4' WHERE metadata_key = 'schema_version'")
        connection.close()
        self.db = ShoeDatabase(self.path)
        self.assertEqual(self.db.get_guild_stats(100).current_streak, 1)
        self.assertEqual(self.db._connection.execute("PRAGMA user_version").fetchone()[0], 5)
        self.db.record_message(100, 301, True)
        update = self.db.record_message(100, None, False)
        self.assertEqual(update.previous_streak, 2)
        self.assertIsNone(update.recap.started_at)
        self.assertFalse(update.recap.contributors_complete)
        self.assertEqual(update.recap.contributors, 1)
        self.db.record_message(100, 301, True)
        fresh = self.db.record_message(100, None, False)
        self.assertIsNotNone(fresh.recap.started_at)
        self.assertTrue(fresh.recap.contributors_complete)

    def test_recap_writes_rollback_with_failed_count(self):
        self.db._connection.execute("""
            CREATE TRIGGER fail_count BEFORE INSERT ON user_stats
            BEGIN SELECT RAISE(ABORT, 'simulated failure'); END
        """)
        with self.assertRaises(DatabaseError):
            self.db.record_message(100, 300, True)
        self.assertEqual(self.db.get_guild_stats(100).current_streak, 0)
        for table in ("active_streaks", "active_streak_contributors"):
            self.assertEqual(self.db._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_forgetme_removes_active_identity_and_marks_count_partial(self):
        self.db.record_message(100, 300, True)
        self.db.record_message(100, 301, True)
        self.db.delete_user_stats(100, 300)
        self.assertEqual(self.db.get_guild_stats(100).current_streak, 2)
        self.assertIsNone(self.db._connection.execute("SELECT user_id FROM active_streak_contributors WHERE user_id = '300'").fetchone())
        self.db.record_message(100, 300, True)
        update = self.db.record_message(100, None, False)
        self.assertFalse(update.recap.contributors_complete)
        self.assertEqual(update.recap.contributors, 2)
        self.assertEqual(self.db.get_rival_counts(100, 300, 301), (1, 1))

    def test_reset_settings_and_relay_forget_clear_recap_state(self):
        for action in ("reset", "channel", "settings", "forget", "remove"):
            with self.subTest(action=action):
                self.db.configure_guild(100, 200, "creative", "relay")
                self.db.record_message(100, 300, True)
                if action == "reset":
                    self.db.reset_guild_stats(100)
                elif action == "channel":
                    self.db.set_shoe_channel(100, 201)
                elif action == "settings":
                    self.db.configure_guild(100, 200, "classic", "standard")
                elif action == "forget":
                    self.db.delete_user_stats(100, 300)
                else:
                    self.db.delete_guild(100)
                self.assertEqual(self.db._connection.execute("SELECT COUNT(*) FROM active_streak_contributors").fetchone()[0], 0)
                self.assertEqual(self.db._connection.execute("SELECT COUNT(*) FROM active_streaks").fetchone()[0], 0)

    def test_identical_settings_keep_recap_and_other_guilds_are_isolated(self):
        self.db.record_message(100, 300, True)
        self.db.configure_guild(101, 201, "creative", "standard")
        for _ in range(3):
            self.db.record_message(101, 302, True)
        self.db.configure_guild(100, 200, "creative", "standard")
        update = self.db.record_message(100, None, False)
        self.assertEqual(update.recap.contributors, 1)
        self.assertTrue(update.recap.contributors_complete)
        self.assertEqual(self.db.get_rival_counts(100, 300, 302), (1, 0))
        self.assertEqual(self.db.get_rival_counts(101, 300, 302), (0, 3))
        self.assertEqual(self.db.get_guild_stats(101).current_streak, 3)


class PlayerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = ShoeDatabase(Path(self.temp.name) / "commands.sqlite3")
        self.db.configure_guild(100, 200, "creative", "relay")
        self.game = ShoeGame(self.db)
        await self.game.load_configuration()
        self.cog = ShoeCommands(self.db, self.game)

    async def asyncTearDown(self):
        await self.game.aclose()
        self.temp.cleanup()

    def interaction(self, guild_id=100):
        return FakeInteraction(user_id=300, guild_id=guild_id, administrator=False)

    async def test_shoecheck_is_private_uses_current_rules_and_never_changes_counts(self):
        self.db.record_message(100, 300, True)
        before = self.db.get_guild_stats(100)
        for mode, text, matches in (("creative", "👟", True), ("creative", "s-h-o-e", True), ("classic", "👟", False), ("classic", "horseshoe", True), ("creative", "@everyone hat", False)):
            with self.subTest(mode=mode, text=text):
                await self.game.configure_guild(100, 200, mode, "relay")
                snapshot = self.db.get_guild_stats(100)
                interaction = self.interaction()
                await ShoeCommands.shoecheck.callback(self.cog, interaction, text)
                self.assertTrue(interaction.response.defer_ephemeral)
                embed = interaction.response.messages[-1][1]["embed"]
                self.assertEqual(embed.title, "Shoe check: match" if matches else "Shoe check: no match")
                self.assertIn("Relay rules still apply", embed.footer.text)
                self.assertNotIn("@everyone", str(embed.to_dict()))
                self.assertEqual(self.db.get_guild_stats(100), snapshot)
        self.assertEqual(self.db.get_guild_stats(100).total_shoes, before.total_shoes)

    async def test_shoecheck_before_setup_is_private_and_helpful(self):
        interaction = self.interaction(999)
        await ShoeCommands.shoecheck.callback(self.cog, interaction, "shoe")
        self.assertTrue(interaction.response.defer_ephemeral)
        self.assertIn("/setup", interaction.response.messages[-1][0])

    async def test_rival_lead_trail_tie_and_zero_counts_without_pings_or_writes(self):
        await self.game.configure_guild(100, 200, "creative", "standard")
        rival = SimpleNamespace(id=301, bot=False, mention="<@301>")
        for yours, theirs, phrase in ((0, 0, "tied"), (3, 1, "lead by **2**"), (1, 3, "trail by **2**"), (3, 3, "tied")):
            with self.subTest(yours=yours, theirs=theirs):
                self.db.reset_guild_stats(100)
                for user, count in ((300, yours), (301, theirs)):
                    for _ in range(count):
                        self.db.record_message(100, user, True)
                snapshot = self.db.get_guild_stats(100)
                interaction = self.interaction()
                await ShoeCommands.rival.callback(self.cog, interaction, rival)
                kwargs = interaction.response.messages[-1][1]
                self.assertIn(phrase, kwargs["embed"].description)
                self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})
                self.assertEqual(self.db.get_guild_stats(100), snapshot)
                self.assertEqual(self.db.get_rival_counts(100, 300, 301), (yours, theirs))

    async def test_rival_rejects_self_and_bots_privately(self):
        for user_id, is_bot in ((300, False), (301, True)):
            interaction = self.interaction()
            await ShoeCommands.rival.callback(self.cog, interaction, SimpleNamespace(id=user_id, bot=is_bot))
            self.assertTrue(interaction.response.messages[-1][1]["ephemeral"])

    async def test_rival_database_failure_has_clean_response(self):
        interaction = self.interaction()
        with patch.object(self.db, "get_rival_counts", side_effect=DatabaseError("private detail")):
            await ShoeCommands.rival.callback(self.cog, interaction, SimpleNamespace(id=301, bot=False))
        self.assertIn("temporarily unavailable", interaction.response.messages[-1][0])
        self.assertNotIn("private detail", interaction.response.messages[-1][0])

    async def test_recap_card_uses_committed_run_and_safe_mentions(self):
        channel = FakeChannel(200)
        with patch("bot.database.time.time", return_value=1000):
            for i, user in enumerate((300, 301, 300), start=1):
                await self.game.handle_message(FakeMessage(message_id=i, guild_id=100, channel=channel, content="shoe", user_id=user))
        with patch("bot.database.time.time", return_value=4665):
            bad = FakeMessage(message_id=4, guild_id=100, channel=channel, content="hat", user_id=302)
            await self.game.handle_message(bad)
            await self.game.handle_message(bad)
        self.assertEqual(len(channel.sent), 1)
        kwargs = channel.sent[0][1]
        embed = kwargs["embed"]
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["Time alive"], "1h 1m")
        self.assertEqual(fields["Contributors"], "2")
        self.assertEqual(fields["Final streak"], "**3** shoes")
        self.assertIn("server best", fields["Record chase"])
        self.assertEqual(kwargs["allowed_mentions"].to_dict(), {"parse": []})

    async def test_recap_falls_back_to_text_without_embed_permission(self):
        class NoEmbedsChannel(FakeChannel):
            async def send(self, text=None, **kwargs):
                if "embed" in kwargs:
                    raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Embed Links")
                await super().send(text, **kwargs)
        channel = NoEmbedsChannel(200)
        await self.game.handle_message(FakeMessage(message_id=1, guild_id=100, channel=channel, content="shoe"))
        await self.game.handle_message(FakeMessage(message_id=2, guild_id=100, channel=channel, content="hat"))
        self.assertEqual(len(channel.sent), 1)
        self.assertIn("**Contributors:** 1", channel.sent[0][0])
        self.assertEqual(channel.sent[0][1]["allowed_mentions"].to_dict(), {"parse": []})


if __name__ == "__main__":
    unittest.main()
