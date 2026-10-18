import asyncio,hashlib,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import httpx
from app.durable_store import DurableStore
from app.folder_backup import FolderBackup,scan
from app.cloud_operations import CloudOperations,CloudOperationError
from app.thumbnail_cache import ThumbnailCache
from app.domain import CloudFile
ROOT=Path(__file__).resolve().parents[1]
class FakeOps:
 def __init__(self):self.api=SimpleNamespace();self.lock=asyncio.Lock();self.rows={};self.created=[];self.uid='account-a';self.session='session-a'
 async def account(self):return self.uid
 def stamp(self):return self.session
 async def list(self,path):return self.rows.get(path,[])
 async def ensure(self,path):self.created.append(path);self.rows.setdefault(path,[])
class FakeJob:
 sequence=0
 def __init__(self,local,provider,directory,adapter,**kw):
  self.local=local;self.directory=directory;self.kw=kw;self.remote_id=None;self.state='queued';self.speed=0;self.hashes={'sha1':'hash'}
 async def run(self):
  FakeJob.sequence+=1;ops=self.kw.pop('_ops',None) or ACTIVE_OPS
  if self.kw['current_owner']()!=self.kw['owner']:raise CloudOperationError('changed')
  f=CloudFile(str(FakeJob.sequence),'baidu',self.directory+'/'+self.kw['remote_name'],False,self.local.stat().st_size,revision=str(FakeJob.sequence))
  self.remote_id=f.id;ops.rows[self.directory].append(f);return f
ACTIVE_OPS=None
class BackupTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  global ACTIVE_OPS
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name);self.local=self.root/'source';self.local.mkdir()
  self.file=self.local/'a.txt';self.file.write_text('one');self.store=DurableStore(self.root/'db.sqlite3');self.ops=FakeOps();ACTIVE_OPS=self.ops
  self.backup=FolderBackup(self.store,{'baidu':self.ops});self.plan=await self.backup.preview(self.local,'baidu','/');self.backup.create(self.plan)
  self.mock=patch('app.folder_backup.UploadJob',FakeJob);self.mock.start()
 async def asyncTearDown(self):self.mock.stop();self.store.close();self.tmp.cleanup()
 async def settle(self):
  await self.backup.run_once(self.store.get('backup',self.plan['id']))
  self.backup.observed[self.plan['id']]={k:(v[0],v[1]-6) for k,v in self.backup.observed[self.plan['id']].items()}
  await self.backup.run_once(self.store.get('backup',self.plan['id']))
 async def test_still_writing_waits(self):
  await self.backup.run_once(self.plan);self.assertEqual(self.ops.created,[])
 async def test_versions_and_local_delete_keep_cloud(self):
  await self.settle();self.file.write_text('two changed');await self.settle();rows=self.ops.rows[self.plan['target']];self.assertEqual(len(rows),2);self.assertNotEqual(rows[0].name,rows[1].name)
  self.file.unlink();await self.settle();self.assertEqual(len(rows),2)
 async def test_restart_no_duplicate_upload(self):
  await self.settle();self.backup=FolderBackup(self.store,{'baidu':self.ops});await self.settle();self.assertEqual(len(self.ops.rows[self.plan['target']]),1)
 async def test_other_account_never_uploads(self):
  self.ops.uid='different-account';await self.backup.run_once(self.plan);self.assertEqual(self.ops.created,[]);self.assertIn('原账号',self.store.get('backup',self.plan['id'])['status'])
 async def test_missing_volume_does_not_delete_remote(self):
  await self.settle();self.file.unlink();self.local.rmdir();await self.backup.run_once(self.plan);self.assertEqual(len(self.ops.rows[self.plan['target']]),1)
 async def test_root_identity_change_blocks(self):
  self.plan['identity']=[999,999];await self.backup.run_once(self.plan);self.assertEqual(self.ops.created,[])
 async def test_remote_delete_excludes_local_update(self):
  await self.settle();self.ops.rows[self.plan['target']]=[];self.file.write_text('updated');await self.settle();self.assertEqual(self.ops.rows[self.plan['target']],[]);self.assertEqual(self.store.all('backup_file')[0]['state'],'excluded')
 async def test_unknown_commit_does_not_duplicate(self):
  await self.settle();record=self.store.all('backup_file')[0];record.update(state='pending',remote_id=None);self.store.put('backup_file',record['id'],record)
  await self.settle();self.assertEqual(len(self.ops.rows[self.plan['target']]),1);self.assertEqual(self.store.all('backup_file')[0]['state'],'excluded')
 async def test_pause_persists(self):
  await self.backup.set_enabled(self.plan['id'],False);await self.backup.run_once(self.store.get('backup',self.plan['id']));self.assertEqual(self.ops.created,[])
 async def test_overlap_rejected(self):
  with self.assertRaises(CloudOperationError):await self.backup.preview(self.local,'baidu','/')
 async def test_temp_files_ignored(self):
  (self.local/'a.part').write_text('busy');(self.local/'~$doc.docx').write_text('busy');files,n=scan(self.local);self.assertEqual(list(files),['a.txt']);self.assertEqual(n,2)
 async def test_database_survives_reopen(self):
  self.store.close();self.store=DurableStore(self.root/'db.sqlite3');self.assertEqual(self.store.get('backup',self.plan['id'])['account'],'account-a')
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
