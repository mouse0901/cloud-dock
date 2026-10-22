"""Upload byte integrity, provider boundaries, conflicts and cancellation."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs
import httpx
from app.domain import CloudFile
from app.upload import UploadJob, UploadError, BLOCK_SIZE
from app.providers.baidu_web import BaiduWebApi
from app.providers.quark_web import QuarkWebApi
from app.providers.web_upload import BaiduUploader, QuarkUploader, UploadAdapter, response_bytes
from app.baidu_web_panel import BaiduWebPanel
from app.quark_panel import QuarkPanel
from test_ui_smoke import PageStub

ROOT = Path(__file__).resolve().parents[1]

class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs')
        self.root = Path(self.tmp.name)
        self.file = self.root/'fixture.txt'
        self.file.write_bytes(b'local upload fixture\n' * 1024)
        self.clients, self.panels = [], []

    async def asyncTearDown(self):
        for panel in self.panels: await panel.shutdown()
        for client in self.clients: await client.aclose()
        self.tmp.cleanup()

    def client(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.clients.append(client)
        return client

    def adapter(self):
        return SimpleNamespace(upload=AsyncMock(), verify=AsyncMock(return_value=CloudFile('1','baidu','/fixture.txt',False,self.file.stat().st_size)))

    async def test_local_change_and_session_change_stop_before_writes(self):
        adapter = self.adapter()
        job = UploadJob(self.file,'baidu','/',adapter)
        self.file.write_bytes(b'changed')
        with self.assertRaises(UploadError): await job.run()
        self.assertEqual(job.error_code,'upload_changed')
        adapter.upload.assert_not_awaited()
        job = UploadJob(self.file,'baidu','/',adapter,owner='old',current_owner=lambda:'new')
        with self.assertRaises(UploadError): await job.run()
        self.assertEqual(job.error_code,'upload_account')
        adapter.upload.assert_not_awaited()

    async def test_conflict_on_second_directory_page(self):
        rows = [CloudFile(str(i),'baidu',f'/other-{i}.txt') for i in range(50)]
        api = SimpleNamespace(authenticated=True,_fetch_files=AsyncMock(side_effect=[rows,[CloudFile('x','baidu','/fixture.txt')]]))
        adapter = UploadAdapter(api)
        job = UploadJob(self.file,'baidu','/',adapter)
        with self.assertRaises(UploadError) as e: await adapter.preflight(job)
        self.assertEqual(e.exception.code,'upload_conflict')
        self.assertEqual(api._fetch_files.call_args.args,('/',1))

    async def test_lost_commit_response_retry_only_verifies(self):
        adapter = self.adapter()
        async def lost(job):
            job.commit_started=True
            raise UploadError('upload_network','network lost')
        adapter.upload.side_effect=lost
        job=UploadJob(self.file,'baidu','/',adapter)
        with self.assertRaises(UploadError): await job.run()
        await job.run()
        adapter.upload.assert_awaited_once()
        adapter.verify.assert_awaited_once()
        self.assertEqual(job.state,'completed')

    async def test_cancel_stream_waits_for_io_and_never_commits(self):
        started,stopped=asyncio.Event(),asyncio.Event()
        adapter=self.adapter()
        async def stream(job):
            try:
                await job.stage('uploading');started.set()
                await asyncio.Event().wait()
            finally:stopped.set()
        adapter.upload.side_effect=stream
        job=UploadJob(self.file,'baidu','/',adapter)
        task=asyncio.create_task(job.run());await started.wait();await job.cancel()
        self.assertTrue(task.done());self.assertTrue(stopped.is_set())
        self.assertEqual(job.state,'cancelled');adapter.verify.assert_not_awaited()

    async def test_baidu_multipart_contains_exact_bytes_and_refuses_overwrite(self):
        payload=b'a' * BLOCK_SIZE + b'last chunk\x00\xff'
        self.file.write_bytes(payload)
        parts,created={},[]
        async def handler(request):
            body=await request.aread()
            if request.url.path.endswith('/precreate'):
                form=parse_qs(body.decode());self.assertEqual(form['rtype'],['0'])
                self.assertEqual(json.loads(form['block_list'][0]),[hashlib.md5(payload[:BLOCK_SIZE]).hexdigest(),hashlib.md5(payload[BLOCK_SIZE:]).hexdigest()])
                return httpx.Response(200,json={'errno':0,'return_type':1,'uploadid':'testid','block_list':[0,1]})
            if request.url.path.endswith('/superfile2'):
                self.assertEqual(request.url.host,'d.pcs.baidu.com')
                boundary=request.headers['content-type'].split('boundary=')[1].encode()
                data=body.split(b'\r\n\r\n',1)[1].removesuffix(b'\r\n--'+boundary+b'--\r\n')
                parts[int(request.url.params['partseq'])]=data
                return httpx.Response(200,json={'md5':hashlib.md5(data).hexdigest()})
            if request.url.path.endswith('/create'):
                self.assertEqual(parse_qs(body.decode())['rtype'],['0'])
                self.assertEqual(b''.join(parts[i] for i in sorted(parts)),payload)
                created.append(CloudFile('123','baidu','/fixture.txt',False,len(payload)))
                return httpx.Response(200,json={'errno':0,'fs_id':123})
            self.fail('unexpected route '+request.url.path)
        api=BaiduWebApi(self.client(handler));api.authenticated=True;api._bdstoken='test-token'
        api._fetch_files=AsyncMock(side_effect=lambda *_:list(created))
        job=UploadJob(self.file,'baidu','/',BaiduUploader(api));await job.run()
        self.assertEqual(job.state,'completed');self.assertEqual(job.remote_id,'123')
        self.assertEqual(job.done,len(payload));self.assertEqual(len(parts),2)

    async def test_baidu_rejected_precreate_allows_retry(self):
        c=self.client(lambda r:httpx.Response(200,json={'errno':-6}))
        api=BaiduWebApi(c);api.authenticated=True;api._bdstoken='test-token';api._fetch_files=AsyncMock(return_value=[])
        job=UploadJob(self.file,'baidu','/',BaiduUploader(api))
        with self.assertRaises(UploadError):await job.run()
        self.assertFalse(job.commit_started)

    async def test_baidu_empty_file_upload_confirms_zero_byte_cloud_file(self):
        self.file.write_bytes(b'')
        created=[]
        async def handler(request):
            self.assertTrue(request.url.path.endswith('/precreate'))
            form=parse_qs((await request.aread()).decode())
            self.assertEqual(form['size'],['0'])
            self.assertEqual(json.loads(form['block_list'][0]),[hashlib.md5(b'').hexdigest()])
            created.append(CloudFile('7','baidu','/fixture.txt',False,0))
            return httpx.Response(200,json={'errno':0,'return_type':2,'fs_id':7})
        api=BaiduWebApi(self.client(handler));api.authenticated=True;api._bdstoken='test-token'
        api._fetch_files=AsyncMock(side_effect=lambda *_:list(created))
        job=UploadJob(self.file,'baidu','/',BaiduUploader(api))
        await job.run()
        self.assertEqual((job.state,job.total,job.done,job.remote_id),('completed',0,0,'7'))

    async def test_quark_empty_file_hash_finish_confirms_cloud_file(self):
        self.file.write_bytes(b'')
        created=[]
        async def handler(request):
            data=json.loads(await request.aread())
            route=request.url.path.split('/1/clouddrive/')[1]
            if route=='file/upload/pre':
                self.assertEqual(data['size'],0)
                value={'task_id':'empty-task'}
            elif route=='file/update/hash':
                self.assertEqual(data['md5'],hashlib.md5(b'').hexdigest())
                self.assertEqual(data['sha1'],hashlib.sha1(b'').hexdigest())
                created.append(CloudFile('8','quark','/fixture.txt',False,0))
                value={'finish':True}
            else:self.fail('unexpected route '+route)
            return httpx.Response(200,json={'status':200,'code':0,'data':value})
        api=QuarkWebApi(self.client(handler));api.authenticated=True
        api._fetch_files=AsyncMock(side_effect=lambda *_:list(created))
        job=UploadJob(self.file,'quark','/',QuarkUploader(api))
        await job.run()
        self.assertEqual((job.state,job.total,job.done),('completed',0,0))

    async def test_baidu_login_handoff_scopes_cookie_and_blocks_other_hosts(self):
        seen=[]
        async def handler(r):
            seen.append(r.url.host)
            if r.url.host=='passport.baidu.com':
                return httpx.Response(302,headers={'location':'https://pan.baidu.com/disk/main?handoff=fixture'})
            if r.url.path=='/disk/main':
                if not r.url.query:return httpx.Response(302,headers={'location':'https://passport.baidu.com/v3/login/api/auth/?tpl=netdisk'})
                return httpx.Response(200,headers={'set-cookie':'STOKEN=fixture; Domain=pan.baidu.com; Path=/; Secure'},text='page')
            if r.url.path.endswith('/gettemplatevariable'):
                self.assertIn('STOKEN=fixture',r.headers['cookie'])
                return httpx.Response(200,json={'errno':0,'result':{'bdstoken':'test-token'}})
            return httpx.Response(200,json={'errno':-8})
        api=BaiduWebApi(self.client(handler));api.authenticated=True;api._fetch_files=AsyncMock(return_value=[])
        with self.assertRaises(UploadError):await UploadJob(self.file,'baidu','/',BaiduUploader(api)).run()
        self.assertIn('passport.baidu.com',seen)
        api=BaiduWebApi(self.client(lambda r:httpx.Response(302,headers={'location':'https://example.org/stolen'})))
        api.authenticated=True;api._fetch_files=AsyncMock(return_value=[])
        with self.assertRaises(UploadError) as e:await UploadJob(self.file,'baidu','/',BaiduUploader(api)).run()
        self.assertEqual(e.exception.code,'upload_session')

    async def test_quark_quota_is_explicit_and_sends_no_file(self):
        calls=[]
        def handler(r):
            calls.append(r.url.path)
            return httpx.Response(400,json={'status':400,'code':32003,'message':'server raw'})
        api=QuarkWebApi(self.client(handler));api.authenticated=True;api._fetch_files=AsyncMock(return_value=[])
        job=UploadJob(self.file,'quark','/',QuarkUploader(api))
        with self.assertRaises(UploadError):await job.run()
        self.assertEqual(job.error_code,'upload_capacity');self.assertIn('容量',job.error)
        self.assertEqual(calls,['/1/clouddrive/file/upload/pre']);self.assertFalse(job.commit_started)

    async def test_quark_oss_payload_and_signature_no_login_cookie(self):
        payload=b'upload chunk\x00\xff'*12000
        self.file.write_bytes(payload)
        created,parts,auth_meta=[],{},[]
        async def control(r):
            d=json.loads(await r.aread());route=r.url.path.split('/1/clouddrive/')[1]
            data={}
            if route=='file/upload/pre':
                self.assertEqual(d['pdir_fid'],'0');self.assertEqual(d['size'],len(payload))
                data={'task_id':'task','upload_id':'upload','bucket':'fixture','obj_key':'test/file',
                      'upload_url':'http://pds.quark.cn','auth_info':'fixture','callback':{'callbackUrl':'https://drive-pc.quark.cn/callback'}}
            elif route=='file/update/hash':
                self.assertEqual(d['md5'],hashlib.md5(payload).hexdigest());data={'finish':False}
            elif route=='file/upload/auth':
                auth_meta.append(d['auth_meta']);data={'auth_key':'OSS fixture:signature'}
            elif route=='file/upload/finish':
                created.append(CloudFile('fid','quark','/fixture.txt',False,len(payload)))
            else:self.fail(route)
            return httpx.Response(200,json={'status':200,'code':0,'data':data,'metadata':{'part_size':65536}})
        async def storage(r):
            self.assertEqual(r.url.scheme,'https');self.assertEqual(r.url.host,'fixture.pds.quark.cn')
            self.assertNotIn('cookie',r.headers)
            data=await r.aread()
            if r.method=='PUT':
                parts[int(r.url.params['partNumber'])]=data
                return httpx.Response(200,headers={'etag':'"'+hashlib.md5(data).hexdigest()+'"'})
            self.assertEqual(r.method,'POST');self.assertIn(b'CompleteMultipartUpload',data)
            self.assertEqual(b''.join(parts[i] for i in sorted(parts)),payload)
            return httpx.Response(200,content=b'<ok/>')
        api=QuarkWebApi(self.client(control));api.authenticated=True;api._fetch_files=AsyncMock(side_effect=lambda *_:list(created))
        api.client.cookies.set('login-secret','do-not-forward',domain='drive-pc.quark.cn')
        job=UploadJob(self.file,'quark','/',QuarkUploader(api,self.client(storage)))
        with patch('app.providers.web_upload.formatdate',return_value='Fri, 16 Oct 2026 00:00:00 GMT'):
            await job.run()
        self.assertEqual(job.state,'completed');self.assertEqual(len(parts),3)
        self.assertEqual(auth_meta[0], 'PUT\n\ntext/plain\nFri, 16 Oct 2026 00:00:00 GMT\nx-oss-date:Fri, 16 Oct 2026 00:00:00 GMT\nx-oss-user-agent:aliyun-sdk-js/6.6.1\n/fixture/test/file?partNumber=1&uploadId=upload')

    async def test_quark_oss_failure_reports_only_safe_code_and_part(self):
        secret = "private-object-key-and-ticket"
        events = []
        client = self.client(lambda _: httpx.Response(400, text=(
            "<Error><Code>InvalidArgument</Code><Message>" + secret + "</Message></Error>")))
        api = QuarkWebApi(client, events.append)
        with self.assertRaises(UploadError):
            await response_bytes(client, 'PUT', 'https://fixture.pds.quark.cn/object',
                diagnostic=api.emit, stage='upload_part', part_number=2)
        self.assertEqual(events, [{'stage':'upload_part','http_status':400,
                                   'part_number':2,'oss_code':'InvalidArgument'}])
        self.assertNotIn(secret, str(events))

    async def test_untrusted_quark_destination_rejected(self):
        for address in ('https://evil.test','https://fixture.pds.quark.cn.evil.test','https://user@fixture.pds.quark.cn','https://fixture.pds.quark.cn/?secret=x'):
            with self.assertRaises(UploadError):QuarkUploader.storage_url({'bucket':'fixture','obj_key':'test/file','upload_url':address})

    async def test_verify_does_not_accept_replaced_remote_id(self):
        api=SimpleNamespace(_fetch_files=AsyncMock(return_value=[CloudFile('other','baidu','/fixture.txt',False,self.file.stat().st_size)]))
        job=UploadJob(self.file,'baidu','/',UploadAdapter(api));job.remote_id='expected'
        with self.assertRaises(UploadError):await job.adapter.verify(job)

    def panel(self, provider='baidu'):
        cls=BaiduWebPanel if provider=='baidu' else QuarkPanel
        panel=cls(PageStub(),client=self.client(lambda r:httpx.Response(200,json={})),diagnostic=lambda e:None)
        self.panels.append(panel);panel.api.authenticated=True;panel.path='/target';panel.reader=SimpleNamespace(root='/')
        panel._ensure_upload_worker=Mock()
        return panel

    async def test_picker_snapshot_target_survives_navigation_and_rejects_logout(self):
        panel=self.panel();snapshot=panel.upload_snapshot();panel.path='/other'
        await panel.queue_upload_paths([self.file],snapshot)
        self.assertEqual(panel.upload_jobs[0].directory,'/target')
        panel.generation+=1
        await panel.queue_upload_paths([self.file],snapshot)
        self.assertEqual(len(panel.upload_jobs),1)
        with self.assertRaises(UploadError):await panel.upload_jobs[0].run()
        self.assertEqual(panel.upload_jobs[0].error_code,'upload_account')

    async def test_picker_callback_multiple_files_and_cancel(self):
        panel=self.panel()
        with patch('app.live_upload_panel.ft.FilePicker') as picker:
            picker.return_value.pick_files=AsyncMock(return_value=[SimpleNamespace(path=str(self.file))])
            await panel.choose_upload()
            self.assertEqual(len(panel.upload_jobs),1)
            picker.return_value.pick_files.return_value=[]
            await panel.choose_upload()
            self.assertEqual(len(panel.upload_jobs),1)

    async def test_share_view_disables_upload_and_deduplicates_queue(self):
        panel=self.panel('quark');panel.share=object()
        await panel.queue_upload_paths([self.file]);self.assertFalse(panel.upload_jobs)
        panel.share=None
        await panel.queue_upload_paths([self.file,self.file]);self.assertEqual(len(panel.upload_jobs),1)
        await panel.cancel_download();self.assertFalse(panel.upload_jobs)

    async def test_cancelling_one_upload_does_not_cancel_next(self):
        panel=self.panel();del panel._ensure_upload_worker
        second=self.root/'second.txt';second.write_text('second')
        started=asyncio.Event()
        async def upload(job):
            if job.item.name=='fixture.txt':started.set();await asyncio.Event().wait()
        adapter=self.adapter();adapter.upload.side_effect=upload
        with patch('app.live_upload_panel.BaiduUploader',return_value=adapter):
            await panel.queue_upload_paths([self.file,second])
            await started.wait();await panel.cancel_one_upload(panel.upload_jobs[0])
            worker=panel._upload_worker
            if worker:await worker
        self.assertEqual([j.state for j in panel.upload_jobs],['cancelled','completed'])

    async def test_logout_awaits_active_upload_and_clears_queue(self):
        panel=self.panel();del panel._ensure_upload_worker
        started,stopped=asyncio.Event(),asyncio.Event()
        adapter=self.adapter()
        async def upload(job):
            try:started.set();await asyncio.Event().wait()
            finally:stopped.set()
        adapter.upload.side_effect=upload
        with patch('app.live_upload_panel.BaiduUploader',return_value=adapter):
            await panel.queue_upload_paths([self.file]);await started.wait();await panel.cancel_download()
        self.assertTrue(stopped.is_set());self.assertFalse(panel.upload_jobs);adapter.verify.assert_not_awaited()
