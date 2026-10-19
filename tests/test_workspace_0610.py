import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from app.cloud_operations import CloudOperationError
from app.domain import CloudFile
from app.durable_store import DurableStore
from app.upload import UploadJob, UploadError
from app.live_download_panel import LiveDownloadMixin
from app.live_upload_panel import LiveUploadMixin
from desktop.navigation import BrowserNavigation
from desktop.transfer_management import TransferManagement

ROOT=Path(__file__).resolve().parents[1]

class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stamp='A';self.target=CloudFile('target','baidu','/docs/final.pdf',False,42)
        self.pages=[[CloudFile(str(i),'baidu',f'/docs/{i}.pdf') for i in range(50)],[self.target]]
        self.p=NS(generation=1,path='/',page_index=0,browser_busy=False,browser_rows=[])
        self.ops=NS(guard=self.guard,api=NS(_fetch_files=AsyncMock(side_effect=lambda path,page:self.pages[page])))
        async def load(path,page):self.p.path=path;self.p.page_index=page;self.p.browser_rows=self.pages[page]
        self.p.load_directory=AsyncMock(side_effect=load)
        self.row={'kind':'search','provider':'baidu','rows':[{'id':'target','parent':'/docs','path':'/docs/final.pdf'}]}
        self.productivity=NS(owner=AsyncMock(return_value=('baidu',self.p,self.ops,'account','A')),jobs={'search':self.row},valid=lambda row:self.stamp=='A')
        self.nav=BrowserNavigation(NS(productivity=self.productivity,files=lambda p:[{'id':x.id} for x in p.browser_rows],context=lambda p:'new'))
    def guard(self,stamp):
        if stamp!=self.stamp:raise CloudOperationError('account changed')
    async def action(self,**kw):return await self.nav.action({'action':'browse_view','provider':'baidu','session':'1','path':'/docs',**kw})
    async def test_page_restored(self):
        r=await self.action(page=1);self.assertEqual(r['files'],[{'id':'target'}]);self.assertEqual(r['page'],1)
    async def test_locates_second_page_not_just_parent(self):
        r=await self.action(action='browse_locate',token='search',id='target');self.assertEqual((r['page'],r['selected']),(1,'target'))
    async def test_invalid_session_does_not_browse(self):
        with self.assertRaises(CloudOperationError):await self.action(session='2')
        self.p.load_directory.assert_not_awaited()
    async def test_search_not_found_does_not_select_wrong_file(self):
        self.pages=[[]]
        with self.assertRaises(CloudOperationError):await self.action(action='browse_locate',token='search',id='target')
        self.p.load_directory.assert_not_awaited()
    async def test_stale_search_account_rejected(self):
        self.row['provider']='quark'
        with self.assertRaises(CloudOperationError):await self.action(action='browse_locate',token='search',id='target')
    async def test_page_and_path_validation(self):
        for args in ({'page':True},{'page':-1},{'page':1000},{'path':'/../private'}):
            with self.subTest(args=args),self.assertRaises(CloudOperationError):await self.action(**args)
    async def test_session_changed_during_read(self):
        async def load(*a):self.stamp='B'
        self.p.load_directory.side_effect=load
        with self.assertRaises(CloudOperationError):await self.action()
    async def test_directory_failure_is_not_an_empty_success(self):
        self.p.directory_load_error='读取失败'
        with self.assertRaisesRegex(CloudOperationError,'读取失败'):await self.action()

class TransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.store=DurableStore(self.root/'tasks.sqlite3');self.addCleanup(self.store.close)
        async def pause(j):
            self.assertTrue(self.p._queue_hold);j.state='paused'
        async def resume(j):
            self.assertTrue(self.p._queue_hold);j.state='queued'
        self.p=NS(jobs=[],upload_jobs=[],pause_download=AsyncMock(side_effect=pause),resume_download=AsyncMock(side_effect=resume),retry_upload=AsyncMock(side_effect=resume),_ensure_live_worker=Mock(),_ensure_upload_worker=Mock(),_live_errors={})
        self.f=NS(store=self.store,capture=Mock(),resume=AsyncMock(),ops={'baidu':NS(account=AsyncMock(return_value='A'))})
        self.bridge=NS(features=self.f,panels={'baidu':self.p},selected_paths=Mock(return_value=[str(self.root/'new')]))
        self.manager=TransferManagement(self.bridge)
    def job(self,id,state='queued',**kw):
        j=NS(_desktop_id=id,state=state,direction='download',requires_restart=False,commit_started=False,_persistent_account='A',**kw);self.p.jobs.append(j);return j
    async def act(self,ids,command,**kw):return await self.manager.action({'action':'transfers_control','ids':ids,'command':command,**kw})
    async def test_pause_group_holds_queued_and_running(self):
        a=self.job('a');b=self.job('b','downloading');c=self.job('c','completed');r=await self.act(['a','b','c'],'pause')
        self.assertEqual((r['changed'],r['skipped']),(2,1));self.assertEqual((a.state,b.state),('paused','paused'));self.assertFalse(self.p._queue_hold)
    async def test_only_failed_retried(self):
        a=self.job('a','failed');b=self.job('b','paused');c=self.job('c');r=await self.act(['a','b','c'],'retry_failed')
        self.assertEqual(r['changed'],1);self.assertEqual(b.state,'paused');self.p.resume_download.assert_awaited_once_with(a)
    async def test_group_resume_does_not_retry_failure(self):
        self.job('a','failed');self.job('b','paused');r=await self.act(['a','b'],'resume');self.assertEqual(r['changed'],1)
    async def test_priority_does_not_interrupt_active(self):
        j=self.job('a','downloading');await self.act(['a','a'],'priority',priority=2);self.assertEqual(j._priority,2);self.assertEqual(j.state,'downloading');self.p.pause_download.assert_not_awaited()
    async def test_archived_priority_persists(self):
        self.store.put('transfer','x',{'id':'x','status':'paused'});await self.act(['x'],'priority',priority=0);self.assertEqual(self.store.get('transfer','x')['priority'],0)
    async def test_archived_failed_only_keeps_paused(self):
        for id,status in [('a','failed'),('b','paused')]:self.store.put('transfer',id,{'id':id,'status':status})
        r=await self.act(['a','b'],'retry_failed');self.assertEqual(r['changed'],1);self.assertEqual(self.f.resume.call_args.args[0]['id'],'a')
    async def test_account_mismatch_no_resume(self):
        j=self.job('a','paused');j._persistent_account='B';r=await self.act(['a'],'resume');self.assertEqual(len(r['errors']),1);self.assertEqual(j.state,'paused')
    async def test_committed_upload_not_paused(self):
        j=self.job('a','committing');j.direction='upload';j.commit_started=True;j.pause=AsyncMock();r=await self.act(['a'],'pause');self.assertEqual(r['skipped'],1);j.pause.assert_not_awaited()
    async def test_group_failure_does_not_block_other_items(self):
        self.job('a','paused');self.job('b','paused');self.p.resume_download.side_effect=[CloudOperationError('first fails'),None];r=await self.act(['a','b'],'resume');self.assertEqual((r['changed'],len(r['errors'])),(1,1));self.assertFalse(self.p._queue_hold)
    async def test_invalid_bulk_and_priority(self):
        for ids,command,kw in [([], 'pause',{}),(['x']*501,'pause',{}),(['x'],'priority',{'priority':True}),(['x'],'erase',{})]:
            with self.subTest(command=command),self.assertRaises(CloudOperationError):await self.act(ids,command,**kw)
    async def test_destination_requires_disk_error_before_picker_consumed(self):
        j=self.job('a','completed');j.error_code='download_disk'
        with self.assertRaises(CloudOperationError):await self.manager.action({'action':'transfers_destination','id':'a','selection':'native'})
        self.bridge.selected_paths.assert_not_called()
    async def test_archived_destination_restarts_without_reusing_partial(self):
        row={'id':'a','status':'failed','direction':'download','total':42,'error_code':'download_disk','destination':str(self.root/'old'),'checkpoint':{'untrusted':'old'}}
        self.store.put('transfer','a',row)
        with patch('desktop.transfer_management.Experience.check_disk'):
            await self.manager.action({'action':'transfers_destination','id':'a','selection':'native'})
        updated=self.f.resume.call_args.args[0];self.assertIsNone(updated['checkpoint']);self.assertEqual(updated['destination'],str(self.root/'new'))
    async def test_live_disk_restart_keeps_identity_and_uses_bound_resolver(self):
        from app.direct_download import DirectDownload
        item=CloudFile('f','baidu','/a.txt',False,5)
        resolver=AsyncMock()
        old=DirectDownload(item,self.root/'old',None,resolver,None,None)
        old.state='failed';old.error_code='download_disk';old._desktop_id='a';old._group_id='group';old._priority=2;old._persistent_account='A'
        self.p.jobs.append(old)
        async def start(item,**kw):
            self.assertIs(kw['resolver_override'],resolver)
            job=DirectDownload(item,kw['destination'],None,resolver,None,None);self.p.jobs.append(job);return job
        self.p.start_download=AsyncMock(side_effect=start)
        with patch('desktop.transfer_management.Experience.check_disk'):
            await self.manager.action({'action':'transfers_destination','id':'a','selection':'native'})
        self.assertEqual(len(self.p.jobs),1);new=self.p.jobs[0];self.assertEqual((new._desktop_id,new._group_id,new._priority),('a','group',2));self.assertEqual(old.state,'cancelled');self.assertEqual(new.destination,self.root/'new')
    async def test_disk_check_failure_preserves_original_task(self):
        from app.direct_download import DirectDownload
        old=DirectDownload(CloudFile('f','baidu','/a.txt',False,5),self.root/'old',None,None,None,None)
        old._desktop_id='a';old.error_code='download_disk';old.state='failed';self.p.jobs.append(old);self.p.start_download=AsyncMock()
        with patch('desktop.transfer_management.Experience.check_disk',side_effect=CloudOperationError('空间不足')),self.assertRaises(CloudOperationError):
            await self.manager.action({'action':'transfers_destination','id':'a','selection':'native'})
        self.assertEqual(old.state,'failed');self.assertEqual(self.p.jobs,[old]);self.p.start_download.assert_not_awaited()

class QueueAndUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_priority_stable_order(self):
        order=[];jobs=[NS(state='queued',_priority=n,id=id) for id,n in [('first',1),('low',0),('high',2),('second',1)]]
        async def run(j):order.append(j.id);j.state='completed'
        p=NS(closed=False,_downloads_stopping=False,jobs=jobs,_run_queued=run,_downloads_changed=Mock(),_live_worker=None)
        await LiveDownloadMixin._drive_download_queue(p);self.assertEqual(order,['high','first','second','low'])
    async def test_upload_priority_and_hold(self):
        order=[]
        def job(id,n):
            async def run():order.append(id);j.state='completed'
            j=NS(state='queued',_priority=n,run=run);return j
        p=NS(closed=False,_uploads_stopping=False,_queue_hold=True,upload_jobs=[job('low',0),job('high',2)],_upload_worker=None,save_remembered=Mock(),reader=None,_uploads_changed=Mock())
        await LiveUploadMixin._drive_upload_queue(p);self.assertEqual(order,[]);p._queue_hold=False
        await LiveUploadMixin._drive_upload_queue(p);self.assertEqual(order,['high','low'])
    async def test_active_upload_pause_then_resume_commits_once(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as folder:
            p=Path(folder)/'file.txt';p.write_bytes(b'hello');started=asyncio.Event();release=asyncio.Event();commits=[]
            async def upload(j):
                await j.stage('uploading');started.set();await release.wait();j.commit_started=True;commits.append(j.item.name)
            adapter=NS(upload=upload,verify=AsyncMock(return_value=CloudFile('remote','baidu','/file.txt',False,5)))
            j=UploadJob(p,'baidu','/',adapter);task=asyncio.create_task(j.run());await started.wait();self.assertTrue(await j.pause());self.assertEqual(j.state,'paused');self.assertTrue(task.cancelled());self.assertEqual(commits,[])
            release.set();j.state='queued';await j.run();self.assertEqual(commits,['file.txt']);self.assertEqual(j.state,'completed')
    async def test_committed_upload_pause_does_not_cancel(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as folder:
            p=Path(folder)/'file.txt';p.write_bytes(b'hello');j=UploadJob(p,'baidu','/',NS());j.commit_started=True;j.state='verifying'
            self.assertFalse(await j.pause());self.assertEqual(j.state,'verifying')
