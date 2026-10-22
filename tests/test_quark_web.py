import asyncio
import json
import unittest

import httpx

from app.domain import CloudFile
from app.providers.quark_web import (QuarkWebApi, QuarkApiError, QuarkShare, QuarkQr,
    QuarkDownload, DRIVE, SHARE, UOP, PAN, MAX_RESPONSE, checked_url, parse_share_link)


def row(fid="f1", name="test.bin", size=4, directory=False, parent="0", **extra):
    return dict(fid=fid, file_name=name, dir=directory, size=size, pdir_fid=parent, updated_at=0, **extra)


def success(data):
    return httpx.Response(200, json={"status": 200, "code": 0, "data": data})


class QuarkInputTests(unittest.TestCase):
    def test_share_text_password_and_fragment(self):
        self.assertEqual(parse_share_link("分享 https://pan.quark.cn/s/abc123def456#/list/share 密码：k8N2"), ("abc123def456", "k8N2"))
        self.assertEqual(parse_share_link("https://pan.quark.cn/s/abc123def456?pwd=Ab12", "ZZ11"), ("abc123def456", "ZZ11"))

    def test_share_rejects_deceptive_origins(self):
        for value in ("https://pan.quark.cn.evil.invalid/s/abc123def456", "https://pan.quark.cn@evil.invalid/s/abc123def456",
                      "http://pan.quark.cn/s/abc123def456", "https://pan.quark.cn:444/s/abc123def456", "https://evil.invalid/"):
            with self.subTest(value=value), self.assertRaises(QuarkApiError):
                parse_share_link(value)

    def test_download_addresses_fail_closed(self):
        for value in ("http://pdds.quark.cn/a", "https://quark.cn.evil.invalid/a", "https://127.0.0.1/a",
                      "https://user@pdds.quark.cn/a", "https://pdds.quark.cn:444/a", "https://pdds.quark.cn/a#b",
                      "https://pdds.quark.cn/a\n", "https://unknown-cdn.invalid/a"):
            with self.subTest(value=value), self.assertRaises(QuarkApiError):
                checked_url(value, download=True)
        self.assertEqual(checked_url("https://pdds.quark.cn/file?token=test", download=True), "https://pdds.quark.cn/file?token=test")
        with self.assertRaises(QuarkApiError):
            checked_url("https://other.quark.cn/api")

    def test_credential_values_not_in_repr(self):
        for obj in (QuarkQr("sensitive-token", "https://su.quark.cn/?token=sensitive-token"),
                    QuarkShare("shareid", "sensitive-token", "sid"),
                    QuarkDownload("https://pdds.quark.cn/?token=sensitive-token", {"Cookie": "sensitive-token"}, 1, "a", "a")):
            self.assertNotIn("sensitive-token", repr(obj))


class QuarkApiTests(unittest.IsolatedAsyncioTestCase):
    async def make_api(self, handler, diagnostic=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
        self.addAsyncCleanup(client.aclose)
        return QuarkWebApi(client, diagnostic)

    async def logged_api(self, handler):
        api = await self.make_api(handler)
        api.authenticated = True
        api.session_id = "session-one"
        return api

    async def test_qr_create_and_waiting_confirmed_expired(self):
        responses = [
            {"status": 2000000, "data": {"members": {"token": "qr-secret"}}},
            {"status": 50004001},
            {"status": 2000000, "data": {"members": {"service_ticket": "st-secret"}}},
            {"status": 50004002},
        ]
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.url.host, "uop.quark.cn")
            self.assertEqual(request.url.params["client_id"], "532")
            self.assertEqual(request.url.params["v"], "1.2")
            return httpx.Response(200, json=responses.pop(0))
        api = await self.make_api(handler)
        qr = await api.create_qr()
        self.assertEqual(qr.token, "qr-secret")
        self.assertTrue(qr.url.startswith("https://su.quark.cn/4_eMHBJ?"))
        self.assertEqual((await api.poll(qr.token)).state, "waiting")
        self.assertEqual((await api.poll(qr.token)).confirmation, "st-secret")
        self.assertEqual((await api.poll(qr.token)).state, "expired")
        self.assertEqual(len(calls), 4)

    async def test_unknown_or_missing_confirmation_not_success(self):
        for response in ({"status": 2000000, "data": {"members": {}}}, {"status": 50009999}):
            api = await self.make_api(lambda _: httpx.Response(200, json=response))
            with self.assertRaises(QuarkApiError):
                await api.poll("token")
            self.assertFalse(api.authenticated)

    async def test_finish_requires_scoped_cookie_and_real_directory(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path == "/account/info":
                self.assertEqual(request.url.params["st"], "ticket")
                return httpx.Response(200, json={"success": True, "data": {"nickname": "not_logged"}},
                                      headers={"set-cookie": "__pus=secret; Domain=.quark.cn; Path=/; Secure"})
            self.assertIn("__pus=secret", request.headers["cookie"])
            return success({"list": [row(directory=True, name="folder")]})
        api = await self.make_api(handler)
        await api.finish_login("ticket")
        self.assertTrue(api.authenticated)
        self.assertTrue(api.session_candidate)
        self.assertTrue(api.session_id)
        files = await api.list_files()
        self.assertEqual(files[0].name, "folder")
        self.assertEqual(len(calls), 2)  # First root uses verified result.
        self.assertEqual(api._directories["/folder"], "f1")

    async def test_pan_only_cookie_cannot_authenticate_drive(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"success": True, "data": {}},
                                  headers={"set-cookie": "__pus=secret; Path=/; Secure"})
        api = await self.make_api(handler)
        with self.assertRaises(QuarkApiError):
            await api.finish_login("ticket")
        self.assertFalse(api.authenticated)
        self.assertEqual(len(calls), 1)
        self.assertEqual(list(api.client.cookies.jar), [])

    async def test_directory_failure_retains_only_candidate_for_retry(self):
        count = 0
        def handler(request):
            nonlocal count
            if request.url.path == "/account/info":
                return httpx.Response(200, json={"success": True, "data": {}},
                                      headers={"set-cookie": "__pus=secret; Domain=.quark.cn; Path=/; Secure"})
            count += 1
            if count == 1:
                return success({"wrong": []})
            return success({"list": []})
        api = await self.make_api(handler)
        with self.assertRaises(QuarkApiError):
            await api.finish_login("ticket")
        self.assertTrue(api.session_candidate)
        self.assertFalse(api.authenticated)
        await api.resume_login()
        self.assertTrue(api.authenticated)
        self.assertEqual(count, 2)

    async def test_cancel_during_verification_clears_session(self):
        def handler(request):
            if request.url.path == "/account/info":
                return httpx.Response(200, json={"success": True, "data": {}},
                                      headers={"set-cookie": "__pus=secret; Domain=.quark.cn; Path=/; Secure"})
            raise asyncio.CancelledError()
        api = await self.make_api(handler)
        with self.assertRaises(asyncio.CancelledError):
            await api.finish_login("ticket")
        self.assertFalse(api.session_candidate)
        self.assertFalse(api.authenticated)
        self.assertEqual(list(api.client.cookies.jar), [])

    async def test_directory_children_use_server_id_not_path(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.params["pdir_fid"] == "0":
                return success({"list": [row("folder-id", "资料", directory=True)]})
            return success({"list": [row("file-id", "你好.txt", parent="folder-id")]})
        api = await self.logged_api(handler)
        await api.list_files()
        rows = await api.list_files("/资料", 2)
        self.assertEqual(rows[0].path, "/资料/你好.txt")
        self.assertEqual(calls[1].url.params["pdir_fid"], "folder-id")
        self.assertEqual(calls[1].url.params["_page"], "3")

    async def test_malformed_directory_is_atomic(self):
        api = await self.logged_api(lambda _: success({"list": [row("good", "safe", directory=True), row("bad", "../escape")]}))
        with self.assertRaises(QuarkApiError):
            await api.list_files()
        self.assertEqual(api._directories, {"/": "0"})
        self.assertEqual(api._files, {})

    async def test_wrong_parent_and_negative_size_rejected(self):
        for item in (row(parent="wrong-parent"), row(size=-1), row(size="4"), row(directory="false")):
            api = await self.logged_api(lambda _: success({"list": [item]}))
            with self.assertRaises(QuarkApiError):
                await api.list_files()

    async def test_unknown_path_does_not_make_request(self):
        api = await self.logged_api(lambda _: self.fail("should not request"))
        with self.assertRaises(QuarkApiError):
            await api.list_files("/unknown")

    async def test_share_read_and_direct_download_never_save(self):
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path.endswith("sharepage/token"):
                self.assertEqual(request.url.host, "drive-h.quark.cn")
                self.assertEqual(json.loads(request.content)["passcode"], "Ab12")
                return success({"stoken": "share-token"})
            if request.url.path.endswith("sharepage/detail"):
                self.assertEqual(request.url.params["stoken"], "share-token")
                return success({"list": [row(share_fid_token="file-token")]})
            self.assertEqual(request.url.path, "/1/clouddrive/file/download")
            self.assertEqual(json.loads(request.content), {"fids": ["f1"], "pwd_id": "abc123def456",
                                                         "stoken": "share-token", "fids_token": ["file-token"]})
            return success([dict(fid="f1", file_name="test.bin", size=4, download_url="https://pdds.quark.cn/data?token=secret")])
        api = await self.logged_api(handler)
        api.client.cookies.set("__pus", "scoped", domain=".quark.cn", path="/")
        api.client.cookies.set("unrelated", "never-send", domain="other.invalid", path="/")
        share = await api.acquire_share("https://pan.quark.cn/s/abc123def456?pwd=Ab12")
        files = await api.list_share(share)
        ticket = await api.get_download(files[0], share=share)
        self.assertEqual(ticket.size, 4)
        self.assertNotIn("Cookie", ticket.headers)
        self.assertEqual(api.client.build_request("GET", ticket.url).headers["cookie"], "__pus=scoped")
        self.assertEqual(len(calls), 3)
        self.assertFalse(any("save" in r.url.path for r in calls))

    async def test_share_root_accepts_original_owner_parent_but_own_root_does_not(self):
        root_file = row(parent="owners-original-folder", share_fid_token="file-token")
        def handler(request):
            if request.url.path.endswith("sharepage/token"):
                return success({"stoken": "share-token"})
            return success({"list": [root_file]})
        api = await self.logged_api(handler)
        share = await api.acquire_share("https://pan.quark.cn/s/abc123def456")
        files = await api.list_share(share)
        self.assertEqual(len(files), 1)
        self.assertEqual(share.file_tokens[files[0].id], "file-token")
        with self.assertRaises(QuarkApiError):
            await api.list_files()

    async def test_download_rejects_wrong_file_size_and_unsafe_host(self):
        for override in ({"fid": "other"}, {"size": 5}, {"file_name": "other.bin"},
                         {"download_url": "https://evil.invalid/file"}):
            data = dict(fid="f1", file_name="test.bin", size=4, download_url="https://pdds.quark.cn/file")
            data.update(override)
            api = await self.logged_api(lambda _: success([data]))
            item = CloudFile("f1", "quark", "/test.bin", False, 4)
            api._files[item.id] = item
            with self.assertRaises(QuarkApiError):
                await api.get_download(item)

    async def test_own_file_download_omits_share_tokens(self):
        def handler(request):
            self.assertEqual(json.loads(request.content), {"fids": ["f1"]})
            return success([dict(fid="f1", file_name="test.bin", download_url="https://pdds.quark.cn/file")])
        api = await self.logged_api(handler)
        item = CloudFile("f1", "quark", "/test.bin", False, 4)
        api._files[item.id] = item
        ticket = await api.download_source(item)
        self.assertEqual(ticket.size, 4)
        self.assertNotIn("Cookie", ticket.headers)
        self.assertEqual(api.validate_download_url(ticket.url), ticket.url)

    async def test_stale_share_blocked_after_account_change(self):
        api = await self.logged_api(lambda _: self.fail("must not request stale share"))
        share = QuarkShare("abc123def456", "stoken", api.session_id)
        api.reset()
        api.authenticated = True
        api.session_id = "different-user-session"
        with self.assertRaises(QuarkApiError):
            await api.list_share(share)

    async def test_no_automatic_transfer_after_permission_error(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"status": 403, "code": 23018, "message": "secret-server-body"})
        api = await self.logged_api(handler)
        item = CloudFile("f1", "quark", "/test.bin", False, 4)
        api._files[item.id] = item
        with self.assertRaises(QuarkApiError) as ctx:
            await api.get_download(item)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret-server-body", str(ctx.exception))

    async def test_redirect_never_followed_even_client_default_true(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(302, headers={"Location": "https://evil.invalid/?st=secret"})
        api = await self.make_api(handler)
        with self.assertRaises(QuarkApiError) as ctx:
            await api.create_qr()
        self.assertEqual(len(calls), 1)
        self.assertNotIn("secret", str(ctx.exception))

    async def test_network_errors_and_diagnostics_do_not_disclose_secrets(self):
        events = []
        api = await self.make_api(lambda request: httpx.Response(200, json={"status": 50009999,
                                    "message": "raw-secret", "token": "raw-secret"}), events.append)
        with self.assertRaises(QuarkApiError) as ctx:
            await api.poll("request-secret")
        self.assertNotIn("secret", str(ctx.exception))
        self.assertNotIn("secret", json.dumps(events))
        self.assertEqual(events[0]["service_code"], 50009999)
        def error(request):
            raise httpx.ConnectError("https://evil.invalid/?token=raw-secret", request=request)
        api = await self.make_api(error)
        with self.assertRaises(QuarkApiError) as ctx:
            await api.poll("request-secret")
        self.assertNotIn("secret", str(ctx.exception))

    async def test_response_size_limit(self):
        api = await self.make_api(lambda _: httpx.Response(200, content=b"x" * (MAX_RESPONSE+1)))
        with self.assertRaises(QuarkApiError) as ctx:
            await api.create_qr()
        self.assertEqual(ctx.exception.code, "quark_response")

    async def test_unauthorized_directory_revokes_authenticated_flag(self):
        api = await self.logged_api(lambda _: httpx.Response(401, json={"status": 401, "code": 31001}))
        with self.assertRaises(QuarkApiError):
            await api.list_files()
        self.assertFalse(api.authenticated)


    async def test_own_and_share_download_md5_are_advisory_but_preserved(self):
        api = await self.logged_api(lambda _: success([dict(fid="f1", file_name="test.bin", size=4,
            download_url="https://pdds.quark.cn/file", md5="A" * 32)]))
        item = CloudFile("f1", "quark", "/test.bin", False, 4)
        api._files[item.id] = item
        share = QuarkShare("abc123def456", "stoken", api.session_id)
        share.files[item.id] = item
        share.file_tokens[item.id] = "file-token"
        for source in (await api.download_source(item), await api.share_download_source(item, share)):
            self.assertEqual(source.md5, "A" * 32)
            self.assertFalse(source.md5_is_content)
            self.assertEqual(source.size, 4)


if __name__ == "__main__":
    unittest.main()
