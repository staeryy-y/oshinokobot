import asyncio
import tempfile
import unittest
from datetime import datetime as RealDatetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import db
from app.bot.cogs.polls import Polls
from app.migrate import run_migrations


class PollSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = str(Path(self.temp.name) / 'test.db')
        await run_migrations(path)
        self.conn = await db.connect(path)
        self.addAsyncCleanup(self.conn.close)
        self.image = Path(self.temp.name) / 'image.png'
        self.image.write_bytes(b'test attachment')
        self.character_id = await db.create_character(
            self.conn, name='Test character', series=None, image_path=str(self.image),
            source_url=None, uploaded_by=None,
        )
        await db.update_guild_config(
            self.conn, channel_id=123, poll_post_time='09:00',
            poll_timezone='America/New_York', active_series=None,
        )
        self.channel = SimpleNamespace(id=123, send=AsyncMock(return_value=SimpleNamespace(id=456)))
        self.bot = SimpleNamespace(
            db=self.conn, get_channel=Mock(return_value=self.channel), add_view=Mock(),
            is_ready=Mock(return_value=True), wait_until_ready=AsyncMock(),
        )
        with patch('discord.ext.tasks.Loop.start'):
            self.cog = Polls(self.bot)

    async def tick(self, instant='2026-09-16T13:00:00+00:00'):
        now = RealDatetime.fromisoformat(instant)
        class Clock(RealDatetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz)
        with patch('app.bot.cogs.polls.datetime', Clock):
            await self.cog.daily_poll_check()

    async def test_due_time_and_repeat_tick(self):
        await self.tick('2026-09-16T12:59:00+00:00')
        self.channel.send.assert_not_awaited()
        await self.tick()
        await self.tick()
        self.channel.send.assert_awaited_once()

    async def test_unpadded_saved_time_is_not_skipped_all_day(self):
        await self.conn.execute("UPDATE guild_config SET poll_post_time = '9:00'")
        await self.conn.commit()
        await self.tick()
        self.channel.send.assert_awaited_once()

    async def test_missing_image_does_not_suppress_retry(self):
        self.image.unlink()
        with self.assertLogs('oshinokobot.bot.polls', level='ERROR'):
            await self.tick()
        self.assertEqual(await db.list_polls(self.conn), [])
        self.image.write_bytes(b'repaired attachment')
        await self.tick()
        self.channel.send.assert_awaited_once()

    async def test_send_oserror_does_not_suppress_retry(self):
        self.channel.send.side_effect = OSError('connection lost')
        with self.assertLogs('oshinokobot.bot.polls', level='ERROR'):
            await self.tick()
        self.assertEqual(await db.list_polls(self.conn), [])
        self.channel.send.side_effect = None
        await self.tick()
        self.assertEqual(self.channel.send.await_count, 2)

    async def test_existing_message_less_row_does_not_count_as_posted(self):
        await db.create_poll(self.conn, character_id=self.character_id, channel_id=123,
                             message_id=None, posted_at='2026-09-16T13:00:00+00:00')
        await db.create_character(self.conn, name='Another', series=None,
                                  image_path=str(self.image), source_url=None, uploaded_by=None)
        await self.tick()
        self.channel.send.assert_awaited_once()

    async def test_previous_local_day_posts_even_on_same_utc_day(self):
        await db.create_poll(self.conn, character_id=self.character_id, channel_id=123,
                             message_id=789, posted_at='2026-09-16T02:00:00+00:00')
        await db.create_character(self.conn, name='Another', series=None,
                                  image_path=str(self.image), source_url=None, uploaded_by=None)
        self.cog._close_poll = AsyncMock()
        await self.tick()
        self.channel.send.assert_awaited_once()

    async def test_manual_post_before_schedule_suppresses_daily_post(self):
        with patch('app.bot.cogs.polls.datetime') as clock:
            clock.now.return_value = RealDatetime(2026, 9, 16, 12, tzinfo=timezone.utc)
            ok, _ = await self.cog.post_new_poll()
        self.assertTrue(ok)
        await self.tick()
        self.channel.send.assert_awaited_once()

    async def test_loop_recovers_from_failure_outside_tick(self):
        loop = self.cog.daily_poll_check
        restarted = asyncio.Event()
        calls = 0

        async def body(cog):
            nonlocal calls
            calls += 1
            if calls == 2:
                restarted.set()
                await asyncio.Event().wait()

        async def fail_sleep(when):
            raise RuntimeError('loop timer failed')

        with patch.object(loop, 'coro', body), \
             patch.object(loop, '_try_sleep_until', fail_sleep), \
             self.assertLogs('oshinokobot.bot.polls', level='ERROR'):
            first_task = loop.start()
            try:
                with self.assertRaises(RuntimeError):
                    await first_task
                await asyncio.wait_for(restarted.wait(), timeout=1)
            finally:
                loop.cancel()
                task = loop.get_task()
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, RuntimeError):
                        pass

    async def test_concurrent_checks_only_post_once(self):
        with patch('app.bot.cogs.polls.datetime') as clock:
            clock.now.return_value = RealDatetime(2026, 9, 16, 13, tzinfo=timezone.utc)
            clock.fromisoformat = RealDatetime.fromisoformat
            clock.strptime = RealDatetime.strptime
            await asyncio.gather(self.cog.daily_poll_check(), self.cog.daily_poll_check())
        self.channel.send.assert_awaited_once()
