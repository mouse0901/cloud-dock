"""Journalled, account-bound cross-drive copies. No source mutations or overwrites."""
import asyncio,secrets,time,shutil
from pathlib import Path,PurePosixPath
from dataclasses import asdict
from app.cloud_operations import CloudOperationError
from app.domain import CloudFile
from app.batch_names import choose_name,policy
from app.sync_model import safe_local,content_hash,remote_mark,remote_equal
from app.sync_transport import SyncTransport
from app.upload import UploadJob,UploadError
from app.direct_download import DownloadError
from app.providers.web_upload import BaiduUploader,QuarkUploader
from desktop.productivity import cloud_path

class CrossCopy:
    def __init__(self,bridge):self.b=bridge;self.jobs={};self.loaded=False;self.tasks={};self.stamps={};self.closed=False
    @property
    def store(self):return self.b.features.store
    def load(self):
        if self.loaded or not self.b.features:return
        self.loaded=True
        for row in self.store.all('cross_copy'):
            if row['state'] in ('running','scanning'):row.update(state='paused',message='上次运行已中断，继续前会核对两端。')
            self.jobs[row['id']]=row
    def save(self,row):
        for record in row['items']:
            for field in ('item','staged','result'):
                if record.get(field):record[field].pop('thumbnail',None)
        self.store.put('cross_copy',row['id'],row)
    def public(self,row,details=True):
        counts={}
        for x in row['items']:counts[x['state']]=counts.get(x['state'],0)+1
        return {k:row[k] for k in ('id','source','target','path','state','message','at','bytes')}|{'counts':counts,'count':len(row['items']),
               'rows':[{'name':x['item']['path'],'target':x['path'],'state':x['state'],'error':x.get('error','')} for x in row['items']] if details else [],
               'progress':row.get('progress',{}),'cache_bytes':sum(x['item']['size'] for x in row['items'] if x.get('cached'))}
    def summaries(self):
        self.load()
        if not self.loaded:return []
        accounts=self.b.features.accounts
        return [self.public(r,False) for r in sorted(self.jobs.values(),key=lambda r:r['at'],reverse=True)
                if accounts.get(r['source'])==r['source_account'] and accounts.get(r['target'])==r['target_account']]
    async def bind(self,row):
        for side in ('source','target'):
            ops=self.b.features.ops[row[side]]
            if await ops.account()!=row[side+'_account']:raise CloudOperationError('请连接创建任务时的两个原账号。')
        self.stamps[row['id']]={p:self.b.features.ops[p].stamp() for p in (row['source'],row['target'])}
    def guard(self,row):
        if self.closed or row['state'] not in ('scanning','running','ready'):raise CloudOperationError('跨盘复制已暂停。')
        for p,stamp in self.stamps.get(row['id'],{}).items():self.b.features.ops[p].guard(stamp)
        if row['id'] not in self.stamps:raise CloudOperationError('请重新确认账号。')
    async def target_root(self,row):
        ops=self.b.features.ops[row['target']]
        if row['path']=='/':return
        item=next((x for x in await ops.list(str(PurePosixPath(row['path']).parent)) if x.path==row['path'] and x.is_dir),None)
        if not item or item.id!=row['target_root_id']:raise CloudOperationError('目标文件夹已移动或被替换，请重新创建复制任务。')
    async def action(self,d):
        self.load();a=d['action']
        if a=='cross_prepare':
            if len(self.jobs)>=100:raise CloudOperationError('请先清理已结束的跨盘记录。')
            src,p,ops,account,stamp=await self.b.productivity.owner(d)
            target=d.get('target_provider');path=cloud_path(d.get('target','/'));mode=policy(d.get('policy','skip'))
            if target not in self.b.features.ops or target==src:raise CloudOperationError('请选择另一个网盘。')
            dst=self.b.features.ops[target];target_account=await dst.account();ids=d.get('ids')
            if d.get('context')!=self.b.context(p) or not isinstance(ids,list) or not 1<=len(ids)<=50:raise CloudOperationError('所选文件已变化，请重新选择。')
            if p.browser_busy:raise CloudOperationError('请等待目录加载完成。')
            if any(not t.done() for t in self.tasks.values()):raise CloudOperationError('请先完成或暂停正在进行的跨盘任务。')
            items=[x for x in p.browser_rows if x.id in ids]
            if len(items)!=len(set(ids)):raise CloudOperationError('所选文件已变化。')
            parent=None
            if path!='/':parent=next((x for x in await dst.list(str(PurePosixPath(path).parent)) if x.path==path and x.is_dir),None)
            if path!='/' and not parent:raise CloudOperationError('目标文件夹不存在。')
            row={'id':secrets.token_hex(16),'source':src,'source_account':account,'target':target,'target_account':target_account,
                 'target_root_id':parent.id if parent else 'root','path':path,'policy':mode,'state':'scanning','message':'正在读取复制清单…','items':[],
                 'bytes':0,'at':time.time()}
            self.jobs[row['id']]=row;await self.bind(row);self.save(row)
            self.tasks[row['id']]=asyncio.create_task(self.scan(row,items));return self.public(row)
        row=self.jobs.get(d.get('id'))
        if not row:raise CloudOperationError('跨盘复制记录不存在。')
        if a=='cross_status':
            for side in ('source','target'):
                if await self.b.features.ops[row[side]].account()!=row[side+'_account']:raise CloudOperationError('请使用原账号查看复制记录。')
            return self.public(row)
        if a=='cross_pause':
            row.update(state='paused',message='已暂停；继续时核对账号和提交结果。');self.save(row)
            task=self.tasks.get(row['id'])
            if task:task.cancel();await asyncio.gather(task,return_exceptions=True)
            return self.public(row)
        if a=='cross_remove':
            if row['state'] in ('running','scanning'):raise CloudOperationError('请先暂停复制。')
            # Only owned cached files, never remote stages or source files.
            for item in row['items']:
                if item.get('checkpoint'):
                    from app.direct_download import DirectDownload
                    from app.parallel_download import ParallelDownload
                    from app.download_checkpoint import restore
                    klass=ParallelDownload if item['checkpoint'].get('segments') else DirectDownload
                    job=klass(CloudFile(**item['item']),self.cache(row,item),None,None,None,None)
                    try:restore(job,item['checkpoint']);await job.cancel()
                    except (DownloadError,OSError):pass  # Changed local files are never removed.
                if item.get('cached'):
                    path=self.cache(row,item)/item['cached']
                    if path.is_file() and not path.is_symlink() and await asyncio.to_thread(content_hash,path)==item.get('hash'):path.unlink()
            self.store.remove('cross_copy',row['id']);self.jobs.pop(row['id']);return {}
        if a=='cross_start':
            if any(not t.done() for t in self.tasks.values()):raise CloudOperationError('请先暂停正在进行的跨盘任务。')
            if row['id'] in self.tasks and not self.tasks[row['id']].done():raise CloudOperationError('该任务仍在处理。')
            if not row.get('scanned'):raise CloudOperationError('扫描尚未完整，请重新创建任务。')
            if row['state']=='ready' and time.time()-row['at']>600:raise CloudOperationError('预览已过期，请重新扫描。')
            await self.bind(row);row.update(state='running',message='正在复制…');self.save(row)
            self.tasks[row['id']]=asyncio.create_task(self.run(row));return self.public(row)
        raise CloudOperationError('跨盘复制操作无效。')
    def cache(self,row,item):
        root=self.b.workspace.data_dir/'cross-copy';root.mkdir(parents=True,exist_ok=True)
        return safe_local(root,row['id']+'/'+item['key'])
    async def scan(self,row,items):
        try:
            ops=self.b.features.ops[row['source']];dst=self.b.features.ops[row['target']]
            occupied={x.name.casefold() for x in await dst.list(row['path'],limit=5000)};queue=[];seen=set();start=time.monotonic()
            for item in items:
                name=choose_name(item.name,occupied,row['policy'],item.is_dir)
                queue.append((item,row['path'].rstrip('/')+'/'+(name or item.name),name is None,0))
            while queue:
                self.guard(row);item,path,skip,depth=queue.pop(0)
                cloud_path(item.path);cloud_path(path)
                if item.path in seen or depth>32 or time.monotonic()-start>120:raise CloudOperationError('目录层级过深、重复或扫描超时，请缩小范围。')
                seen.add(item.path)
                if len(row['items'])>=2000:raise CloudOperationError('一次跨盘复制最多 2000 项，请分批选择。')
                row['items'].append({'key':secrets.token_hex(16),'item':asdict(item),'path':path,'state':'skipped' if skip else 'pending'})
                if not skip:
                    if item.is_dir:
                        rows=await ops.list(item.path,limit=2000);names=set()
                        for child in rows:
                            if child.parent!=item.path:raise CloudOperationError('来源目录包含无效路径，已停止扫描。')
                            if child.name.casefold() in names:raise CloudOperationError('文件夹内有大小写重名，请先处理。')
                            names.add(child.name.casefold());queue.append((child,path+'/'+child.name,False,depth+1))
                    else:row['bytes']+=item.size
            self.guard(row);await self.target_root(row)
            row.update(state='ready',scanned=True,message='请确认复制清单。源文件保留；数据先下载到本机，再上传到目标网盘。')
        except asyncio.CancelledError:return
        except Exception as error:row.update(state='failed',message=self.error(error))
        finally:self.save(row)
    def error(self,e):return str(e) if isinstance(e,(CloudOperationError,UploadError,DownloadError)) else '复制未完成，请检查账号、网络和本地空间后重试。'
    async def run(self,row):
        try:
            src=self.b.features.ops[row['source']];dst=self.b.features.ops[row['target']]
            await self.target_root(row)
            for record in row['items']:
                self.guard(row)
                if record['state'] in ('completed','skipped'):continue
                try:
                    await self.one(row,record,src,dst);record.update(state='completed',error='');self.save(row)
                except asyncio.CancelledError:raise
                except Exception as error:
                    record.update(state='failed',error=self.error(error));self.save(row)
                    # Stop at the first failure so child paths cannot escape a failed parent.
                    raise
            row.update(state='completed',message='跨盘复制完成；源文件已保留。',progress={})
        except asyncio.CancelledError:
            row.update(state='paused',message='已暂停；已完成项目保留。')
        except Exception as error:row.update(state='failed',message=self.error(error))
        finally:self.save(row)
    async def one(self,row,record,src,dst):
        item=CloudFile(**record['item']);parent=str(PurePosixPath(record['path']).parent);name=PurePosixPath(record['path']).name
        self.guard(row)
        await self.target_root(row)
        # Revalidate each created ancestor; never upload into a folder replaced externally.
        for parent_record in row['items']:
            if parent_record['item']['is_dir'] and record['path'].startswith(parent_record['path']+'/'):
                ppath=parent_record['path'];fresh=next((x for x in await dst.list(str(PurePosixPath(ppath).parent)) if x.path==ppath and x.is_dir),None)
                if not fresh or fresh.id!=parent_record.get('created_id'):raise CloudOperationError('目标子文件夹已变化，请核对后重新创建任务。')
        # Every retry checks source revision and target identity again.
        found=next((x for x in await src.list(item.parent) if x.id==item.id and x.path==item.path),None)
        if not found or not remote_equal(remote_mark(found),remote_mark(item)):raise CloudOperationError('源文件已变化，请重新创建复制任务。')
        if item.is_dir:
            async with dst.lock:
                present=next((x for x in await dst.list(parent) if x.name.casefold()==name.casefold()),None)
                if present:
                    if not record.get('created_id') or present.id!=record['created_id']:raise CloudOperationError('目标目录已存在或上次创建结果待核对，未合并内容。')
                else:
                    created=await dst.mkdir(parent,name,exclusive=True);record['created_id']=created.id;self.save(row)
            return
        stage=record.setdefault('stage_name','.clouddock-copy-'+record['key']+Path(name).suffix);self.save(row)
        async with dst.lock:
            rows=await dst.list(parent);published=next((x for x in rows if x.name.casefold()==name.casefold()),None)
            staged=next((x for x in rows if x.name==stage),None)
            if published:
                if record.get('staged') and published.id==record['staged']['id'] and published.size==item.size:
                    await self.cleanup_cache(row,record);return
                raise CloudOperationError('目标出现同名文件，未覆盖。请核对后重试或重新预览。')
        cache=self.cache(row,record);cache.mkdir(parents=True,exist_ok=True)
        cached=cache/record['cached'] if record.get('cached') else None
        stamp=self.stamps[row['id']][row['source']]
        def current():
            try:self.guard(row);return stamp
            except CloudOperationError:return None
        async def progress(done,total):row['progress']={'name':item.name,'done':done,'total':total,'phase':record['state']}
        if not cached or not cached.is_file():
            required=max(0,item.size-(record.get('checkpoint') or {}).get('length',0))
            if shutil.disk_usage(cache).free<required+16*1024*1024:raise CloudOperationError('本地暂存空间不足，请腾出空间后继续。')
            record['state']='downloading';self.save(row)
            from app.direct_download import DirectDownload
            from app.download_checkpoint import checkpoint,restore
            api=src.api;panel=self.b.panels[row['source']]
            resolver=panel.get_download_resolver() if hasattr(panel,'get_download_resolver') else api.new_download_resolver() if hasattr(api,'new_download_resolver') else api.download_source
            factory=getattr(panel,'make_download',DirectDownload)
            job=factory(item,cache,api.client,resolver,api.validate_download_url,progress,owner=stamp,current_owner=current)
            if record.get('checkpoint'):restore(job,record['checkpoint'])
            try:cached=await job.run()
            finally:
                record['checkpoint']=checkpoint(job);self.save(row)
            record.update(cached=Path(cached).name,hash=await asyncio.to_thread(content_hash,cached));self.save(row)
        elif await asyncio.to_thread(content_hash,cached)!=record.get('hash'):raise CloudOperationError('本地暂存文件发生变化，已停止复制。')
        async with dst.lock:
            self.guard(row);await self.target_root(row)
            if staged and not record.get('staged'):
                # A lost commit response is verified by content, never re-uploaded blindly.
                verify=cache/'verify';verify.mkdir(exist_ok=True)
                check=await SyncTransport(dst,self.store).download(staged,verify,stamp,current,progress)
                if await asyncio.to_thread(content_hash,check)!=record['hash']:raise CloudOperationError('上次上传的暂存内容不一致，请核对目标目录。')
                Path(check).unlink();record['staged']=asdict(staged);self.save(row)
            if not staged:
                if record.get('staged'):raise CloudOperationError('上次上传的暂存文件已移动，请核对目标目录。')
                record['state']='uploading';self.save(row)
                adapter=(BaiduUploader if row['target']=='baidu' else QuarkUploader)(dst.api)
                upload=UploadJob(cached,row['target'],parent,adapter,owner=stamp,current_owner=current,progress=progress,remote_name=stage)
                staged=await upload.run();record['staged']=asdict(staged);self.save(row)
            elif record.get('staged') and not remote_equal(remote_mark(staged),remote_mark(CloudFile(**record['staged']))):raise CloudOperationError('云端暂存文件发生变化。')
            self.guard(row);record['state']='publishing';self.save(row)
            result=await dst.relocate(staged,parent,name);record['result']=asdict(result);self.save(row)
        await self.cleanup_cache(row,record)
    async def cleanup_cache(self,row,record):
        if record.get('cached'):
            cached=self.cache(row,record)/record['cached']
            if cached.is_file() and await asyncio.to_thread(content_hash,cached)==record.get('hash'):cached.unlink()
            record.pop('cached',None);self.save(row)
    async def close(self):
        self.closed=True
        for task in self.tasks.values():task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
