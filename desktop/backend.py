"""Loopback bridge for the native WebView2 shell; reuses verified account controllers."""
from __future__ import annotations
import asyncio,base64,hashlib,hmac,json,os,secrets,subprocess,sys,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,unquote
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.runtime_paths import data_dir,work_dir
os.environ['TEMP']=os.environ['TMP']=str(work_dir())
work_dir().mkdir(parents=True,exist_ok=True)
from app.workspace import Workspace
from app.local_files import prepare_download_root
from app.domain import CloudFile

class UiError(Exception):pass
class Page:
    desktop_only=True
    def __init__(self):self.revision=0
    def update(self):self.revision+=1
    def show_dialog(self,*args):self.update()

class Bridge:
    def __init__(self,panels=None,workspace=None,*,defer=False):
        self.page=Page();self.workspace=workspace or Workspace(data_dir())
        self.panels=panels if panels is not None else ({} if defer else self.create_panels())
        self.deferred=defer;self.features=None;self.updates=None;self.update_start=None;self.initializing=None;self.initialization_error='';self.closing=False
        self.stop=asyncio.Event();self.selections={};self.startups=[]
        from desktop.experience import Experience
        self.experience=Experience(self)
        from desktop.productivity import Productivity
        self.productivity=Productivity(self)
        for p in self.panels.values():p.set_download_root(self.workspace.settings['download_dir'])
    def create_panels(self):
        # Import the former Flet controllers off the first-response path.
        from app.baidu_web_panel import BaiduWebPanel
        from app.quark_panel import QuarkPanel
        return {'baidu':BaiduWebPanel(self.page),'quark':QuarkPanel(self.page)}
    async def initialize(self):
        try:
            self.panels=await asyncio.to_thread(self.create_panels)
            for p in self.panels.values():p.set_download_root(self.workspace.settings['download_dir'])
            if not self.closing:await self.start_features()
        except Exception:
            self.initialization_error='工作空间准备失败，请重试；若仍失败，请检查软件是否完整解压。'
        finally:self.deferred=False
    async def start_features(self):
        from desktop.features import Features
        self.features=Features(self);await self.features.start()
        from app.updater import UpdateService
        self.updates=UpdateService(self.workspace,self.update_busy)
        self.update_start=asyncio.create_task(self.updates.start())
        self.startups=[asyncio.create_task(p.startup(saved_only=True)) for p in self.panels.values()]
    def update_busy(self):
        if any(row['kind']=='operation' and row['status']=='running' for row in self.productivity.jobs.values()):return True
        active={'retrying','queued','resolving','preparing','uploading','downloading','committing','verifying'}
        if any(j.state in active for p in self.panels.values() for j in [*p.jobs,*p.upload_jobs]):return True
        return bool(self.features and (self.features.sync.active or self.features.backup.running))
    async def start(self):
        if self.deferred:
            self.initializing=asyncio.create_task(self.initialize());return
        await self.start_features()
    async def close(self):
        self.closing=True
        if self.initializing:await asyncio.gather(self.initializing,return_exceptions=True)
        if self.update_start:self.update_start.cancel();await asyncio.gather(self.update_start,return_exceptions=True)
        if self.updates:await self.updates.close()
        await self.productivity.close()
        await self.experience.close()
        if self.features:await self.features.close()
        for t in self.startups:t.cancel()
        await asyncio.gather(*self.startups,return_exceptions=True)
        await asyncio.gather(*(p.shutdown() for p in self.panels.values()),return_exceptions=True)
    def context(self,p):
        return f'{p.generation}:{p.list_generation}:{p.path}:{p.page_index}:{bool(getattr(p,"share",None))}'
    def files(self,p):
        return [{'id':f.id,'name':f.name,'path':f.path,'size':f.size,'type':'folder' if f.is_dir else
                ('pdf' if f.name.lower().endswith('.pdf') else {'document':'text','other':'text'}.get(f.category,f.category)),
                'date':f.modified,'thumb':self.features.thumbnail_key(f.provider,f) if self.features else ''} for f in p.browser_rows]
    def tasks(self):
        rows=[]
        for provider,p in self.panels.items():
            for j in [*p.jobs,*p.upload_jobs]:
                if not hasattr(j,'_desktop_id'):j._desktop_id=secrets.token_hex(12)
                rows.append({'id':j._desktop_id,'provider':provider,'name':j.item.name,'path':j.item.path,
                    'direction':getattr(j,'direction','download'),'status':j.state,'done':j.done,'total':j.total,
                    'speed':j.speed,'error':j.error,'warning':getattr(j,'warning',''),
                    'restart':j.requires_restart,'committed':getattr(j,'commit_started',False),
                    'target':str(getattr(j,'target','') or ''),'connections':getattr(j,'active_connections',0),
                    'group':getattr(j,'_download_group','')})
        return rows+(self.features.archived() if self.features else [])
    async def snapshot(self):
        providers={}
        for name,p in self.panels.items():
            qr=''
            if p.qr.visible and p.qr.content:
                raw=p.qr.content.content.src
                if isinstance(raw,bytes):qr='data:'+('image/png' if raw.startswith(b'\x89PNG') else 'image/svg+xml')+';base64,'+base64.b64encode(raw).decode()
            color=str(p.status.color).upper()
            providers[name]={'connected':p.reader is not None and p.api.authenticated,'busy':p.browser_busy,'path':p.path or '/',
                'page':p.page_index,'previous':not p.prev_button.disabled,'next':not p.next_button.disabled,
                'files':self.files(p),'context':self.context(p),'message':p.status.value,'qr':qr,
                'error':p.status.value if color in ('#C05020','#B91C1C') else '',
                'retry':p.retry_button.visible,'share':bool(getattr(p,'share',None)),
                'root':p.reader.root if p.reader else '/', 'remember':bool(p.remember_login.value)}
        for name in ('baidu','quark'):
            if name not in providers:providers[name]={'connected':False,'busy':True,'path':'/','page':0,'previous':False,'next':False,'files':[],'context':'initializing','message':'正在恢复工作空间…','qr':'','error':'','retry':False,'share':False,'root':'/','remember':True}
        return {**(self.features.snapshot() if self.features else {'backups':[],'sound_sequence':0,'sound':True}),
                'initializing':self.deferred,'initialization_error':self.initialization_error,
                'accounts_settled':not self.deferred and not self.initialization_error and all(t.done() for t in self.startups),
                'close_to_tray':self.workspace.settings.get('close_to_tray',True),
                'clipboard_links':self.workspace.settings.get('clipboard_links',True),
                'providers':providers,'tasks':self.tasks(),'download_dir':self.workspace.settings['download_dir'],
                'favorites':self.experience.favorites() if self.features else [],
                'file_view':{k:self.workspace.settings.get(k,v) for k,v in {'view':'list','sort_field':'name','sort_desc':False}.items()},
                'update':self.updates.snapshot() if self.updates else {'status':'initializing','current':'0.6.4','enabled':False},
                'version':'0.6.4','migration_notice':(self.workspace.data_dir/'migration.json').exists(),'theme':self.workspace.settings.get('theme','light'),'reduced':self.workspace.settings.get('reduce_motion',False)}
    def selected_paths(self,key,kind):
        value=self.selections.pop(key,None)
        if not value or time.monotonic()-value['at']>300 or value['kind']!=kind:raise UiError('文件选择已过期，请重新选择。')
        return value['paths']
    async def selection(self,d):
        paths=d.get('paths');kind=d.get('kind')
        if kind not in ('files','directory') or not isinstance(paths,list) or not 1<=len(paths)<=500 or any(not isinstance(p,str) or len(p)>32768 for p in paths):raise UiError('文件选择无效。')
        self.selections={k:v for k,v in self.selections.items() if time.monotonic()-v['at']<300}
        key=secrets.token_hex(24);self.selections[key]={'kind':kind,'paths':paths,'at':time.monotonic()};return {'selection':key,'count':len(paths)}
    async def action(self,d):
        action=d.get('action')
        if isinstance(action,str) and action.startswith('tools_'):
            from app.cloud_operations import CloudOperationError
            try:return await self.productivity.action(d)
            except CloudOperationError as error:raise UiError(str(error)) from None
        if action=='file_view':
            if d.get('view') in ('list','grid'):self.workspace.settings['view']=d['view']
            if d.get('sort_field') in ('name','size','date'):self.workspace.settings['sort_field']=d['sort_field']
            if type(d.get('sort_desc')) is bool:self.workspace.settings['sort_desc']=d['sort_desc']
            await self.workspace.save();return {}
        if action in ('capacity_refresh','favorite_toggle','history_clear','folder_preview','folder_status','folder_cancel','folder_confirm'):
            from app.cloud_operations import CloudOperationError
            if not self.features:raise UiError('工作空间正在准备，请稍后重试。')
            try:return await self.experience.action(d)
            except (CloudOperationError,OSError) as error:
                raise UiError(str(error) if isinstance(error,CloudOperationError) else '本地目录不可用，请检查下载位置。') from None
        if action=='diagnostics':
            from app.diagnostics import report
            return {'report':report(self)}
        if action=='shutdown':self.stop.set();return {}
        if isinstance(action,str) and action.startswith('update_'):
            from app.updater import UpdateError
            if not self.updates:raise UiError('更新服务正在准备，请稍后重试。')
            try:
                if action=='update_preferences':await self.updates.preferences(d);return {}
                if action=='update_check':await self.updates.check(True);return {}
                if action=='update_download':await self.updates.download();return {}
                if action=='update_cancel':self.updates.stop_download();return {}
                if action=='update_skip':self.updates.skip();await self.workspace.save();return {}
                if action=='update_install':return {'update':self.updates.install()}
                if action=='update_install_when_idle':self.updates.install_when_idle();return {}
            except UpdateError as e:raise UiError(str(e)) from None
            raise UiError('未识别的更新操作。')
        if action=='desktop_preferences':
            fields=[key for key in ('close_to_tray','clipboard_links') if key in d]
            if not fields or any(type(d[key]) is not bool for key in fields):raise UiError('设置值无效。')
            for key in fields:self.workspace.settings[key]=d[key]
            await self.workspace.save();return {}
        if action=='retry_workspace':
            if self.deferred or not self.initialization_error:return {}
            if self.features:await self.features.close();self.features=None
            await asyncio.gather(*(p.shutdown() for p in self.panels.values()),return_exceptions=True)
            self.panels={};self.initialization_error='';self.deferred=True;await self.start();return {}
        if action=='appearance':
            if d.get('theme') in ('dark','light'):self.workspace.settings['theme']=d['theme']
            if type(d.get('reduced')) is bool:self.workspace.settings['reduce_motion']=d['reduced']
            await self.workspace.save();return {}
        if action=='download_directory':
            folder=self.selected_paths(d.get('selection'),'directory')[0]
            path=prepare_download_root(folder);self.workspace.settings['download_dir']=str(path);await self.workspace.save()
            for p in self.panels.values():p.set_download_root(path)
            return {}
        if self.features and (isinstance(action,str) and action.startswith('sync_') or action in ('sound','clear_thumbnails','backup_toggle','backup_remove','backup_preview','backup_confirm','delete_preview','delete_confirm','trash_list','trash_restore')):
            from app.cloud_operations import CloudOperationError
            try:return await self.features.action(d)
            except CloudOperationError as e:raise UiError(str(e)) from None
        p=self.panels.get(d.get('provider'))
        if p is None:raise UiError('请选择网盘。')
        if action=='login':
            if p.reader is not None and p.api.authenticated:raise UiError('该账号已连接。')
            await p.begin();return {}
        if action=='retry_login':await p.retry_connection();return {}
        if action=='logout':await p.cancel(None);return {}
        if action=='remember':p.remember_login.value=bool(d.get('value'));await p.remember_changed(None);return {}
        if action=='task':
            job=next((j for j in [*p.jobs,*p.upload_jobs] if getattr(j,'_desktop_id',None)==d.get('id')),None)
            if job is None and self.features:
                from app.cloud_operations import CloudOperationError
                try:return await self.features.action(d)
                except (CloudOperationError,OSError) as e:raise UiError(str(e) if isinstance(e,CloudOperationError) else '本地文件不可用，请重新选择。') from None
            if job is None:raise UiError('任务已结束或已清除。')
            command=d.get('command');upload=getattr(job,'direction','download')=='upload'
            mapping={'cancel':p.cancel_one_upload if upload else p.cancel_one_download,'resume':p.retry_upload if upload else p.resume_download}
            if not upload:mapping.update(pause=p.pause_download,open=p.open_completed_folder)
            if command not in mapping:raise UiError('此任务不支持该操作。')
            await mapping[command](job);return {}
        if p.reader is None or not p.api.authenticated:raise UiError('请先连接网盘账号。')
        if self.features and action in ('download','upload'):
            try:self.features.accounts[d['provider']]=await self.features.ops[d['provider']].account()
            except Exception:raise UiError('无法核对账号归属，请稍后重试。') from None
        if action in ('download','upload','page') and d.get('context')!=self.context(p):raise UiError('目录已经变化，请重新选择文件。')
        if action=='browse':
            path=d.get('path','/');await p.load_directory(path,0)
        elif action=='page':await p.paginate(-1 if d.get('delta')==-1 else 1)
        elif action=='refresh':await p.refresh(None)
        elif action=='download':
            ids=d.get('ids',[])
            if not isinstance(ids,list) or len(ids)>50:raise UiError('请选择当前页的文件。')
            items=[f for f in p.browser_rows if f.id in ids and not f.is_dir]
            if len(items)!=len(set(ids)):raise UiError('文件列表已变化，请重新选择。')
            if self.features:
                from desktop.experience import local_parent
                from app.cloud_operations import CloudOperationError
                destination=local_parent(p.download_destination,p.path)
                try:
                    self.experience.check_disk(destination,sum(f.size for f in items))
                    self.experience.safe_directory(destination,p.download_destination)
                except CloudOperationError as error:raise UiError(str(error)) from None
                for f in items:await p.start_download(f,destination=destination)
            else:
                for f in items:await p.start_download(f)
        elif action=='upload':
            paths=self.selected_paths(d.get('selection'),'files')
            if not p.upload_allowed():raise UiError('请打开本人网盘目录后上传。')
            await p.queue_upload_paths(paths)
        elif action=='share':
            if d['provider']!='quark':raise UiError('当前支持打开夸克分享。')
            p.share_link.value=str(d.get('link',''))[:4096];p.share_password.value=str(d.get('password',''))[:32]
            await p.open_share(None)
        elif action=='my_files':
            if d['provider']=='quark':await p.show_my_files(None)
        else:raise UiError('未识别的操作。')
        if self.features:self.features.capture(d['provider'],force=True)
        return {}

class Server(ThreadingHTTPServer):
    daemon_threads=True
    request_queue_size=64
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def respond(self,code,body,kind='application/json',headers=None):
        data=json.dumps(body,ensure_ascii=False).encode() if kind=='application/json' else body
        self.send_response(code);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(data)))

        for k,v in (headers or {}).items():self.send_header(k,v)
        self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def checked(self):
        if self.headers.get('Host')!=self.server.authority:return False
        return self.path.startswith('/'+self.server.token+'/')
    def call(self,coro):
        future=asyncio.run_coroutine_threadsafe(coro,self.server.loop)
        try:return future.result(95)
        except UiError as e:raise e
        except Exception:
            future.cancel()
            raise UiError('操作未完成，请重试或检查账号连接。') from None
    def do_GET(self):
        if not self.checked():return self.respond(404,{'error':'Not found'})
        path=urlsplit(self.path).path[len(self.server.token)+2:]
        if path=='api/state':
            try:return self.respond(200,self.call(self.server.bridge.snapshot()))
            except UiError:return self.respond(503,{'error':'服务暂时忙，请稍后再试。'})
        if path.startswith('api/media/'):
            key=path.rsplit('/',1)[-1]
            try:value=self.call(self.server.bridge.productivity.media_bytes(key,self.headers.get('Range','')))
            except (UiError,OSError):value=None
            if not value:return self.respond(404,{'error':'Preview unavailable'})
            status,data,kind,headers=value;return self.respond(status,data,kind,headers)
        if path.startswith('api/thumbnail/'):
            key=path.rsplit('/',1)[-1]
            try:data=self.call(self.server.bridge.features.thumbnail(key)) if self.server.bridge.features else None
            except UiError:data=None
            if not data:return self.respond(404,{'error':'Thumbnail unavailable'})
            kind='image/png' if data.startswith(b'\x89PNG') else 'image/webp' if data[:4]==b'RIFF' else 'image/jpeg'
            return self.respond(200,data,kind)
        if path=='health':return self.respond(200,{'ok':True})
        if path=='cloud-dock.ico':return self.respond(200,(ROOT/'assets/branding/cloud-dock.ico').read_bytes(),'image/x-icon')
        allowed={'':'index.html','index.html':'index.html','app.js':'app.js','prototype.css':'prototype.css','desktop.css':'desktop.css','theme-reveal.js':'theme-reveal.js','theme-reveal.css':'theme-reveal.css','cloud-dock.png':'cloud-dock.png','features.js':'features.js','features.css':'features.css','lifecycle.js':'lifecycle.js','clipboard.js':'clipboard.js','sync.js':'sync.js','sync.css':'sync.css','startup.js':'startup.js','update.js':'update.js','update.css':'update.css','experience.js':'experience.js','experience.css':'experience.css','productivity.js':'productivity.js','productivity.css':'productivity.css'}
        if path not in allowed:return self.respond(404,{'error':'Not found'})
        file=ROOT/'desktop/web'/allowed[path]
        qa=os.environ.get('CLOUD_DOCK_QA_TIMINGS')
        if qa:
            try:
                with open(qa+'.trace','a',encoding='utf-8') as log:log.write(f'{time.time_ns()//1000000} request_{file.name}\n')
            except OSError:pass
        # These bundled resources have known types. Avoid Windows registry MIME discovery
        # concurrently on every initial resource request.
        kind={'.html':'text/html','.js':'text/javascript','.css':'text/css','.png':'image/png'}[file.suffix]
        start=time.monotonic();self.respond(200,file.read_bytes(),kind)
        if qa:
            try:
                with open(qa+'.http','a',encoding='utf-8') as log:log.write(f'{file.name}: {time.monotonic()-start:.4f}s\n')
            except OSError:pass
    def do_POST(self):
        # Consume bounded request bodies before rejecting: unread data can cause a
        # Windows TCP reset instead of delivering the intended 403/415 response.
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=1024*1024:raise UiError('请求过大。')
            self.connection.settimeout(10)
            raw=self.rfile.read(length)
            if len(raw)!=length:raise UiError('请求不完整。')
            if not self.checked():return self.respond(404,{'error':'Not found'})
            path=urlsplit(self.path).path[len(self.server.token)+2:]
            origin=self.headers.get('Origin')
            if origin and origin!='http://'+self.server.authority:return self.respond(403,{'error':'Origin rejected'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'JSON required'})
            d=json.loads(raw)
            if not isinstance(d,dict):raise UiError('请求格式无效。')
            if path=='api/selection':
                if not hmac.compare_digest(self.headers.get('X-Native-Key',''),self.server.native_key):return self.respond(403,{'error':'Native selection required'})
                result=self.call(self.server.bridge.selection(d))
            elif path=='api/action':result=self.call(self.server.bridge.action(d))
            else:return self.respond(404,{'error':'Not found'})
            self.respond(200,{'ok':True,**result})
        except (UiError,ValueError,TimeoutError) as e:self.respond(400,{'error':str(e) if isinstance(e,UiError) else '请求格式无效。'})

async def main():
    from app.process_lock import ProcessLock
    lease=ProcessLock(data_dir()/'.backend-lock');lease.acquire()
    bridge=Bridge(defer=True);server=Server(('127.0.0.1',0),Handler)
    server.bridge=bridge;server.loop=asyncio.get_running_loop();server.token=secrets.token_urlsafe(32);server.native_key=secrets.token_hex(32)
    server.authority='127.0.0.1:'+str(server.server_address[1]);url='http://'+server.authority+'/'+server.token+'/'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();await bridge.start()
    if '--pipe-host' in sys.argv:
        print(json.dumps({'url':url,'key':server.native_key}),flush=True);host=None
        async def monitor_parent():
            import ctypes
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
            kernel.CloseHandle.argtypes=[wintypes.HANDLE];kernel.CloseHandle.restype=wintypes.BOOL
            handle=kernel.OpenProcess(0x100000,False,int(sys.argv[sys.argv.index('--pipe-host')+1]))
            if not handle:bridge.stop.set();return
            try:
                while not bridge.stop.is_set():
                    if kernel.WaitForSingleObject(handle,0)==0:bridge.stop.set();break
                    await asyncio.sleep(.5)
            finally:kernel.CloseHandle(handle);bridge.stop.set()
        asyncio.create_task(monitor_parent())
    elif '--preview' in sys.argv:
        (work_dir()/'desktop-preview.json').write_text(json.dumps({'url':url,'pid':os.getpid()}),encoding='utf-8')
        host=None
    else:
        env=os.environ.copy();env['CLOUD_DOCK_URL']=url;env['CLOUD_DOCK_NATIVE_KEY']=server.native_key
        host=subprocess.Popen([str(ROOT/'CloudDock.exe'),'--host'],cwd=ROOT,env=env)
        async def monitor():await asyncio.to_thread(host.wait);bridge.stop.set()
        asyncio.create_task(monitor())
    try:await bridge.stop.wait()
    finally:
        await bridge.close();await asyncio.to_thread(server.shutdown);server.server_close();lease.close()
if __name__=='__main__':
    try:asyncio.run(main())
    except Exception:
        (work_dir()/'desktop-error.log').write_text('桌面服务启动失败；请检查文件是否完整、目录是否可写及网络连接。',encoding='utf-8')
        raise
