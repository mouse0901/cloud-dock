"""Network-free regressions for real-account UI and single-file download."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
from app.baidu_auth import AuthorizedDirectory
from app.baidu_download import download_file, download_url, local_filename
from app.baidu_panel import BaiduPanel
from app.domain import CloudFile
from app.providers.baidu_api import BaiduApi, BaiduApiError, Tokens
from test_ui_smoke import PageStub

ROOT = Path(__file__).resolve().parents[1]
ITEM = CloudFile("123", "baidu", "/apps/云屿/test.txt", size=5)

class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "work" / "test-runs")
        self.folder = Path(self.tmp.name)
        self.progress = AsyncMock()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def meta(self, *, size=5, path=ITEM.path):
        return httpx.Response(200, json={"errno": 0, "list": [
            {"fs_id": 123, "path": path, "isdir": 0, "size": size,
             "dlink": "https://d.pcs.baidu.com/file/abc?sign=test"}]})

    def reader(self, client, token="private-test-token"):
        return AuthorizedDirectory(BaiduApi(client), Tokens(token, "refresh", 300), "云屿")

    async def test_download_bytes_and_same_name_do_not_overwrite(self):
        (self.folder/"test.txt").write_bytes(b"existing")
        requests = []
        def handle(req):
            requests.append(req)
            if req.url.host == "pan.baidu.com":
                self.assertEqual(req.url.params["fsids"], "[123]")
                self.assertEqual(req.url.params["dlink"], "1")
                return self.meta()
            self.assertEqual(req.headers["user-agent"], "pan.baidu.com")
            self.assertEqual(req.url.params["access_token"], "private-test-token")
            self.assertEqual(req.url.params["sign"], "test")
            return httpx.Response(200, content=b"hello")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            saved = await download_file(self.reader(client), ITEM, self.folder, self.progress)
        self.assertEqual(saved.read_bytes(), b"hello")
        self.assertEqual((self.folder/"test.txt").read_bytes(), b"existing")
        self.assertFalse(list(self.folder.glob("*.part")))
        self.assertEqual(len(requests), 2)

    async def test_untrusted_redirect_never_receives_token(self):
        hosts = []
        def handle(req):
            hosts.append(req.url.host)
            if req.url.host == "pan.baidu.com": return self.meta()
            return httpx.Response(302, headers={"location": "https://attacker.example/file"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(BaiduApiError):
                await download_file(self.reader(client), ITEM, self.folder, self.progress)
        self.assertNotIn("attacker.example", hosts)
        self.assertEqual(list(self.folder.iterdir()), [])

    async def test_trusted_redirect_preserves_authorization(self):
        def handle(req):
            if req.url.host == "pan.baidu.com": return self.meta()
            if req.url.host == "d.pcs.baidu.com":
                return httpx.Response(302, headers={"location": "https://cdn.baidupcs.com/data?sign=second"})
            self.assertEqual(req.url.params["access_token"], "private-test-token")
            self.assertEqual(req.url.params["sign"], "second")
            return httpx.Response(200, content=b"hello")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            saved = await download_file(self.reader(client), ITEM, self.folder, self.progress)
        self.assertEqual(saved.read_bytes(), b"hello")

    async def test_truncated_body_not_reported_as_completed(self):
        def handle(req):
            return self.meta() if req.url.host == "pan.baidu.com" else httpx.Response(200, content=b"he")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(BaiduApiError) as result:
                await download_file(self.reader(client), ITEM, self.folder, self.progress)
        self.assertEqual(result.exception.code, "download_size")
        self.assertEqual(list(self.folder.iterdir()), [])

    async def test_api_error_body_not_saved_as_file(self):
        payload = b'{"errno":31360,"request_id":"123"}'
        def handle(req):
            if req.url.host == "pan.baidu.com": return self.meta(size=len(payload))
            return httpx.Response(200, content=payload, headers={"content-type":"application/json"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(BaiduApiError) as result:
                await download_file(self.reader(client), ITEM, self.folder, self.progress)
        self.assertEqual(result.exception.code, "download_api")
        self.assertEqual(list(self.folder.iterdir()), [])

    async def test_metadata_for_another_file_is_rejected(self):
        requests = []
        def handle(req):
            requests.append(req)
            return self.meta(path="/apps/other/private.txt")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(BaiduApiError):
                await download_file(self.reader(client), ITEM, self.folder, self.progress)
        self.assertEqual(len(requests), 1)
        self.assertEqual(list(self.folder.iterdir()), [])

    async def test_out_of_scope_and_folders_never_request_metadata(self):
        api = AsyncMock()
        reader = AuthorizedDirectory(api, Tokens("a", "b", 300), "云屿")
        for item in [CloudFile("1","baidu","/private.txt"), CloudFile("2","baidu","/apps/云屿/folder",True)]:
            with self.assertRaises(ValueError):
                await download_file(reader, item, self.folder, self.progress)
        api.download_info.assert_not_awaited()

    async def test_cancel_cleans_partial_file(self):
        started = asyncio.Event()
        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"h" * (256 * 1024)
                started.set()
                await asyncio.Event().wait()
        def handle(req):
            if req.url.host == "pan.baidu.com": return self.meta(size=1024*1024)
            return httpx.Response(200, stream=SlowStream())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            task = asyncio.create_task(download_file(self.reader(client), ITEM, self.folder, self.progress))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_windows_paths_and_http_hosts_are_rejected_or_sanitized(self):
        name = local_filename("../CON:bad?.txt")
        self.assertEqual(Path(name).name, name)
        self.assertNotIn(":", name)
        self.assertNotIn("?", name)
        for address in ["http://d.pcs.baidu.com/file", "https://baidu.com.evil.example/file",
                        "https://user:pass@d.pcs.baidu.com/file", "https://127.0.0.1/file"]:
            with self.assertRaises(BaiduApiError): download_url(address)


class LivePanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.panel = BaiduPanel(PageStub(), client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        self.panel.api = AsyncMock()
        self.panel.reader = AuthorizedDirectory(self.panel.api, Tokens("account-A","r",300), "云屿")
        self.panel.download_destination = ROOT/"work"/"unused-download-test"

    async def asyncTearDown(self):
        await self.panel.shutdown()

    async def test_empty_folder_success_and_navigation(self):
        self.panel.api.list_files.return_value = []
        await self.panel.load_directory("/apps/云屿/测试文件", 0)
        self.assertIn("读取成功", self.panel.status.value)
        self.assertFalse(self.panel.parent_button.disabled)
        await self.panel.parent(None)
        self.assertEqual(self.panel.path, "/apps/云屿")
        self.assertTrue(self.panel.parent_button.disabled)
        self.assertTrue(self.panel.next_button.disabled)

    async def test_logout_discards_late_directory_response(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(*a):
            started.set()
            await release.wait()
            return [CloudFile("1","baidu","/apps/云屿/private-A.txt")]
        self.panel.api.list_files.side_effect = delayed
        task = asyncio.create_task(self.panel.load_directory("/apps/云屿",0))
        await started.wait()
        await self.panel.cancel()
        release.set()
        await task
        self.assertIsNone(self.panel.reader)
        self.assertEqual(self.panel.files.controls, [])
        self.assertEqual(self.panel.path_text.value, "")
        self.assertFalse(self.panel.results.visible)

    async def test_older_page_cannot_replace_newer_page(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(token,path,start,limit):
            if path.endswith("/old"):
                started.set()
                await release.wait()
                return [CloudFile("1","baidu",path+"/old.txt")]
            return [CloudFile("2","baidu",path+"/new.txt")]
        self.panel.api.list_files.side_effect = delayed
        task = asyncio.create_task(self.panel.load_directory("/apps/云屿/old",0))
        await started.wait()
        await self.panel.load_directory("/apps/云屿/new",0)
        release.set()
        await task
        self.assertEqual(self.panel.files.controls[0].title.value, "new.txt")
        self.assertEqual(self.panel.path_text.value, "/apps/云屿/new")

    async def test_two_panels_keep_distinct_account_tokens(self):
        other = BaiduPanel(PageStub(), client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))))
        other.api = AsyncMock()
        other.api.list_files.return_value = []
        other.reader = AuthorizedDirectory(other.api,Tokens("account-B","r2",300),"云屿")
        self.panel.api.list_files.return_value = []
        try:
            await self.panel.load_directory("/apps/云屿",0)
            await other.load_directory("/apps/云屿",0)
            self.assertEqual(self.panel.api.list_files.await_args.args[0], "account-A")
            self.assertEqual(other.api.list_files.await_args.args[0], "account-B")
            await self.panel.cancel()
            self.assertEqual(other.reader.tokens.access_token, "account-B")
        finally:
            await other.shutdown()

if __name__ == "__main__": unittest.main()
