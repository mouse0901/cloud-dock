import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.batch_names import choose_name, renamed
from app.cloud_operations import CloudOperationError, CloudOperations
from app.domain import CloudFile
from app.durable_store import DurableStore
from desktop.batch_operations import BatchOperations, local_tree
from desktop.productivity import Productivity

ROOT = Path(__file__).resolve().parents[1]


class Names(unittest.TestCase):
    def test_reservation_and_casefold_preserve_extension(self):
        names = {'report.pdf', 'report (1).pdf'}
        self.assertEqual(choose_name('REPORT.pdf', names, 'rename'), 'REPORT (2).pdf')
        self.assertEqual(choose_name('REPORT.pdf', names, 'rename'), 'REPORT (3).pdf')
        self.assertIsNone(choose_name('REPORT.pdf', names, 'skip'))

    def test_numbering_and_literal_replace_keep_extension(self):
        self.assertEqual(renamed('报告.old.pdf', {'find':'.old','replace':'','prefix':'项目-','suffix':'-终稿','start':9}, 2), '项目-报告-终稿_011.pdf')
        self.assertEqual(renamed('folder.ext', {'suffix':'-new'}, 0, True), 'folder.ext-new')

    def test_invalid_policy_and_number_rejected(self):
        with self.assertRaises(CloudOperationError): choose_name('x', set(), 'overwrite')
        for start in (True, -1, 0, '3', 1000000):
            with self.subTest(start=start), self.assertRaises(CloudOperationError): renamed('a.txt', {'start':start}, 0)


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT/'work/test-runs').mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.stamp='A'; self.account='account'
        self.tree={'/':[], '/dest':[]}; self.relocate_calls=[]
        self.ops=SimpleNamespace(account=self.get_account, stamp=lambda:self.stamp, guard=self.guard, lock=asyncio.Lock(), list=self.listing,
            mkdir=self.mkdir, relocate=self.relocate, identity='account', identity_stamp='A')
        api=SimpleNamespace(authenticated=True)
        self.panel=SimpleNamespace(path='/',browser_busy=False,browser_rows=[],jobs=[],upload_jobs=[],load_directory=AsyncMock(),page_index=0,
            _uploads_changed=Mock(),_ensure_upload_worker=Mock(),upload_snapshot=lambda:(api,'/',('A',),lambda:('A',)))
        self.store=DurableStore(self.root/'activity.sqlite3'); self.addCleanup(self.store.close)
        self.features=SimpleNamespace(ops={'baidu':self.ops,'quark':self.ops},store=self.store,accounts={},sync=SimpleNamespace(rules=lambda:[],entries=lambda *a:[]),
            account_status=SimpleNamespace(invalidate=Mock()),capture=self.capture)
        self.bridge=SimpleNamespace(features=self.features,panels={'baidu':self.panel,'quark':self.panel},context=lambda p:'context',selected_paths=lambda key,kind:self.paths)
        self.bridge.productivity=Productivity(self.bridge)
        self.batches=BatchOperations(self.bridge); self.addAsyncCleanup(self.batches.close)
        self.paths=[]

    async def get_account(self): return self.account
    def guard(self, stamp):
        if stamp!=self.stamp: raise CloudOperationError('账号改变')
    async def listing(self, path, limit=None): return list(self.tree.get(path, []))
    async def mkdir(self, parent, name, **kw):
        item=CloudFile(name,'baidu',parent.rstrip('/')+'/'+name,True)
        self.tree.setdefault(parent,[]).append(item);self.tree[item.path]=[];return item
    async def relocate(self, item, parent, name):
        self.relocate_calls.append((item.path,parent,name))
        self.tree[item.parent]=[x for x in self.tree[item.parent] if x.id!=item.id]
        result=replace(item,path=parent.rstrip('/')+'/'+name);self.tree.setdefault(parent,[]).append(result);return result
    def capture(self, provider, force=False):
        for job in self.panel.upload_jobs:self.store.put('transfer',job._desktop_id,{'id':job._desktop_id,'status':job.state,'error':job.error})
    def file(self, path='a.txt', content=b'hello'):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content);return p
    def cloud(self, name='a.txt', id='a'):
        item=CloudFile(id,'baidu','/'+name,False,4,revision='v1');self.panel.browser_rows.append(item);self.tree['/'].append(item);return item
    async def action(self, action, **kw): return await self.batches.action({'action':action,'provider':'baidu','context':'context',**kw})
    async def plan(self, kind='upload', **kw):
        r=await self.action('batch_prepare',kind=kind,selection='native',**kw)
        await self.batches.tasks[r['token']]
        return await self.action('batch_status',token=r['token'],provider=kw.get('provider','baidu'))
    async def commit(self, row):
        await self.action('batch_commit',token=row['token'],provider=row['provider'])
        await self.batches.tasks[row['token']]
        return await self.action('batch_result',token=row['token'],provider=row['provider'])

    async def test_folder_tree_and_empty_directory_preview_no_mutation(self):
        file=self.file('tree/sub/file.txt');(self.root/'tree/empty').mkdir();self.paths=[str(file.parents[1]),str(file)]
        r=await self.plan();self.assertEqual(r['state'],'ready');self.assertEqual(r['count'],4)
        self.assertEqual(self.panel.upload_jobs,[]);self.assertEqual(self.tree['/'],[])
        done=await self.commit(r);self.assertEqual(done['counts'],{'completed':3,'queued':1});self.assertIn('/tree/empty',self.tree)
        self.assertEqual(self.panel.upload_jobs[0].item.path,'/tree/sub/file.txt')

    async def test_drop_folder_resolves_current_child_id(self):
        folder=CloudFile('dest','baidu','/dest',True);self.panel.browser_rows=[folder]
        self.paths=[str(self.file('photo.jpg'))]
        row=await self.plan(target_id='dest')
        self.assertEqual(row['rows'][0]['target'],'/dest/photo.jpg')
        self.assertEqual(self.panel.upload_jobs,[])
        await self.commit(row)
        self.assertEqual(self.panel.upload_jobs[0].item.path,'/dest/photo.jpg')

    async def test_drop_target_rejects_file_stale_and_outside_ids(self):
        self.paths=[str(self.file())];self.cloud()
        self.panel.browser_rows.append(CloudFile('outside','baidu','/secret/dest',True))
        for target in ('a','gone','outside'):
            with self.subTest(target=target),self.assertRaises(CloudOperationError):
                await self.plan(target_id=target)
        self.assertEqual(self.panel.upload_jobs,[])

    async def test_folder_skip_skips_whole_subtree(self):
        self.file('tree/a');self.paths=[str(self.root/'tree')];self.cloud('TREE','existing')
        r=await self.plan(policy='skip');self.assertEqual(r['counts'],{'skipped':2})
        await self.commit(r);self.assertEqual(self.panel.upload_jobs,[])

    async def test_folder_keep_both_changes_all_descendant_paths(self):
        self.file('tree/a');self.paths=[str(self.root/'tree')];self.cloud('tree','existing')
        r=await self.plan(policy='rename');self.assertEqual([x['target'] for x in r['rows']],['/tree (1)','/tree (1)/a'])

    async def test_upload_preview_invalidated_if_local_changed(self):
        p=self.file();self.paths=[str(p)];r=await self.plan();p.write_bytes(b'changed')
        done=await self.commit(r);self.assertEqual(done['counts'],{'failed':1});self.assertEqual(self.panel.upload_jobs,[])

    async def test_late_remote_conflict_skips_without_overwrite(self):
        p=self.file();self.paths=[str(p)];r=await self.plan();self.cloud()
        done=await self.commit(r);self.assertEqual(done['counts'],{'skipped':1});self.assertEqual(self.panel.upload_jobs,[])

    async def test_pending_upload_conflict_not_duplicated(self):
        p=self.file();self.paths=[str(p)];r=await self.plan();await self.commit(r)
        second=await self.plan();done=await self.commit(second)
        self.assertEqual(len(self.panel.upload_jobs),1);self.assertEqual(done['counts'],{'skipped':1})

    async def test_confirmation_once_and_account_change_rejected(self):
        self.cloud();r=await self.plan('rename',ids=['a'],rename={'prefix':'new-'})
        self.stamp='B'
        with self.assertRaises(CloudOperationError):await self.action('batch_commit',token=r['token'])
        self.assertEqual(self.relocate_calls,[]);self.stamp='A';await self.commit(r)
        with self.assertRaises(CloudOperationError):await self.action('batch_commit',token=r['token'])
        self.assertEqual(len(self.relocate_calls),1)

    async def test_numbering_follows_displayed_selection_order(self):
        self.cloud('z.txt','z');self.cloud('a.txt','a')
        r=await self.plan('rename',ids=['a','z'],rename={'start':1})
        self.assertEqual([x['target'] for x in r['rows']],['/a_001.txt','/z_002.txt'])

    async def test_mixed_results_continue_after_one_failure(self):
        self.cloud();self.cloud('b.txt','b');real=self.ops.relocate
        async def operation(item,*args):
            if item.id=='a':raise CloudOperationError('文件改变')
            return await real(item,*args)
        self.ops.relocate=operation;r=await self.plan('move',ids=['a','b'],target='/dest')
        done=await self.commit(r);self.assertEqual(done['counts'],{'failed':1,'completed':1})
        restarted=BatchOperations(self.bridge);self.assertEqual(restarted.public(restarted.records_loaded()[r['token']])['counts'],done['counts'])

    async def test_result_bound_to_original_account(self):
        self.cloud();r=await self.plan('move',ids=['a'],target='/dest');await self.commit(r);self.account='other'
        with self.assertRaises(CloudOperationError):await self.action('batch_result',token=r['token'])

    async def test_sync_overlap_blocks_preview(self):
        self.cloud();self.features.sync.rules=lambda:[{'id':'s','provider':'baidu','account':'account','remote_root':'/','enabled':True}]
        r=await self.plan('move',ids=['a'],target='/dest');self.assertEqual(r['state'],'failed');self.assertEqual(self.relocate_calls,[])

    async def test_upload_sync_overlap_blocks_preview(self):
        self.paths=[str(self.file())];self.features.sync.rules=lambda:[{'id':'s','provider':'baidu','account':'account','remote_root':'/','enabled':True}]
        r=await self.plan();self.assertEqual(r['state'],'failed');self.assertEqual(self.panel.upload_jobs,[])

    async def test_resumed_upload_preserves_preview_name_and_batch(self):
        from dataclasses import asdict
        from app.upload import Fingerprint
        from desktop.features import Features
        path=self.file();item=CloudFile('upload','baidu','/dest/a (1).txt',False,path.stat().st_size)
        row={'id':'job','provider':'baidu','account':'account','direction':'upload','item':asdict(item),'local':str(path),'fingerprint':asdict(Fingerprint.read(path)),
            'committed':False,'group':'上传 · 测试文件夹'}
        self.features.bridge=self.bridge
        await Features.resume(self.features,row)
        self.assertEqual(self.panel.upload_jobs[0].item.path,'/dest/a (1).txt');self.assertEqual(self.panel.upload_jobs[0]._download_group,'上传 · 测试文件夹')

    async def test_active_transfer_blocks_move(self):
        item=self.cloud();r=await self.plan('move',ids=['a'],target='/dest');self.panel.jobs=[SimpleNamespace(item=item,state='paused')]
        done=await self.commit(r);self.assertEqual(done['counts'],{'failed':1});self.assertEqual(self.relocate_calls,[])

    async def test_quark_partial_rename_location_is_recorded(self):
        self.cloud();self.tree['/dest']=[CloudFile('existing','quark','/dest/a.txt',False)]
        real=self.ops.relocate
        async def operation(item,parent,name):
            if parent=='/dest':raise CloudOperationError('移动未确认')
            return await real(item,parent,name)
        self.ops.relocate=operation;r=await self.plan('move',ids=['a'],target='/dest',provider='quark')
        done=await self.commit(r);self.assertEqual(done['counts'],{'failed':1});self.assertIn('/a (1).txt',done['rows'][0]['message'])
        self.assertEqual(self.tree['/'][0].path,'/a (1).txt')

    async def test_quark_move_preview_avoids_intermediate_name_collision(self):
        self.cloud();self.cloud('a (1).txt','b');self.tree['/dest']=[CloudFile('other','quark','/dest/a.txt',False)]
        r=await self.plan('move',ids=['a'],target='/dest',provider='quark')
        self.assertEqual(r['rows'][0]['target'],'/dest/a (2).txt')
        done=await self.commit(r);self.assertEqual(done['counts'],{'completed':1})

    async def test_failed_parent_directory_leaves_children_unstarted(self):
        self.file('tree/a');self.paths=[str(self.root/'tree')];r=await self.plan()
        self.ops.mkdir=AsyncMock(side_effect=CloudOperationError('连接中断'))
        done=await self.commit(r);self.assertEqual(done['counts'],{'failed':1,'not_started':1});self.assertEqual(self.panel.upload_jobs,[])

    async def test_source_folder_cannot_move_inside_itself(self):
        self.panel.browser_rows=[CloudFile('dir','baidu','/folder',True)]
        r=await self.plan('move',ids=['dir'],target='/folder/sub');self.assertEqual(r['state'],'failed');self.assertEqual(self.relocate_calls,[])

    async def test_interrupted_cloud_mutations_not_automatically_replayed(self):
        self.cloud();self.cloud('b.txt','b');r=await self.plan('move',ids=['a','b'],target='/dest')
        row=self.batches.plans[r['token']];row['state']='running';row['rows'][0]['state']='running';self.batches.save(row)
        restarted=BatchOperations(self.bridge);result=restarted.reconcile(restarted.records_loaded()[r['token']])
        self.assertEqual([x['state'] for x in result['rows']],['uncertain','not_started']);self.assertEqual(self.relocate_calls,[])

    async def test_upload_result_tracks_queue_then_restart(self):
        self.paths=[str(self.file())];r=await self.plan();await self.commit(r);job=self.panel.upload_jobs[0];job.state='completed';self.capture('baidu')
        done=await self.action('batch_result',token=r['token']);self.assertEqual(done['counts'],{'completed':1})
        self.panel.upload_jobs=[];restarted=BatchOperations(self.bridge)
        self.assertEqual(restarted.public(restarted.reconcile(restarted.records_loaded()[r['token']]))['counts'],{'completed':1})

    async def test_changed_context_and_invalid_page_rejected(self):
        with self.assertRaises(CloudOperationError):await self.action('batch_prepare',kind='upload',context='old')
        self.paths=[str(self.file())];r=await self.plan()
        with self.assertRaises(CloudOperationError):await self.action('batch_status',token=r['token'],offset=-1)

    def test_local_links_and_scan_limit_rejected(self):
        p=self.file('tree/a');self.file('tree/b')
        with patch('desktop.batch_operations.LIMIT',2),self.assertRaises(CloudOperationError):local_tree([str(p.parent)])
        with patch('desktop.batch_operations.stat.S_ISLNK',return_value=True),self.assertRaises(CloudOperationError):local_tree([str(p)])

    async def test_scan_cancel_stops_worker_without_enqueuing(self):
        self.paths=[str(self.file())];started=threading.Event();finished=threading.Event()
        def slow_scan(paths, stop):
            started.set();stop.wait(3);finished.set();return []
        with patch('desktop.batch_operations.local_tree',slow_scan):
            r=await self.action('batch_prepare',kind='upload',selection='native')
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.01)
            self.assertTrue(started.is_set());await self.action('batch_cancel',token=r['token'])
            await asyncio.gather(self.batches.tasks[r['token']],return_exceptions=True)
            self.assertTrue(await asyncio.to_thread(finished.wait,1));self.assertEqual(self.panel.upload_jobs,[])

    async def test_exclusive_mkdir_does_not_reuse_concurrent_directory(self):
        ops=CloudOperations('baidu',SimpleNamespace());ops.stamp=lambda:'A';ops.guard=Mock();ops.list=AsyncMock(return_value=[CloudFile('d','baidu','/FOLDER',True)]);ops.call=AsyncMock()
        with self.assertRaises(CloudOperationError):await ops.mkdir('/','folder',exclusive=True)
        ops.call.assert_not_awaited()
