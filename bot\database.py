"""SQLite persistence for Shoe Bot.

Only Discord snowflake IDs and numerical game statistics are persisted here.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
from typing import Iterator


class DatabaseError(RuntimeError):
    """Raised when a SQLite operation cannot be completed."""


class GuildNotConfigured(DatabaseError):
    """Raised when an operation needs a configured guild that does not exist."""


@dataclass(frozen=True, slots=True)
class GuildStats:
    channel_id: int
    total_shoes: int
    current_streak: int
    best_streak: int


@dataclass(frozen=True, slots=True)
class MessageUpdate:
    total_shoes: int
    current_streak: int
    best_streak: int
    previous_streak: int


@dataclass(frozen=True, slots=True)
class UserStats:
    shoe_count: int
    rank: int | None


@dataclass(frozen=True, slots=True)
class LeaderboardEntry:
    user_id: int
    shoe_count: int
    rank: int


class ShoeDatabase:
    """Small, transaction-safe SQLite data store.

    A re-entrant lock serializes access to the shared connection. Write methods
    also use BEGIN IMMEDIATE so each message update is atomic.
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
            self._initialize_schema()
        except sqlite3.Error as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            raise DatabaseError("Could not initialize the SQLite database") from exc

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY NOT NULL,
                shoe_channel_id TEXT NOT NULL,
                total_shoes INTEGER NOT NULL DEFAULT 0 CHECK (total_shoes >= 0),
                current_streak INTEGER NOT NULL DEFAULT 0 CHECK (current_streak >= 0),
                best_streak INTEGER NOT NULL DEFAULT 0 CHECK (
                    best_streak >= current_streak
                    AND best_streak <= total_shoes
                )
            );

            CREATE TABLE IF NOT EXISTS user_stats (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                shoe_count INTEGER NOT NULL DEFAULT 0 CHECK (shoe_count >= 0),
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id)
                    REFERENCES guild_settings(guild_id)
                    ON DELETE CASCADE
            ) WITHOUT ROWID;

            CREATE INDEX IF NOT EXISTS idx_user_stats_leaderboard
                ON user_stats (guild_id, shoe_count DESC);
            """
        )

    @staticmethod
    def _snowflake(value: int | str, label: str) -> str:
        text = str(value)
        if not text.isdecimal() or int(text) <= 0:
            raise ValueError(f"{label} must be a positive Discord ID")
        return text

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

    def _rollback_without_masking_error(self) -> None:
        if not self._connection.in_transaction:
            return
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            # Preserve the original operation error; SQLite will recover the WAL
            # when the connection or process is reopened.
            pass

    def load_configured_channels(self) -> dict[int, int]:
        with self._lock:
            try:
                rows = self._connection.execute(
                    "SELECT guild_id, shoe_channel_id FROM guild_settings"
                ).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not load guild configuration") from exc
        return {int(row["guild_id"]): int(row["shoe_channel_id"]) for row in rows}

    def set_shoe_channel(self, guild_id: int | str, channel_id: int | str) -> None:
        guild = self._snowflake(guild_id, "guild_id")
        channel = self._snowflake(channel_id, "channel_id")
        with self._write_transaction() as connection:
            connection.execute(
                """
                INSERT INTO guild_settings (guild_id, shoe_channel_id)
                VALUES (?, ?)
                ON CONFLICT (guild_id) DO UPDATE SET
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
                    SELECT shoe_channel_id, total_shoes, current_streak, best_streak
                    FROM guild_settings
                    WHERE guild_id = ?
                    """,
                    (guild,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read guild statistics") from exc

        if row is None:
            return None
        return GuildStats(
            channel_id=int(row["shoe_channel_id"]),
            total_shoes=int(row["total_shoes"]),
            current_streak=int(row["current_streak"]),
            best_streak=int(row["best_streak"]),
        )

    def record_message(
        self,
        guild_id: int | str,
        user_id: int | str | None,
        is_valid: bool,
    ) -> MessageUpdate:
        guild = self._snowflake(guild_id, "guild_id")
        user = self._snowflake(user_id, "user_id") if is_valid and user_id else None
        if is_valid and user is None:
            raise ValueError("user_id is required for a valid Shoe message")

        with self._write_transaction() as connection:
            row = connection.execute(
                """
                SELECT total_shoes, current_streak, best_streak
                FROM guild_settings
                WHERE guild_id = ?
                """,
                (guild,),
            ).fetchone()
            if row is None:
                raise GuildNotConfigured("This server has not configured Shoe Bot")

            previous_streak = int(row["current_streak"])
            if is_valid:
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET total_shoes = total_shoes + 1,
                        current_streak = current_streak + 1,
                        best_streak = CASE
                            WHEN current_streak + 1 > best_streak
                            THEN current_streak + 1
                            ELSE best_streak
                        END
                    WHERE guild_id = ?
                    """,
                    (guild,),
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
                connection.execute(
                    """
                    UPDATE guild_settings
                    SET current_streak = 0
                    WHERE guild_id = ?
                    """,
                    (guild,),
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
        )

    def get_user_stats(
        self, guild_id: int | str, user_id: int | str
    ) -> UserStats:
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
                    WHERE current_user.guild_id = ?
                      AND current_user.user_id = ?
                    """,
                    (guild, user),
                ).fetchone()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read user statistics") from exc

        if row is None:
            return UserStats(shoe_count=0, rank=None)
        return UserStats(
            shoe_count=int(row["shoe_count"]),
            rank=int(row["leaderboard_rank"]),
        )

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
                        SELECT
                            user_id,
                            shoe_count,
                            RANK() OVER (ORDER BY shoe_count DESC) AS leaderboard_rank
                        FROM user_stats
                        WHERE guild_id = ?
                    )
                    SELECT user_id, shoe_count, leaderboard_rank
                    FROM ranked_users
                    ORDER BY shoe_count DESC, user_id ASC
                    LIMIT ?
                    """,
                    (guild, limit),
                ).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError("Could not read the leaderboard") from exc

        return [
            LeaderboardEntry(
                user_id=int(row["user_id"]),
                shoe_count=int(row["shoe_count"]),
                rank=int(row["leaderboard_rank"]),
            )
            for row in rows
        ]

    def reset_guild_stats(self, guild_id: int | str) -> None:
        guild = self._snowflake(guild_id, "guild_id")
        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE guild_settings
                SET total_shoes = 0,
                    current_streak = 0,
                    best_streak = 0
                WHERE guild_id = ?
                """,
                (guild,),
            )
            if cursor.rowcount == 0:
                raise GuildNotConfigured("This server has not configured Shoe Bot")
            connection.execute(
                "DELETE FROM user_stats WHERE guild_id = ?",
                (guild,),
            )

    def delete_user_stats(
        self, guild_id: int | str, user_id: int | str
    ) -> bool:
        """Delete one user's leaderboard row without changing aggregates."""
        guild = self._snowflake(guild_id, "guild_id")
        user = self._snowflake(user_id, "user_id")
        with self._write_transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM user_stats
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild, user),
            )
        return cursor.rowcount > 0

    def delete_guild(self, guild_id: int | str) -> None:
        """Delete all persisted configuration and statistics for one guild."""
        guild = self._snowflake(guild_id, "guild_id")
        with self._write_transaction() as connection:
            # ON DELETE CASCADE removes this guild's user_stats rows.
            connection.execute(
                "DELETE FROM guild_settings WHERE guild_id = ?",
                (guild,),
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                # A future open will recover/checkpoint a valid WAL. Shutdown
                # should still close the handle and allow Discord to exit.
                pass
            finally:
                self._connection.close()
                self._closed = True
