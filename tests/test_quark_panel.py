"""Headless callback tests; these do not claim the native window was operated."""
import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

import httpx

from app.direct_download import DownloadSource, DownloadError
from app.domain import CloudFile
from app.providers.baidu_api import BaiduApiError
from app.providers.quark_web import QuarkApiError, QuarkShare
from app.quark_panel import QuarkPanel, QuarkPanelDirectory, render_qr_svg


class PageStub:
    def add(self, control):
        pass

    def update(self):
        pass


ROOT_ROW = {"fid": "own-file", "file_name": "own.txt", "size": 4, "dir": False, "pdir_fid": "0", "updated_at": 0}
COOKIE = "__pus=test-session; Domain=.quark.cn; Path=/; Secure"


def own_response(request):
    return httpx.Response(200, json={"status": 200, "code": 0, "data": {"list": [ROOT_ROW]}})


class QuarkPanelTests(unittest.IsolatedAsyncioTestCase):
    def make_panel(self, handler=own_response):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        panel = QuarkPanel(PageStub(), client=client, diagnostic=lambda _: None)
        self.addAsyncCleanup(panel.shutdown)
        return panel

    async def connected_panel(self):
        panel = self.make_panel()
        panel.api.authenticated = True
        panel.api.session_candidate = True
        panel.api.session_id = "session-one"
        await panel.show_connected_directory()
        return panel

    async def test_local_svg_has_valid_viewbox(self):
        svg = render_qr_svg("https://su.quark.cn/4_eMHBJ?token=test")
        node = ElementTree.fromstring(svg)
        self.assertTrue(node.tag.endswith("svg"))
        self.assertIn("viewBox", node.attrib)
        self.assertGreater(len(list(node)), 0)

    async def test_scan_success_reads_directory_and_enables_download(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith("getTokenForQrcodeLogin"):
                return httpx.Response(200, json={"status": 2000000, "data": {"members": {"token": "qr-fixture"}}})
            if request.url.path.endswith("getServiceTicketByQrcodeToken"):
                return httpx.Response(200, json={"status": 2000000, "data": {"members": {"service_ticket": "st-fixture"}}})
            if request.url.path == "/account/info":
                return httpx.Response(200, json={"success": True, "data": {}}, headers={"set-cookie": COOKIE})
            return own_response(request)
        panel = self.make_panel(handler)
        self.assertIsNone(panel.task)  # Navigation must not auto-scan.
        self.assertNotIn(panel.form, panel.view.controls)
        self.assertTrue(panel.share_open.disabled)
        with patch("app.quark_panel.asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            await panel.task
        self.assertTrue(panel.api.authenticated)
        self.assertEqual(panel.path, "/")
        self.assertEqual(panel.files.controls[0].title.value, "own.txt")
        self.assertIsNotNone(panel.files.controls[0].trailing)
        self.assertFalse(panel.share_open.disabled)
        self.assertIsNone(panel.qr.content)
        self.assertIn("登录成功", panel.status.value)
        self.assertEqual(panel.download_destination.parent.name, "quark")
        self.assertEqual(len(calls), 4)

    async def test_failed_directory_can_retry_same_approved_session(self):
        ready = False
        exchanges = 0
        def handler(request):
            nonlocal exchanges
            if request.url.path.endswith("getTokenForQrcodeLogin"):
                return httpx.Response(200, json={"status": 2000000, "data": {"members": {"token": "qr-fixture"}}})
            if request.url.path.endswith("getServiceTicketByQrcodeToken"):
                return httpx.Response(200, json={"status": 2000000, "data": {"members": {"service_ticket": "st-fixture"}}})
            if request.url.path == "/account/info":
                exchanges += 1
                return httpx.Response(200, json={"success": True, "data": {}}, headers={"set-cookie": COOKIE})
            if not ready:
                return httpx.Response(200, json={"status": 500, "code": 50001})
            return own_response(request)
        panel = self.make_panel(handler)
        with patch("app.quark_panel.asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            await panel.task
        self.assertTrue(panel.retry_button.visible)
        self.assertTrue(panel.api.session_candidate)
        self.assertFalse(panel.api.authenticated)
        ready = True
        await panel.retry_connection()
        await panel.task
        self.assertTrue(panel.api.authenticated)
        self.assertFalse(panel.retry_button.visible)
        self.assertEqual(exchanges, 1)
        self.assertEqual(panel.files.controls[0].title.value, "own.txt")

    async def test_refresh_and_logout_cancel_active_poll_and_hide_qr(self):
        started = asyncio.Event()
        async def handler(request):
            if request.url.path.endswith("getTokenForQrcodeLogin"):
                return httpx.Response(200, json={"status": 2000000, "data": {"members": {"token": "qr-fixture"}}})
            started.set()
            await asyncio.Event().wait()
        panel = self.make_panel(handler)
        with patch("app.quark_panel.asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            first = panel.task
            await asyncio.wait_for(started.wait(), 1)
            await panel.begin()
            self.assertTrue(first.cancelled())
            await panel.cancel()
        self.assertIsNone(panel.task)
        self.assertFalse(panel.api.authenticated)
        self.assertFalse(panel.qr.visible)
        self.assertIsNone(panel.qr.content)
        self.assertEqual(panel.files.controls, [])
        self.assertEqual(list(panel.client.cookies.jar), [])
        self.assertTrue(panel.share_open.disabled)

    async def test_expired_qr_shows_refresh_message(self):
        def handler(request):
            if request.url.path.endswith("getTokenForQrcodeLogin"):
                return httpx.Response(200, json={"status": 2000000, "data": {"members": {"token": "qr-fixture"}}})
            return httpx.Response(200, json={"status": 50004002})
        panel = self.make_panel(handler)
        with patch("app.quark_panel.asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            await panel.task
        self.assertIn("二维码已过期", panel.status.value)
        self.assertFalse(panel.qr.visible)
        self.assertFalse(panel.begin_button.disabled)

    async def test_share_requires_login(self):
        panel = self.make_panel(lambda _: self.fail("must not make network requests"))
        panel.share_link.value = "https://pan.quark.cn/s/abc123def456"
        await panel.open_share()
        self.assertIn("先扫码", panel.status.value)
        self.assertIsNone(panel.share)

    async def test_share_failure_keeps_current_directory(self):
        panel = await self.connected_panel()
        old_reader, old_controls, old_path = panel.reader, panel.files.controls, panel.path
        panel.api.acquire_share = AsyncMock(side_effect=QuarkApiError("share", "测试分享已失效"))
        await panel.open_share()
        self.assertIs(panel.reader, old_reader)
        self.assertIs(panel.files.controls, old_controls)
        self.assertEqual(panel.path, old_path)
        self.assertIsNone(panel.share)
        self.assertIn("测试分享已失效", panel.status.value)
        self.assertFalse(panel.share_open.disabled)
        self.assertFalse(panel.refresh_button.disabled)

    async def test_share_first_page_failure_does_not_commit_token(self):
        panel = await self.connected_panel()
        old_reader = panel.reader
        candidate = QuarkShare("abc123def456", "stoken", panel.api.session_id)
        panel.api.acquire_share = AsyncMock(return_value=candidate)
        panel.api.list_share = AsyncMock(side_effect=QuarkApiError("share", "分享目录失败"))
        await panel.open_share()
        self.assertIs(panel.reader, old_reader)
        self.assertIsNone(panel.share)
        self.assertEqual(panel.files.controls[0].title.value, "own.txt")

    async def test_share_success_and_return_my_files(self):
        panel = await self.connected_panel()
        share = QuarkShare("abc123def456", "stoken", panel.api.session_id)
        shared = CloudFile("share-file", "quark", "/shared.txt", False, 4)
        panel.api.acquire_share = AsyncMock(return_value=share)
        panel.api.list_share = AsyncMock(return_value=[shared])
        await panel.open_share()
        self.assertIs(panel.share, share)
        self.assertEqual(panel.files.controls[0].title.value, "shared.txt")
        self.assertEqual(panel.api.list_share.await_count, 1)  # Prefetched page reused.
        await panel.show_my_files()
        self.assertIsNone(panel.share)
        self.assertEqual(panel.files.controls[0].title.value, "own.txt")
        self.assertFalse(panel.share_open.disabled)

    async def test_late_share_cannot_replace_my_files_view(self):
        panel = await self.connected_panel()
        started, release = asyncio.Event(), asyncio.Event()
        share = QuarkShare("abc123def456", "stoken", panel.api.session_id)
        panel.api.acquire_share = AsyncMock(return_value=share)
        async def slow_list(value):
            started.set()
            await release.wait()
            return [CloudFile("late-file", "quark", "/late.txt", False, 4)]
        panel.api.list_share = slow_list
        pending = asyncio.create_task(panel.open_share())
        await asyncio.wait_for(started.wait(), 1)
        await panel.show_my_files()
        release.set()
        await pending
        self.assertIsNone(panel.share)
        self.assertEqual(panel.files.controls[0].title.value, "own.txt")
        self.assertIn("目录读取成功", panel.status.value)

    async def test_late_share_cannot_replace_later_folder_navigation(self):
        panel = await self.connected_panel()
        started, release = asyncio.Event(), asyncio.Event()
        share = QuarkShare("abc123def456", "stoken", panel.api.session_id)
        panel.api.acquire_share = AsyncMock(return_value=share)
        async def slow_list(value):
            started.set()
            await release.wait()
            return [CloudFile("late-file", "quark", "/late.txt", False, 4)]
        panel.api.list_share = slow_list
        pending = asyncio.create_task(panel.open_share())
        await asyncio.wait_for(started.wait(), 1)
        panel.api.list_files = AsyncMock(return_value=[CloudFile("folder-file", "quark", "/folder/inside.txt", False, 4)])
        await panel.load_directory("/folder", 0)
        release.set()
        await pending
        self.assertIsNone(panel.share)
        self.assertEqual(panel.path, "/folder")
        self.assertEqual(panel.files.controls[0].title.value, "inside.txt")
        self.assertFalse(panel.share_open.disabled)

    async def test_second_share_wins_over_late_first_share(self):
        panel = await self.connected_panel()
        first = QuarkShare("firstshare12", "first-token", panel.api.session_id)
        second = QuarkShare("secondshare2", "second-token", panel.api.session_id)
        started, release = asyncio.Event(), asyncio.Event()
        panel.api.acquire_share = AsyncMock(side_effect=[first, second])
        async def list_share(share):
            if share is first:
                started.set()
                await release.wait()
            return [CloudFile(share.id, "quark", "/" + share.id + ".txt", False, 4)]
        panel.api.list_share = list_share
        pending = asyncio.create_task(panel.open_share())
        await asyncio.wait_for(started.wait(), 1)
        await panel.open_share()
        release.set()
        await pending
        self.assertIs(panel.share, second)
        self.assertEqual(panel.files.controls[0].title.value, "secondshare2.txt")

    async def test_logout_cancels_share_and_clears_sensitive_input(self):
        panel = await self.connected_panel()
        started = asyncio.Event()
        async def acquire(*args):
            started.set()
            await asyncio.Event().wait()
        panel.api.acquire_share = acquire
        panel.share_link.value = "https://pan.quark.cn/s/abc123def456"
        panel.share_password.value = "Ab12"
        pending = asyncio.create_task(panel.open_share())
        await asyncio.wait_for(started.wait(), 1)
        await panel.cancel()
        self.assertTrue(pending.cancelled())
        self.assertEqual(panel.share_link.value, "")
        self.assertEqual(panel.share_password.value, "")
        self.assertFalse(panel.results.visible)
        self.assertFalse(panel.api.authenticated)

    async def test_share_download_snapshot_survives_view_change(self):
        panel = await self.connected_panel()
        share = QuarkShare("abc123def456", "stoken", panel.api.session_id)
        item = CloudFile("share-file", "quark", "/shared.txt", False, 4)
        panel.share = share
        panel.reader = QuarkPanelDirectory(panel.api, share)
        panel.api.share_download_source = AsyncMock(return_value=DownloadSource("https://pdds.quark.cn/test", 4))
        # Keep the queue idle while checking its bound resolver; no file is written.
        panel._ensure_live_worker = lambda: None
        await panel.start_download(item)
        self.assertEqual(len(panel.jobs), 1)
        job = panel.jobs[0]
        await panel.show_my_files()
        source = await job.resolve_source(item)
        self.assertEqual(source.size, 4)
        panel.api.share_download_source.assert_awaited_once_with(item, share)
        self.assertEqual(job._current_owner(), job._owner)

    async def test_download_resolver_keeps_safe_provider_error(self):
        panel = await self.connected_panel()
        panel.api.download_source = AsyncMock(side_effect=QuarkApiError("quark_denied", "夸克未允许这个下载"))
        with self.assertRaises(DownloadError) as caught:
            await panel.get_download_resolver()(CloudFile("file", "quark", "/test.txt", False, 4))
        self.assertEqual(caught.exception.code, "quark_denied")
        self.assertEqual(str(caught.exception), "夸克未允许这个下载")

    async def test_directory_wrapper_preserves_provider_error_text(self):
        panel = await self.connected_panel()
        panel.api.list_files = AsyncMock(side_effect=QuarkApiError("test_error", "夸克目录测试失败"))
        with self.assertRaises(BaiduApiError) as caught:
            await QuarkPanelDirectory(panel.api).list_page("/", 0)
        self.assertEqual(str(caught.exception), "夸克目录测试失败")
        await panel.show_my_files()
        self.assertIn("夸克目录测试失败", panel.status.value)


if __name__ == "__main__":
    unittest.main()
