import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import httpx
from app.baidu_adaptive_download import BaiduAdaptiveDownload,account_connections
from app.direct_download import DownloadSource,DownloadError
from app.domain import CloudFile
from app.providers.web_upload import BaiduUploader
from app.cloud_operations import CloudOperationError
from app.operation_diagnostics import public_error
from desktop.experience import Experience
from test_parallel_download import Service,DATA,MD5
ROOT=Path(__file__).resolve().parents[1]

class DownloadTests(unittest.IsolatedAsyncioTestCase):
 async def run_job(self,service,connections=16):
  async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as c:
   with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as tmp:
    source=DownloadSource('https://d.pcs.baidu.com/file',len(DATA),md5=MD5)
    job=BaiduAdaptiveDownload(CloudFile('1','baidu','/test.bin',size=len(DATA)),tmp,c,AsyncMock(return_value=source),lambda u:u,AsyncMock(),connections=connections)
    path=await job.run();self.assertEqual(path.read_bytes(),DATA);return job
 async def test_member_uses_parallel_and_keeps_identical_bytes(self):
  service=Service();job=await self.run_job(service,account_connections('paid'))
  self.assertGreater(service.peak,4);self.assertLessEqual(service.peak,16);self.assertEqual(job.transfer_mode,'parallel')
 async def test_large_range_ignored_restarts_clean_stream(self):
  service=Service()
  async def policy(req,start,end):
   if 'range' in req.headers and end>0:return httpx.Response(200,content=DATA)
  service.policy=policy;job=await self.run_job(service)
  self.assertEqual(job.transfer_mode,'single');self.assertEqual(job.done,len(DATA))
 async def test_transient_failure_does_not_reset_segments(self):
  service=Service()
  async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as c:
   with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as tmp:
    job=BaiduAdaptiveDownload(CloudFile('1','baidu','/test.bin',size=len(DATA)),tmp,c,AsyncMock(),lambda u:u,AsyncMock(),connections=16)
    sentinel=object();job.segments=sentinel;job.done=1024
    with patch('app.parallel_download.ParallelDownload._transfer',AsyncMock(side_effect=DownloadError('download_network','temporary'))):
     with self.assertRaises(DownloadError):await job._transfer(None)
    self.assertIs(job.segments,sentinel);self.assertEqual(job.done,1024);job.resolve_source.assert_not_awaited()

class ScanTests(unittest.IsolatedAsyncioTestCase):
 async def test_bounded_workers_complete_nested_plan_without_enqueuing_downloads(self):
  active=peak=0
  async def listing(path,limit):
   nonlocal active,peak
   active+=1;peak=max(peak,active)
   try:
    await asyncio.sleep(.005)
    return [CloudFile('file'+path,'baidu',path+'/a.bin',size=4)]
   finally:active-=1
  ops=SimpleNamespace(guard=lambda s:None,list=listing)
  ex=Experience(None);plan={'stamp':'A','destination':str(ROOT/'work/scan-unused'),'count':0,'bytes':0,'folders':0,'items':[],'directories':[]}
  await ex.scan(ops,plan,[CloudFile(str(i),'baidu','/folder'+str(i),True) for i in range(30)],'/')
  self.assertEqual((plan['state'],plan['count'],plan['bytes']),('ready',30,120));self.assertEqual(peak,4);self.assertEqual(active,0)
 async def test_cancel_stops_all_folder_readers(self):
  entered=asyncio.Event();active=0
  async def listing(path,limit):
   nonlocal active
   active+=1;entered.set()
   try:await asyncio.Event().wait()
   finally:active-=1
  ex=Experience(None);plan={'stamp':'A','destination':str(ROOT/'work/scan-unused'),'count':0,'bytes':0,'folders':0,'items':[],'directories':[]}
  task=asyncio.create_task(ex.scan(SimpleNamespace(guard=lambda s:None,list=listing),plan,[CloudFile(str(i),'baidu','/folder'+str(i),True) for i in range(30)],'/'))
  await entered.wait();task.cancel();await task
  self.assertEqual(plan['state'],'cancelled');self.assertEqual(active,0)

class ErrorTests(unittest.IsolatedAsyncioTestCase):
 async def test_baidu_file_error_does_not_say_upload_or_leak_body(self):
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'errno':12,'errmsg':'PRIVATE raw payload'}))) as c:
   api=SimpleNamespace(client=c,_web_params=lambda:{},emit=lambda e:None)
   with self.assertRaises(CloudOperationError) as caught:await BaiduUploader(api).call('filemanager',{'opera':'delete'},{'filelist':'[]'})
   self.assertIn('12',str(caught.exception));self.assertNotIn('上传',str(caught.exception));self.assertNotIn('PRIVATE',str(caught.exception))
 def test_unexpected_error_log_redacts_message_and_payload(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as tmp,patch('app.operation_diagnostics.work_dir',return_value=Path(tmp)):
   text=public_error('delete_preview',ValueError('SECRET URL cookie password'))
   raw=(Path(tmp)/'operation-errors.jsonl').read_text()
   self.assertNotIn('SECRET',raw+text);self.assertIn('delete_preview',raw);self.assertIn('ValueError',raw)
