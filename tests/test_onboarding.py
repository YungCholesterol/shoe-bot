from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

import discord

from bot.commands import ShoeCommands
from bot.database import ShoeDatabase
from bot.main import BotConfig, ShoeBot
from bot.onboarding import WelcomeService, WelcomeView
from tests.test_commands import FakeInteraction


def channel(channel_id=200, *, visible=True, writable=True, error=None):
    return SimpleNamespace(
        id=channel_id,
        permissions_for=Mock(return_value=SimpleNamespace(view_channel=visible, send_messages=writable)),
        send=AsyncMock(side_effect=error),
    )


def guild(guild_id=100, *, channels=None, system=None):
    return SimpleNamespace(id=guild_id, unavailable=False, me=object(),
                           text_channels=channels or [], system_channel=system)


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'test.sqlite3'
        self.database = ShoeDatabase(self.path)
        self.view = WelcomeView(AsyncMock())
        self.service = WelcomeService(self.database, self.view)

    async def asyncTearDown(self):
        self.view.stop()
        await self.database.aclose()
        self.directory.cleanup()

    async def test_system_channel_is_preferred_without_mentions(self):
        first, system = channel(201), channel(202)
        self.assertTrue(await self.service.send(guild(channels=[first, system], system=system)))
        first.send.assert_not_awaited()
        system.send.assert_awaited_once()
        self.assertEqual(system.send.call_args.kwargs['allowed_mentions'].to_dict(), {'parse': []})
        self.assertIs(system.send.call_args.kwargs['view'], self.view)

    async def test_forbidden_system_channel_falls_back_once(self):
        forbidden = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'no access')
        system, fallback, extra = channel(201, error=forbidden), channel(202), channel(203)
        self.assertTrue(await self.service.send(guild(channels=[system, fallback, extra], system=system)))
        fallback.send.assert_awaited_once()
        extra.send.assert_not_awaited()

    async def test_no_permission_and_uncertain_delivery_do_not_repeat(self):
        hidden, blocked = channel(201, visible=False), channel(202, writable=False)
        self.assertFalse(await self.service.send(guild(channels=[hidden, blocked])))
        hidden.send.assert_not_awaited()
        blocked.send.assert_not_awaited()
        self.assertFalse(await self.service.send(guild(channels=[channel()])))
        uncertain, fallback = channel(204, error=TimeoutError()), channel(205)
        self.assertFalse(await self.service.send(guild(101, channels=[uncertain, fallback])))
        fallback.send.assert_not_awaited()
        self.assertFalse(await self.service.send(guild(101, channels=[fallback])))

    async def test_concurrent_join_and_reconnect_send_only_once(self):
        destination = channel()
        server = guild(channels=[destination])
        results = await asyncio.gather(*(self.service.send(server) for _ in range(5)))
        self.assertEqual(sum(results), 1)
        destination.send.assert_awaited_once()
        await self.database.aclose()
        self.database = ShoeDatabase(self.path)
        self.service = WelcomeService(self.database, self.view)
        self.assertFalse(await self.service.send(server))

    async def test_existing_servers_need_explicit_catchup_and_configured_are_skipped(self):
        target, configured_target = channel(), channel(201)
        servers = [guild(channels=[target]), guild(101, channels=[configured_target])]
        self.database.configure_guild(101, 201, 'creative', 'relay')
        self.assertTrue(await self.service.reconcile(servers))
        target.send.assert_not_awaited()
        self.assertTrue(await self.service.reconcile(servers, include_existing=True))
        await self.service.reconcile(servers, include_existing=True)
        target.send.assert_awaited_once()
        configured_target.send.assert_not_awaited()

    async def test_new_server_added_while_offline_is_welcomed(self):
        await self.service.reconcile([guild()])
        destination = channel()
        await self.service.reconcile([guild(), guild(101, channels=[destination])])
        destination.send.assert_awaited_once()

    async def test_unavailable_server_is_not_claimed_until_available(self):
        destination = channel()
        server = guild(channels=[destination])
        server.unavailable = True
        self.assertFalse(await self.service.send(server))
        server.unavailable = False
        self.assertTrue(await self.service.send(server))

    async def test_removal_clears_history_without_changing_other_servers(self):
        self.database.claim_welcome(100)
        self.database.claim_welcome(101)
        self.database.delete_guild(100)
        self.assertTrue(self.database.claim_welcome(100))
        self.assertFalse(self.database.claim_welcome(101))
        self.database.prepare_welcomes([100])
        self.assertTrue(self.database.claim_welcome(101))

    async def test_upgrade_from_v5_preserves_game_and_streak(self):
        self.database.configure_guild(100, 200, 'creative', 'relay')
        self.database.record_message(100, 300, True)
        await self.database.aclose()
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE guild_welcomes')
            db.execute('PRAGMA user_version = 5')
            db.execute("UPDATE schema_metadata SET metadata_value = '5' WHERE metadata_key = 'schema_version'")
        db.close()
        self.database = ShoeDatabase(self.path)
        self.assertEqual(self.database.get_guild_stats(100).current_streak, 1)
        self.assertEqual(self.database.get_user_stats(100, 300).shoe_count, 1)
        self.assertTrue(self.database.claim_welcome(101))

    async def test_persistent_button_opens_private_admin_wizard_and_rejects_others(self):
        commands = ShoeCommands(self.database, Mock())
        commands._open_settings = AsyncMock()
        view = WelcomeView(commands.open_setup)
        self.addCleanup(view.stop)
        self.assertTrue(view.is_persistent())
        for administrator in (False, True):
            interaction = FakeInteraction(user_id=300, guild_id=100,
                                          administrator=administrator, component=True)
            interaction.guild = guild()
            await view.children[0].callback(interaction)
            if administrator:
                self.assertTrue(interaction.response.defer_ephemeral)
                self.assertIs(interaction.response.type, discord.InteractionResponseType.deferred_channel_message)
                commands._open_settings.assert_awaited_once()
            else:
                commands._open_settings.assert_not_awaited()
                self.assertTrue(interaction.response.messages[0][1]['ephemeral'])
        direct_message = FakeInteraction(user_id=300, guild_id=None, administrator=True, component=True)
        await view.children[0].callback(direct_message)
        self.assertEqual(commands._open_settings.await_count, 1)

    async def test_setup_registers_button_after_restart_and_early_available_does_not_send(self):
        bot = ShoeBot(BotConfig('fake', 123, None, self.path), self.database)
        bot.tree.sync = AsyncMock(return_value=[])
        try:
            await bot.setup_hook()
            self.assertTrue(any(v.is_persistent() for v in bot.persistent_views))
            bot._welcomes.send = AsyncMock()
            await bot.on_guild_available(guild())
            bot._welcomes.send.assert_not_awaited()
            bot._welcome_history_ready = True
            await bot.on_guild_available(guild())
            bot._welcomes.send.assert_awaited_once()
        finally:
            await bot.close()
