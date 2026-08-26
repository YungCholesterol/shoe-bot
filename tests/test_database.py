from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from bot.database import DatabaseError, GuildNotConfigured, ShoeDatabase


class ShoeDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        self.database = ShoeDatabase(self.database_path)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary_directory.cleanup()

    def configure(
        self,
        guild: int = 100,
        channel: int = 200,
        matching: str = "creative",
        gameplay: str = "relay",
    ) -> None:
        self.database.configure_guild(guild, channel, matching, gameplay)

    def complete_standard_streak(self, length: int, user: int = 300):
        for _ in range(length):
            update = self.database.record_message(100, user, True)
            self.assertTrue(update.accepted)
        return self.database.record_message(100, None, False)

    def test_new_servers_default_to_creative_relay(self) -> None:
        self.database.set_shoe_channel(100, 200)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.matching_mode, "creative")
        self.assertEqual(stats.gameplay_mode, "relay")

    def test_relay_rejects_same_user_without_incrementing_counts(self) -> None:
        self.configure()
        first = self.database.record_message(100, 300, True)
        repeated = self.database.record_message(100, 300, True)

        self.assertTrue(first.accepted)
        self.assertFalse(repeated.accepted)
        self.assertEqual(repeated.break_reason, "relay")
        self.assertEqual(repeated.previous_streak, 1)
        self.assertEqual(repeated.current_streak, 0)
        self.assertEqual(repeated.total_shoes, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)
        self.assertEqual(self.database.get_hall_of_fame(100)[0].streak_length, 1)

    def test_relay_allows_alternating_users(self) -> None:
        self.configure()
        for user in (300, 301, 300, 301):
            self.assertTrue(self.database.record_message(100, user, True).accepted)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 4)
        self.assertEqual(stats.current_streak, 4)
        self.assertEqual(stats.best_streak, 4)

    def test_standard_accepts_consecutive_messages_from_same_user(self) -> None:
        self.configure(gameplay="standard")
        for _ in range(3):
            self.assertTrue(self.database.record_message(100, 300, True).accepted)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 3)
        self.assertEqual(stats.current_streak, 3)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 3)

    def test_invalid_message_only_announces_a_nonzero_break(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)
        broken = self.database.record_message(100, None, False)
        already_zero = self.database.record_message(100, None, False)

        self.assertEqual(broken.previous_streak, 1)
        self.assertEqual(broken.break_reason, "invalid")
        self.assertEqual(broken.hall_of_fame_rank, 1)
        self.assertEqual(already_zero.previous_streak, 0)
        self.assertEqual(already_zero.hall_of_fame_rank, None)

    def test_guilds_and_users_are_isolated(self) -> None:
        self.configure(100, 201, gameplay="standard")
        self.configure(101, 202, gameplay="standard")
        self.database.record_message(100, 300, True)
        self.database.record_message(101, 300, True)
        self.database.record_message(101, 300, True)
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)
        self.assertEqual(self.database.get_guild_stats(101).total_shoes, 2)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)
        self.assertEqual(self.database.get_user_stats(101, 300).shoe_count, 2)

    def test_leaderboard_uses_competition_ranks_for_ties(self) -> None:
        self.configure(gameplay="standard")
        for user_id, count in ((301, 3), (302, 2), (303, 2), (304, 1)):
            for _ in range(count):
                self.database.record_message(100, user_id, True)
        self.assertEqual(
            [
                (entry.user_id, entry.shoe_count, entry.rank)
                for entry in self.database.get_leaderboard(100)
            ],
            [(301, 3, 1), (302, 2, 2), (303, 2, 2), (304, 1, 4)],
        )
        self.assertEqual(self.database.get_user_stats(100, 303).rank, 2)
        self.assertIsNone(self.database.get_user_stats(100, 999).rank)

    def test_hall_of_fame_records_completed_distinct_streaks_and_caps_at_ten(self) -> None:
        self.configure(gameplay="standard")
        for length in range(1, 12):
            self.complete_standard_streak(length, 300 + length)
        entries = self.database.get_hall_of_fame(100)
        self.assertEqual(len(entries), 10)
        self.assertEqual([entry.streak_length for entry in entries], list(range(11, 1, -1)))
        self.assertEqual([entry.rank for entry in entries], list(range(1, 11)))
        not_retained = self.complete_standard_streak(1, user=999)
        self.assertIsNone(not_retained.hall_of_fame_rank)

    def test_equal_completed_streak_lengths_have_one_hall_entry(self) -> None:
        self.configure(gameplay="standard")
        self.complete_standard_streak(3)
        first = self.database.get_hall_of_fame(100)[0]
        duplicate = self.complete_standard_streak(3, user=301)
        entries = self.database.get_hall_of_fame(100)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].streak_length, 3)
        self.assertEqual(entries[0].completed_at, first.completed_at)
        self.assertIsNone(duplicate.hall_of_fame_rank)

    def test_active_streak_does_not_enter_hall_until_it_ends(self) -> None:
        self.configure(gameplay="standard")
        for _ in range(5):
            self.database.record_message(100, 300, True)
        self.assertEqual(self.database.get_hall_of_fame(100), [])
        self.database.record_message(100, None, False)
        self.assertEqual(self.database.get_hall_of_fame(100)[0].streak_length, 5)

    def test_reset_is_atomic_and_preserves_channel_and_modes(self) -> None:
        self.configure(channel=201, matching="classic", gameplay="standard")
        self.complete_standard_streak(2)
        self.database.reset_guild_stats(100)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.channel_id, 201)
        self.assertEqual(stats.matching_mode, "classic")
        self.assertEqual(stats.gameplay_mode, "standard")
        self.assertEqual((stats.total_shoes, stats.current_streak, stats.best_streak), (0, 0, 0))
        self.assertEqual(self.database.get_leaderboard(100), [])
        self.assertEqual(self.database.get_hall_of_fame(100), [])

    def test_changing_settings_preserves_statistics(self) -> None:
        self.configure(gameplay="standard")
        self.database.record_message(100, 300, True)
        self.database.configure_guild(100, 201, "classic", "relay")
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.channel_id, 201)
        self.assertEqual(stats.total_shoes, 1)
        self.assertEqual(stats.best_streak, 1)
        self.assertEqual(stats.current_streak, 0)
        self.assertEqual(self.database.get_hall_of_fame(100)[0].streak_length, 1)

    def test_saving_identical_settings_keeps_active_relay_state(self) -> None:
        self.configure(gameplay="relay")
        self.database.record_message(100, 300, True)
        self.database.configure_guild(100, 200, "creative", "relay")
        repeated = self.database.record_message(100, 300, True)
        self.assertFalse(repeated.accepted)
        self.assertEqual(repeated.break_reason, "relay")

    def test_each_meaningful_setting_change_completes_active_streak(self) -> None:
        self.configure()
        cases = (
            (201, "creative", "relay"),
            (200, "classic", "relay"),
            (200, "creative", "standard"),
        )
        for channel, matching, gameplay in cases:
            with self.subTest(channel=channel, matching=matching, gameplay=gameplay):
                self.database.reset_guild_stats(100)
                self.database.configure_guild(100, 200, "creative", "relay")
                self.database.record_message(100, 300, True)
                self.database.configure_guild(100, channel, matching, gameplay)
                stats = self.database.get_guild_stats(100)
                self.assertEqual(stats.current_streak, 0)
                self.assertEqual(stats.total_shoes, 1)
                self.assertEqual(stats.best_streak, 1)
                self.assertEqual(
                    self.database.get_hall_of_fame(100)[0].streak_length,
                    1,
                )

    def test_setting_change_preserves_users_and_prior_hall_records(self) -> None:
        self.configure(gameplay="standard")
        self.database.record_message(100, 300, True)
        self.database.record_message(100, 301, True)
        self.database.record_message(100, None, False)
        self.database.record_message(100, 300, True)

        self.database.configure_guild(100, 201, "classic", "relay")

        stats = self.database.get_guild_stats(100)
        self.assertEqual((stats.total_shoes, stats.current_streak, stats.best_streak), (3, 0, 2))
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 2)
        self.assertEqual(self.database.get_user_stats(100, 301).shoe_count, 1)
        self.assertEqual(
            [entry.streak_length for entry in self.database.get_hall_of_fame(100)],
            [2, 1],
        )

    def test_setting_change_with_zero_streak_does_not_create_record(self) -> None:
        self.configure()
        self.database.configure_guild(100, 201, "classic", "standard")
        self.assertEqual(self.database.get_hall_of_fame(100), [])

    def test_failed_settings_archive_rolls_back_configuration_and_streak(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)
        self.database._connection.execute(
            """
            CREATE TRIGGER fail_settings_hall
            BEFORE INSERT ON hall_of_fame
            BEGIN
                SELECT RAISE(ABORT, 'simulated Hall failure');
            END
            """
        )
        with self.assertRaises(DatabaseError):
            self.database.configure_guild(100, 201, "classic", "standard")
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.channel_id, 200)
        self.assertEqual(stats.matching_mode, "creative")
        self.assertEqual(stats.gameplay_mode, "relay")
        self.assertEqual((stats.total_shoes, stats.current_streak, stats.best_streak), (1, 1, 1))
        row = self.database._connection.execute(
            "SELECT last_contributor_user_id FROM guild_settings WHERE guild_id = ?",
            ("100",),
        ).fetchone()
        self.assertEqual(row["last_contributor_user_id"], "300")
        self.assertEqual(self.database.get_hall_of_fame(100), [])

    def test_unconfigured_guild_cannot_be_updated_or_reset(self) -> None:
        with self.assertRaises(GuildNotConfigured):
            self.database.record_message(100, 300, True)
        with self.assertRaises(GuildNotConfigured):
            self.database.reset_guild_stats(100)

    def test_invalid_configuration_values_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.database.configure_guild(100, 200, "regex", "relay")
        with self.assertRaises(ValueError):
            self.database.configure_guild(100, 200, "creative", "solo")

    def test_concurrent_standard_writes_do_not_lose_updates(self) -> None:
        self.configure(gameplay="standard")

        def add_shoe(index: int) -> None:
            self.database.record_message(100, 300 + (index % 4), True)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(add_shoe, range(200)))
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 200)
        self.assertEqual(stats.current_streak, 200)
        self.assertEqual(stats.best_streak, 200)
        self.assertEqual(
            sum(entry.shoe_count for entry in self.database.get_leaderboard(100)), 200
        )

    def test_concurrent_relay_repeats_linearize_safely(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)

        def repeat(_index: int):
            return self.database.record_message(100, 300, True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(repeat, range(2)))
        self.assertEqual(sorted(result.accepted for result in results), [False, True])
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 2)
        self.assertEqual(stats.current_streak, 1)
        self.assertEqual(self.database.get_hall_of_fame(100)[0].streak_length, 1)

    def test_concurrent_invalid_messages_only_break_once(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)
        self.database.record_message(100, 301, True)

        def invalidate(_index: int):
            return self.database.record_message(100, None, False)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(invalidate, range(2)))
        self.assertEqual(sorted(result.previous_streak for result in results), [0, 2])
        self.assertEqual(len(self.database.get_hall_of_fame(100)), 1)

    def test_two_database_connections_do_not_lose_standard_updates(self) -> None:
        self.configure(gameplay="standard")
        second = ShoeDatabase(self.database_path)
        try:
            def add(index: int) -> None:
                database = self.database if index % 2 == 0 else second
                database.record_message(100, 300 + (index % 4), True)

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(add, range(100)))
            stats = self.database.get_guild_stats(100)
            self.assertEqual(stats.total_shoes, 100)
            self.assertEqual(stats.current_streak, 100)
            self.assertEqual(
                sum(entry.shoe_count for entry in self.database.get_leaderboard(100)),
                100,
            )
        finally:
            second.close()

    def test_two_database_connections_linearize_relay_repeats(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)
        second = ShoeDatabase(self.database_path)
        try:
            def repeat(index: int):
                database = self.database if index == 0 else second
                return database.record_message(100, 300, True)

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(repeat, range(2)))
            self.assertEqual(sorted(result.accepted for result in results), [False, True])
            stats = self.database.get_guild_stats(100)
            self.assertEqual((stats.total_shoes, stats.current_streak), (2, 1))
        finally:
            second.close()

    def test_failed_user_upsert_rolls_back_aggregate_counters(self) -> None:
        self.configure(gameplay="standard")
        self.database._connection.execute(
            """
            CREATE TRIGGER fail_user_write BEFORE INSERT ON user_stats
            BEGIN SELECT RAISE(ABORT, 'test'); END
            """
        )
        with self.assertRaises(DatabaseError):
            self.database.record_message(100, 300, True)
        stats = self.database.get_guild_stats(100)
        self.assertEqual((stats.total_shoes, stats.current_streak), (0, 0))

    def test_failed_hall_write_does_not_reset_streak(self) -> None:
        self.configure(gameplay="standard")
        self.database.record_message(100, 300, True)
        self.database._connection.execute(
            """
            CREATE TRIGGER fail_hall_write BEFORE INSERT ON hall_of_fame
            BEGIN SELECT RAISE(ABORT, 'test'); END
            """
        )
        with self.assertRaises(DatabaseError):
            self.database.record_message(100, None, False)
        self.assertEqual(self.database.get_guild_stats(100).current_streak, 1)

    def test_data_persists_after_reopen(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)
        self.database.close()
        self.database = ShoeDatabase(self.database_path)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 1)
        self.assertEqual(stats.current_streak, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)

    def test_legacy_schema_migrates_idempotently_without_losing_data(self) -> None:
        self.database.close()
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            DROP TABLE IF EXISTS hall_of_fame;
            DROP TABLE IF EXISTS user_stats;
            DROP TABLE IF EXISTS guild_settings;
            DROP TABLE IF EXISTS schema_metadata;
            PRAGMA user_version = 0;
            CREATE TABLE guild_settings (
                guild_id TEXT PRIMARY KEY NOT NULL,
                shoe_channel_id TEXT NOT NULL,
                total_shoes INTEGER NOT NULL DEFAULT 0,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE user_stats (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                shoe_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guild_settings(guild_id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            INSERT INTO guild_settings VALUES ('100', '200', 7, 2, 5);
            INSERT INTO user_stats VALUES ('100', '300', 4);
            """
        )
        connection.close()

        self.database = ShoeDatabase(self.database_path)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(
            (stats.channel_id, stats.total_shoes, stats.current_streak, stats.best_streak),
            (200, 7, 2, 5),
        )
        self.assertEqual((stats.matching_mode, stats.gameplay_mode), ("creative", "relay"))
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 4)
        legacy = self.database.get_hall_of_fame(100)
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0].streak_length, 5)
        self.assertTrue(legacy[0].is_legacy)
        self.assertIsNone(legacy[0].completed_at)
        self.database.close()
        self.database = ShoeDatabase(self.database_path)
        self.assertEqual(len(self.database.get_hall_of_fame(100)), 1)

    def test_newer_database_schema_fails_closed(self) -> None:
        self.database.close()
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA user_version = 99")
        connection.close()
        with self.assertRaises(DatabaseError):
            ShoeDatabase(self.database_path)
        # Give tearDown a harmless closed replacement.
        self.database = ShoeDatabase(Path(self.temporary_directory.name) / "replacement.sqlite3")

    def test_missing_version_two_schema_fails_closed_without_rebuilding(self) -> None:
        self.database.close()
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            PRAGMA foreign_keys = OFF;
            DROP TABLE IF EXISTS hall_of_fame;
            DROP TABLE IF EXISTS user_stats;
            DROP TABLE IF EXISTS schema_metadata;
            DROP TABLE IF EXISTS guild_settings;
            PRAGMA user_version = 2;
            """
        )
        connection.close()

        with self.assertRaises(DatabaseError):
            ShoeDatabase(self.database_path)

        connection = sqlite3.connect(self.database_path)
        try:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(tables, [])
        self.assertEqual(version, 2)
        self.database = ShoeDatabase(
            Path(self.temporary_directory.name) / "replacement.sqlite3"
        )

    def test_failed_migration_rolls_back_every_schema_change(self) -> None:
        self.database.close()
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            DROP TABLE IF EXISTS hall_of_fame;
            DROP TABLE IF EXISTS user_stats;
            DROP TABLE IF EXISTS guild_settings;
            DROP TABLE IF EXISTS schema_metadata;
            PRAGMA user_version = 0;
            CREATE TABLE guild_settings (
                guild_id TEXT PRIMARY KEY NOT NULL,
                shoe_channel_id TEXT NOT NULL,
                total_shoes INTEGER NOT NULL DEFAULT 0,
                current_streak INTEGER NOT NULL DEFAULT 0,
                best_streak INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE user_stats (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                shoe_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guild_settings(guild_id) ON DELETE CASCADE
            ) WITHOUT ROWID;
            CREATE TABLE hall_of_fame (guild_id TEXT);
            INSERT INTO guild_settings VALUES ('100', '200', 5, 2, 4);
            """
        )
        connection.close()

        with self.assertRaises(DatabaseError):
            ShoeDatabase(self.database_path)

        connection = sqlite3.connect(self.database_path)
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(guild_settings)")
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        connection.execute("DROP TABLE hall_of_fame")
        connection.close()
        self.assertNotIn("matching_mode", columns)
        self.assertNotIn("gameplay_mode", columns)
        self.assertNotIn("last_contributor_user_id", columns)
        self.assertEqual(version, 0)

        self.database = ShoeDatabase(self.database_path)
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 5)

    def test_delete_guild_cascades_user_and_hall_rows(self) -> None:
        self.configure(gameplay="standard")
        self.complete_standard_streak(2)
        self.database.delete_guild(100)
        self.assertIsNone(self.database.get_guild_stats(100))
        self.assertEqual(self.database.get_leaderboard(100), [])
        self.assertEqual(self.database.get_hall_of_fame(100), [])

    def test_forgetme_removes_user_id_and_ends_owned_relay_streak(self) -> None:
        self.configure()
        self.database.record_message(100, 300, True)
        result = self.database.delete_user_stats(100, 300)
        stats = self.database.get_guild_stats(100)
        self.assertTrue(result.deleted)
        self.assertEqual(result.ended_relay_streak, 1)
        self.assertEqual(stats.total_shoes, 1)
        self.assertEqual(stats.current_streak, 0)
        self.assertEqual(stats.best_streak, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 0)
        self.assertEqual(self.database.get_hall_of_fame(100)[0].streak_length, 1)

    def test_database_schema_contains_no_message_or_profile_content(self) -> None:
        schema = " ".join(
            str(row[0])
            for row in self.database._connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
            ).fetchall()
        ).casefold()
        for forbidden in (
            "message_text",
            "message_content",
            "message_id",
            "username",
            "display_name",
            "email",
            "attachment",
            "sticker_name",
        ):
            self.assertNotIn(forbidden, schema)

    def test_close_failure_still_stops_executor(self) -> None:
        original_close = self.database._close_connection

        def fail_close() -> None:
            raise RuntimeError("simulated close failure")

        self.database._close_connection = fail_close  # type: ignore[method-assign]
        with self.assertRaises(RuntimeError):
            self.database.close()
        self.assertTrue(self.database._executor_closed)
        self.database._close_connection = original_close  # type: ignore[method-assign]
        original_close()


class AsyncDatabaseWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "async.sqlite3"
        self.database = ShoeDatabase(self.path)

    async def asyncTearDown(self) -> None:
        await self.database.aclose()
        self.temporary_directory.cleanup()

    async def test_worker_runs_off_loop_and_survives_operation_error(self) -> None:
        worker_name = await self.database.run(
            lambda: threading.current_thread().name
        )
        self.assertTrue(worker_name.startswith("shoe-sqlite"))

        def fail() -> None:
            raise ValueError("simulated operation failure")

        with self.assertRaises(ValueError):
            await self.database.run(fail)
        self.assertEqual(await self.database.run(lambda: 42), 42)

    async def test_cancelled_waiter_does_not_cancel_queued_write(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_write() -> None:
            started.set()
            release.wait(timeout=2)
            self.database.configure_guild(100, 200, "creative", "relay")

        waiter = asyncio.create_task(self.database.run(slow_write))
        while not started.is_set():
            await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        stats = await self.database.run(self.database.get_guild_stats, 100)
        self.assertIsNotNone(stats)

    async def test_close_drains_work_rejects_later_jobs_and_is_idempotent(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_write() -> None:
            started.set()
            release.wait(timeout=2)
            self.database.configure_guild(100, 200, "creative", "relay")

        write = asyncio.create_task(self.database.run(slow_write))
        while not started.is_set():
            await asyncio.sleep(0)
        first_close = asyncio.create_task(self.database.aclose())
        second_close = asyncio.create_task(self.database.aclose())
        await asyncio.sleep(0)
        with self.assertRaises(DatabaseError):
            await self.database.run(lambda: None)
        release.set()
        await write
        await asyncio.gather(first_close, second_close)
        reopened = ShoeDatabase(self.path)
        try:
            self.assertIsNotNone(reopened.get_guild_stats(100))
        finally:
            reopened.close()

    async def test_cancelled_close_waiter_does_not_cancel_shared_shutdown(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow_job() -> None:
            started.set()
            release.wait(timeout=2)

        job = asyncio.create_task(self.database.run(slow_job))
        while not started.is_set():
            await asyncio.sleep(0)
        waiter = asyncio.create_task(self.database.aclose())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        release.set()
        await job
        await self.database.aclose()
        self.assertTrue(self.database._executor_closed)


if __name__ == "__main__":
    unittest.main()
