import asyncio,json,os,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from app.durable_store import DurableStore
from app.sync_model import identity
from app.sync_engine import SyncEngine
from app.history_manager import HistoryManager
from app.cloud_operations import CloudOperationError
from app.diagnostics import report
from desktop.migrate import import_profile
from test_sync_060 import Ops
ROOT=Path(__file__).resolve().parents[1]

class MigrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.base=Path(self.tmp.name);self.old=self.base/'portable';(self.old/'data/sessions').mkdir(parents=True)
  self.prefs=b'{"download_dir":"H:/existing-downloads","theme":"dark"}';(self.old/'data/preferences.json').write_bytes(self.prefs)
  (self.old/'data/sessions/baidu.session').write_bytes(b'encrypted-data-not-a-cookie')
  self.new=self.base/'profile'
 def tearDown(self):self.tmp.cleanup()
 def test_import_preserves_source_and_login_but_pauses_automation(self):
  store=DurableStore(self.old/'data/activity.sqlite3');store.put('sync_rule','a',{'id':'a','enabled':True});store.put('backup','b',{'id':'b','enabled':True});store.put('sync_pending','p',{'id':'p','phase':'published'});store.put('sync_resolution','r',{'id':'r'});store.close()
  result=import_profile(self.old,self.new);self.assertEqual(result['paused'],2)
  self.assertEqual((self.new/'preferences.json').read_bytes(),self.prefs);self.assertEqual((self.new/'sessions/baidu.session').read_bytes(),(self.old/'data/sessions/baidu.session').read_bytes())
  store=DurableStore(self.new/'activity.sqlite3');self.assertFalse(store.get('sync_rule','a')['enabled']);self.assertFalse(store.get('backup','b')['enabled']);self.assertIsNotNone(store.get('sync_pending','p'));self.assertEqual(store.all('sync_resolution'),[]);store.close()
  store=DurableStore(self.old/'data/activity.sqlite3');self.assertTrue(store.get('sync_rule','a')['enabled']);store.close()
 def test_existing_destination_is_never_overwritten(self):
  self.new.mkdir();(self.new/'precious.txt').write_text('keep')
  with self.assertRaises(ValueError):import_profile(self.old,self.new)
  self.assertEqual((self.new/'precious.txt').read_text(),'keep')
 def test_nested_import_is_rejected(self):
  with self.assertRaises(ValueError):import_profile(self.old,self.old/'data/new')
 def test_private_webview_cache_is_not_imported(self):
  (self.old/'data/webview').mkdir();(self.old/'data/webview/cookie').write_text('secret');import_profile(self.old,self.new);self.assertFalse((self.new/'webview').exists())
 def test_live_source_is_rejected(self):
  from app.process_lock import ProcessLock
  lease=ProcessLock(self.old/'data/.backend-lock');lease.acquire()
  try:
   with self.assertRaises(OSError):import_profile(self.old,self.new)
  finally:lease.close()
  self.assertFalse(self.new.exists())

class HistoryTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name);self.local=self.root/'files';self.local.mkdir();self.store=DurableStore(self.root/'db');self.ops=Ops();await self.ops.ensure('/target')
  self.engine=SyncEngine(self.store,{'baidu':self.ops});self.r=await self.engine.preview(self.local,'baidu','/target','folder','two_way');self.engine.create(self.r);await self.engine.toggle(self.r['id'],False);self.history=HistoryManager(self.engine)
  self.created=[]
  for i in range(4):
   p=self.local/'.clouddock-history'/self.r['id']/uuid.uuid4().hex/'name.txt';p.parent.mkdir(parents=True);p.write_text(str(i));self.created.append(p)
  (self.local/'name.txt').write_text('current')
 async def asyncTearDown(self):await self.engine.close();self.store.close();self.tmp.cleanup()
 async def preview(self):return await self.history.preview(self.r['id'],'local',1,0)
 async def test_preview_is_readonly_then_only_old_versions_removed(self):
  d=await self.preview();self.assertEqual(d['count'],4);self.assertEqual(d['selected'],3);self.assertEqual(sum(p.exists() for p in self.created),4)
  await self.history.clean(d['confirmation']);self.assertEqual(sum(p.exists() for p in self.created),1);self.assertEqual((self.local/'name.txt').read_text(),'current')
 async def test_modified_history_blocks_cleanup(self):
  d=await self.preview();plan=self.history.previews[d['confirmation']];p=Path(plan['rows'][0]['path']);p.write_text('changed')
  with self.assertRaises(CloudOperationError):await self.history.clean(d['confirmation'])
  self.assertEqual(p.read_text(),'changed')
 async def test_enabled_rule_blocks_preview_and_stale_confirmation(self):
  d=await self.preview();await self.engine.toggle(self.r['id'],True)
  with self.assertRaises(CloudOperationError):await self.preview()
  with self.assertRaises(CloudOperationError):await self.history.clean(d['confirmation'])
 async def test_pending_publication_protects_all_versions(self):
  self.store.put('sync_pending','p',{'rule':self.r['id']})
  with self.assertRaises(CloudOperationError):await self.preview()
 async def test_retention_days_keep_recent_versions(self):
  d=await self.history.preview(self.r['id'],'local',1,30);self.assertEqual(d['selected'],0)
 async def test_cloud_cleanup_revalidates_identity(self):
  base='/target/.clouddock-history/'+self.r['id']
  for i in range(3):
   folder=base+'/'+uuid.uuid4().hex;await self.ops.ensure(folder);item=self.ops.put(folder+'/x.txt',b'old')
   self.store.put('sync_history',item.id,{'id':item.id,'rule':self.r['id'],'side':'cloud','archive':item.path,'remote_id':item.id,'rel':'x.txt','at':1+i})
  d=await self.history.preview(self.r['id'],'cloud',1,0);self.assertEqual(d['selected'],2)
  self.ops.uid='other'
  with self.assertRaises(CloudOperationError):await self.history.clean(d['confirmation'])
 async def test_cloud_cleanup_does_not_touch_current_files(self):
  self.ops.put('/target/x.txt',b'current')
  base='/target/.clouddock-history/'+self.r['id']
  for i in range(3):
   folder=base+'/'+uuid.uuid4().hex;await self.ops.ensure(folder);item=self.ops.put(folder+'/x.txt',b'old')
   self.store.put('sync_history',item.id,{'id':item.id,'rule':self.r['id'],'side':'cloud','archive':item.path,'remote_id':item.id,'rel':'x.txt','at':1+i})
  d=await self.history.preview(self.r['id'],'cloud',1,0);out=await self.history.clean(d['confirmation']);self.assertEqual(out['removed'],2);self.assertIn('/target/x.txt',self.ops.rows)
 async def test_legacy_cloud_history_with_unknown_origin_is_protected(self):
  base='/target/.clouddock-history/'+self.r['id']
  for i in range(3):
   folder=base+'/'+uuid.uuid4().hex;await self.ops.ensure(folder);self.ops.put(folder+'/same-name.txt',b'old')
  d=await self.history.preview(self.r['id'],'cloud',1,0);self.assertEqual(d['count'],3);self.assertEqual(d['selected'],0)
 async def test_confirmation_cannot_be_replayed(self):
  d=await self.preview();await self.history.clean(d['confirmation'])
  with self.assertRaises(CloudOperationError):await self.history.clean(d['confirmation'])

class DiagnosticTests(unittest.TestCase):
 def test_no_raw_errors_or_paths_or_account_identifiers(self):
  secret='PRIVATE_COOKIE_PATH_FILENAME';p=SimpleNamespace(api=SimpleNamespace(authenticated=True),browser_busy=False,password=secret)
  b=SimpleNamespace(deferred=False,initialization_error=secret,panels={'baidu':p},features=None)
  d=json.dumps(report(b));self.assertNotIn(secret,d);self.assertNotIn('password',d);self.assertIn('initialization_failed',d)
