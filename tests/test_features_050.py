import asyncio,hashlib,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import httpx
from app.durable_store import DurableStore
from app.cloud_operations import CloudOperations,CloudOperationError
from app.thumbnail_cache import ThumbnailCache
from app.domain import CloudFile
ROOT=Path(__file__).resolve().parents[1]
class ThumbnailTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.cache=ThumbnailCache(self.tmp.name)
 async def asyncTearDown(self):self.tmp.cleanup()
 async def test_redirect_rejects_external_and_no_credential_leak(self):
  calls=[]
  def handler(r):calls.append(r.url.host);return httpx.Response(302,headers={'location':'https://evil.test/a.jpg'})
  item=CloudFile('id','quark','/a.jpg',thumbnail='https://a.quark.cn/image')
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:result=await self.cache.get('account',item,c,lambda:True)
  self.assertIsNone(result);self.assertEqual(calls,['a.quark.cn'])
 async def test_account_and_version_cache_isolation(self):
  item=CloudFile('id','baidu','/a.jpg',revision='1');other=CloudFile('id','baidu','/a.jpg',revision='2')
  self.assertNotEqual(self.cache.key('a',item),self.cache.key('b',item));self.assertNotEqual(self.cache.key('a',item),self.cache.key('a',other))
 async def test_oversize_and_html_not_cached(self):
  for content in [b'<html>login</html>',b'\xff\xd8\xff'+b'a'*(2*1024*1024)]:
   item=CloudFile(str(len(content)),'baidu','/a.jpg',thumbnail='https://pan.baidu.com/img')
   async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=content))) as c:self.assertIsNone(await self.cache.get('a',item,c,lambda:True))
  self.assertEqual(list(Path(self.tmp.name).glob('*.thumb')),[])
 async def test_cached_image_not_returned_after_logout(self):
  item=CloudFile('id','baidu','/a.jpg',thumbnail='https://pan.baidu.com/img');data=b'\xff\xd8\xfftest'
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=data))) as c:
   self.assertEqual(await self.cache.get('a',item,c,lambda:True),data)
   self.assertIsNone(await self.cache.get('a',item,c,lambda:False))
class MutationTests(unittest.IsolatedAsyncioTestCase):
 async def test_delete_confirms_recycle_record_beyond_first_page(self):
  item=CloudFile('wanted','baidu','/wanted.txt',False,4)
  ops=CloudOperations('baidu',SimpleNamespace());ops.list=AsyncMock(return_value=[item]);ops.call=AsyncMock()
  async def trash(page=0):
   return [{'id':str(i),'fid':str(i)} for i in range(50)] if page==0 else [{'id':'record','fid':'wanted'}]
  ops.trash=AsyncMock(side_effect=trash)
  await ops.delete([item])
  self.assertEqual([call.args[0] for call in ops.trash.await_args_list],[0,1])
  ops.call.assert_awaited_once()
 async def test_restore_still_in_later_recycle_page_is_not_reported_complete(self):
  ops=CloudOperations('baidu',SimpleNamespace());ops.call=AsyncMock()
  async def trash(page=0):
   return [{'id':str(i),'fid':str(i)} for i in range(50)] if page==0 else [{'id':'999','fid':'file'}]
  ops.trash=AsyncMock(side_effect=trash)
  with patch('app.cloud_operations.asyncio.sleep',new_callable=AsyncMock),self.assertRaisesRegex(CloudOperationError,'尚未确认'):
   await ops.restore({'id':'999','path':''})
  self.assertIn(1,[call.args[0] for call in ops.trash.await_args_list])
  ops.call.assert_awaited_once()
 async def test_restore_disappearance_requires_complete_recycle_scan(self):
  ops=CloudOperations('baidu',SimpleNamespace());ops.call=AsyncMock();seen=[False]
  async def trash(page=0):
   if page==0:return [{'id':str(i),'fid':str(i)} for i in range(50)]
   if not seen[0]:seen[0]=True;return [{'id':'999','fid':'file'}]
   return []
  ops.trash=AsyncMock(side_effect=trash)
  with patch('app.cloud_operations.asyncio.sleep',new_callable=AsyncMock) as sleep:
   await ops.restore({'id':'999','path':''})
  sleep.assert_awaited_once();ops.call.assert_awaited_once()
 async def test_delete_rechecks_id_before_path_write(self):
  ops=CloudOperations('baidu',SimpleNamespace());ops.list=AsyncMock(return_value=[CloudFile('other','baidu','/same')]);ops.call=AsyncMock()
  with self.assertRaises(CloudOperationError):await ops.delete([CloudFile('original','baidu','/same')])
  ops.call.assert_not_awaited()
 async def test_restore_duplicate_never_overwrites(self):
  ops=CloudOperations('baidu',SimpleNamespace());ops.list=AsyncMock(return_value=[CloudFile('existing','baidu','/same')]);ops.call=AsyncMock()
  with self.assertRaises(CloudOperationError):await ops.restore({'id':'1','path':'/same'})
  ops.call.assert_not_awaited()

 async def test_quark_restore_collision_is_rejected(self):
  ops=CloudOperations('quark',SimpleNamespace());ops.call=AsyncMock(return_value={'list':[{'file_name':'same'}]})
  with self.assertRaises(CloudOperationError):await ops.restore({'id':'r','name':'same','path':'','parent_id':'0'})
  self.assertEqual(ops.call.await_count,1)
 async def test_delete_changed_version_is_rejected(self):
  ops=CloudOperations('baidu',SimpleNamespace());ops.list=AsyncMock(return_value=[CloudFile('1','baidu','/same',size=4,revision='new')]);ops.call=AsyncMock()
  with self.assertRaises(CloudOperationError):await ops.delete([CloudFile('1','baidu','/same',size=4,revision='old')])
  ops.call.assert_not_awaited()
class JournalTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  from desktop.features import Features
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name)
  self.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500)))
  self.panel=SimpleNamespace(api=SimpleNamespace(client=self.client,authenticated=True),jobs=[],upload_jobs=[],share=None,on_transfer_change=None)
  self.bridge=SimpleNamespace(panels={'baidu':self.panel},workspace=SimpleNamespace(data_dir=self.root,settings={}))
  self.f=Features(self.bridge);self.ops=self.f.ops['baidu'];self.ops.stamp=lambda:'session';self.ops.identity_stamp='session';self.f.accounts['baidu']='account'
 async def asyncTearDown(self):await self.f.close();await self.client.aclose();self.tmp.cleanup()
 def job(self,state='downloading'):
  return SimpleNamespace(item=CloudFile('f','baidu','/safe.txt',size=4,revision='v'),state=state,done=2,total=4,error='',requires_restart=False,destination=self.root/'downloads',target=None,pause=AsyncMock(),cancel=AsyncMock())
 async def test_incomplete_task_survives_restart_without_credentials(self):
  j=self.job();self.panel.jobs=[j];self.f.capture('baidu');self.panel.jobs=[]
  rows=self.f.archived();self.assertEqual(rows[0]['status'],'paused');self.assertTrue(rows[0]['archived'])
  raw=json.dumps(self.f.store.all('transfer'));self.assertNotIn('http',raw);self.assertNotIn('thumbnail',raw);self.assertNotIn('cookie',raw)
 async def test_transfer_creation_time_survives_progress_and_archive(self):
  j=self.job();self.panel.jobs=[j];self.f.capture('baidu')
  first=self.f.store.get('transfer',j._desktop_id)['created_at']
  self.assertGreater(first,0)
  j.done=4;j.state='completed';self.f.capture('baidu')
  self.assertEqual(self.f.store.get('transfer',j._desktop_id)['created_at'],first)
  self.panel.jobs=[]
  self.assertEqual(self.f.archived()[0]['created_at'],first)
 async def test_legacy_transfer_history_uses_latest_activity_order(self):
  for key in ('older','newer'):
   self.f.store.put('transfer',key,{'id':key,'provider':'baidu','account':'account','status':'completed','direction':'download'})
  self.assertEqual([row['id'] for row in self.f.archived()],['newer','older'])
 async def test_resume_wrong_account_never_starts(self):
  j=self.job();self.panel.jobs=[j];self.f.capture('baidu');row=self.f.store.all('transfer')[0];self.ops.account=AsyncMock(return_value='different')
  with self.assertRaises(CloudOperationError):await self.f.resume(row)
 async def test_upload_changed_file_requires_reselection(self):
  from dataclasses import asdict
  from app.upload import Fingerprint
  path=self.root/'upload.txt';path.write_text('old');fp=asdict(Fingerprint.read(path));path.write_text('new and longer')
  self.ops.account=AsyncMock(return_value='account');self.ops.list=AsyncMock(return_value=[])
  row={'provider':'baidu','account':'account','item':{'id':'f','provider':'baidu','path':'/upload.txt','size':3},'direction':'upload','local':str(path),'fingerprint':fp}
  with self.assertRaises(CloudOperationError):await self.f.resume(row)
 async def test_share_job_is_never_saved_as_own_file(self):
  j=self.job();j._share_download=True;self.panel.jobs=[j];self.f.capture('baidu');self.assertEqual(self.f.store.all('transfer'),[])
 async def test_completion_event_is_deduplicated(self):
  j=self.job('completed');self.panel.jobs=[j];self.f.capture('baidu');self.assertTrue(self.f.completed_pending);self.f.completed_pending=False;self.f.capture('baidu');self.assertFalse(self.f.completed_pending)
 async def test_session_changed_prevents_persisting_old_jobs_to_new_account(self):
  j=self.job();self.panel.jobs=[j];self.f.capture('baidu');self.f.accounts['baidu']='new-account';self.f.capture('baidu');self.assertEqual(self.f.store.all('transfer')[0]['account'],'account')
