"""Account-bound navigation, transfer history, and bounded folder download plans."""
from __future__ import annotations
import asyncio
import hashlib
import secrets
import shutil
import time
from pathlib import Path, PurePosixPath
from app.cloud_operations import CloudOperationError
from app.direct_download import _safe_name


def local_parent(root, remote):
    parts = PurePosixPath(remote).parts
    if not remote.startswith('/') or '..' in parts or '\\' in remote:
        raise CloudOperationError('云端路径无效。')
    return Path(root).joinpath(*(_safe_name(x) for x in parts[1:]))


class Experience:
    def __init__(self, bridge):
        self.bridge = bridge
        self.plans = {}
        self.scans = {}

    def favorites(self):
        features = self.bridge.features
        result = []
        for row in features.store.all('favorite'):
            ops = features.ops.get(row['provider'])
            if ops and ops.stamp() and ops.identity_stamp == ops.stamp() and row['account'] == ops.identity:
                result.append({k:row[k] for k in ('id','provider','path','name')})
        return result

    def clear_history(self, ids):
        if not isinstance(ids, list) or len(ids)>500 or any(not isinstance(x,str) for x in ids):
            raise CloudOperationError('请先选择要清理的已完成记录。')
        requested = set(ids)
        for p in self.bridge.panels.values():
            for jobs in (p.jobs, p.upload_jobs):
                for j in list(jobs):
                    if getattr(j,'_desktop_id',None) in requested and j.state in ('completed','cancelled'):
                        jobs.remove(j)
        store = self.bridge.features.store
        for row in store.all('transfer'):
            if row['id'] in requested and row['status'] in ('completed','cancelled'):
                store.remove('transfer',row['id'])
        return {}

    async def action(self, d):
        action = d['action']
        if action == 'history_clear': return self.clear_history(d.get('ids'))
        name = d.get('provider')
        p = self.bridge.panels.get(name)
        features = self.bridge.features
        if p is None or not p.api.authenticated or p.reader is None:
            raise CloudOperationError('请先连接网盘账号。')
        ops = features.ops[name]
        if action == 'capacity_refresh':
            features.account_status.schedule(name, force=True)
            return {}
        if action in ('folder_status','folder_cancel','folder_confirm'):
            plan = self.plans.get(d.get('token'))
            if not plan or plan['provider'] != name or plan['stamp'] != ops.stamp() or time.monotonic()-plan['at']>600:
                raise CloudOperationError('下载清单已过期或账号变化，请重新选择。')
            if action == 'folder_cancel':
                task=self.scans.get(d['token'])
                if task:task.cancel()
                plan['state']='cancelled';return {}
            if action == 'folder_status':
                return {k:plan[k] for k in ('state','count','bytes','folders','error','destination','group')}
            if plan['state'] != 'ready':raise CloudOperationError('请等待扫描完成后确认。')
            # Freeze and consume before awaiting enqueue; a double click cannot enqueue twice.
            plan['state']='submitting'
            try:
                ops.guard(plan['stamp'])
                if p.generation != plan['generation'] or getattr(p,'share',None):
                    raise CloudOperationError('网盘会话已变化，请重新选择。')
                self.check_disk(Path(plan['destination']), plan['bytes'])
                self.safe_directory(Path(plan['destination']),Path(plan['destination']))
                for directory in plan['directories']:
                    self.safe_directory(Path(directory), Path(plan['destination']))
                count=0
                for item,destination in plan['items']:
                    ops.guard(plan['stamp'])
                    job=await p.start_download(item,destination=destination,group=plan['group'],notify=False)
                    if job is not None:count+=1
                p._downloads_changed();p._ensure_live_worker()
                plan['state']='submitted'
                features.capture(name,force=True)
                return {'count':count}
            except Exception:
                plan['state']='failed'
                raise
        if getattr(p,'share',None):raise CloudOperationError('请先返回本人网盘目录。')
        if d.get('context') != self.bridge.context(p) or p.browser_busy:
            raise CloudOperationError('目录已经变化，请刷新后重试。')
        account = await ops.account()
        features.accounts[name]=account
        stamp=ops.stamp();ops.guard(stamp)
        if d.get('context') != self.bridge.context(p):raise CloudOperationError('目录已经变化，请重试。')
        if action == 'favorite_toggle':
            path=p.path
            key=hashlib.sha256((account+':'+path).encode()).hexdigest()
            if features.store.get('favorite',key):features.store.remove('favorite',key)
            else:
                if len(features.store.all('favorite'))>=100:raise CloudOperationError('常用位置已达 100 项，请先移除不需要的位置。')
                features.store.put('favorite',key,{'id':key,'provider':name,'account':account,'path':path,
                    'name':PurePosixPath(path).name or ('百度网盘' if name=='baidu' else '夸克网盘')})
            return {}
        if action != 'folder_preview':raise CloudOperationError('未识别的操作。')
        ids=d.get('ids',[])
        if not isinstance(ids,list) or not ids or len(ids)>50:raise CloudOperationError('一次请选择 1–50 项。')
        items=[x for x in p.browser_rows if x.id in ids]
        if len(items)!=len(set(ids)):raise CloudOperationError('请选择当前目录中的项目。')
        for key,plan in list(self.plans.items()):
            if time.monotonic()-plan['at']>600:
                task=self.scans.pop(key,None)
                if task:task.cancel()
                self.plans.pop(key,None)
        if any(not t.done() for t in self.scans.values()):raise CloudOperationError('请先完成或取消上一次文件夹扫描。')
        while len(self.plans)>=8:
            old=next(iter(self.plans));self.plans.pop(old);self.scans.pop(old,None)
        token=secrets.token_hex(24)
        root=local_parent(p.download_destination,p.path)
        plan={'provider':name,'stamp':stamp,'generation':p.generation,'at':time.monotonic(),
              'state':'scanning','count':0,'bytes':0,'folders':0,'items':[],'directories':[],
              'error':'','destination':str(root),'group':items[0].name if len(items)==1 else f'{len(items)} 个项目'}
        self.plans[token]=plan
        self.scans[token]=asyncio.create_task(self.scan(ops,plan,items,p.path))
        return {'token':token}

    async def scan(self, ops, plan, items, parent):
        seen=set();destinations={};queue=asyncio.Queue()
        for item in items:queue.put_nowait((item,0))
        async def visit(item, depth):
            ops.guard(plan['stamp'])
            if depth>64 or len(seen)>=10000:raise CloudOperationError('单次最多扫描 10000 项、64 层；请选择更小的文件夹。')
            if item.id in seen:return
            seen.add(item.id)
            relative=PurePosixPath(item.path).relative_to(PurePosixPath(parent))
            if '..' in relative.parts:raise CloudOperationError('文件不在所选目录内。')
            target=Path(plan['destination']).joinpath(*(_safe_name(x) for x in relative.parts))
            key=str(target).casefold()
            if key in destinations and destinations[key]!=item.path:
                raise CloudOperationError('部分文件名在 Windows 上重名，请分别下载。')
            destinations[key]=item.path
            if item.is_dir:
                plan['folders']+=1;plan['directories'].append(str(target))
                for child in await ops.list(item.path,limit=10000-len(seen)):
                    if child.parent!=item.path:raise CloudOperationError('网盘返回了所选文件夹以外的项目，已停止扫描。')
                    queue.put_nowait((child,depth+1))
            else:
                if type(item.size) is not int or item.size<0:raise CloudOperationError('文件大小无法确认，请刷新。')
                plan['count']+=1;plan['bytes']+=item.size
                plan['items'].append((item,target.parent))
        async def worker():
            while True:
                item,depth=await queue.get()
                try:await visit(item,depth)
                finally:queue.task_done()
        workers=[asyncio.create_task(worker()) for _ in range(4)]
        joined=asyncio.create_task(queue.join())
        try:
            async with asyncio.timeout(600):
                done,_=await asyncio.wait([joined,*workers],return_when=asyncio.FIRST_COMPLETED)
                for task in done:task.result()
                ops.guard(plan['stamp']);plan['state']='ready'
        except asyncio.CancelledError:
            plan['state']='cancelled'
        except Exception as error:
            from app.operation_diagnostics import record
            record('folder_scan',error)
            plan['state']='failed'
            plan['error']=str(error) if isinstance(error,CloudOperationError) else ('扫描超过 10 分钟，请分批选择较小的文件夹；尚未创建下载任务。' if isinstance(error,TimeoutError) else '读取子目录失败，请检查网络后重试；尚未创建下载任务。')
            plan['items']=[];plan['directories']=[]
        finally:
            for task in [joined,*workers]:task.cancel()
            await asyncio.gather(joined,*workers,return_exceptions=True)

    @staticmethod
    def check_disk(destination, required):
        ancestor=destination
        while not ancestor.exists():
            if ancestor.parent==ancestor:raise CloudOperationError('下载磁盘不可用。')
            ancestor=ancestor.parent
        if shutil.disk_usage(ancestor).free < required+16*1024*1024:
            raise CloudOperationError('本地磁盘空间不足，请更改下载位置。')

    @staticmethod
    def safe_directory(directory, root):
        root=Path(root).absolute();directory=Path(directory).absolute()
        directory.relative_to(root)
        # Refuse existing junctions/symlinks along the entire destination chain.
        for part in [directory,*directory.parents]:
            if part.is_symlink() or getattr(part,'is_junction',lambda:False)():
                raise CloudOperationError('下载位置包含目录链接，请选择普通文件夹。')
        directory.mkdir(parents=True,exist_ok=True)

    async def close(self):
        for task in self.scans.values():task.cancel()
        await asyncio.gather(*self.scans.values(),return_exceptions=True)
