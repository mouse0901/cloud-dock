"""Direct web QR flow: mocked authenticated stages, no personal credentials."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from app.baidu_web_panel import BaiduWebPanel
from app.providers.baidu_web import BaiduWebApi, parse_jsonp
from app.providers.baidu_api import BaiduApiError
from test_ui_smoke import PageStub

PNG = b"\x89PNG\r\n\x1a\nfixture"
COOKIE = "BDUSS=fixture-session; Domain=.baidu.com; Path=/; Secure"
ROW = {"fs_id": 123, "path": "/existing.txt", "isdir": 0, "size": 5}

class WebApiTests(unittest.IsolatedAsyncioTestCase):
    def test_jsonp_only_parses_data(self):
        self.assertEqual(parse_jsonp('cloudDockCallback({"errno":0});'), {"errno":0})
        for payload in ['evil({"errno":0});', 'cloudDockCallback({"errno":0});evil()',
                        'cloudDockCallback((function(){return {}})())', '[1,2]']:
            with self.assertRaises(BaiduApiError):
                parse_jsonp(payload)

    def test_passport_mixed_quotes_preserve_user_text_and_escapes(self):
        payload = r"""cloudDockCallback({
            "errInfo":{"no":"0"}, 'data': {
                'name': 'O\'Brien "中文"',
                "escaped": "apostrophe\' / \x41 \u4e2d \\ folder",
                'contents': 'null, true, }, alert(1)'
            }
        })"""
        result = parse_jsonp(payload)
        self.assertEqual(result["data"]["name"], """O'Brien "中文" """.rstrip())
        self.assertEqual(result["data"]["escaped"], "apostrophe' / A 中 \\ folder")
        self.assertEqual(result["data"]["contents"], "null, true, }, alert(1)")

    def test_mixed_quote_parser_still_rejects_code_and_invalid_escapes(self):
        for payload in [
            "cloudDockCallback({'ok': (function(){return 1})()})",
            "cloudDockCallback({'ok': alert(1)})",
            "cloudDockCallback({'ok': 'a' + 'b'})",
            "cloudDockCallback({'ok': 'unfinished})",
            r"cloudDockCallback({'ok': '\xGG'})",
            "cloudDockCallback({'ok':1});evil()",
        ]:
            with self.assertRaises(BaiduApiError): parse_jsonp(payload)

    async def test_mixed_quote_success_and_stage_diagnostics_hide_credentials(self):
        events=[]
        def handle(req):
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,headers={"set-cookie":COOKIE},text=
                    """cloudDockCallback({"errInfo":{"no":"0"},'data':{'token':'fixture-private-value'}})""")
            if req.url.path == "/disk/main":
                return httpx.Response(200,text='{"bdstoken":"fixture-csrf"}')
            return httpx.Response(200,json={"errno":0,"list":[]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client,events.append)
            await api.finish_login("fixture-confirmation")
            self.assertTrue(api.authenticated)
        self.assertEqual(events[0]["stage"],"finish")
        self.assertEqual(events[0]["service_code"],"0")
        self.assertTrue(events[0]["parsed"])
        encoded=json.dumps(events)
        for private in ["fixture-private-value","fixture-confirmation","fixture-session","fixture-csrf"]:
            self.assertNotIn(private,encoded)

    async def test_format_error_identifies_stage_without_response_text(self):
        events=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:
                httpx.Response(200,text="<html>private-account-value</html>"))) as client:
            api=BaiduWebApi(client,events.append)
            with self.assertRaises(BaiduApiError) as result:
                await api.finish_login("fixture")
            self.assertIn("尚未收到网盘会话",str(result.exception))
            self.assertNotIn("private-account-value",str(result.exception))
            self.assertNotIn("private-account-value",json.dumps(events))
            self.assertEqual(events[0]["format"],"html")
            self.assertFalse(events[0]["parsed"])

    async def test_web_callback_format_does_not_block_verified_session(self):
        requested=[]
        def handle(req):
            requested.append(req.url.path)
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,headers={"set-cookie":COOKIE},
                    text="cloudDockCallback({data:{ignored:encodeURIComponent('fixture')}})")
            if req.url.path == "/disk/main": return httpx.Response(200,text="")
            return httpx.Response(200,json={"errno":0,"list":[ROW]})
        events=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client,events.append)
            await api.finish_login("fixture")
            self.assertTrue(api.authenticated)
            rows=await api.list_files("/")
            self.assertEqual(rows[0].path,ROW["path"])
            self.assertEqual(requested.count("/api/list"),1)
            self.assertEqual(events[-1]["outcome"],"verified")
            self.assertFalse(events[0]["parsed"])

    async def test_cookie_without_valid_directory_never_counts_as_login(self):
        def handle(req):
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,headers={"set-cookie":COOKIE},text="web callback")
            if req.url.path == "/disk/main": return httpx.Response(200,text="")
            return httpx.Response(200,json={"errno":-6})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)
            self.assertTrue(api.session_candidate)
            self.assertEqual(len(client.cookies),1)
            self.assertIsNone(api._initial_root)
            api.reset()
            self.assertFalse(api.session_candidate)
            self.assertEqual(len(client.cookies),0)

    async def test_passport_only_cookie_is_not_a_pan_session(self):
        seen=[]
        def handle(req):
            seen.append(req.url.path)
            return httpx.Response(200,headers={"set-cookie":
                "BDUSS=fixture-session; Domain=passport.baidu.com; Path=/; Secure"},
                json={"errInfo":{"no":0}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)
        self.assertEqual(seen,["/v3/login/main/qrbdusslogin"])

    async def test_service_rejection_is_not_overridden_by_cookie(self):
        seen=[]
        def handle(req):
            seen.append(req.url.path)
            return httpx.Response(200,headers={"set-cookie":COOKIE},
                json={"errInfo":{"no":400023}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)
            self.assertEqual(len(client.cookies),0)
        self.assertEqual(seen,["/v3/login/main/qrbdusslogin"])

    async def test_missing_list_cannot_verify_login(self):
        def handle(req):
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,headers={"set-cookie":COOKIE},json={"errInfo":{"no":0}})
            if req.url.path == "/disk/main": return httpx.Response(200,text="")
            return httpx.Response(200,json={"errno":0})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)

    async def test_qr_uses_no_developer_credentials(self):
        seen = []
        def handle(req):
            seen.append(req)
            if req.url.path.endswith("getqrcode"):
                self.assertNotIn("client_id", req.url.params)
                self.assertNotIn("client_secret", req.url.params)
                return httpx.Response(200,json={"errno":0,"sign":"fixture-sign",
                    "imgurl":"passport.baidu.com/qr.png"})
            return httpx.Response(200,content=PNG)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            qr = await BaiduWebApi(client).create_qr()
            self.assertEqual(qr.image,PNG)
            self.assertNotIn(qr.sign,repr(qr))
        self.assertEqual(len(seen),2)

    async def test_untrusted_image_never_requested(self):
        seen = []
        def handle(req):
            seen.append(req.url.host)
            return httpx.Response(200,json={"errno":0,"sign":"fixture-sign",
                "imgurl":"https://attacker.example/qr.png"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with self.assertRaises(BaiduApiError): await BaiduWebApi(client).create_qr()
        self.assertEqual(seen,["passport.baidu.com"])

    async def test_scan_wait_confirm_and_cancel_states(self):
        bodies = [{"errno":1},
                  {"errno":0,"channel_v":json.dumps({"status":1})},
                  {"errno":0,"channel_v":json.dumps({"status":0,"v":"fixture-confirmation"})},
                  {"errno":0,"channel_v":{"status":2}}]
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(200,json=bodies.pop(0)))) as client:
            api = BaiduWebApi(client)
            states = [await api.poll("fixture") for _ in range(4)]
        self.assertEqual([s.state for s in states],["waiting","scanned","confirmed","cancelled"])
        self.assertNotIn("fixture-confirmation",repr(states[2]))

    async def test_confirmation_requires_session_and_lists_existing_root(self):
        seen = []
        def handle(req):
            seen.append(req.url.path)
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,headers={"set-cookie":COOKIE},
                    text='cloudDockCallback({"errInfo":{"no":"0"}})')
            if req.url.path == "/disk/main":
                return httpx.Response(200,text='{"bdstoken":"fixture-csrf"}')
            self.assertIn("BDUSS=fixture-session",req.headers["cookie"])
            self.assertEqual(req.url.params["dir"],"/")
            self.assertEqual(req.url.params["page"],"1")
            self.assertNotIn("bdstoken",req.url.params)
            return httpx.Response(200,json={"errno":0,"list":[ROW]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api = BaiduWebApi(client)
            await api.finish_login("fixture-confirmation")
            rows = await api.list_files("/")
            self.assertEqual(rows[0].path,"/existing.txt")
            self.assertFalse(rows[0].is_dir)
            api.reset()
            self.assertFalse(api.authenticated)
            self.assertEqual(len(client.cookies),0)
        self.assertEqual(seen,["/v3/login/main/qrbdusslogin","/api/list"])

    async def test_missing_cookie_is_not_success(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req: httpx.Response(200,json={"errInfo":{"no":0}}))) as client:
            api = BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)

    async def test_verification_error_clears_partial_session(self):
        for response in [{"errInfo":{"no":400023}}, {"errInfo":"unexpected"}]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda req: httpx.Response(200,json=response,headers={"set-cookie":COOKIE}))) as client:
                api = BaiduWebApi(client)
                with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
                self.assertEqual(len(client.cookies),0)
                self.assertFalse(api.authenticated)

    async def test_cancel_during_verification_clears_session(self):
        started = asyncio.Event()
        async def handle(req):
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            started.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api = BaiduWebApi(client)
            task = asyncio.create_task(api.finish_login("fixture"))
            await asyncio.wait_for(started.wait(),1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(api.authenticated)
            self.assertEqual(len(client.cookies),0)

    async def test_landing_redirect_cannot_leak_session(self):
        seen=[]
        def handle(req):
            seen.append(req.url.host)
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            if req.url.path == "/api/list":
                return httpx.Response(200,json={"errno":-6})
            return httpx.Response(302,headers={"location":"https://attacker.example/"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)
            self.assertTrue(api.session_candidate)
            api.reset()
            self.assertEqual(len(client.cookies),0)
        self.assertNotIn("attacker.example",seen)

    async def test_directory_success_does_not_visit_landing_redirect(self):
        seen=[]
        def handle(req):
            seen.append(req.url.path)
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            if req.url.path == "/api/list":
                return httpx.Response(200,json={"errno":0,"list":[]})
            return httpx.Response(302,headers={"location":"https://attacker.example/"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            await api.finish_login("fixture")
            self.assertTrue(api.authenticated)
        self.assertNotIn("/disk/main",seen)

    async def test_optional_pan_fragment_redirect_drops_fragment_and_recovers(self):
        seen=[]
        def handle(req):
            seen.append(str(req.url))
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            if req.url.path == "/api/list":
                return httpx.Response(200,json={"errno":0,"list":[]} if req.url.params.get("bdstoken")=="fixture-csrf" else {"errno":-6})
            if req.url.path == "/disk/main":
                return httpx.Response(302,headers={"location":"/disk/home#/index?private=fixture"})
            self.assertEqual(req.url.path,"/disk/home")
            self.assertFalse(req.url.fragment)
            return httpx.Response(200,text='{"bdstoken":"fixture-csrf"}')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            await api.finish_login("fixture")
            self.assertTrue(api.authenticated)
        self.assertFalse(any("#" in address for address in seen))

    async def test_connection_retry_reuses_approved_cookie_without_login_exchange(self):
        verified=False
        exchanges=0
        def handle(req):
            nonlocal exchanges
            if req.url.path.endswith("qrbdusslogin"):
                exchanges+=1
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            if req.url.path == "/api/list":
                self.assertIn("BDUSS=fixture-session",req.headers["cookie"])
                return httpx.Response(200,json={"errno":0,"list":[ROW]} if verified else {"errno":-6})
            return httpx.Response(200,text="")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError): await api.finish_login("fixture")
            self.assertFalse(api.authenticated)
            verified=True
            await api.resume_login()
            self.assertTrue(api.authenticated)
            self.assertEqual(exchanges,1)
            self.assertEqual((await api.list_files("/"))[0].path,ROW["path"])

    async def test_directory_rejects_missing_or_unrelated_results(self):
        for body in [{"errno":0},{"errno":0,"list":[dict(ROW,path="/elsewhere/private.txt")]},
                     {"errno":0,"list":[dict(ROW,isdir="invalid")]},{"errno":-6}]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda req: httpx.Response(200,json=body))) as client:
                api=BaiduWebApi(client);api.authenticated=True
                with self.assertRaises(BaiduApiError): await api.list_files("/")

    async def test_network_error_does_not_print_confirmation(self):
        def handle(req):
            raise httpx.ConnectError("private fixture-confirmation",request=req)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(BaiduApiError) as error: await api.finish_login("fixture-confirmation")
            self.assertNotIn("fixture",str(error.exception))

    async def test_accounts_have_separate_cookie_jars(self):
        async with httpx.AsyncClient() as first, httpx.AsyncClient() as second:
            first.cookies.set("BDUSS","account-a",domain=".baidu.com")
            second.cookies.set("BDUSS","account-b",domain=".baidu.com")
            BaiduWebApi(first).reset()
            self.assertEqual(second.cookies.get("BDUSS"),"account-b")

class WebPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_form_and_successful_flow_enables_real_download(self):
        def handle(req):
            if req.url.path.endswith("getqrcode"):
                return httpx.Response(200,json={"errno":0,"sign":"fixture","imgurl":"passport.baidu.com/qr.png"})
            if req.url.path == "/qr.png": return httpx.Response(200,content=PNG)
            if req.url.path.endswith("unicast"):
                return httpx.Response(200,json={"errno":0,"channel_v":{"status":0,"v":"fixture"}})
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            if req.url.path=="/disk/main": return httpx.Response(200,text="")
            return httpx.Response(200,json={"errno":0,"list":[ROW]})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        panel=BaiduWebPanel(PageStub(),client=client)
        try:
            self.assertNotIn(panel.form,panel.view.controls)
            with patch("app.baidu_web_panel.asyncio.sleep",new=AsyncMock()):
                await panel.begin()
                await panel.task
            self.assertEqual(panel.path,"/")
            self.assertIn("目录读取成功",panel.status.value)
            self.assertIsNotNone(panel.files.controls[0].trailing)
            self.assertIsNotNone(panel.download_destination)
            self.assertIsNone(panel.qr.content)
            await panel.cancel()
            self.assertEqual(len(client.cookies),0)
            self.assertFalse(panel.results.visible)
            self.assertEqual(panel.files.controls,[])
        finally: await panel.shutdown()

    async def test_failed_connection_shows_retry_and_logout_clears_candidate(self):
        verified=False
        def handle(req):
            if req.url.path.endswith("getqrcode"):
                return httpx.Response(200,json={"errno":0,"sign":"fixture","imgurl":"passport.baidu.com/qr.png"})
            if req.url.path == "/qr.png": return httpx.Response(200,content=PNG)
            if req.url.path.endswith("unicast"):
                return httpx.Response(200,json={"errno":0,"channel_v":{"status":0,"v":"fixture"}})
            if req.url.path.endswith("qrbdusslogin"):
                return httpx.Response(200,json={"errInfo":{"no":0}},headers={"set-cookie":COOKIE})
            if req.url.path=="/api/list":
                return httpx.Response(200,json={"errno":0,"list":[ROW]} if verified else {"errno":-6})
            return httpx.Response(302,headers={"location":"https://attacker.example/"})
        panel=BaiduWebPanel(PageStub(),client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        try:
            with patch("app.baidu_web_panel.asyncio.sleep",new=AsyncMock()):
                await panel.begin()
                await panel.task
            self.assertTrue(panel.retry_button.visible)
            self.assertIsNone(panel.reader)
            self.assertTrue(panel.api.session_candidate)
            verified=True
            await panel.retry_connection()
            await panel.task
            self.assertIn("目录读取成功",panel.status.value)
            self.assertFalse(panel.retry_button.visible)
            await panel.cancel()
            self.assertFalse(panel.api.session_candidate)
            self.assertFalse(panel.retry_button.visible)
            self.assertEqual(len(panel.client.cookies),0)
        finally: await panel.shutdown()

    async def test_refresh_and_logout_cancel_active_poll(self):
        started=asyncio.Event()
        async def handle(req):
            if req.url.path.endswith("getqrcode"):
                return httpx.Response(200,json={"errno":0,"sign":"fixture","imgurl":"passport.baidu.com/qr.png"})
            if req.url.path == "/qr.png": return httpx.Response(200,content=PNG)
            started.set()
            await asyncio.Event().wait()
        panel=BaiduWebPanel(PageStub(),client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        try:
            with patch("app.baidu_web_panel.asyncio.sleep",new=AsyncMock()):
                await panel.begin()
                previous=panel.task
                await asyncio.wait_for(started.wait(),1)
                await panel.begin()
                self.assertTrue(previous.cancelled())
                await panel.cancel()
            self.assertIsNone(panel.task)
            self.assertIsNone(panel.reader)
            self.assertIsNone(panel.qr.content)
            self.assertFalse(panel.api.authenticated)
        finally: await panel.shutdown()

if __name__=="__main__": unittest.main()
