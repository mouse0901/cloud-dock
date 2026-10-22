import asyncio,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
import httpx
from app.domain import CloudFile
from app.cloud_operations import CloudOperationError,CloudOperations
from app.direct_download import DirectDownload,DownloadError,DownloadSource
from app.parallel_download import ParallelDownload
from app.baidu_adaptive_download import BaiduAdaptiveDownload
from app.download_checkpoint import checkpoint,restore
from desktop.productivity import Productivity,cloud_path,file_name,byte_range
from test_parallel_download import Service,DATA,MD5,URL,validate
ROOT=Path(__file__).resolve().parents[1]

class CheckpointTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  (ROOT/'work/test-runs').mkdir(parents=True,exist_ok=True)
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.addCleanup(self.tmp.cleanup)
  self.service=Service();self.client=httpx.AsyncClient(transport=httpx.MockTransport(self.service));self.addAsyncCleanup(self.client.aclose)
  self.item=CloudFile('file','quark','/fixture.bin',False,len(DATA),revision='v1')
  self.source=AsyncMock(return_value=DownloadSource(URL,len(DATA),md5=MD5))
 def job(self,cls=DirectDownload,**kwargs):return cls(self.item,self.tmp.name,self.client,self.source,validate,AsyncMock(),**kwargs)
 async def paused(self,cls=DirectDownload,**kwargs):
  self.service.block=True;j=self.job(cls,**kwargs);task=asyncio.create_task(j.run());await asyncio.wait_for(self.service.blocked.wait(),5);await j.pause();await asyncio.gather(task,return_exceptions=True);return j
 async def test_direct_new_instance_resumes_actual_bytes_with_fresh_ticket(self):
  old=await self.paused();self.assertGreater(old.done,0);cp=json.loads(json.dumps(checkpoint(old)))
  self.assertNotIn('http',json.dumps(cp));self.assertNotIn('cookie',json.dumps(cp));self.service.calls.clear()
  new=self.job();restore(new,cp);self.service.block=False;self.service.release.set();result=await new.run()
  self.assertEqual(result.read_bytes(),DATA);self.assertEqual(self.service.calls[0].headers['range'],f'bytes={old.done}-');self.assertEqual(self.source.await_count,2)
 async def test_parallel_new_instance_uses_recorded_ranges(self):
  old=await self.paused(ParallelDownload,connections=4);cp=json.loads(json.dumps(checkpoint(old)));self.assertTrue(cp['segments']);self.assertLess(old.done,len(DATA))
  new=self.job(ParallelDownload,connections=4);restore(new,cp);self.service.block=False;self.service.release.set();result=await new.run();self.assertEqual(result.read_bytes(),DATA);self.assertTrue(new.checksum_verified)
 async def test_baidu_one_connection_can_restore_parallel_plan(self):
  old=await self.paused(ParallelDownload,connections=4);new=self.job(BaiduAdaptiveDownload,connections=1);restore(new,checkpoint(old));self.service.block=False;self.service.release.set();self.assertEqual((await new.run()).read_bytes(),DATA)
 async def test_changed_partial_is_not_used_or_removed(self):
  old=await self.paused();cp=checkpoint(old);old.part.write_bytes(b'other')
  with self.assertRaises(DownloadError):restore(self.job(),cp)
  self.assertEqual(old.part.read_bytes(),b'other')
 async def test_existing_target_is_not_overwritten(self):
  old=await self.paused();cp=checkpoint(old);old.target.write_bytes(b'keep')
  with self.assertRaises(DownloadError):restore(self.job(),cp)
  self.assertEqual(old.target.read_bytes(),b'keep')
 async def test_invalid_range_plan_is_rejected(self):
  old=await self.paused(ParallelDownload,connections=4);cp=checkpoint(old);cp['segments'][0][0]=1
  with self.assertRaises(DownloadError):restore(self.job(ParallelDownload),cp)
 async def test_checkpoint_path_escape_and_headers_are_rejected(self):
  old=await self.paused();cp=checkpoint(old)
  for key,value in [('part','../outside'),('target','../outside'),('target','C:secret'),('validator',['etag','evil\r\nHeader: value'])]:
   with self.subTest(key=key),self.assertRaises(DownloadError):restore(self.job(),{**cp,key:value})
 async def test_running_job_has_no_unflushed_checkpoint(self):
  j=self.job();self.assertIsNone(checkpoint(j))

 async def test_shutdown_saves_checkpoint_without_removing_owned_partial(self):
  from desktop.features import Features
  from app.durable_store import DurableStore
  old=await self.paused();root=Path(self.tmp.name)
  panel=SimpleNamespace(api=SimpleNamespace(client=self.client,authenticated=True),jobs=[old],upload_jobs=[])
  bridge=SimpleNamespace(panels={'quark':panel},workspace=SimpleNamespace(data_dir=root,settings={}))
  features=Features(bridge);features.ops['quark'].stamp=lambda:'A';features.ops['quark'].identity_stamp='A';features.accounts['quark']='account'
  await features.close();self.assertTrue(old.part.exists());self.assertEqual(panel.jobs,[])
  store=DurableStore(root/'activity.sqlite3')
  try:
   row=store.all('transfer')[0];self.assertTrue(row['checkpoint']);self.assertEqual(row['account'],'account');self.assertEqual(row['done'],old.done)
  finally:store.close()

class ToolTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.addCleanup(self.tmp.cleanup);self.stamp='A'
  self.item=CloudFile('one','baidu','/one.txt',False,4,revision='v1');self.directory=CloudFile('dir','baidu','/dir',True)
  self.ops=SimpleNamespace(account=AsyncMock(return_value='account'),stamp=lambda:self.stamp,guard=self.guard,lock=asyncio.Lock(),list=AsyncMock(return_value=[]),relocate=AsyncMock(),copy=AsyncMock(),mkdir=AsyncMock(),api=SimpleNamespace(_fetch_files=AsyncMock(return_value=[])))
  self.panel=SimpleNamespace(path='/',browser_busy=False,browser_rows=[self.item,self.directory],jobs=[],upload_jobs=[],load_directory=AsyncMock(),page_index=0)
  self.store=SimpleNamespace(all=lambda kind:[]);self.sync=SimpleNamespace(rules=lambda:[],entries=lambda kind,rid:[])
  self.f=SimpleNamespace(ops={'baidu':self.ops},sync=self.sync,store=self.store,account_status=SimpleNamespace(invalidate=Mock()))
  self.bridge=SimpleNamespace(features=self.f,panels={'baidu':self.panel},workspace=SimpleNamespace(data_dir=Path(self.tmp.name)),context=lambda p:'context')
  self.tools=Productivity(self.bridge);self.addAsyncCleanup(self.tools.close)
 def guard(self,s):
  if s!=self.stamp:raise CloudOperationError('changed')
 async def action(self,action,**kw):return await self.tools.action({'action':action,'provider':'baidu','context':'context',**kw})
 async def plan(self,**kw):return await self.action('tools_plan',kind='rename',ids=['one'],name='two.txt',**kw)
 async def test_quark_copy_can_be_planned_for_current_account(self):
  item=CloudFile('one','quark','/one.txt',False,4,revision='v1')
  self.panel.browser_rows=[item]
  self.bridge.panels={'quark':self.panel};self.f.ops={'quark':self.ops}
  result=await self.tools.action({'action':'tools_plan','provider':'quark','context':'context','kind':'copy','ids':['one'],'target':'/dest'})
  self.assertEqual((result['kind'],result['source'],result['target']),('copy','/one.txt','/dest/one.txt'))
  self.ops.copy.assert_not_awaited()
 async def test_plan_is_readonly_and_confirmation_single_use(self):
  p=await self.plan();self.ops.relocate.assert_not_awaited();r=await self.action('tools_commit',confirmation=p['confirmation']);await self.tools.tasks[r['token']]
  self.assertEqual(self.tools.jobs[r['token']]['status'],'completed');self.ops.relocate.assert_awaited_once()
  with self.assertRaises(CloudOperationError):await self.action('tools_commit',confirmation=p['confirmation'])
 async def test_stale_directory_rejected(self):
  with self.assertRaises(CloudOperationError):await self.action('tools_plan',kind='mkdir',name='folder',context='old')
 async def test_account_switch_invalidates_plan(self):
  p=await self.plan();self.stamp='B'
  with self.assertRaises(CloudOperationError):await self.action('tools_commit',confirmation=p['confirmation'])
  self.ops.relocate.assert_not_awaited()
 async def test_name_collision_blocks_plan(self):
  self.ops.list.return_value=[CloudFile('existing','baidu','/TWO.TXT',False,10)]
  with self.assertRaises(CloudOperationError):await self.plan()
 async def test_folder_cannot_move_into_self(self):
  with self.assertRaises(CloudOperationError):await self.action('tools_plan',kind='move',ids=['dir'],target='/dir/child')
 async def test_active_sync_blocks_mutation(self):
  self.sync.rules=lambda:[{'id':'sync','provider':'baidu','account':'account','remote_root':'/','enabled':True}]
  with self.assertRaises(CloudOperationError):await self.plan()
 async def test_active_transfer_blocks_confirmed_mutation(self):
  p=await self.plan();self.panel.jobs=[SimpleNamespace(item=self.item,state='paused')];r=await self.action('tools_commit',confirmation=p['confirmation']);await self.tools.tasks[r['token']]
  self.assertEqual(self.tools.jobs[r['token']]['status'],'failed');self.ops.relocate.assert_not_awaited()
 async def test_search_paginates_and_visits_subfolders(self):
  first=[CloudFile(str(i),'baidu',f'/f{i}.txt') for i in range(49)]+[self.directory]
  async def fetch(path,page):return first if (path,page)==('/',0) else [CloudFile('nested','baidu','/dir/needle.txt')] if path=='/dir' else []
  self.ops.api._fetch_files.side_effect=fetch;r=await self.action('tools_search',term='needle');await self.tools.tasks[r['token']];result=await self.action('tools_status',token=r['token'])
  self.assertEqual(result['status'],'completed');self.assertEqual(result['count'],51);self.assertEqual(result['rows'][0]['parent'],'/dir');self.ops.api._fetch_files.assert_any_await('/',1)
 async def test_search_interrupted_is_never_complete(self):
  async def fetch(path,page):
   if path=='/':return [self.directory,self.item]
   raise TimeoutError()
  self.ops.api._fetch_files.side_effect=fetch;r=await self.action('tools_search',term='one');await self.tools.tasks[r['token']];result=await self.action('tools_status',token=r['token']);self.assertEqual(result['status'],'partial')
 async def test_search_cancel_cancels_inflight_request(self):
  event=asyncio.Event()
  async def fetch(*args):event.set();await asyncio.Event().wait()
  self.ops.api._fetch_files.side_effect=fetch;r=await self.action('tools_search',term='one');task=self.tools.tasks[r['token']];await event.wait();await self.action('tools_cancel',token=r['token']);await asyncio.gather(task,return_exceptions=True);self.assertTrue(task.cancelled())
 async def test_old_account_cannot_read_search_results(self):
  r=await self.action('tools_search',term='one');await self.tools.tasks[r['token']];self.stamp='B'
  with self.assertRaises(CloudOperationError):await self.action('tools_status',token=r['token'])
 async def test_preview_unsupported_and_size_limits(self):
  with self.assertRaises(CloudOperationError):await self.action('tools_preview',ids=['one'])
  self.panel.browser_rows=[CloudFile('big','baidu','/big.mp4',False,129*1024*1024)]
  with self.assertRaises(CloudOperationError):await self.action('tools_preview',ids=['big'])
 async def test_media_ranges_and_account_guard(self):
  r=self.tools.begin('baidu','A','preview');path=Path(self.tmp.name)/'media.mp4';path.write_bytes(b'0123456789');r['total']=10
  self.tools.media[r['token']]={'path':path,'mime':'video/mp4','stamp':path.stat().st_mtime_ns}
  status,data,mime,headers=await self.tools.media_bytes(r['token'],'bytes=3-6');self.assertEqual((status,data),(206,b'3456'));self.assertEqual(headers['Content-Range'],'bytes 3-6/10')
  self.assertEqual((await self.tools.media_bytes(r['token'],'bytes=999-'))[0],416);self.stamp='B';self.assertIsNone(await self.tools.media_bytes(r['token'],''))
 async def test_media_modified_on_disk_rejected(self):
  r=self.tools.begin('baidu','A','preview');path=Path(self.tmp.name)/'file.jpg';path.write_bytes(b'abc');r['total']=3
  self.tools.media[r['token']]={'path':path,'mime':'image/jpeg','stamp':path.stat().st_mtime_ns};path.write_bytes(b'changed');self.assertIsNone(await self.tools.media_bytes(r['token'],''))
 def test_path_and_name_validation(self):
  for value in ['/../a','relative','/a//b','/a/','/a\\b']:
   with self.assertRaises(CloudOperationError):cloud_path(value)
  for value in ['..','a/b','a\\b',' ','a\n']:
   with self.assertRaises(CloudOperationError):file_name(value)
 def test_http_ranges(self):
  self.assertEqual(byte_range('bytes=-3',10),(7,9,206));self.assertEqual(byte_range('',10),(0,9,200))
  self.assertEqual(byte_range('bytes=1-',10),(1,9,206))
  for value in ['bytes=10-','bytes=3-2','bytes=0-1,3-4','bytes=-0']:
   with self.assertRaises(ValueError):byte_range(value,10)

class CopyTests(unittest.IsolatedAsyncioTestCase):
 async def test_copy_calls_provider_without_overwrite_and_confirms_result(self):
  api=SimpleNamespace();ops=CloudOperations('baidu',api);ops.stamp=lambda:'A';item=CloudFile('src','baidu','/a.txt',False,4,revision='v');copied=CloudFile('new','baidu','/dest/a.txt',False,4)
  ops.list=AsyncMock(side_effect=[[item],[],[copied]]);ops.call=AsyncMock();self.assertEqual(await ops.copy(item,'/dest'),copied)
  args=ops.call.await_args.args;self.assertEqual(args[1]['ondup'],'fail');self.assertEqual(args[2]['opera'],'copy')
 async def test_account_switch_during_target_listing_never_writes(self):
  ops=CloudOperations('baidu',SimpleNamespace());stamp=['A'];ops.stamp=lambda:stamp[0];item=CloudFile('src','baidu','/a.txt',False,4)
  async def listing(path):
   if path=='/dest':stamp[0]='B';return []
   return [item]
  ops.list=listing;ops.call=AsyncMock()
  with self.assertRaises(CloudOperationError):await ops.copy(item,'/dest')
  ops.call.assert_not_awaited()
 async def test_quark_copy_uses_same_drive_endpoint_and_confirms_result(self):
  api=SimpleNamespace(_directories={'/dest':'target-fid'});ops=CloudOperations('quark',api);ops.stamp=lambda:'A'
  item=CloudFile('src','quark','/a.txt',False,4,revision='v');copied=CloudFile('new','quark','/dest/a.txt',False,4)
  ops.list=AsyncMock(side_effect=[[item],[],[copied]]);ops.call=AsyncMock()
  self.assertEqual(await ops.copy(item,'/dest'),copied)
  ops.call.assert_awaited_once_with('file/copy',{'action_type':1,'to_pdir_fid':'target-fid','filelist':['src'],'exclude_fids':[]})
 async def test_quark_copy_duplicate_never_submits(self):
  api=SimpleNamespace(_directories={'/dest':'target-fid'});ops=CloudOperations('quark',api);ops.stamp=lambda:'A'
  item=CloudFile('src','quark','/a.txt',False,4,revision='v')
  ops.list=AsyncMock(side_effect=[[item],[CloudFile('other','quark','/dest/A.TXT',False,5)]])
  ops.call=AsyncMock()
  with self.assertRaisesRegex(CloudOperationError,'同名'):await ops.copy(item,'/dest')
  ops.call.assert_not_awaited()
 async def test_quark_copy_unknown_target_never_submits(self):
  ops=CloudOperations('quark',SimpleNamespace(_directories={}));ops.stamp=lambda:'A'
  item=CloudFile('src','quark','/a.txt',False,4,revision='v')
  ops.list=AsyncMock(side_effect=[[item],[]]);ops.call=AsyncMock()
  with self.assertRaisesRegex(CloudOperationError,'目标文件夹'):await ops.copy(item,'/dest')
  ops.call.assert_not_awaited()
 async def test_quark_copy_account_switch_never_submits(self):
  api=SimpleNamespace(_directories={'/dest':'target-fid'});ops=CloudOperations('quark',api);stamp=['A'];ops.stamp=lambda:stamp[0]
  item=CloudFile('src','quark','/a.txt',False,4,revision='v')
  async def listing(path):
   if path=='/dest':stamp[0]='B';return []
   return [item]
  ops.list=listing;ops.call=AsyncMock()
  with self.assertRaisesRegex(CloudOperationError,'账号已切换'):await ops.copy(item,'/dest')
  ops.call.assert_not_awaited()
 async def test_quark_copy_unconfirmed_result_is_not_resubmitted(self):
  api=SimpleNamespace(_directories={'/dest':'target-fid'});ops=CloudOperations('quark',api);ops.stamp=lambda:'A'
  item=CloudFile('src','quark','/a.txt',False,4,revision='v')
  ops.list=AsyncMock(side_effect=[[item]]+[[]]*9);ops.call=AsyncMock()
  with patch('app.cloud_operations.asyncio.sleep',new_callable=AsyncMock),self.assertRaisesRegex(CloudOperationError,'未重复提交'):
   await ops.copy(item,'/dest')
  self.assertEqual(ops.call.await_count,1)

class RecoveryTests(unittest.IsolatedAsyncioTestCase):
 async def test_transient_failure_retries_with_fresh_run(self):
  from app.live_download_panel import LiveDownloadMixin
  job=SimpleNamespace(state='queued',requires_restart=False,error='',run=AsyncMock(side_effect=[DownloadError('download_network','offline'),'saved']))
  panel=SimpleNamespace(_downloads_changed=Mock())
  with patch('app.live_download_panel.asyncio.sleep',new_callable=AsyncMock) as sleep:
   self.assertEqual(await LiveDownloadMixin._run_queued(panel,job),'saved');self.assertEqual(job.run.await_count,2);sleep.assert_awaited_once_with(3)
 async def test_checksum_failure_is_never_auto_retried(self):
  from app.live_download_panel import LiveDownloadMixin
  job=SimpleNamespace(state='queued',requires_restart=True,error='',run=AsyncMock(side_effect=DownloadError('download_checksum','bad data')))
  with self.assertRaises(DownloadError):await LiveDownloadMixin._run_queued(SimpleNamespace(_downloads_changed=Mock()),job)
  self.assertEqual(job.run.await_count,1)
 async def test_manual_pause_while_backoff_stops_reconnect(self):
  from app.live_download_panel import LiveDownloadMixin
  job=SimpleNamespace(state='queued',requires_restart=False,error='',run=AsyncMock(side_effect=DownloadError('download_network','offline')))
  async def sleep(_):job.state='paused'
  with patch('app.live_download_panel.asyncio.sleep',sleep):await LiveDownloadMixin._run_queued(SimpleNamespace(_downloads_changed=Mock()),job)
  self.assertEqual(job.run.await_count,1);self.assertEqual(job.state,'paused')
 async def test_reconnect_is_bounded(self):
  from app.live_download_panel import LiveDownloadMixin
  job=SimpleNamespace(state='queued',requires_restart=False,error='',run=AsyncMock(side_effect=DownloadError('download_network','offline')))
  with patch('app.live_download_panel.asyncio.sleep',new_callable=AsyncMock),self.assertRaises(DownloadError):await LiveDownloadMixin._run_queued(SimpleNamespace(_downloads_changed=Mock()),job)
  self.assertEqual(job.run.await_count,3)
