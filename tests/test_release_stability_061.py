"""Accelerated fault/restart scenarios, not a substitute for a real 24-hour soak."""
import asyncio,unittest
from unittest.mock import patch
import test_sync_060 as fixture
from app.cloud_operations import CloudOperationError
from app.durable_store import DurableStore
from app.sync_engine import SyncEngine
class StabilityTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):self.h=fixture.SyncTests();await self.h.asyncSetUp()
 async def asyncTearDown(self):await self.h.asyncTearDown()
 async def test_repeated_offline_reconnect_preserves_current_and_history(self):
  h=self.h;await h.settle()
  for i in range(30):
   data=('edit-'+str(i)).encode();h.file.write_bytes(data)
   if i%3==0:
    with patch.object(h.ops,'list',side_effect=CloudOperationError('offline')):await h.settle()
    self.assertEqual(h.file.read_bytes(),data)
   await h.settle();self.assertEqual(h.ops.contents[h.ops.rows['/target/a.txt'].id],data)
  self.assertEqual(h.engine.entries('sync_pending',h.rule['id']),[])
  self.assertGreaterEqual(len(h.store.all('sync_history')),30)
 async def test_restart_after_lost_publish_recovers_from_database(self):
  h=self.h;await h.settle();h.file.write_bytes(b'new payload');h.ops.fail_after='publish';await h.engine.check(h.rule['id'],True)
  rid=h.rule['id'];h.engine.observed[rid]={k:(v[0],v[1]-10) for k,v in h.engine.observed.get(rid,{}).items()}
  await h.engine.check(rid,True);await h.engine.close();h.store.close()
  h.store=DurableStore(h.root/'state.db');h.engine=SyncEngine(h.store,{'baidu':h.ops});await h.settle()
  self.assertEqual(h.ops.contents[h.ops.rows['/target/a.txt'].id],b'new payload');self.assertEqual(h.engine.entries('sync_pending',rid),[])
 async def test_batches_and_account_change_never_cross_account(self):
  h=self.h
  for i in range(60):(h.local/('f'+str(i)+'.txt')).write_text(str(i))
  await h.settle();h.ops.uid='different'
  count=h.ops.uploads
  for i in range(4):await h.settle()
  self.assertEqual(h.ops.uploads,count)
  h.ops.uid='owner'
  for i in range(10):await h.settle()
  self.assertEqual(sum(f.parent=='/target' and not f.is_dir for f in h.ops.rows.values()),61)
  for i in range(60):self.assertEqual(h.ops.contents[h.ops.rows['/target/f'+str(i)+'.txt'].id],str(i).encode())
