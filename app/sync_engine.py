"""Account-bound file/folder synchronization with explicit conflict resolution."""
from __future__ import annotations
import asyncio,fnmatch,hashlib,json,os,time
from pathlib import Path,PurePosixPath
from uuid import uuid4
from dataclasses import asdict
from app.sync_model import *
from app.sync_transport import SyncTransport
from app.domain import CloudFile

class SyncEngine:
    def __init__(self,store,operations):
        self.store,self.operations=store,operations;self.closed=False;self.task=None
        self.cache={};self.observed={};self.active={};self.locks={};self.wake=asyncio.Event();self.last_local={}
    def rules(self):return self.store.all('sync_rule')
    def save_rule(self,rule):self.store.put('sync_rule',rule['id'],rule)
    def entries(self,kind,rid):return [x for x in self.store.all(kind) if x.get('rule')==rid]
    def baseline(self,rid):return {x['rel']:x for x in self.entries('sync_base',rid)}
    def lock(self,rid):return self.locks.setdefault(rid,asyncio.Lock())
    def log(self,rule,rel,message):
        key=uuid4().hex;self.store.put('sync_event',key,{'id':key,'rule':rule['id'],'rel':rel,'message':message,'at':time.time()})
        rows=sorted(self.entries('sync_event',rule['id']),key=lambda x:x['at'])
        for row in rows[:-150]:self.store.remove('sync_event',row['id'])
    def commit(self,rule,rel,local,remote,ignored=False):
        key=rule['id']+':'+rel
        value={'id':key,'rule':rule['id'],'rel':rel,'local':local,'remote':remote,'ignored':ignored}
        pending=self.store.get('sync_pending',key)
        history=None
        if pending and pending.get('kind')=='upload' and pending.get('old') and pending.get('archive'):
            archive=pending['archive']+'/'+PurePosixPath(pending['path']).name
            history={'id':hashlib.sha256(archive.encode()).hexdigest(),'rule':rule['id'],'rel':rel,'side':'cloud',
                     'archive':archive,'remote_id':pending['old']['id'],'at':pending.get('at',time.time())}
        # Baseline and journal completion are one SQLite transaction.
        with self.store.db:
            if history:self.store.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('sync_history',history['id'],json.dumps(history)))
            self.store.db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',('sync_base',key,json.dumps(value)))
            self.store.db.execute("DELETE FROM records WHERE id=? AND kind IN ('sync_pending','sync_issue')",(key,))
    def issue(self,rule,rel,kind,local,remote,message):
        key=rule['id']+':'+rel
        self.store.put('sync_issue',key,{'id':key,'rule':rule['id'],'rel':rel,'kind':kind,'local':local,'remote':remote,'message':message,'at':time.time()})
    async def root(self,rule,ops):
        if await ops.account()!=rule['account']:raise CloudOperationError('等待原账号登录，同步不会切换到其他账号。')
        if rule['remote_root']=='/':return 'root'
        parent=str(PurePosixPath(rule['remote_root']).parent)
        found=next((x for x in await ops.list(parent) if x.path==rule['remote_root'] and x.is_dir),None)
        if found is None:raise CloudOperationError('云端同步文件夹不可用，未把它当作删除操作。')
        if rule.get('remote_identity') and found.id!=rule['remote_identity']:raise CloudOperationError('云端同步文件夹身份变化，请重新建立任务。')
        return found.id
    async def remote_snapshot(self,rule,ops):
        await self.root(rule,ops);result={};seen={};queue=[(rule['remote_root'],'')];visited=set()
        while queue:
            path,prefix=queue.pop(0)
            if path in visited:raise CloudOperationError('云端目录结构异常。')
            visited.add(path)
            for item in await ops.list(path):
                if rule['kind']=='file':
                    if item.name!=rule['remote_name']:continue
                    rel=rule['local_name']
                else:rel=prefix+item.name
                if rule['kind']!='file' and ignored(rel,rule['exclude']):continue
                safe_local(rule['local_root'],rel)
                if rel.casefold() in seen:raise CloudOperationError('云端存在 Windows 无法区分的重名项目。')
                seen[rel.casefold()]=True;result[rel]=item
                if item.is_dir:
                    if rule['kind']=='file':raise CloudOperationError('单文件目标变成了文件夹，已停止同步。')
                    queue.append((item.path,rel+'/'))
                if len(result)>LIMIT:raise CloudOperationError('同步范围超过 50000 项，请拆分任务。')
            if rule['kind']=='file':break
        return result
    async def preview(self,local,provider,remote,kind,mode,exclude=None,remote_name=None):
        if exclude is None:exclude=[]
        if provider not in self.operations or kind not in ('file','folder') or mode not in ('upload','download','two_way'):raise CloudOperationError('同步选项无效。')
        if not isinstance(remote,str) or not remote.startswith('/') or '\\' in remote or any(x in ('.','..','') for x in remote.split('/')[1:]) and remote!='/':raise CloudOperationError('请选择云端目录。')
        if not isinstance(exclude,list) or len(exclude)>50 or any(not isinstance(x,str) or len(x)>200 for x in exclude):raise CloudOperationError('排除规则过多或无效。')
        path=Path(local)
        if not path.is_absolute() or not ordinary(path):raise CloudOperationError('请选择本地普通文件或文件夹。')
        root=path if kind=='folder' else path.parent
        local_id=await asyncio.to_thread(identity,root)
        if kind=='file' and not valid_name(path.name):raise CloudOperationError('文件名不适合用于同步。')
        if kind=='file' and remote_name is not None and not valid_name(remote_name):raise CloudOperationError('云端文件名无效。')
        if kind=='file' and not path.exists() and mode!='download':raise CloudOperationError('请选择已存在的本地文件。')
        ops=self.operations[provider];account=await ops.account()
        rule={'id':uuid4().hex,'provider':provider,'account':account,'kind':kind,'mode':mode,'local_root':str(root.resolve()),
              'local_name':path.name if kind=='file' else '', 'local_identity':local_id,'remote_root':remote,
              'remote_name':(remote_name or path.name) if kind=='file' else '', 'exclude':exclude,'enabled':True,'name':path.name,
              'status':'等待首次检查','last':0,'next':0,'failures':0,'count':0,'at':time.time()}
        self.check_overlap(rule)
        rule['remote_identity']=await self.root(rule,ops)
        local_rows=await asyncio.to_thread(local_snapshot,rule,None,True);remote_rows=await self.remote_snapshot(rule,ops)
        counts={'upload':0,'download':0,'compare':0,'conflict':0,'unmanaged':0}
        for rel in local_rows.keys()|remote_rows.keys():
            action=decide(local_rows.get(rel),remote_mark(remote_rows[rel]) if rel in remote_rows else None,None,mode)
            if action in counts:counts[action]+=1
        rule['preview']=counts;rule['count']=len(local_rows.keys()|remote_rows.keys());return rule
    def check_overlap(self,rule):
        kind,remote,account,provider=rule['kind'],rule['remote_root'],rule['account'],rule['provider']
        path=Path(rule['local_root'])/rule['local_name'] if kind=='file' else Path(rule['local_root'])
        for existing in self.rules()+self.store.all('backup'):
            existing_root=Path(existing.get('local_root',existing.get('local','')))
            existing_file=existing_root/existing.get('local_name','') if existing.get('kind')=='file' else existing_root
            overlap=path==existing_file or (kind=='folder' and path in existing_file.parents) or (existing.get('kind')!='file' and existing_file in path.parents)
            if overlap:raise CloudOperationError('本地范围与已有同步或备份任务重叠，请先解除旧任务。')
            if existing.get('account')==account and existing.get('provider')==provider:
                other=existing.get('remote_root',existing.get('target',''))
                here=remote.rstrip('/')+'/'+rule['remote_name'] if kind=='file' else remote
                if existing.get('kind')=='file':other=other.rstrip('/')+'/'+existing['remote_name']
                if other and (here==other or existing.get('kind')!='file' and here.startswith(other.rstrip('/')+'/') or kind=='folder' and other.startswith(here.rstrip('/')+'/')):raise CloudOperationError('云端范围与已有任务重叠。')
    def create(self,rule):
        if time.time()-rule['at']>300:raise CloudOperationError('同步预览已过期，请重新检查。')
        self.check_overlap(rule)
        self.save_rule(rule);self.wake.set()
    async def toggle(self,rid,enabled):
        if type(enabled) is not bool:raise CloudOperationError('开关值无效。')
        rule=self.store.get('sync_rule',rid)
        if not rule:raise CloudOperationError('同步任务不存在。')
        rule.update(enabled=enabled,next=0,status='等待检查' if enabled else '已暂停');self.save_rule(rule);self.wake.set()
    async def remove(self,rid):
        await self.toggle(rid,False)
        async with self.lock(rid):
            if self.entries('sync_pending',rid):raise CloudOperationError('有上次未完成的更新，请先核对结果再解除任务。')
            for kind in ('sync_base','sync_issue','sync_event','sync_resolution'):
                for row in self.entries(kind,rid):self.store.remove(kind,row['id'])
            self.store.remove('sync_rule',rid)
        self.cache.pop(rid,None);self.observed.pop(rid,None)
    def owner(self,rule,ops):
        stamp=ops.stamp()
        def current():
            saved=self.store.get('sync_rule',rule['id'])
            return stamp if not self.closed and saved and saved['enabled'] and ops.stamp()==stamp else None
        return stamp,current
    def progress(self,rule,rel,direction):
        async def changed(done,total):self.active[rule['id']]={'name':rel,'done':done,'total':total,'direction':direction}
        return changed
    def migrate(self,rule,old,new,remote,bases,remote_changed=False):
        for rel,base in list(bases.items()):
            if rel==old or rel.startswith(old+'/'):
                target=new+rel[len(old):]
                if target not in remote:raise CloudOperationError('改名后的目录尚未完整读取，请重试核对。')
                self.commit(rule,target,base['local'],base['remote'] if remote_changed else remote_mark(remote[target]),base.get('ignored',False))
                self.store.remove('sync_base',rule['id']+':'+rel);self.store.remove('sync_issue',rule['id']+':'+rel)
        self.store.remove('sync_pending',rule['id']+':'+old)
        self.log(rule,new,'已同步改名或移动，未重新传输文件内容')
    async def rename_cloud(self,rule,record,ops):
        transport=SyncTransport(ops,self.store);item=CloudFile(**record['item']);newpath=rule['remote_root'].rstrip('/')+'/'+record['new']
        parent=str(PurePosixPath(newpath).parent);await ops.ensure(parent)
        candidates=[item.path,item.parent.rstrip('/')+'/'+record['stage'],parent.rstrip('/')+'/'+record['stage'],newpath]
        found=None
        for candidate in dict.fromkeys(candidates):
            value=await transport.find(candidate)
            if value and value.id==item.id:found=value;break
        if found is None:raise CloudOperationError('改名结果待核对，未重复操作。')
        owner,current=self.owner(rule,ops)
        if current()!=owner:raise CloudOperationError('同步已暂停。')
        if found.path!=newpath:
            if found.name!=record['stage']:found=await ops.relocate(found,found.parent,record['stage'])
            if found.parent!=parent:found=await ops.relocate(found,parent,found.name)
            if current()!=owner:raise CloudOperationError('同步已暂停。')
            found=await ops.relocate(found,parent,PurePosixPath(newpath).name)
        remote=await self.remote_snapshot(rule,ops);self.migrate(rule,record['rel'],record['new'],remote,record['bases'])
    async def detect_rename(self,rule,local,remote,bases,ops):
        if rule['kind']=='file':return False
        for old,base in sorted(bases.items(),key=lambda pair:pair[0].count('/')):
            if not base.get('local') or not base.get('remote') or base.get('ignored'):continue
            relevant={p:b for p,b in bases.items() if p==old or p.startswith(old+'/')}
            if any(self.store.get('sync_issue',rule['id']+':'+p) for p in relevant):continue
            if old not in local and old in remote and rule['mode']!='download':
                before=base['local'];identity_key=before.get('identity') if before['dir'] else [before['fp']['device'],before['fp']['inode']]
                matches=[p for p,v in local.items() if p not in bases and v['dir']==before['dir'] and (v.get('identity') if v['dir'] else [v['fp']['device'],v['fp']['inode']])==identity_key]
                if len(matches)!=1 or matches[0] in remote:continue
                if any(p not in remote or not remote_equal(remote_mark(remote[p]),b['remote']) for p,b in relevant.items()):continue
                new=matches[0];record={'id':rule['id']+':'+old,'rule':rule['id'],'rel':old,'new':new,'kind':'rename_remote','item':asdict(remote[old]),'stage':'.clouddock-stage-move-'+uuid4().hex,'bases':relevant}
                self.store.put('sync_pending',record['id'],record);await self.rename_cloud(rule,record,ops);return True
            if old not in remote and old in local and rule['mode']!='upload':
                matches=[p for p,v in remote.items() if p not in bases and v.id==base['remote']['id']]
                if len(matches)!=1 or matches[0] in local:continue
                if any(p not in local or not local_equal(local[p],b['local']) for p,b in relevant.items()):continue
                new=matches[0];target=safe_local(rule['local_root'],new);source=safe_local(rule['local_root'],old)
                record={'id':rule['id']+':'+old,'rule':rule['id'],'rel':old,'new':new,'kind':'rename_local','bases':relevant}
                self.store.put('sync_pending',record['id'],record);target.parent.mkdir(parents=True,exist_ok=True);source.rename(target)
                self.migrate(rule,old,new,remote,relevant,True);return True
        return False
    async def apply(self,rule,rel,action,local,item,ops):
        transport=SyncTransport(ops,self.store);owner,current=self.owner(rule,ops)
        if current()!=owner:raise CloudOperationError('同步已暂停。')
        remote=remote_mark(item) if item else None
        target=safe_local(rule['local_root'],rel)
        if action=='upload':
            if local['dir']:
                path=rule['remote_root'].rstrip('/')+'/'+rel;await ops.ensure(path)
                item=await transport.find(path);self.commit(rule,rel,local,remote_mark(item));return
            item,source=await transport.upload(rule,rel,local,remote,owner,current,self.progress(rule,rel,'upload'))
            self.commit(rule,rel,source,remote_mark(item));self.log(rule,rel,'已上传更新；被替换版本保留在云端历史目录')
        elif action=='download':
            if item.is_dir:target.mkdir(parents=True,exist_ok=True)
            else:item=await transport.pull(rule,rel,item,local,owner,current,self.progress(rule,rel,'download'))
            fresh=await asyncio.to_thread(local_snapshot,rule,self.cache.get(rule['id']))
            self.commit(rule,rel,fresh.get(rel),remote_mark(item));self.log(rule,rel,'已下载更新；本地旧版本保留在历史目录')
        elif action=='compare':
            digest=await transport.compare(rule,item,owner,current,self.progress(rule,rel,'download'))
            now=await asyncio.to_thread(local_snapshot,rule,self.cache.get(rule['id']))
            await transport.guard_remote(item.path,remote)
            if local_equal(now.get(rel),local) and digest==local['hash']:self.commit(rule,rel,local,remote)
            else:self.issue(rule,rel,'conflict',now.get(rel),remote,'双方存在不同内容，等待选择保留哪一版；被替换版本会另行保存。')
    async def check(self,rid,force=False):
        async with self.lock(rid):
            rule=self.store.get('sync_rule',rid)
            if not rule or not rule['enabled'] or not force and time.time()<rule.get('next',0):return
            ops=self.operations[rule['provider']]
            try:
                rule['status']='正在检查两端变化';self.save_rule(rule)
                async with ops.lock:
                    await self.root(rule,ops)
                    local=await asyncio.to_thread(local_snapshot,rule,self.cache.get(rid));self.cache[rid]=local
                    remote=await self.remote_snapshot(rule,ops)
                    # Recover a journalled replacement before making a new plan.
                    for record in self.entries('sync_pending',rid):
                        rel=record['rel'];kind=record['kind']
                        if kind=='upload':await self.apply(rule,rel,'upload',record['local'],remote.get(rel),ops)
                        elif kind=='download':
                            expected=CloudFile(**record['remote']);present=remote.get(rel)
                            if record['phase'] in ('downloading','downloaded') and not remote_equal(remote_mark(present) if present else None,remote_mark(expected)):
                                self.store.remove('sync_pending',record['id']);self.log(rule,rel,'下载期间云端已变化，保留暂存内容并重新比较')
                            else:await self.apply(rule,rel,'download',record['old_local'],expected,ops)
                        elif kind in ('delete_local','delete_remote'):
                            if rel not in local and rel not in remote:self.commit(rule,rel,None,None)
                            else:self.store.remove('sync_pending',record['id'])
                        elif kind=='rename_remote':await self.rename_cloud(rule,record,ops)
                        elif kind=='rename_local':
                            before=record['bases'][rel]['local'];target=safe_local(rule['local_root'],record['new']);source=safe_local(rule['local_root'],rel)
                            if rel in local and record['new'] not in local and local_equal(local[rel],before):target.parent.mkdir(parents=True,exist_ok=True);source.rename(target)
                            elif rel in local or record['new'] not in local or not local_equal(local[record['new']],before):raise CloudOperationError('本地改名结果待核对。')
                            self.migrate(rule,rel,record['new'],remote,record['bases'],True)
                        else:raise CloudOperationError('上次改名或删除结果待核对，请查看任务详情。')
                        local=await asyncio.to_thread(local_snapshot,rule);remote=await self.remote_snapshot(rule,ops)
                    bases=self.baseline(rid);observed=self.observed.get(rid,{})
                    if await self.detect_rename(rule,local,remote,bases,ops):
                        local=await asyncio.to_thread(local_snapshot,rule);remote=await self.remote_snapshot(rule,ops);bases=self.baseline(rid)
                    now=time.monotonic();self.observed[rid]={rel:(v,observed[rel][1] if rel in observed and local_equal(observed[rel][0],v) else now) for rel,v in local.items()}
                    operations=0;waiting=0;unmanaged=0
                    for rel in sorted(local.keys()|remote.keys()|bases.keys(),key=lambda x:(x.count('/'),x.casefold())):
                        saved=self.store.get('sync_rule',rid)
                        if not saved or not saved['enabled']:break
                        left=local.get(rel);right=remote_mark(remote[rel]) if rel in remote else None
                        action=decide(left,right,bases.get(rel),rule['mode'])
                        if action=='none':
                            self.store.remove('sync_issue',rid+':'+rel);continue
                        if action=='baseline':self.commit(rule,rel,left,right);continue
                        if action=='unmanaged':unmanaged+=1;continue
                        if action in ('delete_local','delete_remote'):
                            self.issue(rule,rel,action,left,right,'检测到删除，确认后才同步到另一端；内容会移入恢复区。');continue
                        if action=='conflict':self.issue(rule,rel,'conflict',left,right,'两端变化冲突或目标端被修改，已保留双方内容。');continue
                        if left and not left['dir'] and now-self.observed[rid][rel][1]<5:waiting+=1;continue
                        issue=self.store.get('sync_issue',rid+':'+rel)
                        if issue and local_equal(issue.get('local'),left) and remote_equal(issue.get('remote'),right):continue
                        await self.apply(rule,rel,action,left,remote.get(rel),ops);operations+=1
                        if operations>=8:waiting+=1;break
                    issues=self.entries('sync_issue',rid)
                    if not issues and not waiting and not self.entries('sync_pending',rid):rule['last_success']=time.time()
                    rule['last_operations']=operations
                    rule.update(last=time.time(),next=time.time()+(5 if waiting else 60),failures=0,count=len(local.keys()|remote.keys()),
                                status=f'{len(issues)} 项需要处理' if issues else '等待文件写入稳定或下一批检查' if waiting else f'已检查 · {unmanaged} 项不属于此方向' if unmanaged else '已同步')
            except asyncio.CancelledError:raise
            except Exception as error:
                from app.upload import UploadError
                from app.direct_download import DownloadError
                rule['failures']=rule.get('failures',0)+1;rule['next']=time.time()+min(900,15*2**min(rule['failures'],6))
                rule['status']=str(error) if isinstance(error,(CloudOperationError,UploadError,DownloadError)) else '本轮检查未完成，请检查位置、账号或网络后重试。'
            finally:
                self.active.pop(rid,None);saved=self.store.get('sync_rule',rid)
                if saved:rule['enabled']=saved['enabled'];rule['status']=rule['status'] if saved['enabled'] else '已暂停';self.save_rule(rule)
    def request_resolution(self,rid,rel,choice):
        rule=self.store.get('sync_rule',rid);issue=self.store.get('sync_issue',rid+':'+rel)
        if not rule or not rule['enabled']:raise CloudOperationError('请先启用该同步任务。')
        if not issue or choice not in ('local','remote','ignore','delete'):raise CloudOperationError('此项已变化，请刷新任务。')
        key=rid+':'+rel
        if self.store.get('sync_resolution',key):raise CloudOperationError('这一项已排队，请等待处理结果。')
        self.store.put('sync_resolution',key,{'id':key,'rule':rid,'rel':rel,'choice':choice,'issue':issue});self.wake.set()
    async def resolve_queued(self,rid):
        for row in self.entries('sync_resolution',rid):
            rule=self.store.get('sync_rule',rid)
            if self.closed or not rule or not rule['enabled']:return
            try:await self.resolve(rid,row['rel'],row['choice'],row['issue'])
            except asyncio.CancelledError:raise
            except Exception as error:
                from app.upload import UploadError
                from app.direct_download import DownloadError
                message=str(error) if isinstance(error,(CloudOperationError,UploadError,DownloadError)) else '处理未完成，请重新检查文件与网络。'
                current=self.store.get('sync_issue',row['id'])
                if current:current['message']=message;self.store.put('sync_issue',row['id'],current)
                self.log(rule,row['rel'],message)
            self.store.remove('sync_resolution',row['id'])
    async def resolve(self,rid,rel,choice,expected=None):
        try:await self._resolve(rid,rel,choice,expected)
        finally:self.active.pop(rid,None)
    async def _resolve(self,rid,rel,choice,expected=None):
        async with self.lock(rid):
            rule=self.store.get('sync_rule',rid)
            if not rule or not rule['enabled']:raise CloudOperationError('请先启用该同步任务。')
            if choice not in ('local','remote','ignore','delete'):raise CloudOperationError('处理方式无效。')
            issue=self.store.get('sync_issue',rid+':'+rel)
            if not issue:raise CloudOperationError('此项已变化，请刷新任务。')
            if expected and (not local_equal(expected['local'],issue['local']) or not remote_equal(expected['remote'],issue['remote'])):raise CloudOperationError('排队期间文件已变化，请重新选择处理方式。')
            ops=self.operations[rule['provider']]
            async with ops.lock:
                local=await asyncio.to_thread(local_snapshot,rule);remote=await self.remote_snapshot(rule,ops)
                left=local.get(rel);item=remote.get(rel);right=remote_mark(item) if item else None
                if not local_equal(left,issue['local']) or not remote_equal(right,issue['remote']):raise CloudOperationError('文件又发生了变化，请重新检查后选择。')
                if choice=='ignore':self.commit(rule,rel,left,right,True)
                elif choice in ('local','remote'):
                    if choice=='local' and not left or choice=='remote' and not right:raise CloudOperationError('选中的一端已没有此项。')
                    if left and right and left['dir']!=right['dir']:raise CloudOperationError('文件与文件夹类型不同，请先改名保留双方内容。')
                    await self.apply(rule,rel,'upload' if choice=='local' else 'download',left,item,ops)
                else:
                    if issue['kind'] not in ('delete_local','delete_remote'):raise CloudOperationError('此项不是单端删除。')
                    if issue['kind']=='delete_remote':
                        if item.is_dir and await ops.list(item.path):raise CloudOperationError('请先处理文件夹内的项目；不会递归删除未确认内容。')
                        self.store.put('sync_pending',rid+':'+rel,{'id':rid+':'+rel,'rule':rid,'rel':rel,'kind':issue['kind'],'local':left,'remote':right})
                        await ops.delete([item])
                    else:
                        target=safe_local(rule['local_root'],rel)
                        if left['dir'] and any(target.iterdir()):raise CloudOperationError('本地文件夹仍有内容，未删除。')
                        archive=safe_local(rule['local_root'],'.clouddock-history/'+rid+'/'+uuid4().hex+'/'+rel)
                        self.store.put('sync_pending',rid+':'+rel,{'id':rid+':'+rel,'rule':rid,'rel':rel,'kind':issue['kind'],'local':left,'remote':right,'archive':str(archive)})
                        archive.parent.mkdir(parents=True,exist_ok=True);target.rename(archive)
                    self.commit(rule,rel,None,None);self.log(rule,rel,'已确认同步删除，内容已移入恢复区')
            rule['next']=0;self.save_rule(rule);self.wake.set()
    async def browse(self,provider,path):
        ops=self.operations.get(provider)
        if ops is None:raise CloudOperationError('请选择网盘。')
        await ops.account();rows=await ops.list(path)
        return [{'name':x.name,'path':x.path,'id':x.id,'dir':x.is_dir} for x in rows]
    def detail(self,rid):
        rule=self.store.get('sync_rule',rid)
        if not rule:raise CloudOperationError('任务不存在。')
        return {'rule':rule,'issues':self.entries('sync_issue',rid)[:100],'events':sorted(self.entries('sync_event',rid),key=lambda x:x['at'],reverse=True)[:100],
                'pending':len(self.entries('sync_pending',rid)),'queued':len(self.entries('sync_resolution',rid))}
    def start(self):self.task=asyncio.create_task(self.loop())
    async def loop(self):
        while not self.closed:
            for rule in self.rules():
                if self.closed:break
                await self.resolve_queued(rule['id'])
                if rule['enabled'] and rule['id'] in self.cache and not rule.get('failures'):
                    try:
                        snapshot=await asyncio.to_thread(local_snapshot,rule,self.cache[rule['id']])
                        if snapshot!=self.cache[rule['id']]:rule['next']=0;self.save_rule(rule)
                    except Exception:pass
                await self.check(rule['id'])
            self.wake.clear()
            try:await asyncio.wait_for(self.wake.wait(),5)
            except asyncio.TimeoutError:pass
    async def close(self):
        self.closed=True
        if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
