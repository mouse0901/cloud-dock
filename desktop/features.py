"""Desktop features own persistence, account identity and explicit mutation intent."""
from __future__ import annotations
import asyncio,json,secrets,time
from dataclasses import asdict
from pathlib import Path
from app.cloud_operations import CloudOperations,CloudOperationError
from app.durable_store import DurableStore
from app.folder_backup import FolderBackup
from app.sync_engine import SyncEngine
from app.thumbnail_cache import ThumbnailCache
from app.domain import CloudFile
from app.upload import UploadJob,Fingerprint
from app.providers.web_upload import BaiduUploader,QuarkUploader
class Features:
    def __init__(self,bridge):
        self.bridge=bridge;self.store=DurableStore(bridge.workspace.data_dir/'activity.sqlite3')
        self.ops={name:CloudOperations(name,p.api) for name,p in bridge.panels.items()}
        self.backup=FolderBackup(self.store,self.ops)
        self.sync=SyncEngine(self.store,self.ops)
        from app.history_manager import HistoryManager
        self.history=HistoryManager(self.sync)
        self.thumbs=ThumbnailCache(bridge.workspace.data_dir/'thumbnails')
        self.pending={};self.trash_rows={};self.thumb_items={};self.accounts={};self.closing=False
        self.sound_sequence=0;self.completed_pending=False;self.seen_completed=set();self.loop_task=None;self.last_write={};self.last_write_time={}
    async def start(self):
        for name,p in self.bridge.panels.items():
            old=p.on_transfer_change
            def changed(name=name,old=old):
                if old:old()
                if not self.closing:self.capture(name,force=True)
            p.on_transfer_change=changed
        self.backup.start();self.sync.start();self.loop_task=asyncio.create_task(self.loop())
    def capture(self,name,force=False):
        p=self.bridge.panels[name];ops=self.ops[name]
        account=self.accounts.get(name)
        if not account or ops.identity_stamp!=ops.stamp():return
        for j in [*p.jobs,*p.upload_jobs]:
            if getattr(j,'_share_download',False):continue
            if not hasattr(j,'_desktop_id'):j._desktop_id=secrets.token_hex(12)
            if hasattr(j,'_persistent_account') and j._persistent_account!=account:continue
            j._persistent_account=account
            key=j._desktop_id;upload=getattr(j,'direction','download')=='upload'
            row={'id':key,'account':account,'provider':name,'name':j.item.name,'path':j.item.path,'item':{k:v for k,v in asdict(j.item).items() if k!='thumbnail'},
                 'direction':'upload' if upload else 'download','status':j.state,'done':j.done,'total':j.total,'speed':0,'error':j.error,'warning':getattr(j,'warning',''),
                 'restart':j.requires_restart,'committed':getattr(j,'commit_started',False),'target':str(getattr(j,'target','') or ''),'connections':0}
            if upload:row.update(local=str(j.local),fingerprint=asdict(j.fingerprint),directory=j.directory,remote_id=j.remote_id)
            else:row['destination']=str(j.destination)
            marker=(j.state,j.done//(1024*1024),getattr(j,'commit_started',False),getattr(j,'remote_id',None))
            old_marker=self.last_write.get(key)
            if old_marker!=marker and (old_marker is None or old_marker[0]!=marker[0] or old_marker[2:]!=marker[2:] or time.monotonic()-self.last_write_time.get(key,0)>=2):
                self.store.put('transfer',key,row);self.last_write[key]=marker;self.last_write_time[key]=time.monotonic()
            if j.state=='completed' and key not in self.seen_completed:self.seen_completed.add(key);self.completed_pending=True
    def archived(self):
        active={getattr(j,'_desktop_id',None) for p in self.bridge.panels.values() for j in [*p.jobs,*p.upload_jobs]}
        result=[]
        for row in self.store.all('transfer'):
            if row['id'] in active:continue
            public={k:v for k,v in row.items() if k in {'id','provider','name','path','direction','status','done','total','speed','error','warning','restart','committed','target','connections'}}
            public['archived']=True
            if row['status'] not in ('completed','cancelled'):
                public.update(status='paused',error='已保留任务，重新连接原账号后可'+('重新下载。' if row['direction']=='download' else '核对后继续上传。'),restart=False)
            result.append(public)
        return result
    async def loop(self):
        while not self.closing:
            for name,ops in self.ops.items():
                if ops.api.authenticated:
                    try:self.accounts[name]=await ops.account()
                    except Exception:pass
                self.capture(name)
            jobs=[j for p in self.bridge.panels.values() for j in [*p.jobs,*p.upload_jobs]]
            if self.completed_pending and not any(j.state in ('queued','preparing','uploading','downloading','committing','verifying') for j in jobs):
                self.sound_sequence+=1;self.completed_pending=False
            await asyncio.sleep(2)
    def snapshot(self):
        return {'sync_rules':[{**r,'issue_count':len(self.sync.entries('sync_issue',r['id'])),'pending_count':len(self.sync.entries('sync_pending',r['id'])),'active':self.sync.active.get(r['id'])} for r in self.sync.rules()],
                'backups':[{**r,'issues':[{'path':x['id'].split(':',1)[-1],'message':x.get('error','需核对')} for x in self.store.issues(r['id'])][:50],'active':self.backup.running.get(r['id'])} for r in self.backup.rules()],
                'sound_sequence':self.sound_sequence,'sound':self.bridge.workspace.settings.get('completion_sound',True)}
    def thumbnail_key(self,name,item):
        ops=self.ops.get(name);account=self.accounts.get(name)
        if not ops or not account or ops.stamp()!=ops.identity_stamp or not item.thumbnail:return ''
        key=self.thumbs.key(account,item)
        if len(self.thumb_items)>1000:self.thumb_items.clear()
        self.thumb_items[key]=(name,account,item,ops.stamp())
        return key
    async def thumbnail(self,key):
        row=self.thumb_items.get(key)
        if not row:return None
        name,account,item,stamp=row;ops=self.ops[name]
        return await self.thumbs.get(account,item,ops.api.client,lambda:ops.stamp()==stamp and self.accounts.get(name)==account)
    async def resume(self,row):
        name=row['provider'];p=self.bridge.panels[name];ops=self.ops[name]
        if await ops.account()!=row['account']:raise CloudOperationError('请登录创建此任务时的原账号。')
        if getattr(p,'share',None) is not None:raise CloudOperationError('请先返回我的文件。')
        item=CloudFile(**row['item']);files=await ops.list(item.parent)
        if row['direction']=='download':
            current=next((f for f in files if f.id==item.id and f.path==item.path and f.size==item.size and f.revision==item.revision),None)
            if current is None:raise CloudOperationError('云端文件已变化，请重新选择；未复用旧任务。')
            previous=p.download_destination
            try:p.download_destination=Path(row['destination']);await p.start_download(current)
            finally:p.download_destination=previous
            job=next((j for j in p.jobs if j.item.id==item.id and j.item.path==item.path and j.state!='completed'),None)
            if job is None:raise CloudOperationError('无法恢复下载任务，请重新选择文件。')
        else:
            local=Path(row['local'])
            if asdict(Fingerprint.read(local))!=row['fingerprint']:raise CloudOperationError('本地文件已变化，请重新选择上传。')
            api,_,owner,current=p.upload_snapshot()
            job=UploadJob(local,name,item.parent,(BaiduUploader if name=='baidu' else QuarkUploader)(api),owner=owner,current_owner=current,remote_name=item.name,
                          progress=lambda done,total:self.upload_progress(name))
            if row['committed']:
                if not row.get('remote_id'):raise CloudOperationError('上次提交结果不确定，请先在目标目录核对。为避免重复，未重新上传。')
                job.commit_started=True;job.remote_id=row['remote_id']
            job._desktop_id=row['id'];p.upload_jobs.append(job);p._ensure_upload_worker()
        job._desktop_id=row['id'];job._persistent_account=row['account'];self.capture(name)
    async def upload_progress(self,name):self.capture(name,force=True)
    async def action(self,d):
        action=d.get('action');name=d.get('provider');p=self.bridge.panels.get(name)
        if isinstance(action,str) and action.startswith('sync_'):return await self.sync_action(d)
        if action=='sound':
            if type(d.get('value')) is not bool:raise CloudOperationError('设置无效。')
            self.bridge.workspace.settings['completion_sound']=d['value'];await self.bridge.workspace.save();return {}
        if action=='clear_thumbnails':await self.thumbs.clear();return {}
        if action=='backup_remove':
            await self.backup.set_enabled(d.get('id'),False)
            self.store.remove('backup',d.get('id'));return {}
        if action=='backup_toggle':await self.backup.set_enabled(d.get('id'),bool(d.get('enabled')));return {}
        if action=='task':
            row=self.store.get('transfer',d.get('id'))
            if row:
                command=d.get('command')
                if command=='resume':await self.resume(row)
                elif command=='cancel':row.update(status='cancelled',error='');self.store.put('transfer',row['id'],row)
                elif command=='open' and row['status']=='completed' and row['direction']=='download':
                    from app.local_files import open_local_folder
                    await asyncio.to_thread(open_local_folder,Path(row['target']).parent)
                else:raise CloudOperationError('此任务不支持该操作。')
                return {}
        if p is None or not p.api.authenticated:raise CloudOperationError('请先连接网盘账号。')
        ops=self.ops[name];account=await ops.account();self.accounts[name]=account
        if action=='trash_list':
            page=d.get('page',0);rows=await ops.trash(page);token=secrets.token_hex(16)
            self.trash_rows[token]=(name,account,ops.stamp(),time.monotonic(),rows)
            return {'rows':rows,'page':page,'context':token}
        if action=='trash_restore':
            stored=self.trash_rows.get(d.get('context'))
            if not stored or stored[:3]!=(name,account,ops.stamp()) or time.monotonic()-stored[3]>300:raise CloudOperationError('回收站列表已变化，请刷新。')
            row=next((x for x in stored[4] if x['id']==d.get('id')),None)
            if row is None:raise CloudOperationError('请选择已列出的文件。')
            async with ops.lock:await ops.restore(row)
            return {}
        if getattr(p,'share',None) is not None:raise CloudOperationError('请先返回本人网盘。')
        if action in ('delete_preview','backup_preview') and d.get('context')!=self.bridge.context(p):raise CloudOperationError('当前目录已变化，请重新操作。')
        if action=='delete_preview':
            ids=d.get('ids',[]);items=[x for x in p.browser_rows if x.id in ids]
            if not items or len(items)!=len(set(ids)) or len(items)>50:raise CloudOperationError('请选择当前目录中的项目。')
            for j in [*p.jobs,*p.upload_jobs]:
                if j.state not in ('completed','cancelled') and any(j.item.path==x.path or j.item.path.startswith(x.path+'/') for x in items):raise CloudOperationError('所选项目仍有传输任务，请先完成或取消任务。')
            key=secrets.token_hex(16);self.pending[key]=(name,account,ops.stamp(),time.monotonic(),'delete',items)
            return {'confirmation':key,'names':[x.name for x in items]}
        if action=='delete_confirm':
            row=self.consume(d.get('confirmation'),name,account,ops,'delete')
            async with ops.lock:
                self.backup.suppress(name,account,row)
                await ops.delete(row)
            await p.load_directory(p.path,p.page_index);return {}
        if action=='backup_preview':
            local=self.bridge.selected_paths(d.get('selection'),'directory')[0]
            plan=await self.backup.preview(local,name,p.path)
            key=secrets.token_hex(16);self.pending[key]=(name,account,ops.stamp(),time.monotonic(),'backup',plan)
            return {'confirmation':key,'plan':plan}
        if action=='backup_confirm':
            plan=self.consume(d.get('confirmation'),name,account,ops,'backup');self.backup.create(plan);return {}
        raise CloudOperationError('未识别的操作。')
    def consume(self,key,name,account,ops,kind):
        row=self.pending.pop(key,None)
        if not row or row[:3]!=(name,account,ops.stamp()) or row[4]!=kind or time.monotonic()-row[3]>300:raise CloudOperationError('确认已过期，请重新操作。')
        return row[5]
    async def sync_action(self,d):
        action=d['action'];rid=d.get('id')
        if action=='sync_history_preview':return await self.history.preview(rid,d.get('side'),d.get('keep'),d.get('days'))
        if action=='sync_history_clean':return await self.history.clean(d.get('confirmation'))
        if action=='sync_toggle':await self.sync.toggle(rid,d.get('enabled'));return {}
        if action=='sync_remove':await self.sync.remove(rid);return {}
        if action=='sync_detail':return self.sync.detail(rid)
        if action=='sync_check':
            rule=self.store.get('sync_rule',rid)
            if not rule:raise CloudOperationError('同步任务不存在。')
            rule['next']=0;self.sync.save_rule(rule);self.sync.wake.set();return {}
        if action=='sync_resolve':self.sync.request_resolution(rid,d.get('rel'),d.get('choice'));return {'queued':True}
        if action=='sync_open_local':
            from app.local_files import open_local_folder
            rule=self.store.get('sync_rule',rid)
            if not rule:raise CloudOperationError('任务不存在。')
            path=Path(rule['local_root'])
            if d.get('history'):path=path/'.clouddock-history'/rid
            if not path.is_dir():raise CloudOperationError('这个位置尚不存在。')
            await asyncio.to_thread(open_local_folder,path);return {}
        name=d.get('provider');ops=self.ops.get(name)
        if ops is None:raise CloudOperationError('请选择网盘。')
        account=await ops.account()
        if action=='sync_browse':return {'rows':await self.sync.browse(name,d.get('path','/'))}
        if action=='sync_preview':
            kind=d.get('kind');paths=self.bridge.selected_paths(d.get('selection'),'directory' if kind=='folder' else 'files')
            if len(paths)!=1:raise CloudOperationError('单文件同步一次选择一个文件。')
            plan=await self.sync.preview(paths[0],name,d.get('remote'),kind,d.get('mode'),d.get('exclude',[]),d.get('remote_name'))
            key=secrets.token_hex(16);self.pending[key]=(name,account,ops.stamp(),time.monotonic(),'sync',plan)
            return {'confirmation':key,'plan':plan}
        if action=='sync_confirm':
            plan=self.consume(d.get('confirmation'),name,account,ops,'sync');self.sync.create(plan);return {'id':plan['id']}
        raise CloudOperationError('未识别的同步操作。')
    async def close(self):
        self.closing=True
        if self.loop_task:self.loop_task.cancel();await asyncio.gather(self.loop_task,return_exceptions=True)
        await self.sync.close()
        await self.backup.close()
        for name in self.ops:self.capture(name,force=True)
        self.store.close()
