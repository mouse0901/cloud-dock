import asyncio,tempfile,unittest
from pathlib import Path,PurePosixPath
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
from app.domain import CloudFile
from app.durable_store import DurableStore
from app.sync_engine import SyncEngine
from app.sync_model import decide,remote_mark,local_snapshot,content_hash,safe_local
from app.cloud_operations import CloudOperationError
ROOT=Path(__file__).resolve().parents[1]

class Ops:
 def __init__(self):
  self.uid='owner';self.session='session';self.provider='baidu';self.rows={};self.contents={};self.counter=0;self.uploads=0;self.lock=asyncio.Lock();self.fail_after=''
  self.api=SimpleNamespace(client=self,download_source=lambda x:None,validate_download_url=lambda x:x)
 async def account(self):return self.uid
 def stamp(self):return self.session
 async def list(self,path):return [f for f in self.rows.values() if f.parent==path]
 def put(self,path,data=b'',directory=False):
  self.counter+=1;item=CloudFile(str(self.counter),'baidu',path,directory,len(data),revision=str(self.counter));self.rows[path]=item;self.contents[item.id]=data;return item
 async def ensure(self,path):
  here=''
  for p in PurePosixPath(path).parts[1:]:
   here+='/'+p
   if here not in self.rows:self.put(here,directory=True)
  return path
 async def relocate(self,item,parent,name):
  if self.rows.get(item.path)!=item:raise CloudOperationError('cloud changed')
  path=parent.rstrip('/')+'/'+name
  if path in self.rows:raise CloudOperationError('name exists')
  moved=replace(item,path=path);self.rows.pop(item.path);self.rows[path]=moved
  if item.is_dir:
   for old,child in list(self.rows.items()):
    if old.startswith(item.path+'/'):
     target=path+old[len(item.path):];self.rows.pop(old);self.rows[target]=replace(child,path=target)
  phase='archive' if '.clouddock-history' in path else 'publish'
  if self.fail_after==phase:self.fail_after='';raise CloudOperationError('lost reply')
  return moved
 async def delete(self,items):
  for item in items:self.rows.pop(item.path)
class Upload:
 def __init__(self,local,provider,directory,adapter,**kw):self.local=Path(local);self.ops=ACTIVE;self.path=directory.rstrip('/')+'/'+kw['remote_name'];self.kw=kw
 async def run(self):
  if self.kw['current_owner']()!=self.kw['owner']:raise CloudOperationError('account changed')
  self.ops.uploads+=1;item=self.ops.put(self.path,self.local.read_bytes())
  if self.ops.fail_after=='upload':self.ops.fail_after='';raise CloudOperationError('lost commit')
  return item
class Download:
 def __init__(self,item,destination,client,*args,**kw):self.item=item;self.path=Path(destination)/item.name;self.ops=client
 async def run(self):self.path.parent.mkdir(parents=True,exist_ok=True);self.path.write_bytes(self.ops.contents[self.item.id]);return self.path
ACTIVE=None

class DecisionTests(unittest.TestCase):
 def setUp(self):self.l={'dir':False,'hash':'one'};self.r={'dir':False,'id':'1','size':3,'revision':'1'};self.b={'local':self.l,'remote':self.r}
 def test_initial_missing_is_not_delete(self):
  self.assertEqual(decide(None,self.r,None,'two_way'),'download');self.assertEqual(decide(self.l,None,None,'two_way'),'upload')
 def test_initial_same_name_needs_content_comparison(self):self.assertEqual(decide(self.l,self.r,None,'two_way'),'compare')
 def test_deletion_requires_baseline(self):self.assertEqual(decide(None,self.r,self.b,'two_way'),'delete_remote')
 def test_both_changed_never_overwrites(self):self.assertEqual(decide({**self.l,'hash':'two'},{**self.r,'revision':'2'},self.b,'two_way'),'compare')
 def test_delete_vs_modify_conflicts(self):self.assertEqual(decide(None,{**self.r,'revision':'2'},self.b,'two_way'),'conflict')
 def test_one_way_protects_destination_edits(self):self.assertEqual(decide(self.l,{**self.r,'revision':'2'},self.b,'upload'),'conflict')
 def test_tombstone_stays_deleted(self):self.assertEqual(decide(None,None,{'local':None,'remote':None},'two_way'),'none')
 def test_ignored_pair_reopens_on_change(self):self.assertEqual(decide({**self.l,'hash':'new'},self.r,{**self.b,'ignored':True},'two_way'),'conflict')

class SyncTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  global ACTIVE
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name);self.local=self.root/'local';self.local.mkdir();self.file=self.local/'a.txt';self.file.write_bytes(b'one')
  self.store=DurableStore(self.root/'state.db');self.ops=Ops();ACTIVE=self.ops;await self.ops.ensure('/target')
  self.engine=SyncEngine(self.store,{'baidu':self.ops});self.rule=await self.engine.preview(self.local,'baidu','/target','folder','two_way');self.engine.create(self.rule)
  self.patches=[patch('app.sync_transport.UploadJob',Upload),patch('app.sync_transport.DirectDownload',Download)]
  for p in self.patches:p.start()
 async def asyncTearDown(self):
  await self.engine.close()
  for p in self.patches:p.stop()
  self.store.close();self.tmp.cleanup()
 async def settle(self):
  await self.engine.check(self.rule['id'],True)
  rid=self.rule['id'];self.engine.observed[rid]={k:(v[0],v[1]-10) for k,v in self.engine.observed.get(rid,{}).items()}
  await self.engine.check(rid,True)
 def issues(self):return self.engine.entries('sync_issue',self.rule['id'])
 async def test_upload_then_update_keeps_name_and_old_content(self):
  await self.settle();self.assertEqual(self.ops.contents[self.ops.rows['/target/a.txt'].id],b'one')
  self.file.write_bytes(b'two');await self.settle();self.assertEqual(self.ops.contents[self.ops.rows['/target/a.txt'].id],b'two')
  self.assertTrue(any('.clouddock-history' in p and f.name=='a.txt' and self.ops.contents[f.id]==b'one' for p,f in self.ops.rows.items()))
 async def test_remote_change_downloads_and_archives_local(self):
  await self.settle();self.ops.put('/target/a.txt',b'cloud');await self.settle();self.assertEqual(self.file.read_bytes(),b'cloud')
  self.assertTrue(any(p.read_bytes()==b'one' for p in self.local.glob('.clouddock-history/**/*') if p.is_file()))
 async def test_both_changes_conflict_then_choose_remote(self):
  await self.settle();self.file.write_bytes(b'local');self.ops.put('/target/a.txt',b'cloud');await self.settle();self.assertEqual(self.issues()[0]['kind'],'conflict');self.assertEqual(self.file.read_bytes(),b'local')
  await self.engine.resolve(self.rule['id'],'a.txt','remote');self.assertEqual(self.file.read_bytes(),b'cloud');self.assertEqual(self.issues(),[])
 async def test_confirm_delete_and_no_resurrection(self):
  await self.settle();self.file.unlink();await self.settle();self.assertIn('/target/a.txt',self.ops.rows)
  await self.engine.resolve(self.rule['id'],'a.txt','delete');await self.settle();self.assertNotIn('/target/a.txt',self.ops.rows);self.assertFalse(self.file.exists())
 async def test_remote_delete_requires_confirmation_and_retains_local_bytes(self):
  await self.settle();self.ops.rows.pop('/target/a.txt');await self.settle();self.assertTrue(self.file.exists())
  await self.engine.resolve(self.rule['id'],'a.txt','delete');self.assertFalse(self.file.exists());self.assertTrue(any(p.read_bytes()==b'one' for p in self.local.glob('.clouddock-history/**/*') if p.is_file()))
 async def test_stale_confirmation_rejected(self):
  await self.settle();self.file.unlink();await self.settle();self.ops.put('/target/a.txt',b'new cloud')
  with self.assertRaises(CloudOperationError):await self.engine.resolve(self.rule['id'],'a.txt','delete')
 async def test_missing_root_never_deletes(self):
  await self.settle();self.file.unlink();self.local.rmdir();await self.settle();self.assertIn('/target/a.txt',self.ops.rows);self.assertEqual(self.issues(),[])
 async def test_account_switch_never_writes(self):
  self.ops.uid='other';await self.settle();self.assertEqual(self.ops.uploads,0)
 async def test_paused_does_not_write(self):
  await self.engine.toggle(self.rule['id'],False);await self.settle();self.assertEqual(self.ops.uploads,0)
 async def test_reopen_keeps_baseline(self):
  await self.settle();self.engine=SyncEngine(self.store,{'baidu':self.ops});await self.settle();self.assertEqual(self.ops.uploads,1)
 async def test_upload_reply_lost_is_adopted_without_duplicate(self):
  self.ops.fail_after='upload';await self.settle();await self.settle();self.assertEqual(self.ops.uploads,1);self.assertIn('/target/a.txt',self.ops.rows)
 async def test_archive_reply_lost_recovers(self):
  await self.settle();self.file.write_bytes(b'two');self.ops.fail_after='archive';await self.settle();await self.settle();self.assertEqual(self.ops.uploads,2);self.assertEqual(self.ops.contents[self.ops.rows['/target/a.txt'].id],b'two')
 async def test_publish_reply_lost_recovers(self):
  self.ops.fail_after='publish';await self.settle();await self.settle();self.assertEqual(self.ops.uploads,1);self.assertIn('/target/a.txt',self.ops.rows)
 async def test_single_file_does_not_include_neighbor(self):
  await self.engine.remove(self.rule['id']);self.rule=await self.engine.preview(self.file,'baidu','/target','file','two_way');self.engine.create(self.rule);(self.local/'neighbor.txt').write_bytes(b'private')
  await self.settle();self.assertNotIn('/target/neighbor.txt',self.ops.rows)
 async def test_new_download_file_binding(self):
  await self.engine.remove(self.rule['id']);self.ops.put('/target/remote.txt',b'cloud')
  self.rule=await self.engine.preview(self.local/'new.txt','baidu','/target','file','download',remote_name='remote.txt');self.engine.create(self.rule)
  await self.settle();self.assertEqual((self.local/'new.txt').read_bytes(),b'cloud')
 async def test_overlap_rejected(self):
  with self.assertRaises(CloudOperationError):await self.engine.preview(self.file,'baidu','/target','file','two_way')
 async def test_windows_case_collision_blocks_entire_plan(self):
  self.ops.put('/target/A.txt',b'one');self.ops.put('/target/a.txt',b'one');await self.settle();self.assertEqual(self.ops.uploads,0)
 async def test_traversal_rejected(self):
  with self.assertRaises(CloudOperationError):safe_local(self.local,'../escape')
 async def test_empty_folder_created(self):
  (self.local/'empty').mkdir();await self.settle();self.assertTrue(self.ops.rows['/target/empty'].is_dir)
 async def test_initial_equal_content_no_duplicate(self):
  self.ops.put('/target/a.txt',b'one');await self.settle();self.assertEqual(self.ops.uploads,0);self.assertEqual(self.issues(),[])
 async def test_local_file_rename_without_reupload(self):
  await self.settle();self.file.rename(self.local/'b.txt');await self.settle();self.assertNotIn('/target/a.txt',self.ops.rows);self.assertIn('/target/b.txt',self.ops.rows);self.assertEqual(self.ops.uploads,1);self.assertEqual(self.issues(),[])
 async def test_remote_rename_downloads_new_content_as_well(self):
  await self.settle();item=self.ops.rows.pop('/target/a.txt');self.ops.rows['/target/b.txt']=replace(item,path='/target/b.txt',revision='changed',size=7);self.ops.contents[item.id]=b'newdata'
  await self.settle();self.assertFalse(self.file.exists());self.assertEqual((self.local/'b.txt').read_bytes(),b'newdata')
 async def test_directory_rename_preserves_children(self):
  (self.local/'folder').mkdir();(self.local/'folder/x.txt').write_bytes(b'child');await self.settle();uploads=self.ops.uploads
  (self.local/'folder').rename(self.local/'newfolder');await self.settle();self.assertIn('/target/newfolder/x.txt',self.ops.rows);self.assertNotIn('/target/folder',self.ops.rows);self.assertEqual(self.ops.uploads,uploads)
 async def test_rename_reply_lost_is_recovered(self):
  await self.settle();self.file.rename(self.local/'b.txt');self.ops.fail_after='publish';await self.settle();await self.settle();self.assertIn('/target/b.txt',self.ops.rows);self.assertEqual(self.ops.uploads,1);self.assertEqual(self.engine.entries('sync_pending',self.rule['id']),[])
 async def test_cloud_rename_is_propagated_locally(self):
  await self.settle();await self.ops.relocate(self.ops.rows['/target/a.txt'],'/target','renamed.txt');await self.settle();self.assertEqual((self.local/'renamed.txt').read_bytes(),b'one');self.assertFalse(self.file.exists())
 async def test_deletion_can_be_restored_instead(self):
  await self.settle();self.file.unlink();await self.settle();await self.engine.resolve(self.rule['id'],'a.txt','remote');self.assertEqual(self.file.read_bytes(),b'one')
 async def test_remote_deletion_can_be_restored(self):
  await self.settle();self.ops.rows.pop('/target/a.txt');await self.settle();await self.engine.resolve(self.rule['id'],'a.txt','local');self.assertEqual(self.ops.contents[self.ops.rows['/target/a.txt'].id],b'one')
 async def test_parallel_previews_cannot_create_overlapping_rules(self):
  await self.engine.remove(self.rule['id']);one=await self.engine.preview(self.local,'baidu','/target','folder','two_way');two=await self.engine.preview(self.local,'baidu','/target','folder','two_way');self.engine.create(one)
  with self.assertRaises(CloudOperationError):self.engine.create(two)
 async def test_resolved_difference_does_not_leave_stale_issue(self):
  await self.settle();self.file.unlink();await self.settle();self.assertEqual(len(self.issues()),1);self.file.write_bytes(b'one');await self.settle();self.assertEqual(self.issues(),[])
 async def test_independent_single_file_rules_in_same_directory(self):
  await self.engine.remove(self.rule['id']);(self.local/'b.txt').write_bytes(b'two')
  self.engine.create(await self.engine.preview(self.file,'baidu','/target','file','two_way'));self.engine.create(await self.engine.preview(self.local/'b.txt','baidu','/target','file','two_way'));self.assertEqual(len(self.engine.rules()),2)
 async def test_preview_does_not_hash_all_content(self):
  await self.engine.remove(self.rule['id'])
  with patch('app.sync_model.content_hash',side_effect=AssertionError('preview must use metadata')):
   result=await self.engine.preview(self.local,'baidu','/target','folder','two_way');self.assertEqual(result['preview']['upload'],1)
 async def test_resolution_queued_and_restored_after_restart(self):
  await self.settle();self.file.unlink();await self.settle();self.engine.request_resolution(self.rule['id'],'a.txt','remote');self.assertFalse(self.file.exists())
  self.engine=SyncEngine(self.store,{'baidu':self.ops});await self.engine.resolve_queued(self.rule['id']);self.assertEqual(self.file.read_bytes(),b'one');self.assertFalse(self.engine.entries('sync_resolution',self.rule['id']))
 async def test_queued_resolution_rechecks_content(self):
  await self.settle();self.file.unlink();await self.settle();self.engine.request_resolution(self.rule['id'],'a.txt','delete');self.ops.put('/target/a.txt',b'new cloud');await self.engine.resolve_queued(self.rule['id']);self.assertIn('/target/a.txt',self.ops.rows);self.assertTrue(self.issues())
 async def test_paused_queue_does_not_mutate(self):
  await self.settle();self.file.unlink();await self.settle();self.engine.request_resolution(self.rule['id'],'a.txt','delete');await self.engine.toggle(self.rule['id'],False);await self.engine.resolve_queued(self.rule['id']);self.assertIn('/target/a.txt',self.ops.rows);self.assertEqual(len(self.engine.entries('sync_resolution',self.rule['id'])),1)
 async def test_quota_error_does_not_lock_rule_removal(self):
  from app.upload import UploadError
  class QuotaUpload(Upload):
   commit_started=False
   async def run(self):raise UploadError('upload_capacity','quota')
  with patch('app.sync_transport.UploadJob',QuotaUpload):await self.settle()
  self.assertFalse(self.engine.entries('sync_pending',self.rule['id']));await self.engine.remove(self.rule['id']);self.assertFalse(self.engine.rules())
