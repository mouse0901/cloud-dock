"""Headless live download UI tests; real filesystem, simulated HTTP only."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import flet as ft
import httpx

from app.baidu_panel import BaiduPanel
from app.direct_download import DownloadError, DownloadSource
from app.domain import CloudFile
from app.live_download_panel import LiveDownloadMixin
from app.providers.demo import DemoBaiduProvider, PendingQuarkProvider
from app.ui import CloudDock
from app.workspace import Workspace
from test_ui_smoke import PageStub

ROOT = Path(__file__).resolve().parents[1]
ITEM = CloudFile('one', 'baidu', '/first.bin', size=5)
ITEM2 = CloudFile('two', 'baidu', '/second.bin', size=5)


def validate(url):
    if httpx.URL(url).host != 'download.baidu.com':
        raise ValueError('unsupported host')
    return url


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self, started):
        self.started = started

    async def __aiter__(self):
        yield b'he'
        self.started.set()
        await asyncio.Event().wait()


class Panel(LiveDownloadMixin, BaiduPanel):
    def __init__(self, page, destination, transport, on_transfer_change=None):
        super().__init__(page, client=httpx.AsyncClient(transport=transport))
        self.api = SimpleNamespace(client=self.client, session_id='session-A',
            validate_download_url=validate,
            download_source=AsyncMock(return_value=DownloadSource('https://download.baidu.com/file', 5)))
        self.reader = object()
        self.download_destination = destination
        self.on_transfer_change = on_transfer_change
        self.init_live_downloads()


class LiveDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'work' / 'test-runs')
        self.folder = Path(self.tmp.name)
        self.page = PageStub()
        self.panels = []
        self.update_patch = patch.object(ft.Control, 'update', return_value=None)
        self.update_patch.start()

    async def asyncTearDown(self):
        for panel in self.panels:
            await panel.shutdown()
        self.update_patch.stop()
        self.tmp.cleanup()

    def panel(self, handle=None, **kwargs):
        handle = handle or (lambda req: httpx.Response(200, content=b'hello'))
        panel = Panel(self.page, self.folder / str(len(self.panels)), httpx.MockTransport(handle), **kwargs)
        self.panels.append(panel)
        return panel

    async def drain(self, panel):
        worker = panel._live_worker
        if worker is not None:
            await asyncio.wait_for(worker, 3)

    async def test_queue_finishes_real_files_and_renders_separate_control_instances(self):
        changes = Mock()
        panel = self.panel(on_transfer_change=changes)
        await panel.start_download(ITEM)
        await panel.start_download(ITEM2)
        await self.drain(panel)
        self.assertEqual([job.state for job in panel.jobs], ['completed', 'completed'])
        self.assertEqual([job.target.read_bytes() for job in panel.jobs], [b'hello', b'hello'])
        self.assertEqual(panel.api.download_source.await_count, 2)
        self.assertGreater(changes.call_count, 1)
        a, b = panel.render_live_downloads(), panel.render_live_downloads()
        self.assertIsNot(a.controls[0], b.controls[0])
        self.assertEqual(len(panel.render_live_downloads(completed=False).controls), 0)
        self.assertEqual(len(panel.render_live_downloads(completed=True).controls), 2)
        self.assertIn('退出账号或关闭窗口', panel.download_controls()[-1].value)

    async def test_serial_queue_starts_next_after_pause_and_resumes_with_range(self):
        started = asyncio.Event()
        requests = []
        def handle(req):
            requests.append(req)
            if len(requests) == 1:
                return httpx.Response(200, stream=BlockingStream(started))
            if req.headers.get('Range'):
                self.assertEqual(req.headers['Range'], 'bytes=2-')
                return httpx.Response(206, headers={'Content-Range': 'bytes 2-4/5'}, content=b'llo')
            return httpx.Response(200, content=b'hello')
        panel = self.panel(handle)
        await panel.start_download(ITEM)
        await panel.start_download(ITEM2)
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(len(requests), 1)
        first, second = panel.jobs
        self.assertEqual(second.state, 'queued')
        await panel.pause_download(first)
        await self.drain(panel)
        self.assertEqual(first.state, 'paused')
        self.assertEqual(first.part.read_bytes(), b'he')
        self.assertEqual(second.state, 'completed')
        await panel.resume_download(first)
        await self.drain(panel)
        self.assertEqual(first.state, 'completed')
        self.assertEqual(first.target.read_bytes(), b'hello')

    async def test_cancel_queued_and_active_keeps_queue_usable(self):
        started = asyncio.Event()
        calls = 0
        def handle(req):
            nonlocal calls
            calls += 1
            return httpx.Response(200, stream=BlockingStream(started)) if calls == 1 else httpx.Response(200, content=b'hello')
        panel = self.panel(handle)
        await panel.start_download(ITEM)
        await panel.start_download(ITEM2)
        first, second = panel.jobs
        await asyncio.wait_for(started.wait(), 2)
        await panel.cancel_one_download(second)
        await panel.cancel_one_download(first)
        await self.drain(panel)
        self.assertEqual(calls, 1)
        self.assertEqual([job.state for job in panel.jobs], ['cancelled', 'cancelled'])
        self.assertFalse(first.part.exists())
        await panel.start_download(ITEM)
        await self.drain(panel)
        self.assertEqual(panel.jobs[-1].state, 'completed')

    async def test_logout_clears_unfinished_tasks_and_keeps_completed_file(self):
        started = asyncio.Event()
        calls = 0
        def handle(req):
            nonlocal calls
            calls += 1
            return httpx.Response(200, content=b'hello') if calls == 1 else httpx.Response(200, stream=BlockingStream(started))
        panel = self.panel(handle)
        await panel.start_download(ITEM)
        await self.drain(panel)
        finished = panel.jobs[0].target
        await panel.start_download(ITEM2)
        await asyncio.wait_for(started.wait(), 2)
        partial = panel.jobs[1].part
        await panel.cancel()
        self.assertEqual(panel.jobs, [])
        self.assertEqual(panel._live_host.controls, [])
        self.assertFalse(partial.exists())
        self.assertEqual(finished.read_bytes(), b'hello')
        self.assertIsNone(panel.reader)

    async def test_failed_job_exposes_safe_message_and_stage_and_next_job_runs(self):
        panel = self.panel()
        panel.api.download_source.side_effect = [DownloadError('baidu_dlink_denied', '当前账号暂时无法获取下载地址'),
            DownloadSource('https://download.baidu.com/file', 5)]
        await panel.start_download(ITEM)
        await panel.start_download(ITEM2)
        await self.drain(panel)
        first, second = panel.jobs
        self.assertEqual(first.state, 'failed')
        self.assertEqual(first.error, '当前账号暂时无法获取下载地址')
        self.assertEqual(panel._live_errors[first], 'baidu_dlink_denied')
        self.assertEqual(second.state, 'completed')
        panel.api.download_source.side_effect = None
        await panel.resume_download(first)
        await self.drain(panel)
        self.assertEqual(first.state, 'completed')

    async def test_session_change_blocks_queued_job_before_source_request(self):
        panel = self.panel()
        await panel.start_download(ITEM)
        panel.api.session_id = 'session-B'
        await self.drain(panel)
        self.assertEqual(panel.jobs[0].state, 'failed')
        self.assertEqual(panel._live_errors[panel.jobs[0]], 'download_account')
        panel.api.download_source.assert_not_awaited()

    async def test_bound_share_resolver_survives_same_session_view_change(self):
        panel = self.panel()
        panel.download_reader_is_view = True
        share = ['share-A']
        resolved = []
        def get_resolver():
            bound_share = share[0]
            async def resolve(item):
                resolved.append(bound_share)
                return DownloadSource('https://download.baidu.com/file', 5)
            return resolve
        panel.get_download_resolver = get_resolver
        await panel.start_download(ITEM)
        share[0] = 'share-B'
        panel.reader = object()
        await self.drain(panel)
        self.assertEqual(resolved, ['share-A'])
        self.assertEqual(panel.jobs[0].state, 'completed')

    async def test_duplicate_click_does_not_create_duplicate_active_task(self):
        started = asyncio.Event()
        panel = self.panel(lambda req: httpx.Response(200, stream=BlockingStream(started)))
        await panel.start_download(ITEM)
        await panel.start_download(ITEM)
        await asyncio.wait_for(started.wait(), 2)
        self.assertEqual(len(panel.jobs), 1)
        self.assertIn('已在任务列表', panel.download_status.value)

    async def test_shutdown_suppresses_late_progress_and_cleans_task(self):
        started = asyncio.Event()
        callback = Mock()
        panel = self.panel(lambda req: httpx.Response(200, stream=BlockingStream(started)), on_transfer_change=callback)
        await panel.start_download(ITEM)
        await asyncio.wait_for(started.wait(), 2)
        before = callback.call_count
        part = panel.jobs[0].part
        await panel.shutdown()
        self.assertEqual(callback.call_count, before)
        self.assertFalse(part.exists())
        self.assertTrue(panel.client.is_closed)

    async def test_two_providers_download_independently_and_logout_is_isolated(self):
        started_a, started_b = asyncio.Event(), asyncio.Event()
        first = self.panel(lambda req: httpx.Response(200, stream=BlockingStream(started_a)))
        second = self.panel(lambda req: httpx.Response(200, stream=BlockingStream(started_b)))
        second.api.session_id = 'independent-session'
        await first.start_download(ITEM)
        await second.start_download(CloudFile('quark-one', 'quark', '/quark.bin', size=5))
        await asyncio.wait_for(asyncio.gather(started_a.wait(), started_b.wait()), 2)
        self.assertEqual(first.jobs[0].state, 'downloading')
        self.assertEqual(second.jobs[0].state, 'downloading')
        await first.cancel()
        self.assertEqual(first.jobs, [])
        self.assertEqual(second.jobs[0].state, 'downloading')
        self.assertEqual(second.api.session_id, 'independent-session')
        await second.pause_download(second.jobs[0])
        self.assertEqual(second.jobs[0].part.read_bytes(), b'he')

    async def test_nonrecoverable_error_retry_does_not_loop_forever(self):
        panel = self.panel()
        panel.api.download_source.return_value = DownloadSource('https://download.baidu.com/file', 5, md5='0' * 32)
        await panel.start_download(ITEM)
        await self.drain(panel)
        job = panel.jobs[0]
        self.assertEqual(job.state, 'failed')
        await panel.resume_download(job)
        await self.drain(panel)
        self.assertEqual(job.state, 'failed')
        self.assertEqual(panel._live_errors[job], 'download_checksum')
        panel.api.download_source.assert_awaited_once()
        buttons = panel.render_live_downloads().controls[0].content.controls[-1].controls
        self.assertEqual([button.content for button in buttons], ['取消'])
        self.assertIn('取消任务后重新下载', job.error)

    async def test_transfers_page_separates_real_jobs_from_demo_and_updates_only_when_visible(self):
        panel = self.panel()
        quark = self.panel()
        disk = DemoBaiduProvider(self.folder / 'demo.json')
        workspace = Workspace(self.folder / 'workspace')
        ui = CloudDock(self.page, disk, PendingQuarkProvider(), workspace)
        ui.baidu_panel, ui.quark_panel = panel, quark
        panel.on_transfer_change = ui.live_transfers_changed
        await ui.load_files(False)
        ui.render_shell()
        with patch.object(ui, 'render_page') as render:
            ui.live_transfers_changed()
            render.assert_not_called()
            ui.section = 'transfers'
            ui.live_transfers_changed()
            render.assert_called_once()
        await panel.start_download(ITEM)
        await self.drain(panel)
        ui.transfer_tab = 'completed'
        body = ui.transfers_page()
        self.assertEqual(body.controls[0].controls[0].controls[0].value, '传输中心')
        self.assertEqual(ui.live_jobs()[0][1].state, 'completed')
        self.assertTrue(ui.baidu_connected)
        self.assertTrue(ui.quark_connected)
        await ui.disconnect(None)
        self.assertTrue(panel.closed)
        self.assertTrue(quark.closed)


    async def test_completed_advisory_mismatch_displays_warning_and_no_false_checksum_claim(self):
        panel = self.panel()
        panel.api.download_source.return_value = DownloadSource('https://download.baidu.com/file', 5,
            md5='0' * 32, md5_is_content=False)
        await panel.start_download(ITEM)
        await self.drain(panel)
        job = panel.jobs[0]
        self.assertEqual(job.state, 'completed')
        body = panel.render_live_downloads(completed=True).controls[0].content.controls
        text = '\n'.join(c.value for c in body if isinstance(c, ft.Text))
        self.assertIn('摘要与本地不一致', text)
        self.assertIn('文件大小核对通过', text)
        self.assertNotIn('MD5核对通过', text)


if __name__ == '__main__':
    unittest.main()