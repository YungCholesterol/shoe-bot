from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest

import discord

from bot.database import DatabaseError, GuildConfig, MessageUpdate, ShoeDatabase
from bot.shoe_game import FOOTWEAR_EMOJIS, ShoeGame, message_matches_shoe


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
        sticker_names: tuple[str, ...] = (),
        message_type: discord.MessageType = discord.MessageType.default,
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
        self.stickers = [SimpleNamespace(name=name) for name in sticker_names]
        self.type = message_type

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

    def load_guild_configs(self) -> dict[int, GuildConfig]:
        return {100: GuildConfig(200, "creative", "relay")}

    def record_message(self, **_kwargs) -> MessageUpdate:
        self.attempts += 1
        if self.attempts == 1:
            raise DatabaseError("temporary failure")
        return MessageUpdate(1, 1, 1, 0, True, None, None)

    async def run(self, operation, *args, **kwargs):
        return operation(*args, **kwargs)


class MatcherTests(unittest.TestCase):
    def test_classic_is_a_case_insensitive_substring(self) -> None:
        for value in ("shoe", "SHOE", "I love shoes", "shoe!!!", "horseshoe"):
            with self.subTest(value=value):
                self.assertTrue(message_matches_shoe(value, "classic"))
        for value in ("s h o e", "sh0e", "👟", "hat"):
            with self.subTest(value=value):
                self.assertFalse(message_matches_shoe(value, "classic"))

    def test_creative_accepts_fixed_text_variants(self) -> None:
        values = (
            "s h o e",
            "s-h-o-e",
            "s.h.o.e",
            "s_h_o_e",
            "sh0e",
            "shoooe",
            "ssshhhoooeee",
            "ＳＨＯＥ",
            "𝓼𝓱𝓸𝓮",
            "s\u200bhoe",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertTrue(message_matches_shoe(value, "creative"))

    def test_creative_accepts_every_documented_footwear_emoji(self) -> None:
        for emoji in FOOTWEAR_EMOJIS:
            with self.subTest(emoji=emoji):
                self.assertTrue(message_matches_shoe(emoji, "creative"))

    def test_creative_rejects_broad_or_ambiguous_symbols(self) -> None:
        for value in ("🦶", "🧦", "👣", "🛹", "shone", "s h a e", "hat"):
            with self.subTest(value=value):
                self.assertFalse(message_matches_shoe(value, "creative"))

    def test_sticker_name_is_used_only_transiently_in_creative_mode(self) -> None:
        self.assertTrue(message_matches_shoe("", "creative", ["blue_shoe"] ))
        self.assertFalse(message_matches_shoe("", "classic", ["blue_shoe"] ))
        self.assertFalse(message_matches_shoe("", "creative", ["funny_sock"] ))

    def test_custom_emoji_name_in_message_content_counts(self) -> None:
        value = "<:server_shoe:123456789>"
        self.assertTrue(message_matches_shoe(value, "classic"))
        self.assertTrue(message_matches_shoe(value, "creative"))

    def test_many_matches_still_classify_as_one_boolean(self) -> None:
        self.assertIs(message_matches_shoe("shoe 👟 s-h-o-e sh0e", "creative"), True)

    def test_matcher_handles_max_length_input_quickly(self) -> None:
        value = "s" * 2_000
        started = time.perf_counter()
        self.assertFalse(message_matches_shoe(value, "creative"))
        self.assertLess(time.perf_counter() - started, 0.25)


class ShoeGameTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        path = Path(self.temporary_directory.name) / "game.sqlite3"
        self.database = ShoeDatabase(path)
        self.database.configure_guild(100, 200, "creative", "relay")
        self.game = ShoeGame(self.database)
        await self.game.load_configuration()

    async def asyncTearDown(self) -> None:
        await self.database.aclose()
        self.temporary_directory.cleanup()

    async def test_runtime_deduplication_counts_a_message_once(self) -> None:
        channel = FakeChannel(200)
        message = FakeMessage(
            message_id=1,
            guild_id=100,
            channel=channel,
            content="shoe 👟 s-h-o-e",
        )
        await self.game.handle_message(message)
        await self.game.handle_message(message)
        self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(message.content_reads, 1)
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)

    async def test_relay_repeat_reacts_no_and_breaks_without_counting(self) -> None:
        channel = FakeChannel(200)
        first = FakeMessage(
            message_id=1, guild_id=100, channel=channel, content="shoe", user_id=300
        )
        repeated = FakeMessage(
            message_id=2, guild_id=100, channel=channel, content="👟", user_id=300
        )
        await self.game.handle_message(first)
        await self.game.handle_message(repeated)
        self.assertEqual(first.reactions, ["✅"])
        self.assertEqual(repeated.reactions, ["❌"])
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(
            channel.sent[0][0],
            "<@300> broke a 1-message shoe streak by posting twice in a row. "
            "It entered the Hall of Fame at rank #1.",
        )

    async def test_invalid_breaks_once_and_zero_streak_does_not_announce(self) -> None:
        channel = FakeChannel(200)
        messages = (
            FakeMessage(message_id=1, guild_id=100, channel=channel, content="shoe"),
            FakeMessage(message_id=2, guild_id=100, channel=channel, content="hat"),
            FakeMessage(message_id=3, guild_id=100, channel=channel, content="sock"),
        )
        for message in messages:
            await self.game.handle_message(message)
        self.assertEqual(messages[1].reactions, ["❌"])
        self.assertEqual(messages[2].reactions, ["❌"])
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(
            channel.sent[0][0],
            "<@300> broke a 1-message shoe streak. "
            "It entered the Hall of Fame at rank #1.",
        )

    async def test_relay_alternation_builds_streak(self) -> None:
        channel = FakeChannel(200)
        for index, user in enumerate((300, 301, 300, 301), start=1):
            message = FakeMessage(
                message_id=index,
                guild_id=100,
                channel=channel,
                content="shoe",
                user_id=user,
            )
            await self.game.handle_message(message)
            self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(self.database.get_guild_stats(100).current_streak, 4)

    async def test_standard_mode_accepts_repeat_user(self) -> None:
        await self.game.configure_guild(100, 200, "creative", "standard")
        channel = FakeChannel(200)
        for index in (1, 2):
            message = FakeMessage(
                message_id=index, guild_id=100, channel=channel, content="shoe"
            )
            await self.game.handle_message(message)
            self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 2)

    async def test_sticker_name_can_match_without_saving_it(self) -> None:
        channel = FakeChannel(200)
        message = FakeMessage(
            message_id=1,
            guild_id=100,
            channel=channel,
            content="",
            sticker_names=("special_shoe",),
        )
        await self.game.handle_message(message)
        self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)

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

    async def test_system_messages_are_ignored_before_content_access(self) -> None:
        for index, message_type in enumerate(
            (discord.MessageType.pins_add, discord.MessageType.premium_guild_subscription),
            start=10,
        ):
            message = FakeMessage(
                message_id=index,
                guild_id=100,
                channel=FakeChannel(200),
                content="shoe",
                message_type=message_type,
            )
            await self.game.handle_message(message)
            self.assertEqual(message.content_reads, 0)
            self.assertEqual(message.reactions, [])
        self.assertEqual(self.database.get_guild_stats(100).current_streak, 0)

    async def test_replies_are_normal_game_messages(self) -> None:
        message = FakeMessage(
            message_id=20,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
            message_type=discord.MessageType.reply,
        )
        await self.game.handle_message(message)
        self.assertEqual(message.reactions, ["✅"])

    async def test_remove_guild_clears_runtime_and_persistent_configuration(self) -> None:
        await self.game.remove_guild(100)
        self.assertIsNone(self.game.configured_channel_id(100))
        self.assertIsNone(self.database.get_guild_stats(100))

    async def test_reaction_and_send_failures_do_not_undo_committed_counters(self) -> None:
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

    async def test_failed_database_write_releases_dedupe_reservation(self) -> None:
        database = FailOnceDatabase()
        game = ShoeGame(database)  # type: ignore[arg-type]
        await game.load_configuration()
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

    async def test_slow_sqlite_write_does_not_block_event_loop(self) -> None:
        original = self.database.record_message

        def slow_record_message(*args, **kwargs):
            time.sleep(0.10)
            return original(*args, **kwargs)

        self.database.record_message = slow_record_message  # type: ignore[method-assign]
        message = FakeMessage(
            message_id=50,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
        )
        processing = asyncio.create_task(self.game.handle_message(message))
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.05)
        self.assertFalse(processing.done())
        await processing
        self.assertEqual(message.reactions, ["✅"])

    async def test_ordered_worker_preserves_message_submission_order(self) -> None:
        original = self.database.record_message

        def slow_first(*args, **kwargs):
            if kwargs.get("content_matches"):
                time.sleep(0.05)
            return original(*args, **kwargs)

        self.database.record_message = slow_first  # type: ignore[method-assign]
        channel = FakeChannel(200)
        accepted = FakeMessage(
            message_id=60,
            guild_id=100,
            channel=channel,
            content="shoe",
            user_id=300,
        )
        breaker = FakeMessage(
            message_id=61,
            guild_id=100,
            channel=channel,
            content="hat",
            user_id=301,
        )
        first_task = asyncio.create_task(self.game.handle_message(accepted))
        await asyncio.sleep(0)
        second_task = asyncio.create_task(self.game.handle_message(breaker))
        await asyncio.gather(first_task, second_task)
        stats = self.database.get_guild_stats(100)
        self.assertEqual(stats.total_shoes, 1)
        self.assertEqual(stats.current_streak, 0)
        self.assertEqual(accepted.reactions, ["✅"])
        self.assertEqual(breaker.reactions, ["❌"])

    async def test_configuration_and_message_use_one_consistent_ruleset(self) -> None:
        await self.game.configure_guild(100, 200, "classic", "standard")
        original = self.database.configure_guild
        started = threading.Event()
        release = threading.Event()

        def slow_configure(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        self.database.configure_guild = slow_configure  # type: ignore[method-assign]
        change = asyncio.create_task(
            self.game.configure_guild(100, 200, "creative", "standard")
        )
        while not started.is_set():
            await asyncio.sleep(0)
        message = FakeMessage(
            message_id=70,
            guild_id=100,
            channel=FakeChannel(200),
            content="s-h-o-e",
        )
        processing = asyncio.create_task(self.game.handle_message(message))
        await asyncio.sleep(0)
        self.assertFalse(processing.done())
        release.set()
        await asyncio.gather(change, processing)
        self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 1)

    async def test_cancelled_configuration_reconciles_runtime_cache(self) -> None:
        original = self.database.configure_guild
        started = threading.Event()
        release = threading.Event()

        def slow_configure(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        self.database.configure_guild = slow_configure  # type: ignore[method-assign]
        change = asyncio.create_task(
            self.game.configure_guild(100, 201, "classic", "standard")
        )
        while not started.is_set():
            await asyncio.sleep(0)
        change.cancel()
        await asyncio.sleep(0)
        self.assertFalse(change.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await change
        stats = self.database.get_guild_stats(100)
        self.assertEqual(self.game.configured_channel_id(100), 201)
        self.assertEqual(stats.channel_id, 201)
        self.assertEqual(stats.matching_mode, "classic")
        self.assertEqual(stats.gameplay_mode, "standard")

    async def test_cancelled_remove_reconciles_runtime_cache(self) -> None:
        original = self.database.delete_guild
        started = threading.Event()
        release = threading.Event()

        def slow_delete(*args, **kwargs):
            started.set()
            release.wait(timeout=2)
            return original(*args, **kwargs)

        self.database.delete_guild = slow_delete  # type: ignore[method-assign]
        removal = asyncio.create_task(self.game.remove_guild(100))
        while not started.is_set():
            await asyncio.sleep(0)
        removal.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await removal
        self.assertIsNone(self.game.configured_channel_id(100))
        self.assertIsNone(self.database.get_guild_stats(100))

    async def test_waiting_message_cannot_reappear_after_later_reset(self) -> None:
        await self.game._state_lock.acquire()
        message = FakeMessage(
            message_id=80,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
        )
        processing = asyncio.create_task(self.game.handle_message(message))
        await asyncio.sleep(0)
        resetting = asyncio.create_task(self.game.reset_guild_stats(100))
        await asyncio.sleep(0)
        self.game._state_lock.release()
        await asyncio.gather(processing, resetting)
        self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(self.database.get_guild_stats(100).total_shoes, 0)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 0)

    async def test_waiting_message_cannot_reappear_after_later_forgetme(self) -> None:
        await self.game._state_lock.acquire()
        message = FakeMessage(
            message_id=81,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
            user_id=300,
        )
        processing = asyncio.create_task(self.game.handle_message(message))
        await asyncio.sleep(0)
        forgetting = asyncio.create_task(self.game.delete_user_stats(100, 300))
        await asyncio.sleep(0)
        self.game._state_lock.release()
        await asyncio.gather(processing, forgetting)
        self.assertEqual(message.reactions, ["✅"])
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 0)
        self.assertEqual(self.database.get_guild_stats(100).current_streak, 0)

    async def test_shutdown_drains_earlier_message_and_ignores_later_message(self) -> None:
        await self.game._state_lock.acquire()
        earlier = FakeMessage(
            message_id=90,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
            user_id=300,
        )
        later = FakeMessage(
            message_id=91,
            guild_id=100,
            channel=FakeChannel(200),
            content="shoe",
            user_id=301,
        )
        earlier_task = asyncio.create_task(self.game.handle_message(earlier))
        await asyncio.sleep(0)
        shutdown_task = asyncio.create_task(self.game.aclose())
        await asyncio.sleep(0)
        later_task = asyncio.create_task(self.game.handle_message(later))
        await asyncio.sleep(0)
        self.game._state_lock.release()
        await asyncio.gather(earlier_task, shutdown_task, later_task)

        self.assertEqual(earlier.reactions, ["✅"])
        self.assertEqual(later.reactions, [])
        self.assertEqual(later.content_reads, 0)
        reopened = ShoeDatabase(self.database.path)
        try:
            self.assertEqual(reopened.get_guild_stats(100).total_shoes, 1)
            self.assertEqual(reopened.get_user_stats(100, 300).shoe_count, 1)
            self.assertEqual(reopened.get_user_stats(100, 301).shoe_count, 0)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
