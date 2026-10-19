import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
import httpx
from app.account_profiles import AccountProfiles,ProfileVault
from app.session_vault import SessionVault,SessionStoreError,crypt
from app.workspace import Workspace
from app.cloud_operations import CloudOperationError
from app.durable_store import DurableStore
from desktop.accounts import Accounts
from desktop.settings_portability import SettingsPortability
from desktop.backend import Bridge
from desktop.features import Features
from app.health import Health
from app.diagnostics import check,report
ROOT=Path(__file__).resolve().parents[1]

class ProfileTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.path=Path(self.tmp.name);self.c=AccountProfiles(self.path)
 def tearDown(self):self.tmp.cleanup()
 def jar(self,provider,value):
  cookies=httpx.Cookies();cookies.set('BDUSS' if provider=='baidu' else '__pus',value,domain='.baidu.com' if provider=='baidu' else '.quark.cn');return cookies.jar
 def test_legacy_session_is_reused_without_copy(self):
  old=SessionVault(self.path);old.load('baidu');old.save('baidu',self.jar('baidu','legacy-secret'));v=ProfileVault(self.c,'baidu');self.assertEqual(next(iter(v.load('baidu'))).value,'legacy-secret');self.assertFalse(self.c.path.exists())
 def test_each_profile_has_isolated_encrypted_cookies(self):
  v=ProfileVault(self.c,'baidu');v.load('baidu');v.save('baidu',self.jar('baidu','first-secret'));row=self.c.add('baidu','工作');self.c.select('baidu',row['id']);self.assertFalse(v.load('baidu'));v.save('baidu',self.jar('baidu','second-secret'));self.c.select('baidu','default');self.assertEqual(next(iter(v.load('baidu'))).value,'first-secret');self.assertNotIn(b'secret',b''.join(p.read_bytes() for p in self.path.rglob('*.session')))
 def test_metadata_and_identity_are_encrypted(self):
  self.c.rename('quark','default','private-label');self.c.bind('quark','a'*64);raw=self.c.path.read_bytes();self.assertNotIn(b'private-label',raw);self.assertNotIn(b'a'*64,raw);again=AccountProfiles(self.path);self.assertEqual(again.label('quark','a'*64),'private-label')
 def test_bad_catalog_does_not_overwrite_or_login(self):
  self.c.path.write_bytes(b'not a catalog');again=AccountProfiles(self.path);self.assertTrue(again.public()['error']);self.assertEqual(again.public()['providers'],{})
  with self.assertRaises(SessionStoreError):again.vault('baidu')
  self.assertEqual(self.c.path.read_bytes(),b'not a catalog')
 def test_limit_and_invalid_names(self):
  for i in range(4):self.c.add('baidu',str(i))
  with self.assertRaises(SessionStoreError):self.c.add('baidu','sixth')
  for value in ('','a'*33,'bad\nname',None):
   with self.assertRaises(SessionStoreError):self.c.rename('baidu','default',value)
 def test_remove_only_selected_profile(self):
  row=self.c.add('baidu','second');v=self.c.vault('baidu',row['id']);v.load('baidu');v.save('baidu',self.jar('baidu','private'));self.c.remove('baidu',row['id']);self.assertFalse(v.load('baidu'));self.assertEqual(len(self.c.public()['providers']['baidu']['rows']),1)
 def test_active_remove_and_unknown_select_rejected(self):
  with self.assertRaises(SessionStoreError):self.c.remove('baidu','default')
  with self.assertRaises(SessionStoreError):self.c.select('baidu','../escape')

class SwitchTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.c=AccountProfiles(Path(self.tmp.name)/'sessions');self.other=self.c.add('baidu','工作')['id'];self.record={'id':'rule','provider':'baidu','enabled':True}
  self.job=SimpleNamespace(state='paused',cancel=AsyncMock());self.p=SimpleNamespace(jobs=[self.job],upload_jobs=[],save_remembered=Mock(),cancel=AsyncMock(),startup=AsyncMock(),_closing=False,_queue_hold=False)
  self.sync=SimpleNamespace(rules=lambda:[self.record],toggle=AsyncMock(),lock=lambda rid:asyncio.Lock());self.f=SimpleNamespace(sync=self.sync,capture=Mock(),accounts={'baidu':'owner'},account_status=SimpleNamespace(invalidate=Mock()),thumb_items={})
  self.b=SimpleNamespace(profiles=self.c,panels={'baidu':self.p},features=self.f,update_busy=lambda:False);self.a=Accounts(self.b)
 async def asyncTearDown(self):self.tmp.cleanup()
 async def test_switch_preserves_jobs_in_store_and_never_cancels_partial(self):
  await self.a.action({'action':'profiles_switch','provider':'baidu','id':self.other});self.job.cancel.assert_not_awaited();self.f.capture.assert_called_once_with('baidu',force=True);self.sync.toggle.assert_awaited_once_with('rule',False);self.assertEqual(self.c.provider('baidu')['active'],self.other);self.assertEqual(self.p.jobs,[]);self.p.startup.assert_awaited_once_with(saved_only=True);self.assertFalse(self.a.switching)
 async def test_busy_switch_does_not_disconnect(self):
  self.b.update_busy=lambda:True
  with self.assertRaises(CloudOperationError):await self.a.action({'action':'profiles_switch','provider':'baidu','id':self.other})
  self.p.cancel.assert_not_awaited();self.assertEqual(self.c.provider('baidu')['active'],'default')
 async def test_remove_current_keeps_remaining_account(self):
  await self.a.action({'action':'profiles_remove','provider':'baidu','id':'default'});self.assertEqual(self.c.provider('baidu')['active'],self.other);self.assertEqual(len(self.c.provider('baidu')['rows']),1)
 async def test_nonactive_remove_does_not_interrupt_current(self):
  await self.a.action({'action':'profiles_remove','provider':'baidu','id':self.other});self.p.cancel.assert_not_awaited()
 async def test_full_catalog_add_does_not_disconnect(self):
  for i in range(3):self.c.add('baidu','其他'+str(i))
  with self.assertRaises(SessionStoreError):await self.a.action({'action':'profiles_add','provider':'baidu','name':'第六个'})
  self.p.cancel.assert_not_awaited()
 async def test_current_account_switch_is_noop(self):
  await self.a.action({'action':'profiles_switch','provider':'baidu','id':'default'});self.p.cancel.assert_not_awaited()

class SettingsTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name);self.store=DurableStore(self.root/'db');self.b=SimpleNamespace(workspace=Workspace(self.root/'data'),update_busy=lambda:False,features=SimpleNamespace(store=self.store));self.s=SettingsPortability(self.b)
 async def asyncTearDown(self):self.store.close();self.tmp.cleanup()
 async def preview(self,fields):return await self.s.action({'action':'settings_preview','text':json.dumps({'schema':1,'product':'Cloud Dock','settings':fields})})
 async def test_export_excludes_credentials_paths_tasks_and_update_source(self):
  self.b.workspace.settings.update(download_dir='H:/secret',cookie='secret-token',update_proxy_url='http://secret',feedback={'sound_volume':40,'download_sound':False});out=await self.s.action({'action':'settings_export'});raw=json.dumps(out);self.assertNotIn('secret',raw);self.assertNotIn('update_',raw);self.assertEqual(out['settings']['feedback']['sound_volume'],40)
 async def test_import_preview_no_write_then_roundtrip(self):
  before=dict(self.b.workspace.settings);r=await self.preview({'theme':'dark','feedback':{'sound_volume':10}});self.assertEqual(self.b.workspace.settings,before);await self.s.action({'action':'settings_apply','token':r['token']});restored=Workspace(self.root/'data');self.assertEqual(restored.settings['theme'],'dark');self.assertEqual(restored.settings['feedback']['sound_volume'],10);self.assertEqual(restored.settings['download_dir'],before['download_dir'])
 async def test_one_use_confirmation(self):
  r=await self.preview({'theme':'dark'});await self.s.action({'action':'settings_apply','token':r['token']})
  with self.assertRaises(CloudOperationError):await self.s.action({'action':'settings_apply','token':r['token']})
 async def test_invalid_or_sensitive_fields_rejected_atomically(self):
  for f in ({'download_dir':'H:/other'},{'theme':[]},{'close_to_tray':1},{'feedback':{'sound_volume':101}},{'feedback':{'quiet_start':'25:00'}},{'feedback':{'cookie':'secret'}}):
   with self.assertRaises(CloudOperationError):await self.preview(f)
  self.assertEqual(self.b.workspace.settings['theme'],'light')
 async def test_duplicate_and_large_json_rejected(self):
  for raw in ('{"schema":1,"schema":1,"product":"Cloud Dock","settings":{}}','x'*65537):
   with self.assertRaises(CloudOperationError):await self.s.action({'action':'settings_preview','text':raw})
 async def test_import_busy_or_save_failed_preserves_current_settings(self):
  r=await self.preview({'theme':'dark'});self.b.update_busy=lambda:True
  with self.assertRaises(CloudOperationError):await self.s.action({'action':'settings_apply','token':r['token']})
  self.b.update_busy=lambda:False;r=await self.preview({'theme':'dark'})
  with patch.object(self.b.workspace,'save',new=AsyncMock(side_effect=OSError('full'))):
   with self.assertRaises(OSError):await self.s.action({'action':'settings_apply','token':r['token']})
  self.assertEqual(self.b.workspace.settings['theme'],'light')
 async def test_disabling_names_removes_old_notification_names(self):
  self.store.put('notification','n',{'id':'n','name':'private.txt'});r=await self.preview({'feedback':{'notification_filenames':False}});await self.s.action({'action':'settings_apply','token':r['token']});self.assertNotIn('name',self.store.get('notification','n'))

class HealthTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_controllers_restore_each_saved_profile_after_switch(self):
  from desktop.backend import Page
  from app.baidu_web_panel import BaiduWebPanel
  from app.quark_panel import QuarkPanel
  for provider,Controller,cookie,domain in [('baidu',BaiduWebPanel,'BDUSS','.baidu.com'),('quark',QuarkPanel,'__pus','.quark.cn')]:
   with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as folder:
    catalog=AccountProfiles(Path(folder));second=catalog.add(provider,'工作')['id']
    for key,secret in [('default','first-cookie'),(second,'second-cookie')]:
     vault=catalog.vault(provider,key);vault.load(provider);jar=httpx.Cookies();jar.set(cookie,secret,domain=domain);vault.save(provider,jar.jar)
    client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500)))
    p=Controller(Page(),client=client,session_store=ProfileVault(catalog,provider));p.set_download_root(folder)
    seen=[]
    async def restore():seen.append(client.cookies.get(cookie));p.api.authenticated=True
    p.api.restore_login=restore;p.show_connected_directory=AsyncMock()
    features=SimpleNamespace(sync=SimpleNamespace(rules=lambda:[]),capture=Mock(),accounts={},account_status=SimpleNamespace(invalidate=Mock()),thumb_items={})
    manager=Accounts(SimpleNamespace(profiles=catalog,panels={provider:p},features=features,update_busy=lambda:False))
    try:
     await p.startup(saved_only=True);await p.task
     await manager.action({'action':'profiles_switch','provider':provider,'id':second});await p.task
     await manager.action({'action':'profiles_switch','provider':provider,'id':'default'});await p.task
     self.assertEqual(seen,['first-cookie','second-cookie','first-cookie']);self.assertTrue(p.api.authenticated)
     reopened=AccountProfiles(Path(folder));self.assertEqual(next(iter(reopened.vault(provider,second).load(provider))).value,'second-cookie')
    finally:await p.shutdown();await client.aclose()
 async def test_diagnostics_excludes_private_content_and_checks_database(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as folder:
   b=Bridge({},Workspace(Path(folder)));await b.start();b.profiles.rename('baidu','default','private nickname')
   try:
    r=await check(b);self.assertTrue(r['database_ok']);self.assertTrue(r['storage']['writable']);self.assertNotIn('private nickname',json.dumps(r));self.assertNotIn(folder,json.dumps(r));self.assertIn('backend_working_set_bytes',r['performance']);self.assertFalse(list((Path(folder)/'work').glob('health-*')))
   finally:await b.close()
 async def test_completed_job_compaction_keeps_records_and_active_work(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as folder:
   store=DurableStore(Path(folder)/'db');jobs=[]
   for i in range(100):
    j=SimpleNamespace(state='completed',_desktop_id=str(i));jobs.append(j);store.put('transfer',str(i),{'id':str(i)})
   active=SimpleNamespace(state='downloading');jobs.append(active);p=SimpleNamespace(jobs=jobs,upload_jobs=[],_live_errors={});f=SimpleNamespace(bridge=SimpleNamespace(panels={'baidu':p}),store=store,last_write={},last_write_time={},seen_completed=set())
   Features.compact(f,'baidu');self.assertEqual(len(p.jobs),51);self.assertIn(active,p.jobs);self.assertEqual(len(store.all('transfer')),100);store.close()
