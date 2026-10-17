"""Regression checks for QR deadlines, optional labels, and logout paints."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.baidu_web_panel import BaiduWebPanel
from app.domain import CloudFile
from app.providers.baidu_web import QrState, WebQr
from app.providers.quark_web import QuarkQr, QuarkQrState
from app.quark_panel import QuarkPanel


class PageStub:
    def __init__(self):
        self.panel = None
        self.painted_labels = []

    def update(self):
        if self.panel is not None and hasattr(self.panel, "account_text"):
            self.painted_labels.append(self.panel.account_text.value)


class AuthLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def make_panel(self, provider):
        page = PageStub()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: self.fail("unexpected network request")))
        panel = (BaiduWebPanel(page, client=client) if provider == "baidu" else
                 QuarkPanel(page, client=client, diagnostic=lambda _: None))
        page.panel = panel
        self.addAsyncCleanup(panel.shutdown)
        grant = WebQr("fixture", b"\x89PNG\r\n\x1a\nfixture") if provider == "baidu" else QuarkQr("fixture", "https://su.quark.cn/4_eMHBJ?token=fixture")
        state = QrState("confirmed", "fixture") if provider == "baidu" else QuarkQrState("confirmed", "fixture")
        panel.api.create_qr = AsyncMock(return_value=grant)
        panel.api.poll = AsyncMock(return_value=state)
        async def finish(_):
            panel.api.authenticated = True
            panel.api.session_candidate = True
            panel.api._initial_root = [CloudFile("123", provider, "/test.bin", False, 4)]
            if provider == "quark":
                panel.api.session_id = "fixture-session"
        panel.api.finish_login = finish
        if provider == "baidu":
            panel.api.account_label = AsyncMock(return_value="测试账号 · SVIP")
        return panel, page

    async def test_confirmed_login_is_not_limited_by_scan_deadline(self):
        real_timeout, real_sleep = asyncio.timeout, asyncio.sleep
        for provider in ("baidu", "quark"):
            with self.subTest(provider=provider):
                panel, _ = self.make_panel(provider)
                finish = panel.api.finish_login
                async def slow_finish(value):
                    await real_sleep(.08)
                    await finish(value)
                panel.api.finish_login = slow_finish
                contexts = []
                def timeout(seconds):
                    contexts.append(seconds)
                    return real_timeout(.04 if seconds == 300 else .4)
                with patch("asyncio.timeout", side_effect=timeout), patch("asyncio.sleep", new=AsyncMock()):
                    await panel.begin()
                    await panel.task
                self.assertTrue(panel.api.authenticated)
                self.assertTrue(panel.results.visible)
                self.assertNotIn("二维码已过期", panel.status.value)
                self.assertIn(300, contexts)
                self.assertIn(60, contexts)
                self.assertIn(30, contexts)

    async def test_slow_account_label_does_not_expire_scanned_qr(self):
        panel, _ = self.make_panel("baidu")
        real_timeout, real_sleep = asyncio.timeout, asyncio.sleep
        async def slow_label():
            await real_sleep(.08)
            return "测试账号 · SVIP"
        panel.api.account_label = AsyncMock(side_effect=slow_label)
        with patch("asyncio.timeout", side_effect=lambda seconds: real_timeout(.04 if seconds == 300 else .4)), patch("asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            await panel.task
        self.assertTrue(panel.api.authenticated)
        self.assertIn("目录读取成功", panel.status.value)
        self.assertEqual(panel.account_text.value, "")
        panel.api.account_label.assert_not_awaited()

    async def test_optional_label_timeout_keeps_successful_login(self):
        panel, _ = self.make_panel("baidu")
        real_timeout, real_sleep = asyncio.timeout, asyncio.sleep
        async def slow_label():
            await real_sleep(.1)
            return "late-label"
        panel.api.account_label = slow_label
        with patch("asyncio.timeout", side_effect=lambda seconds: real_timeout(.02 if seconds == 8 else .4)), patch("asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            await panel.task
        self.assertTrue(panel.api.authenticated)
        self.assertTrue(panel.results.visible)
        self.assertIn("目录读取成功", panel.status.value)
        self.assertEqual(panel.account_text.value, "")

    async def test_post_confirmation_timeout_is_connection_timeout(self):
        real_timeout, real_sleep = asyncio.timeout, asyncio.sleep
        for provider in ("baidu", "quark"):
            with self.subTest(provider=provider):
                panel, _ = self.make_panel(provider)
                async def stuck_finish(_):
                    await real_sleep(.1)
                panel.api.finish_login = stuck_finish
                with patch("asyncio.timeout", side_effect=lambda seconds: real_timeout(.02 if seconds == 60 else .4)), patch("asyncio.sleep", new=AsyncMock()):
                    await panel.begin()
                    await panel.task
                self.assertIn("连接超时", panel.status.value)
                self.assertNotIn("二维码已过期", panel.status.value)

    async def test_initial_directory_timeout_preserves_connected_account(self):
        real_timeout, real_sleep = asyncio.timeout, asyncio.sleep
        for provider in ("baidu", "quark"):
            with self.subTest(provider=provider):
                panel, _ = self.make_panel(provider)
                async def slow_files(*args):
                    await real_sleep(.1)
                    return []
                panel.api.list_files = slow_files
                with patch("asyncio.timeout", side_effect=lambda seconds: real_timeout(.02 if seconds == 30 else .4)), patch("asyncio.sleep", new=AsyncMock()):
                    await panel.begin()
                    await panel.task
                self.assertTrue(panel.api.authenticated)
                self.assertIsNotNone(panel.reader)
                self.assertTrue(panel.results.visible)
                self.assertIn("目录读取超时", panel.status.value)
                self.assertIn("重新读取", panel.status.value)

    async def test_logout_paints_cleared_account_label(self):
        panel, page = self.make_panel("baidu")
        panel.account_text.value = "旧账号"
        panel.retry_button.visible = True
        await panel.cancel()
        self.assertEqual(panel.account_text.value, "")
        self.assertTrue(page.painted_labels)
        self.assertEqual(page.painted_labels[-1], "")
        self.assertNotIn("旧账号", page.painted_labels)
        self.assertFalse(panel.retry_button.visible)

    async def test_late_account_label_cannot_return_after_logout(self):
        panel, page = self.make_panel("baidu")
        started = asyncio.Event()
        async def delayed_label():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "迟到旧账号"
        panel.api.account_label = AsyncMock(side_effect=delayed_label)
        with patch("asyncio.sleep", new=AsyncMock()):
            await panel.begin()
            await panel.task
            panel.api.account_label.assert_not_awaited()
            await panel.cancel()
        self.assertFalse(panel.api.authenticated)
        self.assertEqual(panel.account_text.value, "")
        self.assertNotIn("迟到旧账号", page.painted_labels)


if __name__ == "__main__":
    unittest.main()
