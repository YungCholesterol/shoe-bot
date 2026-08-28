"""Transaction-safe SQLite persistence for Shoe Bot.

Only Discord snowflake IDs, server configuration, and numerical game statistics
are persisted. Message text never enters this module.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Callable, Iterator, Literal, Sequence, TypeVar


MatchingMode = Literal["classic", "creative"]
GameplayMode = Literal["standard", "relay"]
BreakReason = Literal["invalid", "relay"]

MATCHING_MODES = frozenset({"classic", "creative"})
GAMEPLAY_MODES = frozenset({"standard", "relay"})
LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class DatabaseError(RuntimeError):
    """Raised when a SQLite operation cannot be completed."""


class GuildNotConfigured(DatabaseError):
    """Raised when an operation needs a configured guild that does not exist."""


@dataclass(frozen=True, slots=True)
class GuildConfig:
    channel_id: int
    matching_mode: MatchingMode
    gameplay_mode: GameplayMode
    random_shoe_enabled: bool = False
    random_shoe_channel_ids: tuple[int, ...] = ()
    random_shoe_next_at: int | None = None
    random_shoe_min_minutes: int = 50
    random_shoe_max_minutes: int = 103
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None
    log_channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class GuildStats:
    channel_id: int
    total_shoes: int
    current_streak: int
    best_streak: int
    matching_mode: MatchingMode
    gameplay_mode: GameplayMode
    random_shoe_enabled: bool = False
    random_shoe_channel_ids: tuple[int, ...] = ()
    random_shoe_min_minutes: int = 50
    random_shoe_max_minutes: int = 103
    quiet_start_hour: int | None = None
    quiet_end_hour: int | None = None
    log_channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class MessageUpdate:
    total_shoes: int
    current_streak: int
    best_streak: int
    previous_streak: int
    accepted: bool
    break_reason: BreakReason | None
    hall_of_fame_rank: int | None


@dataclass(frozen=True, slots=True)
class UserStats:
    shoe_count: int
    rank: int | None


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    user_id: int
    shoe_count: int
    rank: int


@dataclass(frozen=True, slots=True)
class HallOfFameEntry:
    rank: int
    streak_length: int
    completed_at: int | None
    is_legacy: bool


@dataclass(frozen=True, slots=True)
class LeaderboardSnapshot:
    stats: GuildStats
    contributors: tuple[LeaderboardEntry, ...]
    hall_of_fame: tuple[HallOfFameEntry, ...]


@dataclass(frozen=True, slots=True)
class UserDeletion:
    deleted: bool
    ended_relay_streak: int


class ShoeDatabase:
    """Small, durable SQLite data store shared by the Discord event loop.

    A re-entrant process lock serializes access to the single connection. Every
    game-changing operation also uses ``BEGIN IMMEDIATE``, making Relay checks,
    counter changes, user totals, and Hall-of-Fame writes one atomic unit.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False

        try:
            self._connection = sqlite3.connect(
                self.path,
                timeout=5.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute("PRAGMA secure_delete = ON")
            self._initialize_schema()
        except sqlite3.Error as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise DatabaseError("Could not initialize the SQLite database") from exc
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="shoe-sqlite",
        )
        self._executor_state_lock = threading.Lock()
        self._executor_closed = False
        self._closing = False
        self._close_future = None
        self._aclose_task: asyncio.Task[None] | None = None

    async def run(
        self,
        operation: Callable[..., T],
        /,
        *args: object,
        **kwargs: object,
    ) -> T:
        """Run one database operation on the ordered SQLite worker.

        Discord dispatches message handlers concurrently. A single worker keeps
        their database transitions in submission order while ensuring SQLite
        waits and durable fsyncs never block the Discord event loop.
        """
        loop = asyncio.get_running_loop()
        with self._executor_state_lock:
            if self._executor_closed or self._closing:
                raise DatabaseError("The SQLite worker is closed")
            try:
                future = self._executor.submit(
                    partial(operation, *args, **kwargs)
                )
            except RuntimeError as exc:
                raise DatabaseError("The SQLite worker is unavailable") from exc
        wrapped = asyncio.wrap_future(future, loop=loop)

        def retrieve_late_exception(completed: asyncio.Future[T]) -> None:
            if completed.cancelled():
                return
            try:
                completed.exception()
            except (asyncio.CancelledError, Exception):
                pass

        wrapped.add_done_callback(retrieve_late_exception)
        return await asyncio.shield(wrapped)

    def _initialize_schema(self) -> None:
        # Do not use executescript here: it can commit before every migration
        # statement. One explicit transaction makes a production upgrade either
        # fully visible or fully rolled back after interruption.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            user_version = int(
                self._connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if user_version > 4:
                raise sqlite3.DatabaseError(
                    "Database schema is newer than this Shoe Bot release"
                )

            existing = self._connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'guild_settings'
                """
            ).fetchone()
            if user_version == 2 and existing is None:
                raise sqlite3.DatabaseError(
                    "Version 2 database is missing guild_settings"
                )
            old_columns: set[str] = set()
            if existing is not None:
                old_columns = {
                    str(row["name"])
                    for row in self._connection.execute(
                        "PRAGMA table_info(guild_settings)"
                    ).fetchall()
                }
                if user_version == 2:
                    required = {
                        "guild_id",
                        "shoe_channel_id",
                        "total_shoes",
                        "current_streak",
                        "best_streak",
                        "matching_mode",
                        "gameplay_mode",
                        "last_contributor_user_id",
                    }
                    if not required.issubset(old_columns):
                        raise sqlite3.DatabaseError(
                            "Version 2 database is missing required columns"
                        )
                    tables = {
                        str(row["name"])
                        for row in self._connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        ).fetchall()
                    }
                    if not {
                        "guild_settings",
                        "user_stats",
                        "hall_of_fame",
                        "schema_metadata",
                    }.issubset(tables):
                        raise sqlite3.DatabaseError(
                            "Version 2 database is missing required tables"
                        )
                    metadata = self._connection.execute(
                        """
                        SELECT metadata_value FROM schema_metadata
                        WHERE metadata_key = 'schema_version'
                        """
                    ).fetchone()
                    if metadata is None or metadata["metadata_value"] != "2":
                        raise sqlite3.DatabaseError(
                            "Database schema version markers disagree"
                        )
            else:
                self._connection.execute(
                    """
                    CREATE TABLE guild_settings (
                        guild_id TEXT PRIMARY KEY NOT NULL,
                        shoe_channel_id TEXT NOT NULL,
                        total_shoes INTEGER NOT NULL DEFAULT 0
                            CHECK (total_shoes >= 0),
                        current_streak INTEGER NOT NULL DEFAULT 0
                            CHECK (current_streak >= 0),
                        best_streak INTEGER NOT NULL DEFAULT 0 CHECK (
                            best_streak >= current_streak
                            AND best_streak <= total_shoes
                        ),
                        matching_mode TEXT NOT NULL DEFAULT 'creative'
                            CHECK (matching_mode IN ('classic', 'creative')),
                        gameplay_mode TEXT NOT NULL DEFAULT 'relay'
                            CHECK (gameplay_mode IN ('standard', 'relay')),
                        last_contributor_user_id TEXT
                    )
                    """
                )

            additions = {
                "matching_mode": (
                    "ALTER TABLE guild_settings ADD COLUMN matching_mode TEXT "
                    "NOT NULL DEFAULT 'creative' "
                    "CHECK (matching_mode IN ('classic', 'creative'))"
                ),
                "gameplay_mode": (
                    "ALTER TABLE guild_settings ADD COLUMN gameplay_mode TEXT "
                    "NOT NULL DEFAULT 'relay' "
                    "CHECK (gameplay_mode IN ('standard', 'relay'))"
                ),
                "last_contributor_user_id": (
                    "ALTER TABLE guild_settings ADD COLUMN "
                    "last_contributor_user_id TEXT"
                ),
            }
            if existing is not None and user_version < 2:
                for column, statement in additions.items():
                    if column not in old_columns:
                        self._connection.execute(statement)

            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    shoe_count INTEGER NOT NULL DEFAULT 0
                        CHECK (shoe_count >= 0),
                    PRIMARY KEY (guild_id, user_id),
                    FOREIGN KEY (guild_id)
                        REFERENCES guild_settings(guild_id)
                        ON DELETE CASCADE
                ) WITHOUT ROWID
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS random_shoe_settings (
                    guild_id TEXT PRIMARY KEY NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
                    next_send_at INTEGER,
                    min_minutes INTEGER NOT NULL DEFAULT 50 CHECK (min_minutes BETWEEN 5 AND 1440),
                    max_minutes INTEGER NOT NULL DEFAULT 103 CHECK (max_minutes BETWEEN min_minutes AND 1440),
                    quiet_start_hour INTEGER CHECK (quiet_start_hour BETWEEN 0 AND 23),
                    quiet_end_hour INTEGER CHECK (quiet_end_hour BETWEEN 0 AND 23),
                    log_channel_id TEXT,
                    FOREIGN KEY (guild_id) REFERENCES guild_settings(guild_id)
                        ON DELETE CASCADE
                ) WITHOUT ROWID
                """
            )
            random_columns = {
                str(row["name"]) for row in self._connection.execute(
                    "PRAGMA table_info(random_shoe_settings)"
                ).fetchall()
            }
            for column, definition in {
                "min_minutes": "INTEGER NOT NULL DEFAULT 50 CHECK (min_minutes BETWEEN 5 AND 1440)",
                "max_minutes": "INTEGER NOT NULL DEFAULT 103 CHECK (max_minutes BETWEEN min_minutes AND 1440)",
                "quiet_start_hour": "INTEGER CHECK (quiet_start_hour BETWEEN 0 AND 23)",
                "quiet_end_hour": "INTEGER CHECK (quiet_end_hour BETWEEN 0 AND 23)",
                "log_channel_id": "TEXT",
            }.items():
                if column not in random_columns:
                    self._connection.execute(
                        f"ALTER TABLE random_shoe_settings ADD COLUMN {column} {definition}"
                    )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS random_shoe_channels (
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    PRIMARY KEY (guild_id, channel_id),
                    FOREIGN KEY (guild_id) REFERENCES guild_settings(guild_id)
                        ON DELETE CASCADE
                ) WITHOUT ROWID
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_user_stats_leaderboard
                ON user_stats (guild_id, shoe_count DESC, user_id ASC)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hall_of_fame (
                    guild_id TEXT NOT NULL,
                    streak_length INTEGER NOT NULL CHECK (streak_length > 0),
                    completed_at INTEGER,
                    is_legacy INTEGER NOT NULL DEFAULT 0
                        CHECK (is_legacy IN (0, 1)),
                    PRIMARY KEY (guild_id, streak_length),
                    FOREIGN KEY (guild_id)
                        REFERENCES guild_settings(guild_id)
                        ON DELETE CASCADE
                ) WITHOUT ROWID
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hall_of_fame_ranking
                ON hall_of_fame (guild_id, streak_length DESC)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    metadata_key TEXT PRIMARY KEY NOT NULL,
                    metadata_value TEXT NOT NULL
                ) WITHOUT ROWID
                """
            )

            if existing is not None and user_version < 2:
                # Preserve the historical best without inventing a completion date.
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO hall_of_fame (
                        guild_id, streak_length, completed_at, is_legacy
                    )
                    SELECT guild_id, best_streak, NULL, 1
                    FROM guild_settings
                    WHERE best_streak > 0
                    """
                )
                self._connection.execute(
                    "UPDATE guild_settings SET last_contributor_user_id = NULL"
                )

            self._connection.execute(
                """
                INSERT INTO schema_metadata (metadata_key, metadata_value)
                VALUES ('schema_version', '4')
                ON CONFLICT (metadata_key) DO UPDATE SET
                    metadata_value = excluded.metadata_value
                """
            )
            if (
                self._connection.execute("PRAGMA foreign_key_check").fetchone()
                is not None
            ):
                raise sqlite3.DatabaseError(
                    "Database foreign-key integrity check failed"
                )
            self._connection.execute("PRAGMA user_version = 4")
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback_without_masking_error()
            raise

    @staticmethod
    def _snowflake(value: int | str, label: str) -> str:
        text = str(value)
        if not text.isdecimal() or int(text) <= 0:
            raise ValueError(f"{label} must be a positive Discord ID")
        return text

    @staticmethod
    def _matching_mode(value: str) -> MatchingMode:
        if value not in MATCHING_MODES:
            raise ValueError("matching_mode must be 'classic' or 'creative'")
        return value  # type: ignore[return-value]

    @staticmethod
    def _gameplay_mode(value: str) -> GameplayMode:
        if value not in GAMEPLAY_MODES:
            raise ValueError("gameplay_mode must be 'standard' or 'relay'")
        return value  # type: ignore[return-value]

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                yield self._connection
                self._connection.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback_without_masking_error()
                raise DatabaseError("SQLite write failed") from exc
            except Exception:
                self._rollback_without_masking_error()
                raise

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold one WAL snapshot across a multi-query read."""
        with self._lock:
            try:
                self._connection.execute("BEGIN")
                yield self._connection
                self._connection.execute("COMMIT")
            except sqlite3.Error as exc:
                self._rollback_without_masking_error()
                raise DatabaseError("SQLite read failed") from exc
            except Exception:
                self._rollback_without_masking_error()
                raise

    def _rollback_without_masking_error(self) -> None:
        if not self._connection.in_transaction:
            return
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _checkpoint_after_deletion(self) -> None:
        """Best-effort WAL truncation after a privacy or server deletion."""
        with self._lock:
            try:
                result = self._connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if result is not None and int(result[0]) != 0:
                    LOGGER.warning("SQLite WAL checkpoint remained busy after deletion")
            except sqlite3.Error:
                # The logical delete is already committed. A later checkpoint or
                # close will safely retry truncating the WAL.
                pass

    @staticmethod
    def _record_completed_streak(
        connection: sqlite3.Connection,
        guild: str,
        streak_length: int,
    ) -> int | None:
        """Insert/prune one aggregate completed streak inside a caller transaction.

        The returned rank is only for a newly added length that remains in the
        top ten. Existing lengths and lengths pruned immediately return ``None``.
        """
        if streak_length <= 0:
            return None
        existing = connection.execute(
            """
            SELECT 1 FROM hall_of_fame
            WHERE guild_id = ? AND streak_length = ?
            """,
            (guild, streak_length),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO hall_of_fame (
                guild_id, streak_length, completed_at, is_legacy
            )
            VALUES (?, ?, CAST(strftime('%s', 'now') AS INTEGER), 0)
            ON CONFLICT (guild_id, streak_length) DO UPDATE SET
                completed_at = CASE
                    WHEN hall_of_fame.is_legacy = 1
                    THEN excluded.completed_at
                    ELSE hall_of_fame.completed_at
                END,
                is_legacy = 0
            """,
            (guild, streak_length),
        )
        connection.execute(
            """
            DELETE FROM hall_of_fame
            WHERE guild_id = ?
              AND streak_length NOT IN (
                  SELECT streak_length
                  FROM hall_of_fame
                  WHERE guild_id = ?
                  ORDER BY streak_length DESC
                  LIMIT 10
              )
            """,
            (guild, guild),
        )
        if existing is not None:
            return None
        retained = connection.execute(
            """
            SELECT 1 + (
                SELECT COUNT(*)
                FROM hall_of_fame AS higher
                WHERE higher.guild_id = current.guild_id
                  AND higher.streak_length > current.streak_length
            ) AS hall_rank
            FROM hall_of_fame AS current
            WHERE current.guild_id = ? AND current.streak_length = ?
            """,
            (guild, streak_length),
        ).fetchone()
        return int(retained["hall_rank"]) if retained is not None else None

    def load_guild_configs(self) -> dict[int, GuildConfig]:
        with self._lock:
            try:
                rows = self._connection.execute(
                    """
                    SELECT g.guild_id, g.shoe_channel_id, g.matching_mode,
                           g.gameplay_mode, COALESCE(r.enabled, 0) AS random_enabled,
                           r.next_send_at, COALESCE(r.min_minutes, 50) AS min_minutes,
                           COALESCE(r.max_minutes, 103) AS max_minutes,
                           r.quiet_start_hour, r.quiet_end_hour, r.log_channel_id
                    FROM guild_settings AS g
                    LEFT JOIN random_shoe_settings AS r ON r.guild_id = g.guild_id
                    """
                ).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not load server configuration") from exc
        configs = {
            int(row["guild_id"]): GuildConfig(
                channel_id=int(row["shoe_channel_id"]),
                matching_mode=self._matching_mode(str(row["matching_mode"])),
                gameplay_mode=self._gameplay_mode(str(row["gameplay_mode"])),
                random_shoe_enabled=bool(row["random_enabled"]),
                random_shoe_next_at=(int(row["next_send_at"]) if row["next_send_at"] is not None else None),
                random_shoe_min_minutes=int(row["min_minutes"]),
                random_shoe_max_minutes=int(row["max_minutes"]),
                quiet_start_hour=(int(row["quiet_start_hour"]) if row["quiet_start_hour"] is not None else None),
                quiet_end_hour=(int(row["quiet_end_hour"]) if row["quiet_end_hour"] is not None else None),
                log_channel_id=(int(row["log_channel_id"]) if row["log_channel_id"] is not None else None),
            )
            for row in rows
        }
        channel_rows = self._connection.execute(
            "SELECT guild_id, channel_id FROM random_shoe_channels ORDER BY channel_id"
        ).fetchall()
        channels: dict[int, list[int]] = {}
        for row in channel_rows:
            channels.setdefault(int(row["guild_id"]), []).append(int(row["channel_id"]))
        return {
            guild_id: GuildConfig(
                config.channel_id, config.matching_mode, config.gameplay_mode,
                config.random_shoe_enabled, tuple(channels.get(guild_id, ())),
                config.random_shoe_next_at, config.random_shoe_min_minutes,
                config.random_shoe_max_minutes, config.quiet_start_hour,
                config.quiet_end_hour, config.log_channel_id,
            ) for guild_id, config in configs.items()
        }

    def configure_random_shoe(
        self, guild_id: int | str, enabled: bool,
        channel_ids: Sequence[int | str], next_send_at: int | None,
        min_minutes: int = 50, max_minutes: int = 103,
        quiet_start_hour: int | None = None, quiet_end_hour: int | None = None,
        log_channel_id: int | str | None = None,
    ) -> None:
        guild = self._snowflake(guild_id, "guild_id")
        channels = tuple(dict.fromkeys(self._snowflake(c, "channel_id") for c in channel_ids))
        if enabled and not channels:
            raise ValueError("at least one channel is required when enabled")
        if enabled and log_channel_id is None:
            raise ValueError("a log channel is required when enabled")
        if not 5 <= min_minutes <= max_minutes <= 1440:
            raise ValueError("timing must satisfy 5 <= minimum <= maximum <= 1440")
        if (quiet_start_hour is None) != (quiet_end_hour is None):
            raise ValueError("quiet hours require both a start and end")
        log_channel = self._snowflake(log_channel_id, "log_channel_id") if log_channel_id is not None else None
        with self._write_transaction() as connection:
            if connection.execute("SELECT 1 FROM guild_settings WHERE guild_id = ?", (guild,)).fetchone() is None:
                raise GuildNotConfigured("server is not configured")
            connection.execute(
                "INSERT INTO random_shoe_settings (guild_id, enabled, next_send_at, min_minutes, max_minutes, quiet_start_hour, quiet_end_hour, log_channel_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (guild_id) DO UPDATE SET enabled=excluded.enabled, next_send_at=excluded.next_send_at, min_minutes=excluded.min_minutes, max_minutes=excluded.max_minutes, quiet_start_hour=excluded.quiet_start_hour, quiet_end_hour=excluded.quiet_end_hour, log_channel_id=excluded.log_channel_id",
                (guild, int(enabled), next_send_at, min_minutes, max_minutes, quiet_start_hour, quiet_end_hour, log_channel),
            )
            connection.execute("DELETE FROM random_shoe_channels WHERE guild_id = ?", (guild,))
            connection.executemany(
                "INSERT INTO random_shoe_channels (guild_id, channel_id) VALUES (?, ?)",
                ((guild, channel) for channel in channels),
            )

    def set_random_shoe_next_at(self, guild_id: int | str, next_send_at: int) -> None:
        guild = self._snowflake(guild_id, "guild_id")
        with self._write_transaction() as connection:
            connection.execute(
                "UPDATE random_shoe_settings SET next_send_at = ? WHERE guild_id = ?",
                (next_send_at, guild),
            )

    def load_configured_channels(self) -> dict[int, int]:
        """Backward-compatible channel-only view used by older integrations."""
        return {
            guild_id: config.channel_id
            for guild_id, config in self.load_guild_configs().items()
        }

    def configure_guild(
        self,
        guild_id: int | str,
        channel_id: int | str,
        matching_mode: str = "creative",
        gameplay_mode: str = "relay",
    ) -> GuildConfig:
        guild = self._snowflake(guild_id, "guild_id")
        channel = self._snowflake(channel_id, "channel_id")
        matching = self._matching_mode(matching_mode)
        gameplay = self._gameplay_mode(gameplay_mode)
        with self._write_transaction() as connection:
            existing = connection.execute(
                """
                SELECT shoe_channel_id, matching_mode, gameplay_mode,
                       current_streak
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild,),
            ).fetchone()
            settings_changed = bool(
                existing is not None
                and (
                    str(existing["shoe_channel_id"]) != channel
                    or str(existing["matching_mode"]) != matching
                    or str(existing["gameplay_mode"]) != gameplay
                )
            )
            if settings_changed and int(existing["current_streak"]) > 0:
                self._record_completed_streak(
                    connection,
                    guild,
                    int(existing["current_streak"]),
                )
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET current_streak = 0, last_contributor_user_id = NULL
                    WHERE guild_id = ?
                    """,
                    (guild,),
                )
            connection.execute(
                """
                INSERT INTO guild_settings (
                    guild_id, shoe_channel_id, matching_mode, gameplay_mode
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT (guild_id) DO UPDATE SET
                    last_contributor_user_id = CASE
                        WHEN excluded.gameplay_mode = 'standard'
                          OR guild_settings.shoe_channel_id != excluded.shoe_channel_id
                          OR guild_settings.matching_mode != excluded.matching_mode
                          OR guild_settings.gameplay_mode != excluded.gameplay_mode
                        THEN NULL
                        ELSE guild_settings.last_contributor_user_id
                    END,
                    shoe_channel_id = excluded.shoe_channel_id,
                    matching_mode = excluded.matching_mode,
                    gameplay_mode = excluded.gameplay_mode
                """,
                (guild, channel, matching, gameplay),
            )
        return GuildConfig(int(channel), matching, gameplay)

    def set_shoe_channel(self, guild_id: int | str, channel_id: int | str) -> None:
        guild = self._snowflake(guild_id, "guild_id")
        channel = self._snowflake(channel_id, "channel_id")
        with self._write_transaction() as connection:
            existing = connection.execute(
                """
                SELECT shoe_channel_id, current_streak
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild,),
            ).fetchone()
            if (
                existing is not None
                and str(existing["shoe_channel_id"]) != channel
                and int(existing["current_streak"]) > 0
            ):
                self._record_completed_streak(
                    connection,
                    guild,
                    int(existing["current_streak"]),
                )
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET current_streak = 0, last_contributor_user_id = NULL
                    WHERE guild_id = ?
                    """,
                    (guild,),
                )
            connection.execute(
                """
                INSERT INTO guild_settings (guild_id, shoe_channel_id)
                VALUES (?, ?)
                ON CONFLICT (guild_id) DO UPDATE SET
                    last_contributor_user_id = CASE
                        WHEN guild_settings.gameplay_mode = 'standard'
                          OR guild_settings.shoe_channel_id != excluded.shoe_channel_id
                        THEN NULL
                        ELSE guild_settings.last_contributor_user_id
                    END,
                    shoe_channel_id = excluded.shoe_channel_id
                """,
                (guild, channel),
            )

    def get_guild_stats(self, guild_id: int | str) -> GuildStats | None:
        guild = self._snowflake(guild_id, "guild_id")
        with self._lock:
            try:
                row = self._connection.execute(
                    """
                    SELECT g.shoe_channel_id, g.total_shoes, g.current_streak,
                           g.best_streak, g.matching_mode, g.gameplay_mode,
                           COALESCE(r.enabled, 0) AS random_enabled,
                           COALESCE(r.min_minutes, 50) AS min_minutes,
                           COALESCE(r.max_minutes, 103) AS max_minutes,
                           r.quiet_start_hour, r.quiet_end_hour, r.log_channel_id
                    FROM guild_settings AS g
                    LEFT JOIN random_shoe_settings AS r ON r.guild_id = g.guild_id
                    WHERE g.guild_id = ?
                    """,
                    (guild,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read server statistics") from exc

            if row is None:
                return None
            channels = self._connection.execute(
                "SELECT channel_id FROM random_shoe_channels WHERE guild_id = ? ORDER BY channel_id",
                (guild,),
            ).fetchall()
        return GuildStats(
            channel_id=int(row["shoe_channel_id"]),
            total_shoes=int(row["total_shoes"]),
            current_streak=int(row["current_streak"]),
            best_streak=int(row["best_streak"]),
            matching_mode=self._matching_mode(str(row["matching_mode"])),
            gameplay_mode=self._gameplay_mode(str(row["gameplay_mode"])),
            random_shoe_enabled=bool(row["random_enabled"]),
            random_shoe_channel_ids=tuple(int(item["channel_id"]) for item in channels),
            random_shoe_min_minutes=int(row["min_minutes"]),
            random_shoe_max_minutes=int(row["max_minutes"]),
            quiet_start_hour=(int(row["quiet_start_hour"]) if row["quiet_start_hour"] is not None else None),
            quiet_end_hour=(int(row["quiet_end_hour"]) if row["quiet_end_hour"] is not None else None),
            log_channel_id=(int(row["log_channel_id"]) if row["log_channel_id"] is not None else None),
        )

    def record_message(
        self,
        guild_id: int | str,
        user_id: int | str | None,
        content_matches: bool | None = None,
        *,
        is_valid: bool | None = None,
    ) -> MessageUpdate:
        """Apply one message under the server's current gameplay mode.

        ``is_valid`` remains as a keyword alias for callers from the original
        release. It means that the content matched; Relay can still reject it.
        """
        if content_matches is None:
            if is_valid is None:
                raise ValueError("content_matches is required")
            content_matches = is_valid
        elif is_valid is not None:
            raise ValueError("provide content_matches or is_valid, not both")

        guild = self._snowflake(guild_id, "guild_id")
        user = self._snowflake(user_id, "user_id") if content_matches and user_id else None
        if content_matches and user is None:
            raise ValueError("user_id is required when message content matches")

        with self._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT total_shoes, current_streak, best_streak,
                       gameplay_mode, last_contributor_user_id
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild,),
            ).fetchone()
            if row is None:
                raise GuildNotConfigured("This server has not configured Shoe Bot")

            previous_streak = int(row["current_streak"])
            gameplay_mode = self._gameplay_mode(str(row["gameplay_mode"]))
            last_contributor = row["last_contributor_user_id"]
            relay_repeat = bool(
                content_matches
                and gameplay_mode == "relay"
                and previous_streak > 0
                and last_contributor == user
            )
            accepted = bool(content_matches and not relay_repeat)
            break_reason: BreakReason | None = None
            hall_rank: int | None = None

            if accepted:
                next_contributor = user if gameplay_mode == "relay" else None
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET total_shoes = total_shoes + 1,
                        current_streak = current_streak + 1,
                        best_streak = MAX(best_streak, current_streak + 1),
                        last_contributor_user_id = ?
                    WHERE guild_id = ?
                    """,
                    (next_contributor, guild),
                )
                connection.execute(
                    """
                    INSERT INTO user_stats (guild_id, user_id, shoe_count)
                    VALUES (?, ?, 1)
                    ON CONFLICT (guild_id, user_id) DO UPDATE SET
                        shoe_count = user_stats.shoe_count + 1
                    """,
                    (guild, user),
                )
            else:
                break_reason = "relay" if relay_repeat else "invalid"
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET current_streak = 0,
                        last_contributor_user_id = NULL
                    WHERE guild_id = ?
                    """,
                    (guild,),
                )
                if previous_streak > 0:
                    hall_rank = self._record_completed_streak(
                        connection, guild, previous_streak
                    )

            updated = connection.execute(
                """
                SELECT total_shoes, current_streak, best_streak
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild,),
            ).fetchone()

        return MessageUpdate(
            total_shoes=int(updated["total_shoes"]),
            current_streak=int(updated["current_streak"]),
            best_streak=int(updated["best_streak"]),
            previous_streak=previous_streak,
            accepted=accepted,
            break_reason=break_reason,
            hall_of_fame_rank=hall_rank,
        )

    def get_user_stats(self, guild_id: int | str, user_id: int | str) -> UserStats:
        guild = self._snowflake(guild_id, "guild_id")
        user = self._snowflake(user_id, "user_id")
        with self._lock:
            try:
                row = self._connection.execute(
                    """
                    SELECT
                        current_user.shoe_count AS shoe_count,
                        1 + (
                            SELECT COUNT(*)
                            FROM user_stats AS higher_user
                            WHERE higher_user.guild_id = current_user.guild_id
                              AND higher_user.shoe_count > current_user.shoe_count
                        ) AS leaderboard_rank
                    FROM user_stats AS current_user
                    WHERE current_user.guild_id = ? AND current_user.user_id = ?
                    """,
                    (guild, user),
                ).fetchone()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read user statistics") from exc

        if row is None:
            return UserStats(shoe_count=0, rank=None)
        return UserStats(int(row["shoe_count"]), int(row["leaderboard_rank"]))

    def get_leaderboard(
        self, guild_id: int | str, limit: int = 10
    ) -> list[LeaderboardEntry]:
        guild = self._snowflake(guild_id, "guild_id")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            try:
                rows = self._connection.execute(
                    """
                    WITH ranked_users AS (
                        SELECT user_id, shoe_count,
                               RANK() OVER (ORDER BY shoe_count DESC) AS rank
                        FROM user_stats
                        WHERE guild_id = ?
                    )
                    SELECT user_id, shoe_count, rank
                    FROM ranked_users
                    ORDER BY shoe_count DESC, user_id ASC
                    LIMIT ?
                    """,
                    (guild, limit),
                ).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read the leaderboard") from exc
        return [
            LeaderboardEntry(int(row["user_id"]), int(row["shoe_count"]), int(row["rank"]))
            for row in rows
        ]

    def get_hall_of_fame(
        self, guild_id: int | str, limit: int = 10
    ) -> list[HallOfFameEntry]:
        guild = self._snowflake(guild_id, "guild_id")
        if limit < 1 or limit > 10:
            raise ValueError("limit must be between 1 and 10")
        with self._lock:
            try:
                rows = self._connection.execute(
                    """
                    SELECT streak_length, completed_at, is_legacy
                    FROM hall_of_fame
                    WHERE guild_id = ?
                    ORDER BY streak_length DESC
                    LIMIT ?
                    """,
                    (guild, limit),
                ).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read the Hall of Fame") from exc
        return [
            HallOfFameEntry(
                rank=index,
                streak_length=int(row["streak_length"]),
                completed_at=(
                    int(row["completed_at"]) if row["completed_at"] is not None else None
                ),
                is_legacy=bool(row["is_legacy"]),
            )
            for index, row in enumerate(rows, start=1)
        ]

    def get_leaderboard_snapshot(
        self,
        guild_id: int | str,
        limit: int = 10,
    ) -> LeaderboardSnapshot | None:
        """Read the stats and both leaderboard panels from one WAL snapshot."""
        guild = self._snowflake(guild_id, "guild_id")
        with self._read_transaction():
            stats = self.get_guild_stats(guild)
            if stats is None:
                return None
            contributors = tuple(self.get_leaderboard(guild, limit=limit))
            hall_of_fame = tuple(self.get_hall_of_fame(guild, limit=min(limit, 10)))
            return LeaderboardSnapshot(stats, contributors, hall_of_fame)

    def reset_guild_stats(self, guild_id: int | str) -> None:
        """Delete all counts while preserving the selected channel and modes."""
        guild = self._snowflake(guild_id, "guild_id")
        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE guild_settings
                SET total_shoes = 0,
                    current_streak = 0,
                    best_streak = 0,
                    last_contributor_user_id = NULL
                WHERE guild_id = ?
                """,
                (guild,),
            )
            if cursor.rowcount == 0:
                raise GuildNotConfigured("This server has not configured Shoe Bot")
            connection.execute("DELETE FROM user_stats WHERE guild_id = ?", (guild,))
            connection.execute("DELETE FROM hall_of_fame WHERE guild_id = ?", (guild,))
        self._checkpoint_after_deletion()

    def delete_user_stats(self, guild_id: int | str, user_id: int | str) -> UserDeletion:
        """Delete one user's row and any Relay-state reference to that user.

        If that user is the current Relay contributor, the active streak is
        ended so deleting the identifier cannot be used to bypass alternation.
        Historical totals and the best streak remain aggregate statistics.
        """
        guild = self._snowflake(guild_id, "guild_id")
        user = self._snowflake(user_id, "user_id")
        with self._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT current_streak, gameplay_mode, last_contributor_user_id
                FROM guild_settings WHERE guild_id = ?
                """,
                (guild,),
            ).fetchone()
            ended_streak = 0
            if (
                row is not None
                and row["gameplay_mode"] == "relay"
                and row["last_contributor_user_id"] == user
            ):
                ended_streak = int(row["current_streak"])
                self._record_completed_streak(connection, guild, ended_streak)
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET current_streak = 0, last_contributor_user_id = NULL
                    WHERE guild_id = ?
                    """,
                    (guild,),
                )
            cursor = connection.execute(
                "DELETE FROM user_stats WHERE guild_id = ? AND user_id = ?",
                (guild, user),
            )
        self._checkpoint_after_deletion()
        return UserDeletion(cursor.rowcount > 0, ended_streak)

    def delete_guild(self, guild_id: int | str) -> None:
        guild = self._snowflake(guild_id, "guild_id")
        with self._write_transaction() as connection:
            connection.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild,))
        self._checkpoint_after_deletion()

    def _close_connection(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                result = self._connection.execute(
                    "PRAGMA wal_checkpoint(TRUNCATE)"
                ).fetchone()
                if result is not None and int(result[0]) != 0:
                    LOGGER.warning("SQLite WAL checkpoint remained busy at shutdown")
            except sqlite3.Error:
                pass
            finally:
                self._connection.close()
                self._closed = True

    async def _aclose_impl(self, close_future) -> None:
        loop = asyncio.get_running_loop()
        try:
            await asyncio.shield(asyncio.wrap_future(close_future, loop=loop))
        finally:
            with self._executor_state_lock:
                if not self._executor_closed:
                    self._executor.shutdown(wait=True, cancel_futures=False)
                    self._executor_closed = True

    async def aclose(self) -> None:
        """Drain the ordered worker, checkpoint, and close without blocking Discord."""
        if self._aclose_task is None:
            with self._executor_state_lock:
                if self._executor_closed:
                    return
                self._closing = True
                if self._close_future is None:
                    self._close_future = self._executor.submit(
                        self._close_connection
                    )
                close_future = self._close_future
            self._aclose_task = asyncio.create_task(
                self._aclose_impl(close_future)
            )
        await asyncio.shield(self._aclose_task)

    def close(self) -> None:
        """Synchronous shutdown for scripts and tests outside Discord's loop."""
        with self._executor_state_lock:
            if self._executor_closed:
                return
            self._closing = True
            if self._close_future is None:
                self._close_future = self._executor.submit(self._close_connection)
            future = self._close_future
        try:
            future.result()
        finally:
            with self._executor_state_lock:
                if not self._executor_closed:
                    self._executor.shutdown(wait=True, cancel_futures=False)
                    self._executor_closed = True
