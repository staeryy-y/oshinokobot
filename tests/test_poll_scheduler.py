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

    def note_interaction(self, guild_id=999):
        return SimpleNamespace(
            guild_id=guild_id, user=SimpleNamespace(id=987654, display_name="Secret author"),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def prepare_note_poll(self):
        await self.tick()
        embed = self.channel.send.call_args.kwargs["embed"]
        post = SimpleNamespace(embeds=[embed], edit=AsyncMock())
        self.channel.guild = SimpleNamespace(id=999)
        self.channel.fetch_message = AsyncMock(return_value=post)
        return await db.get_open_poll(self.conn), post

    async def test_notes_are_private_and_persist_on_close(self):
        poll, post = await self.prepare_note_poll()
        interaction = self.note_interaction()
        await Polls.oshinoko_note.callback(self.cog, interaction, "they're lowkey ass")
        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        notes = await db.get_poll_notes(self.conn, poll["id"])
        self.assertEqual(notes[0]["user_id"], 987654)
        public = await db.get_poll_notes(self.conn, poll["id"], public=True)
        self.assertEqual(public[0].keys(), ["message"])
        embed = post.edit.call_args.kwargs["embed"]
        self.assertIn("they're lowkey ass", str(embed.to_dict()))
        self.assertNotIn("Secret author", str(embed.to_dict()))
        self.assertNotIn("987654", str(embed.to_dict()))
        await self.cog._close_poll(poll)
        self.assertIn("they're lowkey ass", str(post.edit.call_args.kwargs["embed"].to_dict()))
        self.assertEqual(len(await db.get_poll_notes(self.conn, poll["id"])), 1)
        await db.delete_poll(self.conn, poll["id"])
        self.assertEqual(await db.get_poll_notes(self.conn, poll["id"]), [])

    async def test_note_rejects_wrong_server_blank_and_no_open_poll(self):
        poll, post = await self.prepare_note_poll()
        await Polls.oshinoko_note.callback(self.cog, self.note_interaction(111), "wrong server")
        await Polls.oshinoko_note.callback(self.cog, self.note_interaction(), "   ")
        self.assertEqual(await db.get_poll_notes(self.conn, poll["id"]), [])
        await db.close_poll(self.conn, poll["id"], closed_at="2026-09-16T14:00:00+00:00",
                            result_tier=None, result_tag_id=None)
        await Polls.oshinoko_note.callback(self.cog, self.note_interaction(), "closed")
        self.assertEqual(await db.get_poll_notes(self.conn, poll["id"]), [])
        post.edit.assert_not_awaited()

    def test_note_embed_limits_and_escaping(self):
        import discord
        from app.bot.cogs.polls import _set_note_fields
        embed = discord.Embed(title="Character")
        notes = [{"message": "@everyone **hello** " + "x" * 270}] * 100
        _set_note_fields(embed, notes)
        self.assertLessEqual(len(embed), 3900)
        self.assertLessEqual(len(embed.fields), 21)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))
        self.assertNotIn("@everyone", str(embed.to_dict()))
        self.assertIn("more notes saved", embed.fields[-1].value)

    async def test_public_note_template_escapes_html_and_hides_author(self):
        from app.admin.templating import templates
        poll, post = await self.prepare_note_poll()
        await db.create_poll_note(self.conn, poll_id=poll["id"], user_id=987654,
                                  display_name="Secret author", message="<script>alert(1)</script>")
        template = templates.env.get_template("public_poll_detail.html")
        html = template.render(poll=dict(poll, closed_at="2026-09-16T14:00:00"),
            character=await db.get_character(self.conn, self.character_id),
            tier_counts={}, tiers=[], appeal_rows=[], voter_rows=[],
            notes=await db.get_poll_notes(self.conn, poll["id"], public=True))
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert", html)
        self.assertNotIn("Secret author", html)
        self.assertNotIn("987654", html)

    async def test_notes_and_closing_work_without_read_history(self):
        import discord
        poll, post = await self.prepare_note_poll()
        self.channel.fetch_message.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"),
            {"code": 50013, "message": "Missing Permissions"})
        self.channel.get_partial_message = Mock(return_value=post)
        with self.assertLogs('oshinokobot.bot.polls', level='WARNING'):
            await Polls.oshinoko_note.callback(self.cog, self.note_interaction(), "history-free note")
            await self.cog._close_poll(poll)
        self.assertEqual(post.edit.await_count, 2)
        embed = post.edit.call_args.kwargs["embed"]
        self.assertEqual(embed.title, "Test character")
        self.assertIn("history-free note", str(embed.to_dict()))
        self.assertEqual(embed.image.url, "attachment://image.png")
        self.assertEqual(embed.footer.text, "Poll closed")
        self.assertEqual(len(await db.get_poll_notes(self.conn, poll["id"])), 1)
