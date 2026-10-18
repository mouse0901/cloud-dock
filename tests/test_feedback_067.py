import asyncio,json,struct,tempfile,unittest,wave
from datetime import datetime
from pathlib import Path
from app.workspace import Workspace
from app.durable_store import DurableStore
from desktop.feedback import Feedback,preferences,quiet

ROOT=Path(__file__).resolve().parents[1]
def task(key='one',status='completed',direction='download',**kw):
 return dict(id=key,status=status,direction=direction,provider='baidu',name='私人资料.pdf',**kw)

class FeedbackTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.ws=Workspace(Path(self.tmp.name));self.store=DurableStore(Path(self.tmp.name)/'events.sqlite3');self.now=0;self.f=Feedback(self.ws,self.store,lambda:self.now)
 async def asyncTearDown(self):self.store.close();self.tmp.cleanup()
 def finish(self,rows):self.now+=4;self.f.tick(rows)
 async def test_small_fast_job_and_share_job_both_notify(self):
  rows=[task('fast'),task('share',_share_download=True)];self.f.tick(rows);self.finish(rows)
  self.assertEqual(len(self.f.rows()),1);self.assertEqual(self.f.rows()[0]['success'],2)
 async def test_upload_download_have_separate_sounds(self):
  rows=[task('up',direction='upload'),task('down')];self.f.tick(rows);self.finish(rows)
  self.assertEqual({r['kind'] for r in self.f.rows()},{'upload','download'})
  self.assertTrue(all(r['audible'] for r in self.f.poll()['events']))
 async def test_partial_failure_never_signals_all_success(self):
  rows=[task('a'),task('b','failed')];self.f.tick(rows);self.finish(rows);r=self.f.rows()[0]
  self.assertEqual((r['kind'],r['success'],r['failed']),('error',1,1));self.assertFalse(self.f.poll()['events'][0]['audible'])
 async def test_poll_ack_and_duplicate_observations(self):
  rows=[task()];self.f.tick(rows);self.finish(rows);first=self.f.poll()['events'];self.assertEqual(len(first),1)
  self.assertEqual(self.f.poll()['events'],first);self.assertEqual(self.f.poll([first[0]['id']])['events'],[])
  self.finish(rows);self.assertEqual(len(self.f.rows()),1)
 async def test_paused_cancelled_and_archived_are_silent(self):
  rows=[task('a','paused'),task('b','cancelled'),task('c',archived=True)];self.f.tick(rows);self.finish(rows);self.assertFalse(self.f.rows())
 async def test_retry_supersedes_pending_failure(self):
  self.f.tick([task(status='failed')]);self.now+=1;self.f.tick([task(status='queued')]);self.finish([task(status='queued')]);self.assertFalse(self.f.rows())
  self.f.tick([task()]);self.finish([task()]);self.assertEqual(self.f.rows()[0]['kind'],'download')
 async def test_retry_after_failure_can_notify_again(self):
  self.f.tick([task(status='failed')]);self.finish([task(status='failed')]);self.f.tick([task(status='queued')]);self.f.tick([task()]);self.finish([task()]);self.assertEqual(len(self.f.rows()),2)
 async def test_other_long_job_does_not_block_completed_notification(self):
  rows=[task(),task('large','downloading')];self.f.tick(rows);self.finish(rows);self.assertEqual(self.f.rows()[0]['success'],1)
 async def test_long_stream_coalesces_with_bounded_wait(self):
  rows=[]
  for n in range(9):self.now=n*2;rows.append(task(str(n)));self.f.tick(rows)
  self.assertEqual(len(self.f.rows()),1);self.assertEqual(self.f.rows()[0]['success'],9)
 async def test_names_private_by_default_and_purged_on_disable(self):
  self.f.publish('download','完成',name='私人资料.pdf');self.assertNotIn('name',self.f.rows()[0])
  await self.f.configure({'notification_filenames':True});self.f.publish('upload','完成',name='新文件.pdf');self.assertTrue(any('name' in r for r in self.f.rows()))
  await self.f.configure({'notification_filenames':False});self.assertFalse(any('name' in r for r in self.f.rows()))
 async def test_preferences_persist_and_migrate_old_mute(self):
  self.ws.settings['completion_sound']=False;self.assertFalse(preferences(self.ws.settings)['download_sound'])
  await self.f.configure({'upload_sound':True,'sound_volume':22});ws2=Workspace(Path(self.tmp.name));self.assertTrue(preferences(ws2.settings)['upload_sound']);self.assertEqual(preferences(ws2.settings)['sound_volume'],22);self.assertFalse(preferences(ws2.settings)['download_sound'])
 async def test_invalid_settings_do_not_partially_save(self):
  for p in ({'sound_volume':True},{'sound_volume':101},{'quiet_start':'25:61'},{'download_sound':'true'},{'anything':1},{'quiet_end':'08:0x'}):
   with self.assertRaises(ValueError):await self.f.configure(p)
  self.assertNotIn('feedback',self.ws.settings)
 async def test_sound_and_notice_independent(self):
  await self.f.configure({'download_sound':False,'download_notice':True});self.f.publish('download','完成');e=self.f.poll()['events'][0];self.assertFalse(e['audible']);self.assertTrue(e['notice'])
 async def test_history_limit_clear_read_and_restart_do_not_replay(self):
  for n in range(105):self.f.publish('download',str(n))
  self.assertEqual(len(self.f.rows()),100);self.assertEqual(self.f.snapshot()['unread'],100)
  restart=Feedback(self.ws,self.store);self.assertEqual(restart.poll()['events'],[]);self.assertEqual(len(restart.rows()),100)
  self.f.read();self.assertEqual(self.f.snapshot()['unread'],0);self.assertEqual(self.f.poll()['events'],[])
  self.f.read(clear=True);self.assertFalse(self.f.rows())
 async def test_condition_is_edge_triggered(self):
  for _ in range(10):self.f.condition('sync',True,'存在冲突','sync',view='backups')
  self.assertEqual(len(self.f.rows()),1);self.f.condition('sync',False,'');self.f.condition('sync',True,'存在冲突');self.assertEqual(len(self.f.rows()),2)
 async def test_real_bridge_feedback_routes_and_validation(self):
  from desktop.backend import Bridge,UiError
  from types import SimpleNamespace
  bridge=object.__new__(Bridge);bridge.features=SimpleNamespace(feedback=self.f)
  result=await bridge.action({'action':'feedback_preferences','preferences':{'sound_volume':18}})
  self.assertEqual(result['preferences']['sound_volume'],18)
  self.f.publish('download','1 项下载完成');event=(await bridge.action({'action':'feedback_poll'}))['events'][0]
  self.assertEqual((await bridge.action({'action':'feedback_poll','ack':[event['id']]}))['events'],[])
  with self.assertRaises(UiError):await bridge.action({'action':'feedback_preferences','preferences':{'quiet_start':'99:99'}})
  await bridge.action({'action':'feedback_read'});self.assertEqual(self.f.snapshot()['unread'],0)
  await bridge.action({'action':'feedback_clear'});self.assertEqual(self.f.rows(),[])
 def test_quiet_time_cross_midnight_boundaries(self):
  p=preferences({'feedback':{'quiet_enabled':True}})
  for hour,minute,want in [(21,59,False),(22,0,True),(0,1,True),(7,59,True),(8,0,False)]:self.assertEqual(quiet(p,datetime(2026,10,18,hour,minute)),want)
  p.update(quiet_start='08:00',quiet_end='08:00');self.assertTrue(quiet(p,datetime(2026,10,18,12)))
 def test_custom_waveforms_are_distinct_bounded_and_faded(self):
  values=[]
  for name in ('download','upload','error'):
   with wave.open(str(ROOT/'desktop/sounds'/f'{name}.wav')) as w:
    self.assertEqual((w.getnchannels(),w.getsampwidth(),w.getframerate()),(1,2,44100));raw=w.readframes(w.getnframes());v=struct.unpack('<'+'h'*(len(raw)//2),raw)
    self.assertTrue(.4<len(v)/44100<.9);self.assertLess(max(abs(x) for x in v),20000);self.assertLess(abs(v[-1]),10);self.assertEqual(v[0],0);values.append(raw)
  self.assertEqual(len(set(values)),3)

if __name__=='__main__':unittest.main()
