"""One-way versioned folder backup. Never deletes or overwrites cloud content."""
from __future__ import annotations
import asyncio,hashlib,os,stat,time
from pathlib import Path,PurePosixPath
from uuid import uuid4
from dataclasses import asdict
from app.upload import UploadJob,Fingerprint,UploadError
from app.providers.web_upload import BaiduUploader,QuarkUploader
from app.cloud_operations import CloudOperationError
IGNORED=('.part','.tmp','.temp','.crdownload','.download','.swp')
def root_identity(root):
    p=Path(root)
    if p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise CloudOperationError('请选择普通文件夹，暂不跟随链接或联接。')
    p=p.resolve(strict=True);s=p.stat()
    if not p.is_dir():raise CloudOperationError('本地文件夹不可用。')
    return str(p),[s.st_dev,s.st_ino]
def scan(root):
    base=Path(root);files={};skipped=0
    def fail(error):raise error
    for parent,dirs,names in os.walk(base,followlinks=False,onerror=fail):
        for name in list(dirs):
            p=Path(parent)/name
            if p.is_symlink() or getattr(p,'is_junction',lambda:False)() or name in ('.git','$RECYCLE.BIN','System Volume Information'):
                dirs.remove(name);skipped+=1
        for name in names:
            p=Path(parent)/name
            if name.startswith('~$') or name.lower().endswith(IGNORED) or p.is_symlink():skipped+=1;continue
            try:fp=asdict(Fingerprint.read(p))
            except OSError:skipped+=1;continue
            files[p.relative_to(base).as_posix()]=fp
            if len(files)>50000:raise CloudOperationError('单条备份规则最多 50000 个文件，请拆分文件夹。')
    return files,skipped
class FolderBackup:
    def __init__(self,store,operations):
        self.store,self.operations=store,operations
        self.running={};self.observed={};self.closed=False;self.active_job=None;self.loop_task=None
    def rules(self):return self.store.all('backup')
    async def preview(self,local,provider,parent):
        root,identity=await asyncio.to_thread(root_identity,local)
        for rule in self.rules():
            other=Path(rule['local'])
            if Path(root)==other or other in Path(root).parents or Path(root) in other.parents:raise CloudOperationError('该文件夹与已有备份规则重叠，请选择另一个文件夹。')
        for rule in self.store.all('sync_rule'):
            other=Path(rule['local_root'])/rule['local_name'] if rule['kind']=='file' else Path(rule['local_root'])
            if Path(root)==other or Path(root) in other.parents or rule['kind']=='folder' and other in Path(root).parents:raise CloudOperationError('该范围已有同步任务，请勿重复备份。')
        ops=self.operations[provider];account=await ops.account()
        await ops.list(parent)
        files,skipped=await asyncio.to_thread(scan,root)
        id=uuid4().hex;target=parent.rstrip('/')+'/云屿备份-'+Path(root).name[:50]+'-'+id[:8]
        return {'id':id,'provider':provider,'account':account,'local':root,'identity':identity,'target':target,
                'count':len(files),'bytes':sum(f['size'] for f in files.values()),'skipped':skipped,'enabled':True,
                'status':'等待首次扫描','last':0,'completed':0,'versions':0,'at':time.time()}
    def create(self,plan):
        if time.time()-plan['at']>300:raise CloudOperationError('备份预览已过期，请重新选择。')
        self.store.put('backup',plan['id'],plan)
    async def set_enabled(self,id,enabled):
        rule=self.store.get('backup',id)
        if not rule:raise CloudOperationError('备份规则不存在。')
        rule['enabled']=enabled;rule['retry_at']=0;rule['status']='等待扫描' if enabled else '已暂停';self.store.put('backup',id,rule)
        # A running upload checks this persisted flag before every stage/chunk.
        if not enabled and self.active_job and getattr(self.active_job,'backup_id',None)==id:
            await self.active_job.cancel()
    def suppress(self,provider,account,items):
        ids={f.id for f in items};paths=[f.path for f in items]
        for record in self.store.all('backup_file'):
            if record['provider']==provider and record['account']==account and (record.get('remote_id') in ids or any(record.get('remote_path','')==p or record.get('remote_path','').startswith(p+'/') for p in paths)):
                record['state']='excluded';record['error']='云端已删除，停止自动重传';self.store.put('backup_file',record['id'],record)
    async def run_once(self,rule):
        id=rule['id'];ops=self.operations.get(rule['provider'])
        if not rule['enabled'] or time.time()<rule.get('retry_at',0):return
        try:
            if ops is None or await ops.account()!=rule['account']:raise CloudOperationError('等待原账号连接，未切换备份目标。')
            root,identity=await asyncio.to_thread(root_identity,rule['local'])
            if identity!=rule['identity']:raise CloudOperationError('本地文件夹身份变化，请重新建立备份规则。')
            files,skipped=await asyncio.to_thread(scan,root)
            now=time.monotonic();previous=self.observed.get(id,{})
            observations={rel:(fp,previous[rel][1] if rel in previous and previous[rel][0]==fp else now) for rel,fp in files.items()}
            self.observed[id]=observations
            stamp=ops.stamp()
            def owner():
                current=self.store.get('backup',id)
                return stamp if not self.closed and current and current['enabled'] and ops.stamp()==stamp else None
            rows_cache={};done=0;versions=0;excluded=0
            for rel,(fp,since) in observations.items():
                if owner()!=stamp:raise CloudOperationError('备份已暂停，或账号连接发生变化。')
                key=id+':'+rel;old=self.store.get('backup_file',key)
                if old and old['state']=='excluded':excluded+=1;continue
                if old and old.get('fingerprint')==fp and old['state']=='completed':done+=1;versions+=old.get('versions',1);continue
                if now-since<5:continue
                # Cloud deletion or edits are explicit conflicts, not instructions to resurrect/overwrite.
                if old and old.get('remote_id'):
                    remote_parent=str(PurePosixPath(old['remote_path']).parent)
                    if remote_parent not in rows_cache:rows_cache[remote_parent]=await ops.list(remote_parent)
                    found=next((f for f in rows_cache[remote_parent] if f.id==old['remote_id']),None)
                    if found is None or found.revision!=old.get('remote_revision',found.revision):
                        old['state']='excluded';old['error']='云端文件删除或修改，已暂停此文件；重新建规则可另行备份。';self.store.put('backup_file',key,old);excluded+=1;continue
                local=Path(root)/Path(rel);directory=rule['target']+'/'+str(PurePosixPath(rel).parent) if str(PurePosixPath(rel).parent)!='.' else rule['target']
                await ops.ensure(directory)
                if old and old.get('state')=='pending':
                    # A process may have died after the remote commit. Do not issue another write blindly.
                    match=next((f for f in await ops.list(directory) if f.name==old['remote_name']),None)
                    if match:
                        if old.get('remote_id')==match.id and match.size==old['fingerprint']['size']:
                            old.update(state='completed',remote_revision=match.revision);self.store.put('backup_file',key,old);done+=1;continue
                        old.update(state='excluded',error='上次上传结果待人工核对，已保留云端文件。');self.store.put('backup_file',key,old);excluded+=1;continue
                if Fingerprint.read(local)!=Fingerprint(**fp):continue
                version=(old or {}).get('versions',0)+1
                name=local.name if version==1 else local.stem[:100]+'（版本 '+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6]+'）'+local.suffix
                record={'id':key,'rule':id,'provider':rule['provider'],'account':rule['account'],'fingerprint':fp,'state':'pending','remote_name':name,'remote_path':directory+'/'+name,'versions':version}
                self.store.put('backup_file',key,record)
                adapter=(BaiduUploader if rule['provider']=='baidu' else QuarkUploader)(ops.api)
                async def progress(done_bytes,total):
                    if record.get('remote_id')!=job.remote_id:
                        record['remote_id']=job.remote_id;self.store.put('backup_file',key,record)
                    self.running[id]={'name':rel,'done':done_bytes,'total':total,'status':job.state,'speed':job.speed}
                job=UploadJob(local,rule['provider'],directory,adapter,owner=stamp,current_owner=owner,progress=progress,remote_name=name)
                job.backup_id=id;self.active_job=job
                result=await job.run();self.active_job=None
                record.update(state='completed',remote_id=result.id,remote_revision=result.revision,hash=job.hashes['sha1'])
                self.store.put('backup_file',key,record);done+=1;versions+=version
            rule['retry_at']=0
            rule.update(status=('有 '+str(excluded)+' 项需处理' if excluded else '已检查，等待文件变化'),completed=done,versions=versions,last=time.time(),count=len(files),skipped=skipped)
        except asyncio.CancelledError:raise
        except (OSError,CloudOperationError,UploadError) as error:
            rule['status']=str(error) if not isinstance(error,OSError) else '本地目录不可用或无法读取，已暂停本轮备份。'
            rule['retry_at']=time.time()+60
        except Exception:rule['status']='本轮备份未完成，稍后自动重试。';rule['retry_at']=time.time()+60
        finally:
            self.running.pop(id,None);self.active_job=None
            current=self.store.get('backup',id)
            if current:
                rule['enabled']=current['enabled']
                if not rule['enabled']:rule['status']='已暂停'
                self.store.put('backup',id,rule)
    async def loop(self):
        while not self.closed:
            for rule in self.rules():
                if self.closed:break
                ops=self.operations.get(rule["provider"])
                if ops:
                    async with ops.lock:await self.run_once(rule)
            await asyncio.sleep(15)
    def start(self):self.loop_task=asyncio.create_task(self.loop())
    async def close(self):
        self.closed=True
        if self.loop_task:self.loop_task.cancel();await asyncio.gather(self.loop_task,return_exceptions=True)
