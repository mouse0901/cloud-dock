import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import httpx
from app.share_links import parse_links
from app.providers.baidu_share import BaiduShare
from app.providers.baidu_locate import BaiduLocate,tier
from app.providers.baidu_web import BaiduWebApi
from app.cloud_operations import CloudOperationError
from app.direct_download import DownloadError
from app.domain import CloudFile
from desktop.download_dialog import DownloadDialog
from desktop.experience import Experience

class LinkParsing(unittest.TestCase):
 def test_multiple_prose_fragment_and_codes(self):
  links=parse_links('百度 https://pan.baidu.com/s/1abcdef?pwd=AB12#list/path=%2F\n夸克 https\\://pan.quark.cn/s/abcdef12 提取码：W2pz')
  self.assertEqual([(x['provider'],x['password']) for x in links],[('baidu','AB12'),('quark','W2pz')]);self.assertNotIn('#',links[0]['url'])
 def test_markdown_duplicate_and_separate_codes(self):
  links=parse_links('[链接](https://pan.quark.cn/s/abcdef12) https://pan.quark.cn/s/abcdef12\nhttps://pan.baidu.com/s/1abcdef 提取码：ZZ11')
  self.assertEqual(len(links),2);self.assertEqual(links[0]['password'],'');self.assertEqual(links[1]['password'],'ZZ11')
 def test_later_duplicate_can_supply_missing_code_without_overwriting_one(self):
  text=('https://pan.quark.cn/s/abcdef12 '\
        'https://pan.quark.cn/s/abcdef12?pwd=W2pz '\
        'https://pan.quark.cn/s/abcdef12?pwd=ZZ11')
  links=parse_links(text)
  self.assertEqual(len(links),1)
  self.assertEqual(links[0]['password'],'W2pz')
  self.assertEqual(links[0]['url'],'https://pan.quark.cn/s/abcdef12?pwd=W2pz')
 def test_legacy_and_unsafe(self):
  self.assertEqual(parse_links('https://pan.baidu.com/share/init?surl=abcdef&pwd=Ab12')[0]['url'],'https://pan.baidu.com/s/1abcdef?pwd=Ab12')
  for bad in ['https://pan.baidu.com.evil.test/s/1abcdef','https://pan.quark.cn@evil.test/s/abcdef12','https://pan.baidu.com:123/s/1abcdef','https://pan.baidu.com/s/1abcdef.exe','https://pan.quark.cn/s/abcdef12/extra','x'*16385,None]:self.assertEqual(parse_links(bad),[])
 def test_multiple_links_not_silently_truncated(self):self.assertEqual(len(parse_links(' '.join('https://pan.quark.cn/s/abcdef%03d'%i for i in range(50)))),50)

class LocateTests(unittest.IsolatedAsyncioTestCase):
 async def test_pause_resolution_does_not_consume_native_candidates(self):
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(500))) as c:
   api=BaiduWebApi(c);api.download_source=AsyncMock(return_value='ticket');resolver=api.new_download_resolver();item=CloudFile('1','baidu','/file')
   for _ in range(3):await resolver(item)
   self.assertEqual([call.kwargs['route_attempt'] for call in api.download_source.await_args_list],[0,0,0])
   resolver.reject_native(item);await resolver(item);self.assertEqual(api.download_source.await_args.kwargs['route_attempt'],1)
 async def test_real_tier_candidates_cache_and_account_change(self):
  calls=[];flags={'is_svip':1,'is_vip':0,'is_evip':0}
  def handle(r):
   calls.append(r)
   if r.url.path.endswith('gettemplatevariable'):return httpx.Response(200,json={'errno':0,'result':flags})
   self.assertIn('BDUSS=first',r.headers.get('cookie',''));self.assertNotIn('vip',r.url.params)
   return httpx.Response(200,json={'urls':[{'encrypt':1,'url':'https://d.pcs.baidu.com/encrypted'},{'encrypt':0,'url':'https://evil.test/a'},{'encrypt':0,'url':'https://d.pcs.baidu.com/a'},{'encrypt':0,'url':'https://d.pcs.baidu.com/b'}]})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
   c.cookies.set('BDUSS','first',domain='.baidu.com');api=BaiduWebApi(c);api.authenticated=True;loc=BaiduLocate(api);item=CloudFile('1','baidu','/file')
   self.assertTrue((await loc.link(item)).endswith('/a'));self.assertTrue((await loc.link(item,1)).endswith('/b'));self.assertIsNone(await loc.link(item,2))
   self.assertEqual(sum(r.url.path.endswith('gettemplatevariable') for r in calls),1)
   flags['is_svip']=0;c.cookies.set('BDUSS','second',domain='.baidu.com');self.assertIsNone(await loc.link(item));self.assertEqual(loc.member,'free')
 async def test_service_denial_falls_back_and_cancel_propagates(self):
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(403))) as c:
   api=BaiduWebApi(c);api.authenticated=True;loc=BaiduLocate(api);loc.membership=AsyncMock(return_value='svip')
   self.assertIsNone(await loc.link(CloudFile('1','baidu','/file')))
   loc.membership.side_effect=asyncio.CancelledError
   with self.assertRaises(asyncio.CancelledError):await loc.link(CloudFile('1','baidu','/file'))
 def test_tiers_not_collapsed(self):
  self.assertEqual(tier({'is_vip':1,'is_svip':1}),'svip');self.assertEqual(tier({'is_vip':1}),'vip');self.assertEqual(tier({}),'unknown')

class ShareTests(unittest.IsolatedAsyncioTestCase):
 async def test_shared_file_opaque_service_path_does_not_block_open(self):
  share=BaiduShare(None,{'url':'https://pan.baidu.com/s/1abcdef','password':''})
  rows=share.decode([{'fs_id':12,'server_filename':'readme.txt','path':'xxx','size':3,'isdir':0}],'/')
  self.assertEqual((rows[0].path,rows[0].size),('/readme.txt',3))
  self.assertEqual(share.files['12'],rows[0])
  with self.assertRaisesRegex(CloudOperationError,'分享文件信息无法确认'):
   share.decode([{'fs_id':13,'server_filename':'folder','path':'xxx','size':0,'isdir':1}],'/')
 async def test_opaque_share_ticket_is_not_misreported_as_changed_file(self):
  item=CloudFile('12','baidu','/large.bin',False,1024)
  profiles=[]
  async def request(route,params=None,data=None):
   if route=='share/tplconfig':return httpx.Response(200,json={'errno':0,'data':{'sign':'abcdefgh','timestamp':123}})
   profiles.append(params['clienttype'])
   return httpx.Response(200,json={'errno':0,'list':'opaqueEncodedData'})
  share=BaiduShare(None,{'url':'https://pan.baidu.com/s/1abcdef','password':''})
  share.files[item.id]=item;share.id='1';share.uk='2';share.request=request
  with self.assertRaisesRegex(Exception,'无法使用的分享下载票据'):
   await share.source(item)
  self.assertEqual(profiles,[0,12])
 async def test_opaque_web_share_ticket_retries_client_profile_and_checks_file(self):
  item=CloudFile('12','baidu','/large.bin',False,1024)
  profiles=[]
  async def request(route,params=None,data=None):
   if route=='share/tplconfig':return httpx.Response(200,json={'errno':0,'data':{'sign':'abcdefgh','timestamp':123}})
   profiles.append(params['clienttype'])
   if params['clienttype']==0:return httpx.Response(200,json={'errno':0,'list':'opaqueEncodedData'})
   return httpx.Response(200,json={'errno':0,'list':[{'fs_id':12,'size':1024,'dlink':'https://d.pcs.baidu.com/large.bin'}]})
  share=BaiduShare(None,{'url':'https://pan.baidu.com/s/1abcdef','password':''})
  share.files[item.id]=item;share.id='1';share.uk='2';share.request=request
  source=await share.source(item)
  self.assertEqual(source.size,1024)
  self.assertEqual(profiles,[0,12])
 async def test_large_share_alternate_profile_denial_is_not_called_expired(self):
  item=CloudFile('12','baidu','/large.bin',False,1024)
  profiles=[]
  async def request(route,params=None,data=None):
   if route=='share/tplconfig':return httpx.Response(200,json={'errno':0,'data':{'sign':'abcdefgh','timestamp':123}})
   profiles.append(params['clienttype'])
   if params['clienttype']==0:return httpx.Response(200,json={'errno':0,'list':'opaqueEncodedData'})
   return httpx.Response(200,json={'errno':9019})
  share=BaiduShare(None,{'url':'https://pan.baidu.com/s/1abcdef','password':''})
  share.files[item.id]=item;share.id='1';share.uk='2';share.request=request
  with self.assertRaisesRegex(DownloadError,'未提供可用的分享下载地址') as error:
   await share.source(item)
  self.assertEqual(error.exception.code,'share_ticket_unavailable')
  self.assertNotIn('失效',str(error.exception))
  self.assertEqual(profiles,[0,12])
 async def test_client_profile_cannot_swap_large_share_file(self):
  item=CloudFile('12','baidu','/large.bin',False,1024)
  async def request(route,params=None,data=None):
   if route=='share/tplconfig':return httpx.Response(200,json={'errno':0,'data':{'sign':'abcdefgh','timestamp':123}})
   if params['clienttype']==0:return httpx.Response(200,json={'errno':0,'list':'opaqueEncodedData'})
   return httpx.Response(200,json={'errno':0,'list':[{'fs_id':13,'size':1024,'dlink':'https://d.pcs.baidu.com/other.bin'}]})
  share=BaiduShare(None,{'url':'https://pan.baidu.com/s/1abcdef','password':''})
  share.files[item.id]=item;share.id='1';share.uk='2';share.request=request
  with self.assertRaisesRegex(Exception,'所选文件不一致'):
   await share.source(item)
 async def test_code_directory_ticket_and_untrusted_script(self):
  row={'fs_id':12,'server_filename':'图.png','path':'/original/图.png','size':2,'isdir':0}
  seen=[]
  def handle(r):
   seen.append(r)
   if r.url.path=='/share/verify':return httpx.Response(200,json={'errno':0,'randsk':'test-private'})
   if r.url.path.startswith('/s/'):
    self.assertIn('BDCLND=test-private',r.headers['cookie'])
    return httpx.Response(200,text='locals.mset('+json.dumps({'errno':0,'shareid':1,'share_uk':2,'file_list':[row]})+');dangerousCode();')
   if r.url.path=='/share/tplconfig':return httpx.Response(200,json={'errno':0,'data':{'sign':'abcdefgh','timestamp':123}})
   return httpx.Response(200,json={'errno':0,'list':[dict(row,dlink='https://d.pcs.baidu.com/file')]})
  async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
   share=BaiduShare(c,{'url':'https://pan.baidu.com/s/1abcdef','password':'Ab12'});rows=await share.open()
   self.assertEqual(rows[0].path,'/图.png');source=await share.source(rows[0]);self.assertEqual(source.size,2)
   self.assertEqual((await share.list_page('/'))[0],rows[0]);self.assertNotIn('test-private',repr(source))
   with self.assertRaises(CloudOperationError):await share.list_page('/unknown')
 async def test_incorrect_code_and_invalid_metadata(self):
  async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'errno':-9}))) as c:
   share=BaiduShare(c,{'url':'https://pan.baidu.com/s/1abcdef','password':'AAAA'})
   with self.assertRaisesRegex(CloudOperationError,'提取码'):await share.open()
   with self.assertRaises(CloudOperationError):share.decode([{'fs_id':1,'server_filename':'../secret','path':'/s','size':1,'isdir':0}],'/')

class NativeTransferTests(unittest.IsolatedAsyncioTestCase):
 async def test_native_route_caps_ranges_and_uses_bounded_parallelism(self):
  from app.baidu_adaptive_download import BaiduAdaptiveDownload
  from app.providers.baidu_locate import NATIVE_UA
  from app.direct_download import DownloadSource
  from test_parallel_download import Service,DATA,MD5
  service=Service();ranges=[]
  async def policy(r,start,end):
   if 'range' in r.headers:ranges.append(end-start+1)
  service.policy=policy
  async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as c:
   with tempfile.TemporaryDirectory() as tmp:
    job=BaiduAdaptiveDownload(CloudFile('1','baidu','/file.bin',False,len(DATA)),tmp,c,AsyncMock(return_value=DownloadSource('https://d.pcs.baidu.com/file',len(DATA),{'User-Agent':NATIVE_UA},MD5)),lambda u:u,AsyncMock(),connections=8)
    target=await job.run();self.assertEqual(target.read_bytes(),DATA);self.assertLessEqual(max(ranges),4*1024*1024);self.assertEqual(job.connections,16)
 async def test_two_rejected_native_candidates_fall_back_to_web(self):
  from app.baidu_adaptive_download import BaiduAdaptiveDownload
  from app.providers.baidu_locate import NATIVE_UA
  from app.direct_download import DownloadSource
  from test_parallel_download import Service,DATA,MD5
  service=Service()
  async def policy(r,start,end):
   if r.headers.get('User-Agent')==NATIVE_UA and end>0:return httpx.Response(403)
  service.policy=policy
  native=DownloadSource('https://d.pcs.baidu.com/file',len(DATA),{'User-Agent':NATIVE_UA},MD5);web=DownloadSource('https://d.pcs.baidu.com/file',len(DATA),{'User-Agent':'pan.baidu.com'},MD5)
  resolve=AsyncMock(side_effect=[native,native,web])
  async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as c:
   with tempfile.TemporaryDirectory() as tmp:
    job=BaiduAdaptiveDownload(CloudFile('1','baidu','/file.bin',False,len(DATA)),tmp,c,resolve,lambda u:u,AsyncMock(),connections=8)
    self.assertEqual((await job.run()).read_bytes(),DATA);self.assertEqual(resolve.await_count,3);self.assertEqual(job.connections,8)

class DownloadPlans(unittest.IsolatedAsyncioTestCase):
 async def test_quick_requires_explicit_defaults_and_setting(self):
  token=await self.prepare()
  result=await self.dialog.action({'action':'download_quick','token':token})
  self.assertTrue(result['confirmation_required']);self.panel.start_download.assert_not_awaited()
  with self.assertRaises(CloudOperationError):await self.dialog.action({'action':'download_preferences','quick':True})
  await self.dialog.action({'action':'download_commit','token':token,'remember':True,'quick':True})
  self.assertTrue(self.bridge.workspace.settings['quick_download'])
  self.assertTrue(self.bridge.workspace.settings['download_directory_confirmed'])
 async def test_quick_download_uses_one_shot_plan(self):
  self.bridge.workspace.settings.update(quick_download=True,download_directory_confirmed=True)
  token=await self.prepare();result=await self.dialog.action({'action':'download_quick','token':token})
  self.assertEqual(result['count'],1)
  with self.assertRaises(CloudOperationError):await self.dialog.action({'action':'download_quick','token':token})
  self.assertEqual(self.panel.start_download.await_count,1)
 async def test_quick_conflict_folder_and_account_change_need_confirmation(self):
  self.bridge.workspace.settings.update(quick_download=True,download_directory_confirmed=True)
  (self.root/'downloads').mkdir();(self.root/'downloads/a.txt').write_text('existing')
  token=await self.prepare();result=await self.dialog.action({'action':'download_quick','token':token})
  self.assertTrue(result['confirmation_required']);self.assertEqual((self.root/'downloads/a.txt').read_text(),'existing')
  for ids in (['2'],['1','2']):
   token=await self.prepare(ids);self.assertTrue((await self.dialog.action({'action':'download_quick','token':token}))['confirmation_required'])
  self.stamp='other'
  with self.assertRaises(CloudOperationError):await self.dialog.action({'action':'download_quick','token':token})
  self.panel.start_download.assert_not_awaited()
 async def test_quick_single_share_file_uses_confirmed_default(self):
  self.bridge.workspace.settings.update(quick_download=True,download_directory_confirmed=True)
  token=await self.prepare();row=self.dialog.plans[token];row['share_key']='share';row['resolver']=AsyncMock()
  result=await self.dialog.action({'action':'download_quick','token':token})
  self.assertEqual(result['count'],1);self.assertEqual(self.panel.start_download.await_count,1)
  self.assertEqual(self.panel.start_download.await_args.kwargs['share_key'],'share')
  self.assertTrue(self.share_session['used'])
 async def test_quick_off_and_invalid_preference(self):
  for value in ('true',1,None):
   with self.assertRaises(CloudOperationError):await self.dialog.action({'action':'download_preferences','quick':value})
  await self.dialog.action({'action':'download_preferences','quick':False})
  self.assertFalse(self.bridge.workspace.settings['quick_download'])
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.stamp='A'
  self.ops=SimpleNamespace(account=AsyncMock(return_value='account'),stamp=lambda:self.stamp,guard=self.guard,list=AsyncMock(return_value=[]))
  self.file=CloudFile('1','baidu','/docs/a.txt',False,2);self.folder=CloudFile('2','baidu','/docs/dir',True)
  self.panel=SimpleNamespace(api=SimpleNamespace(authenticated=True),reader=object(),generation=1,browser_busy=False,browser_rows=[self.file,self.folder],path='/docs',start_download=AsyncMock(return_value=SimpleNamespace()),_downloads_changed=Mock(),_ensure_live_worker=Mock(),set_download_root=Mock())
  self.share_session={'used':False};self.bridge=SimpleNamespace(panels={'baidu':self.panel},features=SimpleNamespace(ops={'baidu':self.ops},accounts={},capture=Mock()),workspace=SimpleNamespace(settings={'download_dir':str(self.root/'downloads')},save=AsyncMock()),context=lambda p:'ctx',selected_paths=lambda s,k:[str(self.root/'chosen')],shares=SimpleNamespace(session=lambda key:self.share_session))
  self.bridge.experience=Experience(self.bridge);self.dialog=DownloadDialog(self.bridge);self.addAsyncCleanup(self.dialog.close)
 def guard(self,stamp):
  if stamp!=self.stamp:raise CloudOperationError('account changed')
 async def prepare(self,ids=['1']):
  row=await self.dialog.action({'action':'download_prepare','provider':'baidu','context':'ctx','ids':ids});await self.dialog.tasks[row['token']];return row['token']
 async def test_preview_does_not_download_and_confirm_only_once(self):
  token=await self.prepare();row=await self.dialog.action({'action':'download_status','token':token});self.assertEqual(row['count'],1);self.panel.start_download.assert_not_awaited();self.assertFalse((self.root/'downloads').exists())
  await self.dialog.action({'action':'download_destination','token':token,'selection':'opaque'})
  result=await self.dialog.action({'action':'download_commit','token':token,'remember':True});self.assertEqual(result['count'],1);self.assertEqual(self.bridge.workspace.settings['download_dir'],str(self.root/'chosen'))
  with self.assertRaises(CloudOperationError):await self.dialog.action({'action':'download_commit','token':token})
  self.assertEqual(self.panel.start_download.await_count,1)
 async def test_account_switch_invalidates_plan(self):
  token=await self.prepare();self.stamp='B'
  with self.assertRaises(CloudOperationError):await self.dialog.action({'action':'download_commit','token':token})
  self.panel.start_download.assert_not_awaited()
 async def test_large_share_without_direct_url_does_not_create_zero_byte_task(self):
  token=await self.prepare();row=self.dialog.plans[token]
  large=CloudFile('1','baidu','/docs/large-sample.bin',False,12_889_127_493)
  row.update(items=[(large,self.root/'downloads')],bytes=large.size,share_key='share',
             resolver=AsyncMock(side_effect=DownloadError('share_download','百度未提供直接下载地址')))
  with self.assertRaisesRegex(CloudOperationError,'直接下载地址'):
   await self.dialog.action({'action':'download_commit','token':token})
  self.assertEqual(row['state'],'ready')
  self.panel.start_download.assert_not_awaited()
  self.assertFalse((self.root/'downloads').exists())
 async def test_large_share_unavailable_ticket_queues_own_drive_copy(self):
  token=await self.prepare();row=self.dialog.plans[token]
  large=CloudFile('1','baidu','/large-sample.bin',False,1_000_000_000)
  own=CloudFile('500','baidu','/CloudDock 临时下载/下载-abc/large-sample.bin',False,large.size)
  temp=SimpleNamespace(stage=AsyncMock(return_value=(own,'temp-key')),queued=Mock())
  self.bridge.features.share_temp=temp
  self.bridge.shares=SimpleNamespace(session=lambda key:{'used':False})
  row.update(items=[(large,self.root/'downloads')],bytes=large.size,share_key='share',
             resolver=AsyncMock(side_effect=DownloadError('share_ticket_unavailable','分享票据不可用')))
  result=await self.dialog.action({'action':'download_commit','token':token})
  self.assertEqual(result['count'],1)
  temp.stage.assert_awaited_once_with(row,large)
  self.assertEqual(self.panel.start_download.await_args.args[0],own)
  self.assertEqual(self.panel.start_download.await_args.kwargs['share_key'],'')
  self.assertIsNone(self.panel.start_download.await_args.kwargs['resolver_override'])
  temp.queued.assert_called_once()
 async def test_cancel_does_not_create_task_or_directory(self):
  token=await self.prepare();await self.dialog.action({'action':'download_cancel','token':token});self.assertNotIn(token,self.dialog.plans);self.panel.start_download.assert_not_awaited()
 async def test_nested_folder_preserves_relative_path(self):
  self.ops.list.return_value=[CloudFile('3','baidu','/docs/dir/child.txt',False,5)];token=await self.prepare(['2']);row=self.dialog.plans[token];self.assertEqual(row['count'],1);self.assertEqual(row['items'][0][1],self.root/'downloads/dir')
 async def test_scan_rejects_escaped_child(self):
  self.ops.list.return_value=[CloudFile('3','baidu','/outside/child.txt',False,5)];token=await self.prepare(['2']);self.assertEqual(self.dialog.plans[token]['state'],'failed');self.panel.start_download.assert_not_awaited()
