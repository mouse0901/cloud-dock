"""Preview/choose/confirm downloads, with bounded scans and one-shot plans."""
import asyncio,secrets,time
from pathlib import Path,PurePosixPath
from app.cloud_operations import CloudOperationError
from app.direct_download import DownloadError
from app.local_files import prepare_download_root
from desktop.experience import Experience

class DownloadDialog:
    def __init__(self,bridge):self.bridge=bridge;self.plans={};self.tasks={}
    def guard(self,row):
        row['ops'].guard(row['stamp'])
        if row['panel'].generation!=row['generation']:raise CloudOperationError('账号已变化，请重新选择。')
    def get(self,token):
        row=self.plans.get(token)
        if not row or time.monotonic()-row['at']>600:raise CloudOperationError('下载确认已过期，请重新选择。')
        self.guard(row);return row
    async def action(self,d):
        action=d['action']
        if action=='download_preferences':
            if type(d.get('quick')) is not bool:raise CloudOperationError('下载设置无效。')
            if d['quick'] and not self.bridge.workspace.settings.get('download_directory_confirmed'):
                raise CloudOperationError('请先在下载确认中选择默认保存位置。')
            self.bridge.workspace.settings['quick_download']=d['quick'];await self.bridge.workspace.save()
            return {'quick':d['quick']}
        if action=='download_prepare':
            if not self.bridge.features:raise CloudOperationError('工作空间正在准备。')
            share=self.bridge.shares.session(d.get('session')) if d.get('session') else None
            name=share['provider'] if share else d.get('provider');p=self.bridge.panels.get(name)
            if p is None or not p.api.authenticated or p.reader is None:raise CloudOperationError('请先登录网盘账号。')
            ops=self.bridge.features.ops[name]
            self.bridge.features.accounts[name]=await ops.account();stamp=ops.stamp();ops.guard(stamp)
            if share:
                if d.get('context')!=str(share['revision']):raise CloudOperationError('分享目录已经变化。')
                rows=share['rows'];parent=share['path'];resolver=share['source'];share_key=d['session']
            else:
                if d.get('context')!=self.bridge.context(p) or p.browser_busy or getattr(p,'share',None):raise CloudOperationError('目录已经变化，请重新选择。')
                rows=p.browser_rows;parent=p.path;resolver=None;share_key=''
            ids=d.get('ids')
            if not isinstance(ids,list) or not 1<=len(ids)<=50 or any(not isinstance(x,str) for x in ids):raise CloudOperationError('一次请选择 1–50 项。')
            selected=[x for x in rows if x.id in ids]
            if len(selected)!=len(set(ids)):raise CloudOperationError('请选择当前目录中的文件。')
            if sum(not t.done() for t in self.tasks.values())>=2:raise CloudOperationError('请先完成或取消正在扫描的下载。')
            for key,row in list(self.plans.items()):
                if time.monotonic()-row['at']>600:
                    task=self.tasks.pop(key,None)
                    if task:task.cancel()
                    self.plans.pop(key,None)
            if len(self.plans)>=24:raise CloudOperationError('下载确认过多，请稍后重试。')
            token=secrets.token_hex(24);root=Path(self.bridge.workspace.settings['download_dir'])
            row={'provider':name,'stamp':stamp,'generation':p.generation,'at':time.monotonic(),'state':'scanning',
                'count':0,'bytes':0,'folders':0,'items':[],'directories':[],'error':'','destination':str(root),
                'group':selected[0].name if len(selected)==1 else f'{len(selected)} 个项目','names':[x.name for x in selected],
                'ops':ops,'panel':p,'resolver':resolver,'share_key':share_key,'remember':False}
            self.plans[token]=row
            class ScanOps:
                def guard(inner,value):self.guard(row)
                async def list(inner,path,limit=None):
                    if not share:return await ops.list(path,limit)
                    found=[]
                    for page in range(200):
                        self.guard(row);items=await share['list'](path,page);self.guard(row);found.extend(items)
                        if limit is not None and len(found)>limit:raise CloudOperationError('分享内容过多，请分批下载。')
                        if len(items)<50:return found
                    raise CloudOperationError('分享文件夹过大，请分批下载。')
            self.tasks[token]=asyncio.create_task(self.bridge.experience.scan(ScanOps(),row,selected,parent))
            return {'token':token,**self.snapshot(row)}
        row=self.get(d.get('token'))
        if action=='download_quick':
            settings=self.bridge.workspace.settings
            from app.direct_download import _safe_name
            eligible=(row['state']=='ready' and settings.get('quick_download') is True
                      and settings.get('download_directory_confirmed') is True
                      and not row['folders'] and row['count']==1
                      and row['destination']==settings['download_dir'])
            if eligible:
                item,parent=row['items'][0]
                # Explicit confirmation remains necessary for any occupied name.
                eligible=not (Path(parent)/_safe_name(item.name)).exists()
            if not eligible:return {'confirmation_required':True,**self.snapshot(row)}
            action='download_commit'
        if action=='download_status':return self.snapshot(row)
        if action=='download_cancel':
            if row['state'] not in ('submitted','submitting'):
                self.tasks.pop(d['token']).cancel();row['state']='cancelled';self.plans.pop(d['token'],None)
            return {}
        if action=='download_destination':
            if row['state']!='ready':raise CloudOperationError('请等待扫描完成后选择位置。')
            selected=self.bridge.selected_paths(d.get('selection'),'directory')[0]
            destination=prepare_download_root(selected);old=Path(row['destination'])
            row['items']=[(item,destination/Path(parent).relative_to(old)) for item,parent in row['items']]
            row['directories']=[str(destination/Path(path).relative_to(old)) for path in row['directories']]
            row['destination']=str(destination);return self.snapshot(row)
        if action!='download_commit' or row['state']!='ready':raise CloudOperationError('下载清单尚未准备好或已提交。')
        row['state']='submitting'
        try:
            root=Path(row['destination']);Experience.check_disk(root,row['bytes'])
            fallback=set()
            if row['provider']=='baidu' and row['share_key'] and row['resolver']:
                for item,_ in row['items']:
                    if item.size<1_000_000_000:continue
                    try:await row['resolver'](item)
                    except DownloadError as error:
                        if error.code!='share_ticket_unavailable':
                            row['state']='ready'
                            raise CloudOperationError(str(error)) from None
                        fallback.add((item.id,item.path))
            Experience.safe_directory(root,root)
            for directory in row['directories']:Experience.safe_directory(Path(directory),root)
            if d.get('remember') is True:
                self.bridge.workspace.settings['download_dir']=str(root)
                self.bridge.workspace.settings['download_directory_confirmed']=True
                if type(d.get('quick')) is bool:self.bridge.workspace.settings['quick_download']=d['quick']
                await self.bridge.workspace.save()
                for p in self.bridge.panels.values():p.set_download_root(root)
            count=0
            for item,parent in row['items']:
                self.guard(row)
                queued_item=item;resolver=row['resolver'];share_key=row['share_key'];temporary=None
                if (item.id,item.path) in fallback:
                    queued_item,temporary=await self.bridge.features.share_temp.stage(row,item)
                    resolver=None;share_key=''
                job=await row['panel'].start_download(queued_item,destination=parent,group=row['group'],notify=False,
                    resolver_override=resolver,share_key=share_key)
                if job is not None:
                    job._group_id=d['token'];count+=1
                    if temporary:
                        job._share_download=False
                        job._desktop_id=secrets.token_hex(12)
                        self.bridge.features.share_temp.queued(temporary,job)
                elif temporary:
                    raise CloudOperationError('临时文件已转存至网盘，但下载任务未能创建。请在 CloudDock 临时下载中核对。')
            if row['share_key']:self.bridge.shares.session(row['share_key'])['used']=True
            row['panel']._downloads_changed();row['panel']._ensure_live_worker()
            self.bridge.features.capture(row['provider'],force=True);row['state']='submitted'
            self.plans.pop(d['token'],None);self.tasks.pop(d['token'],None)
            return {'count':count,'destination':row['destination']}
        except BaseException:
            # Already-created jobs remain visible and runnable if a later item
            # fails. This consumed plan cannot enqueue them a second time.
            row['panel']._downloads_changed();row['panel']._ensure_live_worker()
            if row['state']!='ready':row['state']='failed'
            raise
    @staticmethod
    def snapshot(row):return {k:row[k] for k in ('state','count','bytes','folders','error','destination','group','names')}
    async def close(self):
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
