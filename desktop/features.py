"""Desktop features own persistence, account identity and explicit mutation intent."""
from __future__ import annotations
import asyncio,json,secrets,time
from dataclasses import asdict
from pathlib import Path
from app.cloud_operations import CloudOperations,CloudOperationError
from app.durable_store import DurableStore
from app.sync_engine import SyncEngine
from app.thumbnail_cache import ThumbnailCache
from app.domain import CloudFile
from app.upload import UploadJob,Fingerprint
from app.providers.web_upload import BaiduUploader,QuarkUploader
class Features:
    def __init__(self,bridge):
        self.bridge=bridge;self.store=DurableStore(bridge.workspace.data_dir/'activity.sqlite3')
        self.ops={name:CloudOperations(name,p.api) for name,p in bridge.panels.items()}
        from app.account_status import AccountStatus
        self.account_status=AccountStatus(self.ops)
        from app.baidu_adaptive_download import account_connections
        if 'baidu' in bridge.panels:
            bridge.panels['baidu'].download_policy=lambda:account_connections(self.account_status.internal_membership('baidu'))
        for name,ops in self.ops.items():
            ops.api.capacity_check=lambda size,name=name:self.account_status.check_upload(name,size)
            ops.api.capacity_changed=lambda name=name:self.account_status.invalidate(name)
        # Retire legacy rules, keeping their records and all local/cloud content.
        from app.retired_backup import retire
        retire(self.store)
        self.sync=SyncEngine(self.store,self.ops)
        from app.history_manager import HistoryManager
        self.history=HistoryManager(self.sync)
        from desktop.sync_review import SyncReview
        self.review=SyncReview(self)
        self.thumbs=ThumbnailCache(bridge.workspace.data_dir/'thumbnails')
        from desktop.feedback import Feedback
        self.feedback=Feedback(bridge.workspace,self.store)
        from desktop.share_temp_download import ShareTempDownload
        self.share_temp=ShareTempDownload(bridge)
        self.pending={};self.trash_rows={};self.thumb_items={};self.accounts={};self.closing=False
        self.sound_sequence=0;self.completed_pending=False;self.seen_completed=set();self.loop_task=None;self.last_write={};self.last_write_time={}
    async def start(self):
        for name,p in self.bridge.panels.items():
            old=p.on_transfer_change
            def changed(name=name,old=old):
                if old:old()
                if not self.closing:self.capture(name,force=True)
            p.on_transfer_change=changed
        self.sync.start();self.loop_task=asyncio.create_task(self.loop())
    def capture(self,name,force=False):
        p=self.bridge.panels[name];ops=self.ops[name]
        account=self.accounts.get(name)
        if not account or ops.identity_stamp!=ops.stamp():return
        for j in [*p.jobs,*p.upload_jobs]:
            if getattr(j,'_share_download',False):continue
            if not hasattr(j,'_desktop_id'):j._desktop_id=secrets.token_hex(12)
            if hasattr(j,'_persistent_account') and j._persistent_account!=account:continue
            if not hasattr(j,'_desktop_created_at'):
                previous=self.store.get('transfer',j._desktop_id)
                j._desktop_created_at=(previous or {}).get('created_at',0) if previous else time.time()
            j._persistent_account=account
            key=j._desktop_id;upload=getattr(j,'direction','download')=='upload'
            row={'id':key,'created_at':j._desktop_created_at,'account':account,'provider':name,'name':j.item.name,'path':j.item.path,'item':{k:v for k,v in asdict(j.item).items() if k!='thumbnail'},
                 'direction':'upload' if upload else 'download','status':j.state,'done':j.done,'total':j.total,'speed':0,'error':j.error,'warning':getattr(j,'warning',''),
                 'restart':j.requires_restart,'committed':getattr(j,'commit_started',False),'target':str(getattr(j,'target','') or ''),'connections':0,'group':getattr(j,'_download_group',''),'group_id':getattr(j,'_group_id',''),'priority':getattr(j,'_priority',1),'error_code':getattr(j,'error_code','') or next((code for entry,code in getattr(p,'_live_errors',{}).items() if entry is j),'')}
            if upload:row.update(local=str(j.local),fingerprint=asdict(j.fingerprint),directory=j.directory,remote_id=j.remote_id)
            else:
                from app.download_checkpoint import checkpoint
                row['destination']=str(j.destination)
                row['checkpoint']=checkpoint(j)
                if getattr(j,'_temporary_share',None):row['temporary_share']=j._temporary_share
            marker=(j.state,j.done//(1024*1024),getattr(j,'commit_started',False),getattr(j,'remote_id',None),getattr(j,'_priority',1),getattr(j,'_group_id',''))
            old_marker=self.last_write.get(key)
            if old_marker!=marker and (old_marker is None or old_marker[0]!=marker[0] or old_marker[2:]!=marker[2:] or time.monotonic()-self.last_write_time.get(key,0)>=2):
                self.store.put('transfer',key,row);self.last_write[key]=marker;self.last_write_time[key]=time.monotonic()
            if row.get('temporary_share') and j.state=='completed':self.share_temp.schedule_completed(row)
            if j.state=='completed' and key not in self.seen_completed:
                self.seen_completed.add(key);self.completed_pending=True
                if upload:self.account_status.invalidate(name)
    def archived(self):
        active={getattr(j,'_desktop_id',None) for p in self.bridge.panels.values() for j in [*p.jobs,*p.upload_jobs]}
        result=[]
        for row in self.store.recent_transfers():
            if row['id'] in active:continue
            public={k:v for k,v in row.items() if k in {'id','created_at','provider','name','path','direction','status','done','total','speed','error','warning','restart','committed','target','connections','group','group_id','priority','error_code','temporary_share'}}
            public['original_status']=row['status'];public['archived']=True;public['resumable']=bool(row.get('checkpoint'))
            public['account_name']=self.bridge.profiles.label(row['provider'],row.get('account','')) if hasattr(self.bridge,'profiles') else '原账号'
            public['account_current']=self.accounts.get(row['provider'])==row.get('account')
            if row['status'] not in ('completed','cancelled'):
                public.update(status='paused',error='已保留任务，重新连接原账号后可'+(('从已保存断点继续。' if row.get('checkpoint') else '重新下载。') if row['direction']=='download' else '核对后继续上传。'),restart=False)
            result.append(public)
        return result
    async def loop(self):
        while not self.closing:
            for name,ops in self.ops.items():
                if ops.api.authenticated:
                    try:
                        self.accounts[name]=await ops.account()
                        if hasattr(self.bridge,'profiles'):self.bridge.profiles.bind(name,self.accounts[name])
                    except Exception:pass
                self.capture(name)
                self.compact(name)
            for row in self.store.recent_transfers():
                if row.get('temporary_share') and row.get('status')=='completed':
                    self.share_temp.schedule_completed(row)
            review=[r for r in self.store.all('share_temp') if r.get('state') in ('intent','transferring','ready','needs_review')]
            self.feedback.condition('share_temp_review',bool(review),
                f'{len(review)} 项临时转存待核对，请检查百度网盘的 CloudDock 临时下载',
                'error',provider='baidu',view='transfers')
            jobs=[j for p in self.bridge.panels.values() for j in [*p.jobs,*p.upload_jobs]]
            if self.completed_pending and not any(j.state in ('queued','preparing','uploading','downloading','committing','verifying') for j in jobs):
                self.sound_sequence+=1;self.completed_pending=False
            self.feedback.tick(self.bridge.tasks())
            issue_count=len(self.store.all('sync_issue'))+len(self.store.all('sync_pending'))
            self.feedback.condition('sync',issue_count>0,f'同步有 {issue_count} 项需要处理','sync',view='backups')
            for name,p in self.bridge.panels.items():
                q=self.account_status.public(name)
                self.feedback.condition('space:'+name,q.get('total',0)>0 and q.get('free',0)/q['total']<.05,'网盘剩余空间不足 5%','error',provider=name,view='files')
                message=str(p.status.value or '')
                self.feedback.condition('login:'+name,not p.api.authenticated and any(x in message for x in ('登录失效','登录过期','重新登录','授权失效')),'账号需要重新连接','error',provider=name,view='accounts')
            await asyncio.sleep(2)
    def compact(self,name):
        p=self.bridge.panels[name]
        for jobs in (p.jobs,p.upload_jobs):
            ended=[j for j in jobs if j.state in ('completed','cancelled') and self.store.get('transfer',getattr(j,'_desktop_id',''))]
            for j in ended[:-50]:
                jobs.remove(j);key=j._desktop_id;self.last_write.pop(key,None);self.last_write_time.pop(key,None)
                getattr(p,'_live_errors',{}).pop(j,None)
        if len(self.seen_completed)>2048:self.seen_completed={getattr(j,'_desktop_id','') for p in self.bridge.panels.values() for j in [*p.jobs,*p.upload_jobs] if j.state=='completed'}
    def snapshot(self):
        return {'feedback':self.feedback.snapshot(),'capacities':{name:self.account_status.public(name) for name in self.ops},
                'sync_rules':[{**r,'issue_count':len(self.sync.entries('sync_issue',r['id'])),'pending_count':len(self.sync.entries('sync_pending',r['id'])),'active':self.sync.active.get(r['id'])} for r in self.sync.rules()],
                'backups':[],
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
            from app.download_checkpoint import restore
            from app.direct_download import DownloadError
            job=await p.start_download(current,destination=Path(row['destination']),notify=False)
            if job is None:raise CloudOperationError('这个文件已有传输任务。')
            try:
                if row.get('checkpoint'):restore(job,row['checkpoint'])
            except (DownloadError,OSError) as error:
                p.jobs.remove(job)
                raise CloudOperationError(str(error)) from None
            job._desktop_id=row['id'];job._desktop_created_at=row.get('created_at',0);job._persistent_account=row['account']
            p._ensure_live_worker()
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
        job._desktop_id=row['id'];job._desktop_created_at=row.get('created_at',0);job._persistent_account=row['account'];job._download_group=row.get('group','');job._group_id=row.get('group_id','');job._priority=row.get('priority',1)
        if row.get('temporary_share'):job._temporary_share=row['temporary_share']
        self.capture(name)
    async def upload_progress(self,name):self.capture(name,force=True)
    async def action(self,d):
        action=d.get('action');name=d.get('provider');p=self.bridge.panels.get(name)
        if isinstance(action,str) and action.startswith('sync_'):return await self.sync_action(d)
        if action=='sound':
            if type(d.get('value')) is not bool:raise CloudOperationError('设置无效。')
            self.bridge.workspace.settings['completion_sound']=d['value'];await self.bridge.workspace.save();return {}
        if action=='clear_thumbnails':await self.thumbs.clear();return {}
        if action=='task':
            row=self.store.get('transfer',d.get('id'))
            if row:
                command=d.get('command')
                if command=='cleanup_temp' and row.get('temporary_share') and row['status']=='completed':
                    await self.share_temp.retry_cleanup(row['temporary_share'],row)
                elif command=='resume':await self.resume(row)
                elif command=='cancel':
                    # Keep an unverifiable leftover intact; never unlink an arbitrary stored path.
                    if row.get('checkpoint'):
                        from app.download_checkpoint import restore
                        from app.direct_download import DirectDownload,DownloadError
                        job=DirectDownload(CloudFile(**row['item']),Path(row['destination']),None,None,None,None)
                        if row['checkpoint'].get('segments'):
                            from app.parallel_download import ParallelDownload
                            job=ParallelDownload(job.item,job.destination,None,None,None,None)
                        try:restore(job,row['checkpoint']);await job.cancel()
                        except (DownloadError,OSError):pass
                    row.update(status='cancelled',error='',checkpoint=None);self.store.put('transfer',row['id'],row)
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
            self.account_status.invalidate(name)
            return {}
        if getattr(p,'share',None) is not None:raise CloudOperationError('请先返回本人网盘。')
        if action=='delete_preview' and d.get('context')!=self.bridge.context(p):raise CloudOperationError('当前目录已变化，请重新操作。')
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
                await ops.delete(row)
            self.account_status.invalidate(name)
            await p.load_directory(p.path,p.page_index);return {}
        raise CloudOperationError('未识别的操作。')
    def consume(self,key,name,account,ops,kind):
        row=self.pending.pop(key,None)
        if not row or row[:3]!=(name,account,ops.stamp()) or row[4]!=kind or time.monotonic()-row[3]>300:raise CloudOperationError('确认已过期，请重新操作。')
        return row[5]
    async def sync_action(self,d):
        action=d['action'];rid=d.get('id')
        if action in ('sync_changes','sync_versions','sync_restore_version','sync_templates'):return await self.review.action(d)
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
        await self.share_temp.close()
        await self.sync.close()
        await self.account_status.close()
        for name,p in self.bridge.panels.items():
            # Stop the queue first so pausing one job cannot start another during shutdown.
            p._downloads_stopping=True
            worker=getattr(p,'_live_worker',None)
            if worker:worker.cancel();await asyncio.gather(worker,return_exceptions=True)
            for job in p.jobs:
                if job.state not in ('completed','cancelled'):
                    if getattr(job,'_share_download',False):await job.cancel()
                    else:await job.pause()
            self.last_write.clear()
            self.capture(name,force=True)
            # Shutdown closes sessions next; retained checkpoints belong to the original account.
            p.jobs.clear()
        self.store.close()
