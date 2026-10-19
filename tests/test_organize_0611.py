import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock,patch
from uuid import uuid4
from app.cloud_operations import CloudOperationError
from app.durable_store import DurableStore
from app.retired_backup import retire
from app.sync_engine import SyncEngine
from app.sync_model import local_snapshot,remote_mark
from app.history_manager import HistoryManager
from desktop.sync_review import SyncReview
from desktop.cross_copy import CrossCopy
from desktop.link_inbox import LinkInbox
from desktop.share_actions import ShareActions
from test_sync_060 import Ops as OriginalOps,Download
ROOT=Path(__file__).resolve().parents[1]

class Ops(OriginalOps):
 def guard(self,stamp):
  if not stamp or stamp!=self.session:raise CloudOperationError('account changed')
 async def list(self,path,limit=None):return await super().list(path)
 async def mkdir(self,parent,name,exclusive=False):
  path=parent.rstrip('/')+'/'+name
  if path in self.rows and exclusive:raise CloudOperationError('exists')
  return self.rows.get(path) or self.put(path,directory=True)

class ReviewTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name);self.local=self.root/'local';self.local.mkdir();(self.local/'a.txt').write_bytes(b'current')
  self.store=DurableStore(self.root/'state.db');self.ops=Ops();await self.ops.ensure('/target')
  self.engine=SyncEngine(self.store,{'baidu':self.ops});self.rule=await self.engine.preview(self.local,'baidu','/target','folder','two_way');self.engine.create(self.rule)
  self.f=SimpleNamespace(sync=self.engine,store=self.store,ops={'baidu':self.ops},history=HistoryManager(self.engine));self.review=SyncReview(self.f)
 async def asyncTearDown(self):await self.engine.close();self.store.close();self.tmp.cleanup()
 async def archive(self):
  await self.engine.toggle(self.rule['id'],False)
  path=self.local/'.clouddock-history'/self.rule['id']/uuid4().hex/'a.txt';path.parent.mkdir(parents=True);path.write_bytes(b'old');return path
 async def test_retirement_preserves_all_content_and_is_idempotent(self):
  self.store.put('backup','old',{'id':'old','enabled':True,'local':str(self.local),'target':'/old'})
  self.store.put('backup_file','old:a.txt',{'id':'old:a.txt','state':'done'});retire(self.store);retire(self.store)
  self.assertFalse(self.store.get('backup','old')['enabled']);self.assertTrue(self.store.get('backup','old')['retired']);self.assertTrue((self.local/'a.txt').exists());self.assertIsNotNone(self.store.get('backup_file','old:a.txt'))
 async def test_changes_does_not_upload_or_delete(self):
  self.ops.put('/target/b.txt',b'other');before=dict(self.ops.rows)
  out=await self.review.changes(self.rule['id']);self.assertEqual(out['counts']['upload'],1);self.assertEqual(out['counts']['download'],1);self.assertEqual(self.ops.rows,before);self.assertEqual(self.store.all('sync_base'),[])
 async def test_delete_requires_baseline_and_remains_preview(self):
  item=self.ops.put('/target/a.txt',b'current');local=local_snapshot(self.rule)['a.txt'];self.engine.commit(self.rule,'a.txt',local,remote_mark(item));(self.local/'a.txt').unlink()
  out=await self.review.changes(self.rule['id']);self.assertEqual(out['rows'][0]['action'],'delete_remote');self.assertIn('/target/a.txt',self.ops.rows)
 async def test_disconnected_volume_never_becomes_delete(self):
  before=dict(self.ops.rows);moved=self.local.with_name('offline');self.local.rename(moved)
  with self.assertRaises(CloudOperationError):await self.review.changes(self.rule['id'])
  self.assertEqual(self.ops.rows,before);moved.rename(self.local);out=await self.review.changes(self.rule['id']);self.assertEqual(out['counts']['upload'],1)
 async def test_network_recovery_keeps_baseline(self):
  before=self.engine.baseline(self.rule['id']);old=self.ops.list;self.ops.list=AsyncMock(side_effect=OSError('offline'))
  await self.engine.check(self.rule['id'],True);self.assertEqual(self.engine.baseline(self.rule['id']),before);self.assertGreater(self.store.get('sync_rule',self.rule['id'])['failures'],0)
  self.ops.list=old;await self.engine.check(self.rule['id'],True);self.assertEqual(self.store.get('sync_rule',self.rule['id'])['failures'],0)
 async def test_history_requires_paused_rule(self):
  with self.assertRaises(CloudOperationError):await self.review.versions(self.rule['id'],'local')
 async def test_restore_local_copy_does_not_overwrite_or_remove_history(self):
  original=await self.archive();row=(await self.review.versions(self.rule['id'],'local'))['rows'][0]
  out=await self.review.restore(row['token']);self.assertEqual(Path(out['path']).read_bytes(),b'old');self.assertEqual(original.read_bytes(),b'old');self.assertEqual((self.local/'a.txt').read_bytes(),b'current');self.assertFalse(self.store.get('sync_rule',self.rule['id'])['enabled'])
 async def test_local_history_available_when_cloud_offline(self):
  await self.archive();self.ops.account=AsyncMock(side_effect=OSError('offline'));self.ops.list=AsyncMock(side_effect=OSError('offline'));self.ops.session=None
  row=(await self.review.versions(self.rule['id'],'local'))['rows'][0];out=await self.review.restore(row['token']);self.assertTrue(Path(out['path']).is_file());self.ops.account.assert_not_awaited()
 async def test_history_token_single_use(self):
  await self.archive();row=(await self.review.versions(self.rule['id'],'local'))['rows'][0];await self.review.restore(row['token'])
  with self.assertRaises(CloudOperationError):await self.review.restore(row['token'])
 async def test_changed_history_refuses_restore(self):
  path=await self.archive();row=(await self.review.versions(self.rule['id'],'local'))['rows'][0];path.write_bytes(b'changed')
  with self.assertRaises(CloudOperationError):await self.review.restore(row['token'])
  self.assertEqual(len(list(self.local.glob('*.txt'))),1)
 async def test_resuming_rule_invalidates_restore(self):
  await self.archive();row=(await self.review.versions(self.rule['id'],'local'))['rows'][0];await self.engine.toggle(self.rule['id'],True)
  with self.assertRaises(CloudOperationError):await self.review.restore(row['token'])
 async def test_cloud_history_recovery_checks_account(self):
  await self.engine.toggle(self.rule['id'],False);self.ops.uid='other'
  with self.assertRaises(CloudOperationError):await self.review.versions(self.rule['id'],'cloud')
 async def test_cloud_history_restore_copy(self):
  await self.engine.toggle(self.rule['id'],False);base='/target/.clouddock-history/'+self.rule['id']+'/'+uuid4().hex;await self.ops.ensure(base);self.ops.put(base+'/a.txt',b'oldcloud')
  row=(await self.review.versions(self.rule['id'],'cloud'))['rows'][0]
  with patch('app.sync_transport.DirectDownload',Download):out=await self.review.restore(row['token'])
  self.assertEqual(Path(out['path']).read_bytes(),b'oldcloud');self.assertEqual((self.local/'a.txt').read_bytes(),b'current')
 async def test_exclusion_preview_prunes_whole_folder(self):
  await self.engine.remove(self.rule['id']);(self.local/'Intermediate').mkdir();(self.local/'Intermediate'/'cache.bin').write_bytes(b'temp')
  p=await self.engine.preview(self.local,'baidu','/target','folder','two_way',['Intermediate']);self.assertEqual(p['excluded']['rows'],['Intermediate']);self.assertEqual(p['count'],1)

class CopyDownload(Download):
 def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.state='queued'
 async def run(self):result=await super().run();self.state='completed';return result
class CopyUpload:
 count=0;lose_reply=False
 def __init__(self,local,provider,directory,adapter,**kw):self.local=Path(local);self.ops=adapter.api.client;self.path=directory.rstrip('/')+'/'+kw['remote_name'];self.kw=kw
 async def run(self):
  if self.kw['current_owner']()!=self.kw['owner']:raise CloudOperationError('owner changed')
  CopyUpload.count+=1;item=self.ops.put(self.path,self.local.read_bytes())
  if CopyUpload.lose_reply:CopyUpload.lose_reply=False;raise CloudOperationError('lost upload reply')
  return item
class CrossTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name);self.store=DurableStore(self.root/'db')
  self.src=Ops();self.dst=Ops();self.dst.provider='quark';self.dst.uid='q-owner';self.dst.session='q-session';self.file=self.src.put('/a.txt',b'content')
  p=SimpleNamespace(browser_rows=[self.file],browser_busy=False,share=None,api=self.src.api,make_download=CopyDownload)
  f=SimpleNamespace(store=self.store,ops={'baidu':self.src,'quark':self.dst},accounts={'baidu':self.src.uid,'quark':self.dst.uid})
  self.bridge=SimpleNamespace(features=f,workspace=SimpleNamespace(data_dir=self.root),panels={'baidu':p},context=lambda p:'ctx')
  self.bridge.productivity=SimpleNamespace(owner=AsyncMock(return_value=('baidu',p,self.src,self.src.uid,self.src.session)))
  self.cross=CrossCopy(self.bridge);CopyUpload.count=0;CopyUpload.lose_reply=False;self.patch=patch('desktop.cross_copy.UploadJob',CopyUpload);self.patch.start()
 async def asyncTearDown(self):await self.cross.close();self.patch.stop();self.store.close();self.tmp.cleanup()
 async def plan(self,mode='skip'):
  out=await self.cross.action({'action':'cross_prepare','provider':'baidu','target_provider':'quark','target':'/','context':'ctx','ids':[self.file.id],'policy':mode})
  await self.cross.tasks[out['id']];return self.cross.jobs[out['id']]
 async def execute(self,row):await self.cross.action({'action':'cross_start','id':row['id']});await self.cross.tasks[row['id']]
 async def test_preview_no_mutations_and_normal_copy(self):
  row=await self.plan();self.assertEqual(row['state'],'ready');self.assertEqual(self.dst.rows,{})
  await self.execute(row);self.assertEqual(row['state'],'completed');self.assertEqual(self.dst.contents[self.dst.rows['/a.txt'].id],b'content');self.assertIn('/a.txt',self.src.rows)
 async def test_same_name_skip(self):
  self.dst.put('/a.txt',b'old');row=await self.plan();await self.execute(row);self.assertEqual(CopyUpload.count,0);self.assertEqual(self.dst.contents[self.dst.rows['/a.txt'].id],b'old')
 async def test_keep_both_does_not_overwrite(self):
  self.dst.put('/a.txt',b'old');row=await self.plan('rename');await self.execute(row);self.assertEqual(row['state'],'completed');self.assertIn('/a (1).txt',self.dst.rows)
 async def test_source_changed_after_preview(self):
  row=await self.plan();self.src.put('/a.txt',b'new');await self.execute(row);self.assertEqual(row['state'],'failed');self.assertEqual(CopyUpload.count,0)
 async def test_original_account_required(self):
  row=await self.plan();self.dst.uid='different'
  with self.assertRaises(CloudOperationError):await self.execute(row)
  with self.assertRaises(CloudOperationError):await self.cross.action({'action':'cross_status','id':row['id']})
 async def test_new_destination_collision_stops(self):
  row=await self.plan();self.dst.put('/a.txt',b'other');await self.execute(row);self.assertEqual(row['state'],'failed');self.assertEqual(CopyUpload.count,0)
 async def test_lost_publish_reply_is_not_reuploaded(self):
  row=await self.plan();self.dst.fail_after='publish';await self.execute(row);self.assertEqual(row['state'],'failed');await self.execute(row);self.assertEqual(row['state'],'completed');self.assertEqual(CopyUpload.count,1)
 async def test_lost_upload_reply_verifies_content_before_adopting(self):
  row=await self.plan();CopyUpload.lose_reply=True;await self.execute(row);self.assertEqual(row['state'],'failed')
  with patch('app.sync_transport.DirectDownload',Download):await self.execute(row)
  self.assertEqual(row['state'],'completed');self.assertEqual(CopyUpload.count,1)
 async def test_restart_requires_explicit_resume(self):
  row=await self.plan();row['state']='running';self.cross.save(row);new=CrossCopy(self.bridge);new.load();self.assertEqual(new.jobs[row['id']]['state'],'paused');self.assertFalse(new.tasks)
 async def test_folder_structure_and_empty_directories(self):
  self.file=self.src.put('/folder',directory=True);self.src.put('/folder/nested',directory=True);self.src.put('/folder/nested/f.txt',b'hello');self.src.put('/folder/empty',directory=True);self.bridge.panels['baidu'].browser_rows=[self.file]
  row=await self.plan();await self.execute(row);self.assertEqual(row['state'],'completed');self.assertIn('/folder/nested/f.txt',self.dst.rows);self.assertTrue(self.dst.rows['/folder/empty'].is_dir)
 async def test_summaries_hide_other_accounts_and_omit_large_rows(self):
  await self.plan();self.assertEqual(self.cross.summaries()[0]['rows'],[]);self.bridge.features.accounts['quark']='other';self.assertEqual(self.cross.summaries(),[])
 async def test_remove_preserves_cloud_source_and_target(self):
  row=await self.plan();await self.execute(row);await self.cross.action({'action':'cross_remove','id':row['id']});self.assertIn('/a.txt',self.src.rows);self.assertIn('/a.txt',self.dst.rows)

class InboxTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.store=DurableStore(Path(self.tmp.name)/'db');self.inbox=LinkInbox(SimpleNamespace(features=SimpleNamespace(store=self.store)))
 async def asyncTearDown(self):self.store.close();self.tmp.cleanup()
 async def test_encrypted_dedup_and_password_edit(self):
  url='https://pan.quark.cn/s/11bbafa97c9b?pwd=W2pz';a=await self.inbox.action({'action':'inbox_add','text':url});await self.inbox.action({'action':'inbox_edit','text':url,'password':'abcd','title':'资料'})
  raw=json.dumps(self.store.all('link_inbox'));self.assertNotIn('11bbafa97c9b',raw);self.assertNotIn('abcd',raw);rows=self.inbox.rows();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['password'],'abcd');self.assertEqual(rows[0]['id'],a['id'])
 async def test_multiple_links_rejected(self):
  with self.assertRaises(CloudOperationError):await self.inbox.action({'action':'inbox_add','text':'https://pan.quark.cn/s/11bbafa97c9b https://pan.quark.cn/s/22bbafa97c9b'})
 async def test_no_implicit_clipboard_storage(self):self.assertEqual(self.inbox.rows(),[])
 async def test_edit_preserves_last_folder_and_page(self):
  link={'url':'https://pan.quark.cn/s/11bbafa97c9b','provider':'quark','password':'W2pz'}
  self.inbox.save(link,path='/folder',page=2)
  row=await self.inbox.action({'action':'inbox_edit','text':link['url'],'password':'abcd','title':'资料'})
  self.assertEqual((row['path'],row['page']),('/folder',2))
 async def test_clear_only_inbox(self):
  self.store.put('sync_rule','r',{'id':'r'});await self.inbox.action({'action':'inbox_clear'});self.assertIsNotNone(self.store.get('sync_rule','r'))

class SharingTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.ops=Ops();self.file=self.ops.put('/a.txt',b'one');self.panel=SimpleNamespace(browser_rows=[self.file],browser_busy=False)
  self.b=SimpleNamespace(context=lambda p:'ctx',features=SimpleNamespace(ops={'baidu':self.ops}),productivity=SimpleNamespace(owner=AsyncMock(return_value=('baidu',self.panel,self.ops,self.ops.uid,self.ops.session))))
  self.s=ShareActions(self.b);self.s.baidu=AsyncMock(return_value={'link':'https://pan.baidu.com/s/1abcdefghijk'})
 async def prepare(self,**kw):return await self.s.action({'action':'sharing_prepare','kind':'create','provider':'baidu','context':'ctx','ids':[self.file.id],'days':7,'password':'a1b2',**kw})
 async def test_prepare_read_only_and_commit_single_use(self):
  r=await self.prepare();self.s.baidu.assert_not_awaited();out=await self.s.action({'action':'sharing_commit','token':r['token']});self.assertEqual(out['password'],'a1b2')
  with self.assertRaises(CloudOperationError):await self.s.action({'action':'sharing_commit','token':r['token']})
  self.s.baidu.assert_awaited_once();self.assertEqual(self.s.baidu.call_args.args[2]['schannel'],'4')
 async def test_mutation_of_selected_file_blocks_share(self):
  r=await self.prepare();self.ops.put('/a.txt',b'other')
  with self.assertRaises(CloudOperationError):await self.s.action({'action':'sharing_commit','token':r['token']})
  self.s.baidu.assert_not_awaited()
 async def test_empty_password_and_invalid_expiry_rejected(self):
  for kwargs in ({'password':''},{'days':0},{'days':True}):
   with self.assertRaises(CloudOperationError):await self.prepare(**kwargs)
 async def test_changed_account_stops_before_publish(self):
  r=await self.prepare();self.ops.session='new'
  with self.assertRaises(CloudOperationError):await self.s.action({'action':'sharing_commit','token':r['token']})
  self.s.baidu.assert_not_awaited()
 async def test_wrong_response_domain_never_exposed(self):
  self.s.baidu.return_value={'link':'https://evil.example/token'};r=await self.prepare()
  with self.assertRaises(CloudOperationError):await self.s.action({'action':'sharing_commit','token':r['token']})
 async def test_quark_seven_day_private_share_contract(self):
  self.ops.provider='quark';self.b.features.ops={'quark':self.ops};self.b.productivity.owner.return_value=('quark',self.panel,self.ops,self.ops.uid,self.ops.session)
  self.ops.call=AsyncMock(side_effect=[{'task_id':'task123'},{'share_id':'share123'},{'share_url':'https://pan.quark.cn/s/11bbafa97c9b','passcode':'a1b2'}])
  r=await self.prepare(provider='quark');out=await self.s.action({'action':'sharing_commit','token':r['token']})
  self.assertEqual(out['days'],7);calls=self.ops.call.await_args_list
  self.assertEqual(calls[0].args[1]['expired_type'],3);self.assertEqual(calls[0].args[1]['url_type'],2);self.assertEqual(calls[0].args[1]['passcode'],'a1b2')
 async def test_cloud_save_uses_frozen_share_directory_and_new_folder(self):
  shared=replace(self.file,path='/nested/a.txt');listing=AsyncMock(return_value=[shared])
  session={'provider':'baidu','panel':self.panel,'ops':self.ops,'rows':[shared],'revision':1,'path':'/nested','page':0,'share':SimpleNamespace(id='share-id',uk='owner'),'list':listing,'client':None}
  self.b.shares=SimpleNamespace(session=lambda token:session)
  self.ops.put('/target',directory=True)
  prepared=await self.s.action({'action':'sharing_prepare','kind':'save','session':'s','context':'1','ids':[shared.id],'target':'/target'})
  self.assertNotIn(prepared['target'],self.ops.rows)
  session['path']='/';session['page']=3
  async def transfer(ops,route,data,extra,client):
   self.assertEqual(route,'transfer');self.assertEqual(extra,{'shareid':'share-id','from':'owner'});self.ops.put(data['path']+'/a.txt',b'one');return {}
  self.s.baidu.side_effect=transfer
  out=await self.s.action({'action':'sharing_commit','token':prepared['token']})
  listing.assert_awaited_once_with('/nested',0);self.assertEqual(out['count'],1);self.assertIn('/a.txt',self.ops.rows);self.assertEqual(out['path'],prepared['target'])
