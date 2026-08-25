from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from bot.database import GuildNotConfigured, ShoeDatabase


class ShoeDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        self.database = ShoeDatabase(self.database_path)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary_directory.cleanup()

    def test_valid_and_invalid_messages_update_streaks(self) -> None:
        self.database.set_shoe_channel(100, 200)

        first = self.database.record_message(100, 300, True)
        second = self.database.record_message(100, 300, True)
        broken = self.database.record_message(100, None, False)
        already_zero = self.database.record_message(100, None, False)

        self.assertEqual(first.current_streak, 1)
        self.assertEqual(second.current_streak, 2)
        self.assertEqual(broken.previous_streak, 2)
        self.assertEqual(broken.current_streak, 0)
        self.assertEqual(already_zero.previous_streak, 0)

        stats = self.database.get_guild_stats(100)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.total_shoes, 2)
        self.assertEqual(stats.current_streak, 0)
        self.assertEqual(stats.best_streak, 2)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 2)

    def test_guild_statistics_and_users_are_isolated(self) -> None:
        self.database.set_shoe_channel(100, 201)
        self.database.set_shoe_channel(101, 202)
        self.database.record_message(100, 300, True)
        self.database.record_message(101, 300, True)
        self.database.record_message(101, 300, True)

        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)
        self.assertEqual(self.database.get_guild_stats(101).total_shoes, 2)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)
        self.assertEqual(self.database.get_user_stats(101, 300).shoe_count, 2)

    def test_leaderboard_uses_competition_ranks_for_ties(self) -> None:
        self.database.set_shoe_channel(100, 200)
        for user_id, count in ((301, 3), (302, 2), (303, 2), (304, 1)):
            for _ in range(count):
                self.database.record_message(100, user_id, True)

        leaderboard = self.database.get_leaderboard(100)
        self.assertEqual(
            [(entry.user_id, entry.shoe_count, entry.rank) for entry in leaderboard],
            [(301, 3, 1), (302, 2, 2), (303, 2, 2), (304, 1, 4)],
        )
        self.assertEqual(self.database.get_user_stats(100, 303).rank, 2)
        self.assertIsNone(self.database.get_user_stats(100, 999).rank)

    def test_reset_is_atomic_and_only_affects_one_guild(self) -> None:
        self.database.set_shoe_channel(100, 201)
        self.database.set_shoe_channel(101, 202)
        self.database.record_message(100, 300, True)
        self.database.record_message(101, 301, True)

        self.database.reset_guild_stats(100)

        reset_stats = self.database.get_guild_stats(100)
        other_stats = self.database.get_guild_stats(101)
        self.assertEqual(reset_stats.channel_id, 201)
        self.assertEqual(reset_stats.total_shoes, 0)
        self.assertEqual(reset_stats.best_streak, 0)
        self.assertEqual(self.database.get_leaderboard(100), [])
        self.assertEqual(other_stats.total_shoes, 1)
        self.assertEqual(self.database.get_user_stats(101, 301).shoe_count, 1)

    def test_changing_channel_preserves_statistics(self) -> None:
        self.database.set_shoe_channel(100, 200)
        self.database.record_message(100, 300, True)
        self.database.set_shoe_channel(100, 201)

        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.channel_id, 201)
        self.assertEqual(stats.total_shoes, 1)

    def test_unconfigured_guild_cannot_be_updated_or_reset(self) -> None:
        with self.assertRaises(GuildNotConfigured):
            self.database.record_message(100, 300, True)
        with self.assertRaises(GuildNotConfigured):
            self.database.reset_guild_stats(100)

    def test_concurrent_writes_do_not_lose_updates(self) -> None:
        self.database.set_shoe_channel(100, 200)

        def add_shoe(index: int) -> None:
            self.database.record_message(100, 300 + (index % 4), True)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(add_shoe, range(200)))

        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 200)
        self.assertEqual(stats.current_streak, 200)
        self.assertEqual(stats.best_streak, 200)
        self.assertEqual(
            sum(entry.shoe_count for entry in self.database.get_leaderboard(100)),
            200,
        )

    def test_concurrent_reset_and_writes_leave_consistent_invariants(self) -> None:
        self.database.set_shoe_channel(100, 200)

        operations = [("write", index) for index in range(100)]
        operations.insert(50, ("reset", 0))

        def run(operation: tuple[str, int]) -> None:
            kind, index = operation
            if kind == "reset":
                self.database.reset_guild_stats(100)
            else:
                self.database.record_message(100, 300 + (index % 4), True)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(run, operations))

        stats = self.database.get_guild_stats(100)
        leaderboard_total = sum(
            entry.shoe_count for entry in self.database.get_leaderboard(100)
        )
        self.assertEqual(stats.total_shoes, leaderboard_total)
        self.assertEqual(stats.current_streak, stats.total_shoes)
        self.assertEqual(stats.best_streak, stats.total_shoes)
        self.assertLessEqual(stats.total_shoes, 100)

    def test_data_persists_after_reopen(self) -> None:
        self.database.set_shoe_channel(100, 200)
        self.database.record_message(100, 300, True)
        self.database.close()

        self.database = ShoeDatabase(self.database_path)

        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.channel_id, 200)
        self.assertEqual(stats.total_shoes, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)

    def test_deleting_guild_cascades_only_its_user_rows(self) -> None:
        self.database.set_shoe_channel(100, 200)
        self.database.set_shoe_channel(101, 201)
        self.database.record_message(100, 300, True)
        self.database.record_message(101, 301, True)

        self.database.delete_guild(100)

        self.assertIsNone(self.database.get_guild_stats(100))
        self.assertEqual(self.database.get_leaderboard(100), [])
        self.assertEqual(self.database.get_guild_stats(101).total_shoes, 1)
        self.assertEqual(self.database.get_user_stats(101, 301).shoe_count, 1)

    def test_deleting_user_stats_preserves_anonymous_server_aggregates(self) -> None:
        self.database.set_shoe_channel(100, 200)
        self.database.set_shoe_channel(101, 201)
        self.database.record_message(100, 300, True)
        self.database.record_message(100, 300, True)
        self.database.record_message(101, 300, True)

        self.assertTrue(self.database.delete_user_stats(100, 300))

        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 2)
        self.assertEqual(stats.current_streak, 2)
        self.assertEqual(stats.best_streak, 2)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 0)
        self.assertEqual(self.database.get_leaderboard(100), [])
        self.assertEqual(self.database.get_user_stats(101, 300).shoe_count, 1)
        self.assertFalse(self.database.delete_user_stats(100, 300))


if __name__ == "__main__":
    unittest.main()
