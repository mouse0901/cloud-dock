import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import httpx
from app.account_status import AccountStatus, membership, quota_values
from app.cloud_operations import CloudOperationError
from app.domain import CloudFile
from app.durable_store import DurableStore
from app.workspace import Workspace
from desktop.experience import Experience, local_parent
from app.baidu_web_panel import BaiduWebPanel
from app.direct_download import DownloadSource
from test_parallel_download import Service, DATA, MD5
from test_ui_smoke import PageStub

ROOT=Path(__file__).resolve().parents[1]

class MetadataTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.stamp='A';self.body={'total':100,'used':30}
  self.api=SimpleNamespace(request=AsyncMock(return_value=self.body),_web_params=lambda:{},
    parse_response=lambda r,s:r,_check_service=lambda b,s:None)
  self.ops=SimpleNamespace(api=self.api,stamp=lambda:self.stamp,guard=self.guard)
  self.s=AccountStatus({'baidu':self.ops});self.addAsyncCleanup(self.s.close)
 def guard(self,stamp):
  if stamp!=self.stamp:raise CloudOperationError('changed')
 async def test_nonblocking_public_schema_excludes_membership(self):
  gate=asyncio.Event()
  async def request(url,params):
   await gate.wait();return self.body if url.endswith('quota') else {'result':{'is_vip':1}}
  self.api.request.side_effect=request
  self.assertEqual(self.s.public('baidu')['state'],'loading')
  gate.set();await self.s.tasks['baidu']
  self.assertEqual(self.s.public('baidu')['free'],70)
  self.assertEqual(self.s.internal_membership('baidu'),'paid')
  self.assertNotIn('member',json.dumps(self.s.public('baidu')))
 async def test_failure_is_unknown_not_zero_and_does_not_block_upload(self):
  self.api.request.side_effect=TimeoutError
  await self.s.refresh('baidu',self.s.entry('baidu'))
  result=self.s.public('baidu');self.assertEqual(result['state'],'unavailable');self.assertNotIn('free',result)
  self.assertEqual(self.s.internal_membership('baidu'),'unknown');self.s.check_upload('baidu',10**9)
 async def test_stale_response_cannot_cross_account_switch(self):
  gate=asyncio.Event()
  async def request(*args):await gate.wait();return self.body
  self.api.request.side_effect=request;self.s.public('baidu');await asyncio.sleep(0)
  old=self.s.tasks['baidu'];self.stamp='B';self.s.public('baidu');gate.set()
  await asyncio.gather(old,self.s.tasks['baidu'],return_exceptions=True)
  self.assertEqual(self.s.entries['baidu']['stamp'],'B')
  self.stamp=None;self.assertEqual(self.s.public('baidu'),{'state':'disconnected'})
 async def test_fresh_insufficient_capacity_blocks_upload_but_stale_does_not(self):
  await self.s.refresh('baidu',self.s.entry('baidu'))
  with self.assertRaises(CloudOperationError):self.s.check_upload('baidu',71)
  self.s.entries['baidu']['failed']=True;self.s.check_upload('baidu',71)
 async def test_optional_membership_failure_keeps_capacity(self):
  async def request(url,params):
   if not url.endswith('quota'):raise ValueError('no optional fields')
   return self.body
  self.api.request.side_effect=request;await self.s.refresh('baidu',self.s.entry('baidu'))
  self.assertEqual(self.s.public('baidu')['state'],'ready');self.assertEqual(self.s.internal_membership('baidu'),'unknown')
 def test_missing_membership_is_never_inferred_as_free(self):
  for provider in ('baidu','quark'):self.assertEqual(membership(provider,{}),'unknown')
  self.assertEqual(membership('baidu',{'is_vip':0,'is_svip':0}),'unknown')
  self.assertEqual(membership('baidu',{'is_vip':0,'is_svip':0,'is_evip':0}),'free')
  self.assertEqual(membership('quark',{'member_type':'NORMAL'}),'free')
  self.assertEqual(membership('quark',{'member_type':'UNFAMILIAR'}),'unknown')
 def test_capacity_rejects_malformed_and_allows_overquota(self):
  for value in (None,True,-1,1.5,'x',2**64):
   with self.assertRaises(ValueError):quota_values(value,0)
  self.assertEqual(quota_values('100','120'),{'total':100,'used':120,'free':0,'over':20})

 async def test_quark_capacity_contract_and_private_membership(self):
  self.api._request=AsyncMock(return_value={'data':{'total_capacity':1000,'use_capacity':125,'member_type':'NORMAL'}})
  self.api._success=lambda b,stage:b['data']
  status=AccountStatus({'quark':self.ops});self.addAsyncCleanup(status.close)
  await status.refresh('quark',status.entry('quark'))
  public=status.public('quark');self.assertEqual(public['free'],875)
  self.assertEqual(status.internal_membership('quark'),'free')
  self.assertNotIn('NORMAL',json.dumps(public));self.assertNotIn('member',json.dumps(public))

class ExperienceTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.store=DurableStore(self.root/'state.sqlite3');self.addCleanup(self.store.close)
  self.stamp='A';self.folder=CloudFile('dir','baidu','/assets',True,0)
  self.file=CloudFile('file','baidu','/assets/clip.mp4',False,7)
  self.ops=SimpleNamespace(stamp=lambda:self.stamp,identity='account',identity_stamp='A',account=AsyncMock(return_value='account'),guard=self.guard,list=AsyncMock(return_value=[self.file]))
  self.panel=SimpleNamespace(api=SimpleNamespace(authenticated=True),reader=object(),browser_busy=False,
    browser_rows=[self.folder],path='/',generation=1,jobs=[],upload_jobs=[],download_destination=self.root/'downloads',
    start_download=AsyncMock(return_value=object()),_downloads_changed=Mock(),_ensure_live_worker=Mock())
  self.features=SimpleNamespace(ops={'baidu':self.ops},store=self.store,accounts={},capture=Mock())
  self.bridge=SimpleNamespace(panels={'baidu':self.panel},features=self.features,context=lambda p:'view')
  self.exp=Experience(self.bridge);self.addAsyncCleanup(self.exp.close)
 def guard(self,stamp):
  if stamp!=self.stamp:raise CloudOperationError('changed')
 async def plan(self):
  r=await self.exp.action({'action':'folder_preview','provider':'baidu','context':'view','ids':['dir']})
  await self.exp.scans[r['token']];return r['token']
 async def test_preview_does_not_create_downloads_then_confirms_once(self):
  token=await self.plan();plan=self.exp.plans[token]
  self.panel.start_download.assert_not_awaited();self.assertFalse(self.panel.download_destination.exists())
  self.assertEqual((plan['state'],plan['count'],plan['bytes']),('ready',1,7))
  action={'action':'folder_confirm','provider':'baidu','token':token}
  result=await self.exp.action(action);self.assertEqual(result['count'],1)
  self.assertTrue((self.panel.download_destination/'assets').is_dir())
  with self.assertRaises(CloudOperationError):await self.exp.action(action)
 async def test_account_changed_after_preview_never_downloads(self):
  token=await self.plan();self.stamp='B'
  with self.assertRaises(CloudOperationError):await self.exp.action({'action':'folder_confirm','provider':'baidu','token':token})
  self.panel.start_download.assert_not_awaited()
 async def test_scan_failure_has_no_partial_queue(self):
  self.ops.list.side_effect=TimeoutError;token=await self.plan()
  self.assertEqual(self.exp.plans[token]['state'],'failed');self.assertEqual(self.exp.plans[token]['items'],[])
  self.panel.start_download.assert_not_awaited()
 async def test_windows_name_collisions_abort_entire_plan(self):
  self.ops.list.return_value=[self.file,CloudFile('other','baidu','/assets/CLIP.mp4',False,2)]
  token=await self.plan();self.assertEqual(self.exp.plans[token]['state'],'failed')
 async def test_folder_traversal_is_rejected(self):
  self.ops.list.return_value=[CloudFile('evil','baidu','/elsewhere/file',False,1)]
  token=await self.plan();self.assertEqual(self.exp.plans[token]['state'],'failed')
 def test_safe_parent_rejects_escape_and_sanitizes_names(self):
  with self.assertRaises(CloudOperationError):local_parent(self.root,'/../escape')
  self.assertEqual(local_parent(self.root,'/CON/hello?'),self.root/'_CON'/'hello_')
 async def test_favorites_scoped_to_account_and_current_context(self):
  d={'action':'favorite_toggle','provider':'baidu','context':'view'}
  await self.exp.action(d);self.assertEqual(len(self.exp.favorites()),1)
  self.stamp='B';self.assertEqual(self.exp.favorites(),[])
  self.stamp='A';await self.exp.action(d);self.assertEqual(self.exp.favorites(),[])
 async def test_history_clear_keeps_files_and_active_tasks(self):
  saved=self.root/'kept.txt';saved.write_text('keep')
  for id,status in [('done','completed'),('run','downloading')]:
   self.store.put('transfer',id,{'id':id,'status':status,'target':str(saved)})
   self.panel.jobs.append(SimpleNamespace(_desktop_id=id,state=status))
  self.exp.clear_history(['done','run'])
  self.assertEqual(saved.read_text(),'keep');self.assertIsNone(self.store.get('transfer','done'));self.assertIsNotNone(self.store.get('transfer','run'))
  self.assertEqual([j._desktop_id for j in self.panel.jobs],['run'])
 async def test_view_preferences_roundtrip(self):
  w=Workspace(self.root/'prefs');w.settings.update(view='grid',sort_field='date',sort_desc=True);await w.save()
  self.assertEqual(Workspace(self.root/'prefs').settings['sort_desc'],True)

class BaiduRangeTests(unittest.IsolatedAsyncioTestCase):
 async def test_baidu_uses_bounded_ranges_and_preserves_bytes(self):
  service=Service()
  async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
   panel=BaiduWebPanel(PageStub(),client=client,diagnostic=lambda e:None)
   panel.download_connections=4
   with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as tmp:
    item=CloudFile('b','baidu','/asset.bin',False,len(DATA))
    source=DownloadSource('https://d.pcs.baidu.com/file',len(DATA),md5=MD5)
    job=panel.make_download(item,tmp,client,AsyncMock(return_value=source),lambda url:url,AsyncMock())
    saved=await job.run();self.assertEqual(saved.read_bytes(),DATA);self.assertLessEqual(service.peak,4);self.assertGreater(service.peak,1)
   await panel.shutdown()

 async def test_rejected_parallel_ranges_fall_back_without_mixing_partial_bytes(self):
  from app.baidu_adaptive_download import BaiduAdaptiveDownload,account_connections
  self.assertEqual([account_connections(x) for x in ('paid','unknown','free')],[8,4,4])
  service=Service()
  async def policy(request,start,end):
   if 'range' in request.headers and end>0:return httpx.Response(403)
  service.policy=policy
  async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as client:
   with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as tmp:
    item=CloudFile('b','baidu','/asset.bin',False,len(DATA));source=DownloadSource('https://d.pcs.baidu.com/file',len(DATA),md5=MD5)
    resolve=AsyncMock(return_value=source)
    job=BaiduAdaptiveDownload(item,tmp,client,resolve,lambda url:url,AsyncMock(),connections=4)
    saved=await job.run();self.assertEqual(saved.read_bytes(),DATA);self.assertEqual(job.transfer_mode,'single');self.assertEqual(job.connections,1)
    self.assertEqual(resolve.await_count,3)
