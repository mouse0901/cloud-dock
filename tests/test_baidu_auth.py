"""HTTP mock and lifecycle tests; no real credentials or cloud files are used."""
from __future__ import annotations
import asyncio
import unittest
from unittest.mock import AsyncMock
import httpx
from app.providers.baidu_api import BaiduApi, BaiduApiError, DeviceGrant, Tokens, official_device_url
from app.baidu_auth import DeviceAuthorizer, AuthorizedDirectory, application_root, within_application


class Clock:
    def __init__(self):
        self.now = 0
        self.delays = []
    def __call__(self):
        return self.now
    async def sleep(self, delay):
        self.delays.append(delay)
        self.now += delay


class PollingTests(unittest.IsolatedAsyncioTestCase):
    def setup_flow(self, responses, lifetime=60):
        api = AsyncMock()
        api.device_code.return_value = DeviceGrant("device", "user", "https://openapi.baidu.com/device",
            "https://openapi.baidu.com/device/qrcode/test/user", 5, lifetime)
        api.exchange_device.side_effect = responses
        clock = Clock()
        return api, clock, DeviceAuthorizer(api, clock=clock, sleep=clock.sleep)

    async def test_pending_slowdown_and_success_respect_spacing(self):
        token = Tokens("mock-access", "mock-refresh", 3600)
        api, clock, flow = self.setup_flow([
            BaiduApiError("authorization_pending", "pending"),
            BaiduApiError("slow_down", "slow"), token])
        grant_cb, wait_cb = AsyncMock(), AsyncMock()
        self.assertIs(await flow.authorize("app", "secret", grant_cb, wait_cb), token)
        self.assertEqual(clock.delays, [6, 6, 11])
        self.assertEqual(api.exchange_device.await_count, 3)
        self.assertEqual(wait_cb.await_count, 2)

    async def test_expiry_stops_requests(self):
        api, clock, flow = self.setup_flow([BaiduApiError("authorization_pending", "pending")], lifetime=10)
        with self.assertRaises(BaiduApiError) as result:
            await flow.authorize("app", "secret", AsyncMock(), AsyncMock())
        self.assertEqual(result.exception.code, "expired_token")
        self.assertEqual(api.exchange_device.await_count, 1)
        self.assertEqual(clock.delays, [6, 4])

    async def test_denial_is_not_retried(self):
        api, clock, flow = self.setup_flow([BaiduApiError("access_denied", "denied")])
        with self.assertRaises(BaiduApiError):
            await flow.authorize("app", "secret", AsyncMock(), AsyncMock())
        self.assertEqual(api.exchange_device.await_count, 1)

    async def test_cancellation_stops_polling(self):
        api = AsyncMock()
        api.device_code.return_value = DeviceGrant("device", "user", "https://openapi.baidu.com/device",
            "https://openapi.baidu.com/device/qrcode/test/user", 5, 300)
        sleeping = asyncio.Event()
        async def wait(delay):
            sleeping.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(DeviceAuthorizer(api, sleep=wait).authorize("app", "secret", AsyncMock(), AsyncMock()))
        await sleeping.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        api.exchange_device.assert_not_awaited()


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_http400_preserves_pending(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(400, json={"error": "authorization_pending"}))) as client:
            with self.assertRaises(BaiduApiError) as result:
                await BaiduApi(client).exchange_device("app", "mock-secret", "device")
        self.assertEqual(result.exception.code, "authorization_pending")

    async def test_exchange_contract_and_no_sensitive_repr(self):
        def handler(req):
            self.assertEqual(req.url.host, "openapi.baidu.com")
            self.assertEqual(req.url.params["grant_type"], "device_token")
            self.assertEqual(req.url.params["client_secret"], "mock-secret")
            self.assertEqual(req.headers["User-Agent"], "pan.baidu.com")
            return httpx.Response(200, json={"access_token": "mock-access", "refresh_token": "mock-refresh", "expires_in": 3600})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            token = await BaiduApi(client).exchange_device("app", "mock-secret", "device")
        self.assertNotIn("mock-access", repr(token))
        self.assertNotIn("mock-refresh", repr(token))

    async def test_transport_error_never_exposes_url(self):
        def handler(req):
            raise httpx.ConnectError("failed " + str(req.url), request=req)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(BaiduApiError) as result:
                await BaiduApi(client).exchange_device("app", "private-secret", "device")
        self.assertEqual(result.exception.code, "network")
        self.assertNotIn("private-secret", str(result.exception))
        self.assertTrue(result.exception.__suppress_context__)

    async def test_missing_list_is_not_empty_success(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json={"errno": 0}))) as client:
            with self.assertRaises(BaiduApiError):
                await BaiduApi(client).list_files("test")

    async def test_untrusted_qr_url_is_rejected(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            "device_code": "device", "user_code": "user", "verification_url": "https://openapi.baidu.com/device",
            "qrcode_url": "https://example.com/qr", "interval": 5, "expires_in": 300}))) as client:
            with self.assertRaises(BaiduApiError):
                await BaiduApi(client).device_code("app")


class BoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_never_requests_outside_application(self):
        api = AsyncMock()
        reader = AuthorizedDirectory(api, Tokens("mock", "mock", 3600), "云屿")
        for path in ["/", "/apps", "/apps/云屿other", "/apps/云屿/../private", "/apps/云屿/a/../../private"]:
            with self.assertRaises(ValueError):
                await reader.list_page(path)
        api.list_files.assert_not_awaited()
        self.assertTrue(within_application("/apps/云屿/folder", reader.root))

    async def test_authorized_folder_and_page_parameters(self):
        api = AsyncMock()
        api.list_files.return_value = []
        reader = AuthorizedDirectory(api, Tokens("mock", "mock", 3600), "云屿")
        await reader.list_page("/apps/云屿", 2)
        api.list_files.assert_awaited_once_with("mock", "/apps/云屿", 100, 50)

    async def test_scope_mismatch_response_is_not_shown(self):
        from app.domain import CloudFile
        api = AsyncMock()
        api.list_files.return_value = [CloudFile("123", "baidu", "/other/private.txt")]
        reader = AuthorizedDirectory(api, Tokens("mock", "mock", 3600), "云屿")
        with self.assertRaises(BaiduApiError):
            await reader.list_page(reader.root)

    def test_appname_and_auth_domain_validation(self):
        self.assertEqual(application_root("云屿"), "/apps/云屿")
        for name in ["../bad", "", "a/b"]:
            with self.assertRaises(ValueError):
                application_root(name)
        for url in ["http://openapi.baidu.com/device", "https://openapi.baidu.com.evil.example/device",
                    "https://user:secret@openapi.baidu.com/device"]:
            with self.assertRaises(ValueError):
                official_device_url(url)


class ProbeUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_form_and_cancel_clear_session(self):
        from baidu_login import LoginProbe
        class Page:
            def __init__(self): self.controls = []
            def add(self, control): self.controls.append(control)
            def update(self): pass
        probe = LoginProbe(Page())
        try:
            probe.mount()
            await probe.begin(None)
            self.assertIn("AppKey", probe.status.value)
            self.assertIsNone(probe.task)
            probe.app_key.value, probe.secret.value = "mock-app", "mock-secret"
            await probe.cancel()
            self.assertEqual(probe.app_key.value, "")
            self.assertEqual(probe.secret.value, "")
            self.assertIsNone(probe.reader)
            self.assertFalse(probe.qr.visible)
        finally:
            await probe.shutdown()


if __name__ == "__main__":
    unittest.main()
