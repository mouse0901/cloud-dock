"""Account-bound file actions, cancellable full-drive scans and local media previews."""
from __future__ import annotations
import asyncio,secrets,time,json,re,shutil
from pathlib import Path,PurePosixPath
from app.cloud_operations import CloudOperationError
from app.direct_download import DownloadError

IMAGE_TYPES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif','.bmp':'image/bmp'}
VIDEO_TYPES={'.mp4':'video/mp4','.m4v':'video/mp4','.webm':'video/webm','.mov':'video/quicktime'}

def cloud_path(value):
    if not isinstance(value,str) or not value.startswith('/') or len(value)>4096 or '\\' in value or any(ord(c)<32 for c in value):raise CloudOperationError('云端路径无效。')
    if value!='/' and any(c in ('','.','..') for c in value.split('/')[1:]):raise CloudOperationError('云端路径无效。')
    return value

def file_name(value):
    if not isinstance(value,str) or not value.strip() or len(value)>200 or value in ('.','..') or any(c in value for c in '/\\') or any(ord(c)<32 for c in value):raise CloudOperationError('请输入有效的名称。')
    return value

def entry(item):
    return {'id':item.id,'name':item.name,'path':item.path,'parent':item.parent,'size':item.size,'directory':item.is_dir}

def byte_range(value,total):
    if not value:return 0,total-1,200
    m=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
    if not m or not any(m.groups()) or total<=0:raise ValueError('range')
    a,b=m.groups()
    if not a:
        if int(b)<=0:raise ValueError('range')
        start,end=max(0,total-int(b)),total-1
    else:start,end=int(a),min(int(b) if b else total-1,total-1)
    if not 0<=start<=end<total:raise ValueError('range')
    return start,min(end,start+2*1024*1024-1),206

class Productivity:
    def __init__(self,bridge):self.bridge=bridge;self.plans={};self.jobs={};self.tasks={};self.media={};self.closed=False
    async def owner(self,d):
        name=d.get('provider');f=self.bridge.features
        if not f or name not in f.ops:raise CloudOperationError('工作空间正在准备。')
        p=self.bridge.panels[name];ops=f.ops[name];account=await ops.account()
        if getattr(p,'share',None):raise CloudOperationError('请返回本人网盘后操作。')
        return name,p,ops,account,ops.stamp()
    def valid(self,row):
        ops=self.bridge.features.ops[row['provider']]
        return not self.closed and ops.stamp()==row['_stamp']
    def public(self,row):
        if not self.valid(row):raise CloudOperationError('账号已变化，请重新操作。')
        return {k:v for k,v in row.items() if not k.startswith('_')}
    def begin(self,name,stamp,kind):
        # Bound retained results and cached preview files within one application run.
        for key,row in list(self.jobs.items()):
            if row['status']!='running' and (time.monotonic()-row['_time']>900 or len(self.jobs)>=20):self.discard(key)
        if len(self.jobs)>=20:raise CloudOperationError('后台操作较多，请等待或关闭一个预览。')
        token=secrets.token_hex(32);row={'token':token,'provider':name,'kind':kind,'status':'running','message':'正在准备…','count':0,'rows':[],'_stamp':stamp,'_time':time.monotonic()}
        self.jobs[token]=row;return row
    def discard(self,key):
        row=self.jobs.get(key)
        task=self.tasks.pop(key,None)
        if task and not task.done():task.cancel()
        self.jobs.pop(key,None);media=self.media.pop(key,None)
        if media:
            try:media['path'].unlink();media['path'].parent.rmdir()
            except OSError:pass
    def overlap(self,name,account,paths):
        f=self.bridge.features
        for rule in f.sync.rules()+f.store.all('backup'):
            if rule.get('provider')!=name or rule.get('account')!=account:continue
            root=rule.get('remote_root',rule.get('target',''))
            if rule.get('kind')=='file':root=root.rstrip('/')+'/'+rule['remote_name']
            if root and any(p==root or p.startswith(root.rstrip('/')+'/') or root.startswith(p.rstrip('/')+'/') for p in paths):
                if rule.get('enabled') or f.sync.entries('sync_pending',rule['id']):raise CloudOperationError('这个位置关联同步或备份，请先暂停任务并处理未完成更新。')
    async def action(self,d):
        action=d['action']
        if action in ('tools_status','tools_cancel'):
            row=self.jobs.get(d.get('token'))
            if not row:raise CloudOperationError('操作记录已过期。')
            if action=='tools_cancel':
                if row['kind']=='operation':raise CloudOperationError('已提交的文件操作不能中途取消，请等待结果。')
                self.discard(row['token']);return {}
            return self.public(row)
        name,p,ops,account,stamp=await self.owner(d)
        if action=='tools_browse':
            path=cloud_path(d.get('path','/'));rows=await ops.list(path,limit=5000);ops.guard(stamp)
            return {'path':path,'rows':[entry(x) for x in rows if x.is_dir]}
        if action=='tools_search':
            term=d.get('term','')
            if not isinstance(term,str) or not 1<=len(term.strip())<=100:raise CloudOperationError('输入 1–100 个字符的文件名关键词。')
            for key,row in list(self.jobs.items()):
                if row['kind']=='search' and row['provider']==name:self.discard(key)
            row=self.begin(name,stamp,'search');self.tasks[row['token']]=asyncio.create_task(self.search(row,ops,term.strip().casefold()))
            return self.public(row)
        if action in ('tools_plan','tools_preview'):
            if d.get('context')!=self.bridge.context(p) or p.browser_busy:raise CloudOperationError('目录已变化，请重新选择。')
            ids=d.get('ids',[])
            if not isinstance(ids,list) or len(ids)>1:raise CloudOperationError('每次选择一个项目。')
            item=next((f for f in p.browser_rows if ids==[f.id]),None)
            if action=='tools_preview':
                if not item or item.is_dir:raise CloudOperationError('请选择一个文件。')
                suffix=Path(item.name).suffix.lower();mime=IMAGE_TYPES.get(suffix) or VIDEO_TYPES.get(suffix)
                if not mime:raise CloudOperationError('此格式暂不支持内置预览，请下载后用本地软件打开。')
                maximum=32*1024*1024 if suffix in IMAGE_TYPES else 128*1024*1024
                if not 0<item.size<=maximum:raise CloudOperationError(f'预览上限为 {maximum//1024//1024} MB，请下载此文件后打开。')
                # Only one active media preparation per window.
                for key,old in list(self.jobs.items()):
                    if old['kind']=='preview':self.discard(key)
                row=self.begin(name,stamp,'preview');row.update(name=item.name,total=item.size,mime=mime)
                self.tasks[row['token']]=asyncio.create_task(self.prepare_preview(row,p,item,account))
                return self.public(row)
            kind=d.get('kind')
            if kind not in ('mkdir','rename','move','copy'):raise CloudOperationError('未知文件操作。')
            if kind!='mkdir' and item is None:raise CloudOperationError('请选择当前目录中的项目。')
            if kind=='copy' and name!='baidu':raise CloudOperationError('夸克暂未接入云端复制。')
            title=file_name(d.get('name')) if kind in ('mkdir','rename') else item.name
            target=cloud_path(d.get('target',p.path)) if kind in ('move','copy') else p.path
            if item and item.is_dir and (target==item.path or target.startswith(item.path+'/')):raise CloudOperationError('不能把文件夹放进自身或子目录。')
            self.overlap(name,account,[target.rstrip('/')+'/'+title]+([item.path] if item else []))
            if any(x.name.casefold()==title.casefold() for x in await ops.list(target)):raise CloudOperationError('目标有同名项目，请更换名称或位置。')
            ops.guard(stamp)
            self.plans={k:v for k,v in self.plans.items() if time.monotonic()-v['_time']<300}
            if len(self.plans)>=20:raise CloudOperationError('待确认操作过多，请稍后重试。')
            token=secrets.token_hex(32);self.plans[token]={'provider':name,'account':account,'_stamp':stamp,'_time':time.monotonic(),'kind':kind,'item':item,'target':target,'name':title}
            return {'confirmation':token,'source':item.path if item else '', 'target':target.rstrip('/')+'/'+title,'kind':kind}
        if action=='tools_commit':
            plan=self.plans.pop(d.get('confirmation'),None)
            if not plan or (plan['provider'],plan['account'],plan['_stamp'])!=(name,account,stamp) or time.monotonic()-plan['_time']>300:raise CloudOperationError('确认已过期，请重新操作。')
            row=self.begin(name,stamp,'operation');self.tasks[row['token']]=asyncio.create_task(self.mutate(row,plan,p,ops));return self.public(row)
        raise CloudOperationError('未识别的操作。')
    async def search(self,row,ops,term):
        queue=['/'];visited=set()
        try:
            deadline=time.monotonic()+300
            while queue:
                if time.monotonic()>deadline:raise CloudOperationError('搜索超时。')
                path=queue.pop(0)
                if path in visited:raise CloudOperationError('目录结构重复，搜索已停止。')
                visited.add(path)
                for page in range(1000):
                    ops.guard(row['_stamp']);files=await ops.api._fetch_files(path,page);ops.guard(row['_stamp'])
                    for item in files:
                        cloud_path(item.path)
                        if item.parent!=path:raise CloudOperationError('文件路径与目录不一致。')
                        row['count']+=1
                        if term in item.name.casefold():row['rows'].append(entry(item))
                        if item.is_dir:queue.append(item.path)
                        if row['count']>=50000 or len(row['rows'])>=500:
                            row.update(status='partial',message='已达到本次扫描上限，以下为部分结果。请缩小关键词范围。');return
                    row['message']=f'已检查 {row["count"]} 项 · 正在搜索 {path}'
                    if len(files)<50:break
                    await asyncio.sleep(.08)
                else:raise CloudOperationError('单个文件夹过大，搜索未完成。')
                await asyncio.sleep(.08)
            row.update(status='completed',message=f'全盘搜索完成 · 共检查 {row["count"]} 项')
        except asyncio.CancelledError:raise
        except Exception:row.update(status='partial' if row['rows'] else 'failed',message='搜索未完成，请检查账号与网络。当前结果不代表全部文件。')
    async def mutate(self,row,plan,p,ops):
        try:
            async with ops.lock:
                ops.guard(plan['_stamp']);item=plan['item'];kind=plan['kind'];target=plan['target'];title=plan['name']
                self.overlap(plan['provider'],plan['account'],[target.rstrip('/')+'/'+title]+([item.path] if item else []))
                for j in [*p.jobs,*p.upload_jobs]:
                    if j.state not in ('completed','cancelled') and item and (j.item.path==item.path or j.item.path.startswith(item.path+'/')):raise CloudOperationError('该项目仍有传输任务，请先完成或取消。')
                if kind=='mkdir':
                    if any(x.name.casefold()==title.casefold() for x in await ops.list(target)):raise CloudOperationError('目标已有同名项目。')
                    await ops.mkdir(target,title)
                elif kind=='copy':await ops.copy(item,target)
                else:await ops.relocate(item,target,title)
                ops.guard(plan['_stamp'])
            row.update(status='completed',message='操作完成')
            self.bridge.features.account_status.invalidate(plan['provider'])
            try:await p.load_directory(p.path,p.page_index)
            except Exception:row['message']='操作已完成，目录刷新失败，请手动刷新。'
        except asyncio.CancelledError:
            row.update(status='uncertain',message='操作已中断，请刷新目录核对结果。');raise
        except Exception as error:row.update(status='failed',message=str(error) if isinstance(error,CloudOperationError) else '结果尚未确认，请刷新源目录和目标目录核对，勿重复提交。')
    async def prepare_preview(self,row,p,item,account):
        folder=self.bridge.workspace.data_dir/'previews'/row['token'];job=None
        try:
            folder.mkdir(parents=True)
            if shutil.disk_usage(folder).free<item.size+64*1024*1024:raise CloudOperationError('预览空间不足。')
            api=p.api;resolver=api.new_download_resolver() if hasattr(api,'new_download_resolver') else api.download_source
            async def progress(done,total):row.update(count=done,message='正在载入预览…')
            job=p.make_download(item,folder,api.client,resolver,api.validate_download_url,progress,owner=row['_stamp'],current_owner=lambda:row['_stamp'] if self.valid(row) else None)
            path=await job.run()
            if not self.valid(row):raise CloudOperationError('账号已变化。')
            self.media[row['token']]={'path':path,'mime':row['mime'],'stamp':path.stat().st_mtime_ns}
            row.update(status='completed',message='预览已就绪')
        except asyncio.CancelledError:
            if job:await job.cancel()
            raise
        except Exception:row.update(status='failed',message='预览读取失败，请检查连接或下载后打开。')
        finally:
            if row['token'] not in self.media:
                if job:
                    await job.cancel()
                    if job.state=='completed' and job.target and job.target.is_file() and not job.target.is_symlink():job.target.unlink()
                try:folder.rmdir()
                except OSError:pass
    async def media_bytes(self,key,requested):
        row=self.jobs.get(key);media=self.media.get(key)
        if not row or not media or not self.valid(row):return None
        path=media['path'];st=path.stat()
        if path.is_symlink() or st.st_mtime_ns!=media['stamp'] or st.st_size!=row['total']:return None
        try:start,end,status=byte_range(requested,st.st_size)
        except ValueError:return (416,b'',media['mime'],{'Content-Range':f'bytes */{st.st_size}'})
        def read():
            with path.open('rb') as f:f.seek(start);return f.read(end-start+1)
        data=await asyncio.to_thread(read)
        if not self.valid(row):return None
        headers={'Accept-Ranges':'bytes'}
        if status==206:headers['Content-Range']=f'bytes {start}-{end}/{st.st_size}'
        return status,data,media['mime'],headers
    async def close(self):
        self.closed=True
        tasks=list(self.tasks.values())
        for key,task in self.tasks.items():
            if self.jobs.get(key,{}).get('kind')!='operation':task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for key in list(self.jobs):self.discard(key)
