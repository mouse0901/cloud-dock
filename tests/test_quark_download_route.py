"""Quark desktop download route regressions; mock transport, no user account."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

from app.direct_download import DirectDownload
from app.domain import CloudFile
from app.providers.quark_web import (
    DOWNLOAD_UA, UA, DRIVE, PAN, QuarkApiError, QuarkShare, QuarkWebApi,
)

ROOT = Path(__file__).resolve().parents[1]


def ok(data):
    return httpx.Response(200, json={"status": 200, "code": 0, "data": data})


def ticket(item, **changes):
    result = {"fid": item.id, "file_name": item.name, "size": item.size,
              "download_url": "https://pdds.quark.cn/file?sign=private"}
    result.update(changes)
    return result


class LargeBody(httpx.AsyncByteStream):
    block = b"0123456789ABCDEF" * 65536  # 1 MiB

    async def __aiter__(self):
        for _ in range(53):
            yield self.block


class QuarkDownloadRouteTests(unittest.IsolatedAsyncioTestCase):
    async def api(self, handler, events=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        api = QuarkWebApi(client, events.append if events is not None else None)
        api.authenticated, api.session_id = True, "test-session"
        api.client.cookies.set("__puus", "scoped-test", domain=".quark.cn", path="/")
        api.client.cookies.set("private-other", "do-not-send", domain="other.invalid", path="/")
        item = CloudFile("file1", "quark", "/video.mp4", False, 53 * len(LargeBody.block))
        api._files[item.id] = item
        return api, item

    async def test_large_file_old_browser_fails_desktop_stream_succeeds(self):
        calls, events = [], []
        digest = hashlib.md5()
        for _ in range(53):
            digest.update(LargeBody.block)

        def handler(request):
            calls.append(request)
            if request.url.path.endswith("/file/download"):
                if request.headers["user-agent"] == UA:
                    return httpx.Response(400, json={"status": 400, "code": 23018})
                self.assertEqual(request.headers["user-agent"], DOWNLOAD_UA)
                self.assertEqual(json.loads(request.content), {"fids": ["file1"]})
                self.assertEqual(request.url.params["fr"], "pc")
                return ok([ticket(item, md5=digest.hexdigest())])
            self.assertEqual(request.headers["user-agent"], DOWNLOAD_UA)
            self.assertEqual(request.headers["cookie"], "__puus=scoped-test")
            self.assertEqual(request.headers["referer"], PAN + "/")
            if request.url.host == "pdds.quark.cn":
                return httpx.Response(302, headers={"Location": "https://cdn.quark.cn/bytes"})
            self.assertEqual(request.url.host, "cdn.quark.cn")
            return httpx.Response(200, stream=LargeBody(), headers={
                "content-length": str(item.size), "content-type": "video/mp4"})

        api, item = await self.api(handler, events)
        # Regression control: the previous request profile hits the simulated limit.
        with self.assertRaises(QuarkApiError) as error:
            await api._request("POST", DRIVE + "/file/download", "download_link",
                               data={"fids": [item.id]}, user_agent=UA)
        self.assertEqual(error.exception.code, "quark_download_restricted")
        self.assertIn("23018", str(error.exception))
        parent = ROOT / "work/test-runs"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            transfer = DirectDownload(item, folder, api.client, api.download_source,
                                      api.validate_download_url, AsyncMock())
            saved = await transfer.run()
            self.assertEqual(transfer.state, "completed")
            self.assertEqual(saved.stat().st_size, item.size)
            with saved.open('rb') as stream:
                self.assertEqual(hashlib.file_digest(stream, 'md5').hexdigest(), digest.hexdigest())
            self.assertTrue(transfer.checksum_verified)
        self.assertEqual(len(calls), 4)
        self.assertEqual(events[-1]["route"], "desktop")
        self.assertNotIn("private", json.dumps(events))

    async def test_share_direct_entry_preserves_tokens_and_cookie_scope(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.url.path, "/1/clouddrive/file/download")
            self.assertEqual(request.headers["user-agent"], DOWNLOAD_UA)
            self.assertEqual(json.loads(request.content), {
                "fids": ["file1"], "pwd_id": "share-id", "stoken": "share-token",
                "fids_token": ["file-token"]})
            self.assertEqual(request.url.params["entry"], "ft")
            self.assertEqual(request.headers["cookie"], "__puus=scoped-test")
            return ok([ticket(item)])
        api, item = await self.api(handler)
        share = QuarkShare("share-id", "share-token", api.session_id)
        share.files[item.id], share.file_tokens[item.id] = item, "file-token"
        source = await api.share_download_source(item, share)
        self.assertEqual(len(calls), 1)
        self.assertEqual(source.headers["User-Agent"], DOWNLOAD_UA)
        self.assertNotIn("Cookie", source.headers)
        self.assertFalse(any("save" in r.url.path for r in calls))

    async def test_restriction_is_contextual_and_not_retried_across_profiles(self):
        for http_status in (200, 400):
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(http_status, json={"status": 400, "code": 23018,
                    "message": "private-token-in-server-error"})
            api, item = await self.api(handler)
            with self.assertRaises(QuarkApiError) as caught:
                await api.get_download(item)
            self.assertEqual(len(calls), 1)
            self.assertEqual(caught.exception.code, "quark_download_restricted")
            self.assertEqual(caught.exception.service_code, 23018)
            self.assertNotIn("分享", str(caught.exception))
            self.assertNotIn("容量", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))
            self.assertTrue(api.authenticated)

    async def test_auth_permission_throttle_and_other_errors_do_not_switch_routes(self):
        for http_status, service_status, code in (
            (401, 401, 31001), (403, 403, 23018), (412, 412, 23018),
            (429, 429, 23018), (200, 403, 23018), (200, 429, 23018), (500, 500, 999),
        ):
            with self.subTest(status=http_status, service_status=service_status):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(http_status, json={"status": service_status, "code": code})
                api, item = await self.api(handler)
                with self.assertRaises(QuarkApiError):
                    await api.get_download(item)
                self.assertEqual(len(calls), 1)

    async def test_bad_file_or_address_never_triggers_another_candidate(self):
        for changes in ({"fid": "other"}, {"size": 0}, {"download_url": "https://other.invalid/private"}):
            calls = []
            def handler(request):
                calls.append(request)
                return ok([ticket(item, **changes)])
            api, item = await self.api(handler)
            with self.assertRaises(QuarkApiError):
                await api.get_download(item)
            self.assertEqual(len(calls), 1)

    async def test_reset_during_route_rejection_cannot_start_next_request(self):
        calls = []
        def handler(request):
            calls.append(request)
            api.reset()
            return httpx.Response(400, json={"status": 400, "code": 23018})
        api, item = await self.api(handler)
        with self.assertRaises(asyncio.CancelledError):
            await api.get_download(item)
        self.assertEqual(len(calls), 1)
        self.assertFalse(api.authenticated)

    async def test_directory_still_uses_browser_identity(self):
        def handler(request):
            self.assertEqual(request.headers["user-agent"], UA)
            return ok({"list": []})
        api, _ = await self.api(handler)
        self.assertEqual(await api.list_files(), [])


if __name__ == "__main__":
    unittest.main()
