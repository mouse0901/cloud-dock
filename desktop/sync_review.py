"""Read-only three-way previews and no-clobber recovery of historical versions."""
import asyncio,os,secrets,shutil,time
from pathlib import Path
from app.cloud_operations import CloudOperationError
from app.sync_model import local_snapshot,remote_mark,decide,identity,safe_local,content_hash
from app.sync_transport import SyncTransport

TEMPLATES={'unreal':['DerivedDataCache','Intermediate','Saved','Binaries'],
           'development':['node_modules','__pycache__','.venv','dist','build'],
           'media':['Cache','Media Cache','Preview Files','*.pek','*.cfa']}

class SyncReview:
    def __init__(self,features):self.f=features;self.previews={}
    def rule(self,rid):
        r=self.f.store.get('sync_rule',rid)
        if not r:raise CloudOperationError('同步任务不存在。')
        return r
    async def changes(self,rid):
        engine=self.f.sync
        async with engine.lock(rid):
            r=self.rule(rid);ops=self.f.ops[r['provider']]
            async with ops.lock:
                stamp=ops.stamp();await engine.root(r,ops)
                local=await asyncio.to_thread(local_snapshot,r)
                remote=await engine.remote_snapshot(r,ops);ops.guard(stamp)
            bases=engine.baseline(rid);rows=[];counts={}
            for rel in sorted(local.keys()|remote.keys()|bases.keys(),key=str.casefold):
                left=local.get(rel);item=remote.get(rel);right=remote_mark(item) if item else None
                action=decide(left,right,bases.get(rel),r['mode'])
                counts[action]=counts.get(action,0)+1
                if action not in ('none','baseline'):
                    rows.append({'rel':rel,'action':action,'local':left,'remote':right,
                                 'remote_modified':item.modified if item else None,'baseline':bases.get(rel)})
            return {'rows':rows,'counts':counts,'at':time.time(),'enabled':r['enabled'],
                    'pending':len(engine.entries('sync_pending',rid))}
    async def versions(self,rid,side):
        if side not in ('local','cloud'):raise CloudOperationError('历史位置无效。')
        h=self.f.history;e=self.f.sync
        async with e.lock(rid):
            r=h.rule(rid);ops=self.f.ops[r['provider']];stamp=ops.stamp()
            if side=='cloud':await e.root(r,ops)
            if side=='local':rows=await asyncio.to_thread(h.local_rows,r)
            else:
                async with ops.lock:rows=await h.cloud_rows(r,ops)
            if side=='cloud':ops.guard(stamp)
            # Each opaque selection is bound to the exact version and original account.
            self.previews={k:v for k,v in self.previews.items() if time.monotonic()-v['time']<600 and (v['rule'],v['side'])!=(rid,side)}
            result=[]
            for row in sorted(rows,key=lambda x:x.get('at',0),reverse=True):
                token=secrets.token_hex(24)
                self.previews[token]={'rule':rid,'side':side,'row':row,'stamp':stamp,'time':time.monotonic()}
                result.append({'token':token,'rel':row['rel'],'size':row['size'],'at':row.get('at',0)})
            return {'rows':result,'bytes':sum(x['size'] for x in rows)}
    async def restore(self,token):
        p=self.previews.get(token)
        if not p:raise CloudOperationError('版本清单已过期，请重新读取。')
        rid=p['rule']
        if rid in self.f.sync.restoring:raise CloudOperationError('此任务正在恢复另一个历史版本。')
        self.f.sync.restoring.add(rid)
        try:return await self._restore(token)
        finally:self.f.sync.restoring.discard(rid)
    async def _restore(self,token):
        p=self.previews.pop(token,None)
        if not p or time.monotonic()-p['time']>600:raise CloudOperationError('版本清单已过期，请重新读取。')
        h=self.f.history;e=self.f.sync
        async with e.lock(p['rule']):
            r=h.rule(p['rule']);ops=self.f.ops[r['provider']]
            if p['side']=='cloud':ops.guard(p['stamp']);await e.root(r,ops)
            if identity(r['local_root'])!=r['local_identity']:raise CloudOperationError('本地磁盘或位置已改变。')
            row=p['row'];base=safe_local(r['local_root'],row['rel']);base.parent.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(base.parent).free<row['size']+16*1024*1024:raise CloudOperationError('本地剩余空间不足，无法恢复副本。')
            target=base.with_name(base.stem+'（恢复-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)+'）'+base.suffix)
            safe_local(r['local_root'],target.relative_to(r['local_root']).as_posix())
            def current():
                saved=self.f.store.get('sync_rule',r['id'])
                return p['stamp'] if saved and not saved['enabled'] and ops.stamp()==p['stamp'] else None
            cached=None
            if p['side']=='local':
                source=Path(row['path']);fresh=await asyncio.to_thread(h.local_rows,r)
                found=next((x for x in fresh if x['path']==row['path'] and x['stamp']==row['stamp']),None)
                if not found:raise CloudOperationError('该历史版本已变化，请重新读取。')
                source_hash=await asyncio.to_thread(content_hash,source)
            else:
                async with ops.lock:
                    fresh=await h.cloud_rows(r,ops)
                    found=next((x for x in fresh if x.get('item')==row.get('item')),None)
                    if not found:raise CloudOperationError('该云端历史版本已变化。')
                    from app.domain import CloudFile
                    folder=safe_local(r['local_root'],'.clouddock-stage/'+r['id']+'/'+secrets.token_hex(16));folder.mkdir(parents=True)
                    async def progress(done,total):pass
                    cached=await SyncTransport(ops,self.f.store).download(CloudFile(**found['item']),folder,p['stamp'],current,progress)
                    source=Path(cached);source_hash=await asyncio.to_thread(content_hash,source)
            def publish():
                if identity(r['local_root'])!=r['local_identity']:raise CloudOperationError('恢复条件已变化，请保持任务暂停。')
                safe_local(r['local_root'],target.relative_to(r['local_root']).as_posix())
                created=False
                try:
                    with source.open('rb') as src,target.open('xb') as out:
                        created=True;shutil.copyfileobj(src,out,1024*1024)
                    if content_hash(target)!=source_hash:raise CloudOperationError('恢复副本校验失败。')
                except BaseException:
                    # Only remove the exclusive output created by this operation.
                    if created and target.exists():target.unlink()
                    raise
            if current()!=p['stamp']:raise CloudOperationError('恢复条件已变化，请保持任务暂停。')
            await asyncio.to_thread(publish)
            if cached:Path(cached).unlink(missing_ok=True)
            e.log(r,row['rel'],'已恢复为本地副本：'+target.name)
            return {'name':target.name,'path':str(target)}
    async def action(self,d):
        a=d['action']
        if a=='sync_changes':return await self.changes(d.get('id'))
        if a=='sync_versions':return await self.versions(d.get('id'),d.get('side','local'))
        if a=='sync_restore_version':return await self.restore(d.get('token'))
        if a=='sync_templates':return {'templates':TEMPLATES}
        raise CloudOperationError('同步查看操作无效。')
