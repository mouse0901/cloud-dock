"""Startup remains responsive and desktop preferences survive without cloud requests."""
import asyncio,json,subprocess,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from app.workspace import Workspace
from desktop.backend import Bridge,UiError

ROOT=Path(__file__).resolve().parents[1]

class StartupTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.path=Path(self.tmp.name)
  self.bridge=Bridge({},Workspace(self.path/'data'))
 async def asyncTearDown(self):
  await self.bridge.close();self.tmp.cleanup()
 async def test_tray_default_and_persisted_opt_out(self):
  self.assertTrue((await self.bridge.snapshot())['close_to_tray'])
  await self.bridge.action({'action':'desktop_preferences','close_to_tray':False})
  restored=Workspace(self.path/'data')
  self.assertFalse(restored.settings['close_to_tray'])
  self.assertEqual(restored.settings['download_dir'],self.bridge.workspace.settings['download_dir'])
 async def test_invalid_preference_is_rejected_not_coerced(self):
  for value in ('false',0,None,[],{}):
   with self.assertRaises(UiError):await self.bridge.action({'action':'desktop_preferences','close_to_tray':value})
  self.assertTrue(self.bridge.workspace.settings['close_to_tray'])
 async def test_preferences_and_shutdown_work_before_controllers(self):
  self.bridge.deferred=True
  await self.bridge.action({'action':'desktop_preferences','close_to_tray':False})
  await self.bridge.action({'action':'shutdown'})
  self.assertTrue(self.bridge.stop.is_set());self.assertFalse((await self.bridge.snapshot())['close_to_tray'])
 async def test_clipboard_opt_out_and_atomic_preference_validation(self):
  self.assertTrue((await self.bridge.snapshot())['clipboard_links'])
  await self.bridge.action({'action':'desktop_preferences','clipboard_links':False})
  self.assertFalse(Workspace(self.path/'data').settings['clipboard_links'])
  self.assertTrue(self.bridge.workspace.settings['close_to_tray'])
  with self.assertRaises(UiError):await self.bridge.action({'action':'desktop_preferences','close_to_tray':False,'clipboard_links':'true'})
  self.assertTrue(self.bridge.workspace.settings['close_to_tray'])
  self.assertFalse(self.bridge.workspace.settings['clipboard_links'])
 async def test_slow_controller_import_does_not_block_snapshot(self):
  started=threading.Event();release=threading.Event()
  def slow():started.set();release.wait(3);return {}
  self.bridge.deferred=True
  with patch.object(self.bridge,'create_panels',side_effect=slow),patch.object(self.bridge,'start_features',new=AsyncMock()):
   await self.bridge.start();await asyncio.to_thread(started.wait,1)
   try:
    state=await asyncio.wait_for(self.bridge.snapshot(),.5)
    self.assertTrue(state['initializing']);self.assertEqual(set(state['providers']),{'baidu','quark'})
   finally:release.set();await self.bridge.initializing
 async def test_initialization_failure_is_visible_and_retryable(self):
  self.bridge.deferred=True
  with patch.object(self.bridge,'create_panels',side_effect=RuntimeError('private detail')):
   await self.bridge.start();await self.bridge.initializing
  state=await self.bridge.snapshot()
  self.assertFalse(state['initializing']);self.assertTrue(state['initialization_error']);self.assertNotIn('private detail',state['initialization_error'])
  with patch.object(self.bridge,'create_panels',return_value={}),patch.object(self.bridge,'start_features',new=AsyncMock()):
   await self.bridge.action({'action':'retry_workspace'});await self.bridge.initializing
   self.assertFalse(self.bridge.initialization_error)
 async def test_shutdown_during_initialization_does_not_start_accounts(self):
  started=threading.Event();release=threading.Event()
  def slow():started.set();release.wait(3);return {}
  self.bridge.deferred=True
  with patch.object(self.bridge,'create_panels',side_effect=slow),patch.object(self.bridge,'start_features',new=AsyncMock()) as start:
   await self.bridge.start();await asyncio.to_thread(started.wait,1)
   close=asyncio.create_task(self.bridge.close());await asyncio.sleep(0)
   release.set();await close;start.assert_not_awaited()

class BootstrapImportTests(unittest.TestCase):
 def test_bootstrap_does_not_import_flet_or_network_clients(self):
  result=subprocess.run([sys.executable,'-B','-c','import desktop.backend,sys,json; print(json.dumps({n:n in sys.modules for n in ("flet","httpx","desktop.features")}))'],cwd=ROOT,capture_output=True,text=True,check=True)
  self.assertEqual(json.loads(result.stdout),{'flet':False,'httpx':False,'desktop.features':False})
