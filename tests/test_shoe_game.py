from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import discord

from bot.database import DatabaseError, MessageUpdate, ShoeDatabase
from bot.shoe_game import ShoeGame


class FakeChannel:
    def __init__(self, channel_id: int, fail_send: bool = False) -> None:
        self.id = channel_id
        self.fail_send = fail_send
        self.sent: list[tuple[str, dict]] = []

    async def send(self, text: str, **kwargs) -> None:
        if self.fail_send:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                "Missing Send Messages",
            )
        self.sent.append((text, kwargs))


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int,
        guild_id: int | None,
        channel: FakeChannel,
        content: str,
        user_id: int = 300,
        author_is_bot: bool = False,
        webhook_id: int | None = None,
        fail_reaction: bool = False,
    ) -> None:
        self.id = message_id
        self.guild = SimpleNamespace(id=guild_id) if guild_id is not None else None
        self.channel = channel
        self._content = content
        self.content_reads = 0
        self.author = SimpleNamespace(
            id=user_id,
            bot=author_is_bot,
            mention=f"<@{user_id}>",
        )
        self.webhook_id = webhook_id
        self.fail_reaction = fail_reaction
        self.reactions: list[str] = []

    @property
    def content(self) -> str:
        self.content_reads += 1
        return self._content

    async def add_reaction(self, reaction: str) -> None:
        if self.fail_reaction:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                "Missing Add Reactions",
            )
        self.reactions.append(reaction)


class FailOnceDatabase:
    def __init__(self) -> None:
        self.attempts = 0

    def load_configured_channels(self) -> dict[int, int]:
        return {100: 200}

    def record_message(self, **_kwargs) -> MessageUpdate:
        self.attempts += 1
        if self.attempts == 1:
            raise DatabaseError("temporary failure")
        return MessageUpdate(1, 1, 1, 0)


class ShoeGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        path = Path(self.temporary_directory.name) / "game.sqlite3"
        self.database = ShoeDatabase(path)
        self.database.set_shoe_channel(100, 200)
        self.game = ShoeGame(self.database)
        self.game.load_configuration()

    async def asyncTearDown(self) -> None:
        self.database.close()
        self.temporary_directory.cleanup()

    async def test_casefold_substring_and_runtime_deduplication(self) -> None:
        channel = FakeChannel(200)
        message = FakeMessage(
            message_id=1,
            guild_id=100,
            channel=channel,
            content="These SHOElaces are cool",
        )

        await self.game.handle_message(message)
        await self.game.handle_message(message)

        self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(message.content_reads, 1)
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)

    async def test_invalid_message_breaks_nonzero_streak_once(self) -> None:
        channel = FakeChannel(200)
        valid = FakeMessage(
            message_id=1,
            guild_id=100,
            channel=channel,
            content="shoe",
        )
        invalid = FakeMessage(
            message_id=2,
            guild_id=100,
            channel=channel,
            content="hat",
        )
        another_invalid = FakeMessage(
            message_id=3,
            guild_id=100,
            channel=channel,
            content="sock",
        )

        await self.game.handle_message(valid)
        await self.game.handle_message(invalid)
        await self.game.handle_message(another_invalid)

        self.assertEqual(invalid.reactions, ["❌"])
        self.assertEqual(another_invalid.reactions, ["❌"])
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(channel.sent[0][0], "💥 <@300> broke the shoe streak at 1!")

    async def test_ignored_messages_are_filtered_before_content_access(self) -> None:
        cases = [
            FakeMessage(
                message_id=1,
                guild_id=None,
                channel=FakeChannel(200),
                content="private shoe",
            ),
            FakeMessage(
                message_id=2,
                guild_id=100,
                channel=FakeChannel(201),
                content="wrong channel shoe",
            ),
            FakeMessage(
                message_id=3,
                guild_id=100,
                channel=FakeChannel(200),
                content="bot shoe",
                author_is_bot=True,
            ),
            FakeMessage(
                message_id=4,
                guild_id=100,
                channel=FakeChannel(200),
                content="webhook shoe",
                webhook_id=999,
            ),
        ]

        for message in cases:
            await self.game.handle_message(message)

        self.assertEqual([message.content_reads for message in cases], [0, 0, 0, 0])
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 0)

    async def test_remove_guild_clears_cache_and_persistent_data(self) -> None:
        self.game.remove_guild(100)

        self.assertIsNone(self.game.configured_channel_id(100))
        self.assertIsNone(self.database.get_guild_stats(100))

    async def test_reaction_and_send_failures_do_not_crash_or_undo_counters(self) -> None:
        channel = FakeChannel(200, fail_send=True)
        valid = FakeMessage(
            message_id=1,
            guild_id=100,
            channel=channel,
            content="shoe",
            fail_reaction=True,
        )
        invalid = FakeMessage(
            message_id=2,
            guild_id=100,
            channel=channel,
            content="hat",
            fail_reaction=True,
        )

        await self.game.handle_message(valid)
        await self.game.handle_message(invalid)

        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 1)
        self.assertEqual(stats.current_streak, 0)
        self.assertEqual(stats.best_streak, 1)

    async def test_failed_database_write_removes_runtime_dedupe_reservation(self) -> None:
        database = FailOnceDatabase()
        game = ShoeGame(database)
        game.load_configuration()
        message = FakeMessage(
            message_id=42,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
        )

        await game.handle_message(message)
        await game.handle_message(message)

        self.assertEqual(database.attempts, 2)
        self.assertEqual(message.reactions, ["✅"])


if __name__ == "__main__":
    unittest.main()
