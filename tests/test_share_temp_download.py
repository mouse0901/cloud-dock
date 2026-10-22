"""Safety checks for the journaled Baidu large-share fallback."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.domain import CloudFile
from app.durable_store import DurableStore
from app.cloud_operations import CloudOperationError
from desktop.experience import Experience
from desktop.share_temp_download import ShareTempDownload, ROOT


class TemporaryShareTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=DurableStore(Path(self.tmp.name)/'state.sqlite3');self.addCleanup(self.store.close)
        self.item=CloudFile('111','baidu','/large-sample.bin',False,128)
        self.cloud_file=None;self.folder=None;self.deleted=[]
        async def mkdir(parent,name,exclusive=False):
            self.folder=CloudFile('222','baidu',parent+'/'+name,True,0)
            return self.folder
        async def listing(path):
            if path==ROOT:return [self.folder] if self.folder else []
            if self.folder and path==self.folder.path:return [self.cloud_file] if self.cloud_file else []
            return []
        async def transfer(*args):
            self.cloud_file=CloudFile('333','baidu',self.folder.path+'/large-sample.bin',False,128)
        async def delete(rows):self.deleted.extend(rows)
        self.ops=SimpleNamespace(lock=asyncio.Lock(),account=AsyncMock(return_value='owner'),
            guard=Mock(),ensure=AsyncMock(return_value=ROOT),mkdir=mkdir,list=listing,delete=delete)
        share=SimpleNamespace(files={'111':self.item},id='77',uk='88')
        self.feedback=SimpleNamespace(publish=Mock())
        features=SimpleNamespace(store=self.store,ops={'baidu':self.ops},feedback=self.feedback,
            account_status=SimpleNamespace(check_upload=Mock()))
        self.bridge=SimpleNamespace(features=features,
            shares=SimpleNamespace(session=lambda _: {'share':share,'client':object()}),
            sharing=SimpleNamespace(baidu=transfer))
        self.manager=ShareTempDownload(self.bridge)
        self.plan={'provider':'baidu','share_key':'session','ops':self.ops,'stamp':'stamp'}

    async def prepared(self):
        own,key=await self.manager.stage(self.plan,self.item)
        self.assertEqual(own.id,'333')
        job=SimpleNamespace(_desktop_id='job',destination=Path(self.tmp.name),item=own)
        self.manager.queued(key,job)
        target=Path(self.tmp.name)/'large-sample.bin';target.write_bytes(b'x'*128)
        row={'id':'job','status':'completed','target':str(target),'temporary_share':key}
        return key,row,target

    async def test_verified_completion_recycles_only_its_own_folder(self):
        key,row,_=await self.prepared()
        await self.manager.cleanup(key,row)
        self.assertEqual(self.deleted,[self.folder])
        self.assertEqual(self.store.get('share_temp',key)['state'],'recycled')

    async def test_incomplete_local_file_keeps_cloud_copy(self):
        key,row,target=await self.prepared();target.write_bytes(b'incomplete')
        await self.manager.cleanup(key,row)
        self.assertEqual(self.deleted,[])
        self.assertEqual(self.store.get('share_temp',key)['state'],'cleanup_failed')

    async def test_changed_cloud_file_keeps_entire_folder(self):
        key,row,_=await self.prepared()
        self.cloud_file=CloudFile('other','baidu',self.folder.path+'/large-sample.bin',False,128)
        await self.manager.cleanup(key,row)
        self.assertEqual(self.deleted,[])
        self.assertEqual(self.store.get('share_temp',key)['state'],'cleanup_failed')

    async def test_other_account_cannot_delete_folder(self):
        key,row,_=await self.prepared()
        self.ops.account=AsyncMock(return_value='someone-else')
        await self.manager.cleanup(key,row)
        self.assertEqual(self.deleted,[])
        self.assertEqual(self.store.get('share_temp',key)['state'],'downloading')

    async def test_history_cannot_hide_unfinished_cleanup(self):
        key,row,_=await self.prepared()
        self.store.put('transfer','job',{'id':'job','status':'completed','temporary_share':key})
        self.bridge.panels={}
        with self.assertRaises(CloudOperationError):
            Experience(self.bridge).clear_history(['job'])
        self.assertIsNotNone(self.store.get('transfer','job'))
        self.manager.record(key,state='recycled')
        Experience(self.bridge).clear_history(['job'])
        self.assertIsNone(self.store.get('transfer','job'))
