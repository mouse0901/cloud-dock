import base64
import json
import unittest
import tempfile
from pathlib import Path
import httpx
from app.domain import CloudFile
from app.direct_download import DownloadError, DirectDownload
from app.providers.baidu_api import BaiduApiError
from app.providers.baidu_web import BaiduWebApi
from app.providers.baidu_web_download import web_signature

ITEM = CloudFile("123", "baidu", "/资料/test.bin", False, 5)
META = {"fs_id":123, "path":ITEM.path, "isdir":0, "size":5,
        "server_mtime":1234, "md5":"opaque-server-identity",
        "dlink":"https://d.pcs.baidu.com/file?ticket=private"}

class BaiduWebDownloadTests(unittest.IsolatedAsyncioTestCase):
    def test_signature_known_rc4_vector(self):
        self.assertEqual(base64.b64decode(web_signature("Key", "Plaintext")).hex(),
                         "bbf316e8d940af0ad3")
        for key,data in [("", "a"), ("x", None), ("中文","x")]:
            with self.assertRaises(BaiduApiError): web_signature(key,data)

    async def test_metadata_bound_to_selection_and_cookie_jar(self):
        def handle(request):
            self.assertEqual(request.url.path, "/api/filemetas")
            self.assertEqual(json.loads(request.url.params["target"]), [ITEM.path])
            self.assertIn("BDUSS=private-cookie", request.headers["cookie"])
            return httpx.Response(200, json={"errno":0,"info":[META]})
        events=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            client.cookies.set("BDUSS","private-cookie",domain=".baidu.com")
            api=BaiduWebApi(client,events.append);api.authenticated=True
            ticket=await api.download_source(ITEM)
            self.assertEqual(ticket.size,5)
            self.assertEqual(ticket.url,META["dlink"])
            self.assertIsNone(ticket.md5)
            self.assertNotIn("private",repr(ticket))
            self.assertNotIn("private",json.dumps(events))

    async def test_metadata_rejects_changed_or_unrelated_file(self):
        for row in [dict(META,fs_id=99),dict(META,path="/other"),
                    dict(META,size=7),dict(META,isdir=1),dict(META,dlink="https://evil.example/file")]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda r:httpx.Response(200,json={"errno":0,"info":[row]}))) as client:
                api=BaiduWebApi(client);api.authenticated=True
                with self.assertRaises(DownloadError):await api.download_source(ITEM)

    async def test_resume_refuses_changed_content_identity(self):
        row=dict(META)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r:httpx.Response(200,json={"errno":0,"info":[row]}))) as client:
            api=BaiduWebApi(client);api.authenticated=True
            resolve=api.new_download_resolver()
            await resolve(ITEM)
            row["server_mtime"]=9999
            with self.assertRaises(DownloadError) as error:await resolve(ITEM)
            self.assertEqual(error.exception.code,"download_changed")
            fresh_resolve=api.new_download_resolver()
            self.assertEqual((await fresh_resolve(ITEM)).size, ITEM.size)
            # A newer task must not clear the original transfer's version pin.
            with self.assertRaises(DownloadError):await resolve(ITEM)

    async def test_fixed_signature_fallback_never_executes_server_code(self):
        seen=[]
        def handle(request):
            seen.append(request.url.path)
            if request.url.path.endswith("filemetas"):
                return httpx.Response(200,json={"errno":0,"info":[dict(META,dlink="")]})
            if request.url.path.endswith("gettemplatevariable"):
                self.assertNotIn("sign2",request.url.params["fields"])
                return httpx.Response(200,json={"errno":0,"result":{
                    "sign1":"Plaintext","sign3":"Key","timestamp":123,
                    "sign2":"raise Exception('must never run')"}})
            self.assertEqual(request.url.params["sign"],web_signature("Key","Plaintext"))
            return httpx.Response(200,json={"errno":0,"dlink":[{"fs_id":123,"dlink":META["dlink"]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client);api.authenticated=True
            self.assertEqual((await api.download_source(ITEM)).url,META["dlink"])
            self.assertEqual(len(seen),3)

    async def test_unauthenticated_or_directory_never_requests(self):
        async def handle(r):self.fail("No request expected")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api=BaiduWebApi(client)
            with self.assertRaises(DownloadError):await api.download_source(ITEM)
            api.authenticated=True
            with self.assertRaises(DownloadError):await api.download_source(CloudFile("123","baidu","/dir",True))

    async def test_server_errors_are_numeric_and_do_not_leak_response(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:
                httpx.Response(200,json={"errno":-6,"message":"private-token"}))) as client:
            api=BaiduWebApi(client);api.authenticated=True
            with self.assertRaises(DownloadError) as error:await api.download_source(ITEM)
            self.assertIn("重新扫码",str(error.exception))
            self.assertNotIn("private",str(error.exception))

    async def test_svip_precedence_and_unavailable_membership(self):
        for flags,expected in [({"is_vip":1,"is_svip":1},"SVIP"),
                               ({"is_vip":1,"is_svip":0},"VIP"),
                               ({"is_vip":0,"is_svip":0},"普通账号"),
                               ({},"会员状态待确认")]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:
                    httpx.Response(200,json={"errno":0,"result":flags}))) as client:
                api=BaiduWebApi(client);api.authenticated=True
                self.assertEqual(await api.account_label(),expected)

    async def test_pcs_identifier_survives_redirect_and_real_download_pipeline(self):
        seen=[]
        def handle(request):
            seen.append(request.url.host)
            if request.url.path == '/api/filemetas':
                return httpx.Response(200,json={'errno':0,'info':[META]})
            if request.headers.get('User-Agent')!='pan.baidu.com':
                return httpx.Response(403,json={'error_code':31326})
            if request.url.host == 'd.pcs.baidu.com':
                self.assertIn('BDUSS=session-fixture', request.headers.get('Cookie',''))
                return httpx.Response(302,headers={'Location':'https://cdn.baidupcs.com/movie'})
            self.assertNotIn('cookie',request.headers)
            return httpx.Response(200,content=b'hello')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'work/test-runs') as folder:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                client.cookies.set('BDUSS','session-fixture',domain='.baidu.com')
                api=BaiduWebApi(client);api.authenticated=True
                async def progress(done,total):pass
                job=DirectDownload(ITEM,folder,client,api.new_download_resolver(),api.validate_download_url,progress)
                saved=await job.run()
                self.assertEqual(job.state,'completed');self.assertEqual(saved.read_bytes(),b'hello')
                self.assertEqual(seen,['pan.baidu.com','d.pcs.baidu.com','cdn.baidupcs.com'])
