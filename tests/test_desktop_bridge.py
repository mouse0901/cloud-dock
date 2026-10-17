"""Loopback access controls and native-picker boundaries for the desktop shell."""
import asyncio,json,secrets,threading,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import httpx
from desktop.backend import Bridge,Server,Handler,UiError
from app.baidu_web_panel import BaiduWebPanel
from app.quark_panel import QuarkPanel
from app.workspace import Workspace
from app.domain import CloudFile
from test_ui_smoke import PageStub
ROOT=Path(__file__).resolve().parents[1]

class DesktopBridgeTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs');self.root=Path(self.tmp.name)
  panels={}
  for name,cls in [('baidu',BaiduWebPanel),('quark',QuarkPanel)]:
   p=cls(PageStub(),client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500))),diagnostic=lambda e:None)
   p.api.authenticated=True;p.reader=SimpleNamespace(root='/');p.path='/';p.browser_rows=[CloudFile('fixture',name,'/test.txt',False,4)]
   p.start_download=AsyncMock();p.queue_upload_paths=AsyncMock();panels[name]=p
  self.bridge=Bridge(panels,Workspace(self.root/'data'))
  self.server=Server(('127.0.0.1',0),Handler);self.server.bridge=self.bridge;self.server.loop=asyncio.get_running_loop()
  self.server.token=secrets.token_hex(32);self.server.native_key=secrets.token_hex(32)
  self.server.authority='127.0.0.1:'+str(self.server.server_address[1])
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.base='http://'+self.server.authority;self.url=self.base+'/'+self.server.token+'/'
  self.client=httpx.AsyncClient(trust_env=False)
 async def asyncTearDown(self):
  await self.client.aclose();await self.bridge.close();await asyncio.to_thread(self.server.shutdown);self.server.server_close();self.tmp.cleanup()
 async def test_unauthed_host_and_traversal_rejected(self):
  self.assertEqual((await self.client.get(self.base+'/api/state')).status_code,404)
  self.assertEqual((await self.client.get(self.url+'api/state',headers={'Host':'evil.example'})).status_code,404)
  self.assertEqual((await self.client.get(self.url+'data/sessions/baidu.json')).status_code,404)
  self.assertEqual((await self.client.get(self.url+'%2e%2e/data/preferences.json')).status_code,404)
 async def test_snapshot_contains_files_but_no_session_material(self):
  r=await self.client.get(self.url+'api/state');self.assertEqual(r.status_code,200)
  d=r.json();self.assertEqual(d['providers']['baidu']['files'][0]['name'],'test.txt')
  self.assertFalse(any(x in r.text.lower() for x in ['bduss','stoken','auth_info','native_key']))
 async def test_origin_and_content_type_protected(self):
  body={'action':'logout','provider':'baidu'}
  self.assertEqual((await self.client.post(self.url+'api/action',json=body,headers={'Origin':'https://evil.example'})).status_code,403)
  self.assertEqual((await self.client.post(self.url+'api/action',content=json.dumps(body))).status_code,415)
  self.assertTrue(self.bridge.panels['baidu'].api.authenticated)
 async def test_native_file_selection_is_required_and_single_use(self):
  body={'kind':'files','paths':[str(self.root/'chosen.txt')]}
  r=await self.client.post(self.url+'api/selection',json=body);self.assertEqual(r.status_code,403)
  r=await self.client.post(self.url+'api/selection',json=body,headers={'X-Native-Key':self.server.native_key});self.assertEqual(r.status_code,200)
  key=r.json()['selection'];p=self.bridge.panels['baidu'];action={'action':'upload','provider':'baidu','selection':key,'context':self.bridge.context(p)}
  self.assertEqual((await self.client.post(self.url+'api/action',json=action)).status_code,200);p.queue_upload_paths.assert_awaited_once_with(body['paths'])
  self.assertEqual((await self.client.post(self.url+'api/action',json=action)).status_code,400)
 async def test_stale_directory_and_arbitrary_file_ids_do_not_transfer(self):
  p=self.bridge.panels['baidu'];body={'action':'download','provider':'baidu','context':'old','ids':['fixture']}
  self.assertEqual((await self.client.post(self.url+'api/action',json=body)).status_code,400)
  body['context']=self.bridge.context(p);body['ids']=['not-listed']
  self.assertEqual((await self.client.post(self.url+'api/action',json=body)).status_code,400);p.start_download.assert_not_awaited()
 async def test_known_file_download_routes_to_correct_provider(self):
  p=self.bridge.panels['quark'];r=await self.client.post(self.url+'api/action',json={'action':'download','provider':'quark','context':self.bridge.context(p),'ids':['fixture']})
  self.assertEqual(r.status_code,200);p.start_download.assert_awaited_once_with(p.browser_rows[0]);self.bridge.panels['baidu'].start_download.assert_not_awaited()
 async def test_reduced_motion_and_theme_survive_restart(self):
  r=await self.client.post(self.url+'api/action',json={'action':'appearance','theme':'dark','reduced':True});self.assertEqual(r.status_code,200)
  settings=Workspace(self.root/'data').settings;self.assertEqual(settings['theme'],'dark');self.assertTrue(settings['reduce_motion'])
 async def test_upload_unavailable_on_share_view(self):
  p=self.bridge.panels['quark'];p.share=object();s=await self.bridge.selection({'kind':'files','paths':[str(self.root/'selected.txt')]})
  with self.assertRaises(UiError):await self.bridge.action({'action':'upload','provider':'quark','context':self.bridge.context(p),'selection':s['selection']})
  p.queue_upload_paths.assert_not_awaited()
