"""Explicit history cleanup. Paused rules only; no pending publication may exist."""
from __future__ import annotations
import asyncio,datetime,os,re,secrets,time
from pathlib import Path,PurePosixPath
from collections import defaultdict
from dataclasses import asdict
from app.cloud_operations import CloudOperationError
from app.sync_model import safe_local,identity,ordinary,remote_mark,remote_equal,content_hash
from app.domain import CloudFile

class HistoryManager:
    def __init__(self,engine):self.engine=engine;self.previews={}
    def rule(self,rid):
        r=self.engine.store.get('sync_rule',rid)
        if not r:raise CloudOperationError('同步任务不存在。')
        if not re.fullmatch('[a-f0-9]{32}',rid):raise CloudOperationError('任务标识无效。')
        if r['enabled']:raise CloudOperationError('请先暂停此同步任务，再管理历史版本。')
        if self.engine.entries('sync_pending',rid):raise CloudOperationError('尚有更新待核对，历史版本暂时受保护。')
        return r
    def local_rows(self,r):
        if identity(r['local_root'])!=r['local_identity']:raise CloudOperationError('本地同步位置已变化。')
        base=safe_local(r['local_root'],'.clouddock-history/'+r['id']);rows=[]
        if not base.exists():return rows
        for version in base.iterdir():
            if not ordinary(version) or not re.fullmatch('[a-f0-9]{32}',version.name) or not version.is_dir():raise CloudOperationError('历史目录结构有变化，请手动检查。')
            st=version.stat();created=getattr(st,'st_birthtime',st.st_ctime)
            for directory,dirs,files in os.walk(version,followlinks=False):
                for name in [*dirs,*files]:safe_local(r['local_root'],(Path(directory)/name).relative_to(r['local_root']).as_posix())
                for name in files:
                    p=Path(directory)/name;st=p.stat();rel=p.relative_to(version).as_posix()
                    rows.append({'rel':rel,'size':st.st_size,'at':created,'path':str(p),'stamp':[st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns]})
                    if len(rows)>5000:raise CloudOperationError('历史记录超过 5000 项，请分批手动整理。')
        return rows
    async def cloud_rows(self,r,ops):
        await self.engine.root(r,ops)
        base=r['remote_root'].rstrip('/')+'/.clouddock-history/'+r['id'];rows=[]
        parent=str(PurePosixPath(base).parent)
        # Missing history root is normal; never create cloud directories just to inspect.
        top=next((x for x in await ops.list(r['remote_root']) if x.name=='.clouddock-history' and x.is_dir),None)
        if not top:return rows
        if not any(x.path==base and x.is_dir for x in await ops.list(parent)):return rows
        index={x['archive']:x for x in self.engine.entries('sync_history',r['id']) if x.get('side')=='cloud'}
        for version in await ops.list(base):
            if not version.is_dir or not re.fullmatch('[a-f0-9]{32}',version.name):raise CloudOperationError('云端历史目录结构有变化，请手动检查。')
            try:created=datetime.datetime.fromisoformat(version.modified).timestamp()
            except (ValueError,TypeError):created=0
            queue=[(version.path,0)]
            while queue:
                folder,depth=queue.pop()
                if depth>25:raise CloudOperationError('历史目录层级过深。')
                for item in await ops.list(folder):
                    if not item.path.startswith(version.path+'/'):raise CloudOperationError('历史路径无效。')
                    if item.is_dir:queue.append((item.path,depth+1))
                    else:
                        known=index.get(item.path)
                        if known and known.get('remote_id')!=item.id:known=None
                        rows.append({'rel':known['rel'] if known else item.path[len(version.path)+1:],'size':item.size,
                                     'at':known['at'] if known else 0,'protected':known is None,'history_id':known['id'] if known else None,'item':asdict(item)})
                    if len(rows)+len(queue)>5000:raise CloudOperationError('历史记录超过 5000 项，请分批整理。')
        return rows
    async def preview(self,rid,side,keep,days):
        if side not in ('local','cloud') or type(keep) is not int or not 1<=keep<=100 or type(days) is not int or not 0<=days<=3650:raise CloudOperationError('保留规则无效。')
        async with self.engine.lock(rid):
            r=self.rule(rid);ops=self.engine.operations[r['provider']]
            if side=='local':rows=await asyncio.to_thread(self.local_rows,r);stamp=None
            else:
                async with ops.lock:rows=await self.cloud_rows(r,ops);stamp=ops.stamp()
            grouped=defaultdict(list)
            for row in rows:
                if not row.get('protected'):grouped[row['rel'].casefold()].append(row)
            candidates=[]
            for versions in grouped.values():
                ordered=sorted(versions,key=lambda x:x['at'],reverse=True)
                # Unknown creation times are protected, never assumed to be oldest.
                candidates.extend(x for x in ordered[keep:] if x['at']>0 and x['at']<time.time()-days*86400)
            candidates.sort(key=lambda x:x['at']);chosen=candidates[:20]
            for row in chosen:
                if side=='local':row['hash']=await asyncio.to_thread(content_hash,Path(row['path']))
            self.rule(rid)
            token=secrets.token_hex(24)
            self.previews={k:v for k,v in self.previews.items() if time.monotonic()-v['at']<300}
            self.previews[token]={'rule':rid,'side':side,'rows':chosen,'at':time.monotonic(),'stamp':stamp,'local_identity':r['local_identity']}
            return {'confirmation':token,'count':len(rows),'bytes':sum(x['size'] for x in rows),'eligible':len(candidates),'selected':len(chosen),
                    'reclaim':sum(x['size'] for x in chosen),'rows':[{'name':x['rel'],'size':x['size'],'at':x['at']} for x in chosen],
                    'unknown_dates':sum(not x['at'] for x in rows),'side':side}
    async def clean(self,token):
        plan=self.previews.pop(token,None)
        if not plan or time.monotonic()-plan['at']>300:raise CloudOperationError('清理预览已过期，请重新预览。')
        rid=plan['rule'];removed=0
        async with self.engine.lock(rid):
            r=self.rule(rid);ops=self.engine.operations[r['provider']]
            if plan['side']=='cloud':
                async with ops.lock:
                    await self.engine.root(r,ops)
                    if ops.stamp()!=plan['stamp']:raise CloudOperationError('账号已变化，请重新预览。')
                    for row in plan['rows']:
                        self.rule(rid)
                        item=CloudFile(**row['item'])
                        now=next((x for x in await ops.list(item.parent) if x.id==item.id and x.path==item.path),None)
                        if not now or not remote_equal(remote_mark(now),remote_mark(item)):raise CloudOperationError('历史文件已变化，已停止；请重新预览核对。')
                        await ops.delete([now]);removed+=1
                        if row.get('history_id'):self.engine.store.remove('sync_history',row['history_id'])
            else:
                def remove(row):
                    if identity(r['local_root'])!=plan['local_identity']:raise CloudOperationError('本地位置已变化。')
                    p=Path(row['path']);rel=p.relative_to(r['local_root']).as_posix();safe_local(r['local_root'],rel)
                    if not p.is_relative_to(Path(r['local_root'])/'.clouddock-history'/rid):raise CloudOperationError('历史路径越界。')
                    s=p.stat()
                    if [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns]!=row['stamp'] or content_hash(p)!=row['hash']:raise CloudOperationError('历史内容已变化，请重新预览。')
                    p.unlink()
                for row in plan['rows']:
                    self.rule(rid)
                    try:await asyncio.to_thread(remove,row)
                    except OSError:raise CloudOperationError('历史文件不可用，已停止；请重新预览核对。') from None
                    removed+=1
            self.engine.log(r,'','已按确认清理 '+str(removed)+' 个'+('本地' if plan['side']=='local' else '云端')+'历史文件')
        return {'removed':removed}
