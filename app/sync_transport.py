"""Journalled replacements. Old content is retained before a name is reused."""
from __future__ import annotations
import asyncio,os,shutil,time
from dataclasses import asdict
from pathlib import Path,PurePosixPath
from uuid import uuid4
from app.cloud_operations import CloudOperationError
from app.domain import CloudFile
from app.upload import UploadJob
from app.direct_download import DirectDownload
from app.providers.web_upload import BaiduUploader,QuarkUploader
from app.sync_model import content_hash,safe_local,remote_mark,remote_equal,local_snapshot,identity

class SyncTransport:
    def __init__(self,ops,store):self.ops,self.store=ops,store
    def guard_local(self,rule):
        if identity(rule['local_root'])!=rule['local_identity']:raise CloudOperationError('本地同步位置已变化，更新已暂停。')
    async def find(self,path):
        return next((x for x in await self.ops.list(str(PurePosixPath(path).parent)) if x.path==path),None)
    async def guard_remote(self,path,expected):
        now=await self.find(path)
        if not remote_equal(remote_mark(now) if now else None,expected):raise CloudOperationError('云端已变化，已停止这次更新。')
        return now
    async def download(self,item,destination,owner,current,progress):
        api=self.ops.api
        resolver=api.new_download_resolver() if hasattr(api,'new_download_resolver') else api.download_source
        job=DirectDownload(item,destination,api.client,resolver,api.validate_download_url,progress,owner=owner,current_owner=current)
        return await job.run()
    def pending(self,rule,rel):return self.store.get('sync_pending',rule['id']+':'+rel)
    def save(self,record):self.store.put('sync_pending',record['id'],record)
    async def upload(self,rule,rel,local,old,owner,current,progress):
        self.guard_local(rule)
        key=rule['id']+':'+rel;source=safe_local(rule['local_root'],rel)
        path=rule['remote_root'].rstrip('/')+'/'+(rule['remote_name'] if rule['kind']=='file' else rel)
        parent=str(PurePosixPath(path).parent);await self.ops.ensure(parent)
        record=self.pending(rule,rel)
        if record is None:
            uid=uuid4().hex;name='.clouddock-stage-'+uid+Path(rel).suffix
            record={'id':key,'rule':rule['id'],'rel':rel,'kind':'upload','phase':'uploading','local':local,'old':old,
                    'path':path,'stage_path':parent.rstrip('/')+'/'+name,'archive':rule['remote_root'].rstrip('/')+'/.clouddock-history/'+rule['id']+'/'+uid,'at':time.time()}
            self.save(record)
        if record['kind']!='upload':raise CloudOperationError('上次操作仍待核对。')
        if current()!=owner:raise CloudOperationError('同步已暂停或账号改变。')
        published=await self.find(record['path'])
        if record.get('staged') and published and published.id==record['staged']['id']:
            if record.get('result') and remote_equal(remote_mark(published),remote_mark(CloudFile(**record['result']))):return published,record['local']
            if published.size!=record['local']['fp']['size'] or await self.compare(rule,published,owner,current,progress)!=record['local']['hash']:raise CloudOperationError('上次发布后的文件又被修改，已保留双方内容，请核对。')
            return published,record['local']
        staged=await self.find(record['stage_path'])
        if staged is None:
            if record['phase']!='uploading':raise CloudOperationError('暂存文件缺失，原文件与历史版本已保留，请核对。')
            if await asyncio.to_thread(content_hash,source)!=record['local']['hash']:raise CloudOperationError('本地文件已改变，上次上传待核对。')
            adapter=(BaiduUploader if rule['provider']=='baidu' else QuarkUploader)(self.ops.api)
            job=UploadJob(source,rule['provider'],parent,adapter,owner=owner,current_owner=current,progress=progress,remote_name=PurePosixPath(record['stage_path']).name)
            try:staged=await job.run()
            except BaseException:
                # A known pre-commit failure (for example quota) created no visible file.
                # Do not trap the rule behind an uncertain-operation prompt in that case.
                if not getattr(job,'commit_started',True):self.store.remove('sync_pending',key)
                raise
            record['staged']=asdict(staged);record['phase']='staged';self.save(record)
        elif not record.get('staged'):
            # A crash may have lost the commit reply; verify bytes before adopting it.
            folder=safe_local(rule['local_root'],'.clouddock-stage/'+rule['id']+'/'+uuid4().hex);folder.mkdir(parents=True)
            downloaded=await self.download(staged,folder,owner,current,progress)
            if await asyncio.to_thread(content_hash,downloaded)!=record['local']['hash']:raise CloudOperationError('上次上传内容无法确认，已保留暂存文件。')
            downloaded.unlink();record['staged']=asdict(staged);record['phase']='staged';self.save(record)
        elif not remote_equal(remote_mark(staged),remote_mark(CloudFile(**record['staged']))):raise CloudOperationError('暂存文件被修改，已停止更新。')
        if current()!=owner:raise CloudOperationError('同步已暂停或账号改变。')
        old=record['old'];current_file=await self.find(record['path'])
        if old:
            if current_file:
                await self.guard_remote(record['path'],old)
                await self.ops.ensure(record['archive']);record['phase']='archiving';self.save(record)
                archived=await self.ops.relocate(current_file,record['archive'],current_file.name)
                record['archived']=asdict(archived);record['phase']='archived';self.save(record)
            else:
                archived=await self.find(record['archive']+'/'+PurePosixPath(record['path']).name)
                if not archived or archived.id!=old['id']:raise CloudOperationError('原文件位置已变化，暂存文件保留，需核对。')
        elif current_file:raise CloudOperationError('同步期间出现同名文件，未覆盖。')
        if current()!=owner:raise CloudOperationError('同步已暂停或账号改变。')
        record['phase']='publishing';self.save(record)
        staged=await self.find(record['stage_path'])
        if staged is None:raise CloudOperationError('暂存文件尚未确认。')
        result=await self.ops.relocate(staged,parent,PurePosixPath(path).name)
        record['phase']='published';record['result']=asdict(result);self.save(record)
        return result,record['local']
    async def pull(self,rule,rel,item,old_local,owner,current,progress):
        self.guard_local(rule)
        key=rule['id']+':'+rel;record=self.pending(rule,rel)
        target=safe_local(rule['local_root'],rel)
        if record is None:
            uid=uuid4().hex
            record={'id':key,'rule':rule['id'],'rel':rel,'kind':'download','phase':'downloading','remote':asdict(item),'old_local':old_local,
                    'stage':str(safe_local(rule['local_root'],'.clouddock-stage/'+rule['id']+'/'+uid)),
                    'archive':str(safe_local(rule['local_root'],'.clouddock-history/'+rule['id']+'/'+uid+'/'+rel)),'at':time.time()}
            self.save(record)
        if record['kind']!='download':raise CloudOperationError('上次操作仍待核对。')
        expected=CloudFile(**record['remote'])
        if record.get('hash') and target.is_file() and await asyncio.to_thread(content_hash,target)==record['hash']:
            return expected
        await self.guard_remote(expected.path,remote_mark(expected))
        stage=safe_local(rule['local_root'],Path(record['stage']).relative_to(rule['local_root']).as_posix());stage.mkdir(parents=True,exist_ok=True)
        cached=Path(record['downloaded']) if record.get('downloaded') else None
        if cached is None or not cached.is_file():
            cached=await self.download(expected,stage,owner,current,progress)
            record.update(downloaded=str(cached),hash=await asyncio.to_thread(content_hash,cached),phase='downloaded');self.save(record)
        if await asyncio.to_thread(content_hash,cached)!=record['hash']:raise CloudOperationError('本地暂存内容已改变。')
        await self.guard_remote(expected.path,remote_mark(expected))
        if current()!=owner:raise CloudOperationError('同步已暂停或账号改变。')
        def publish():
            self.guard_local(rule)
            safe_local(rule['local_root'],rel)
            old=record['old_local']
            if target.exists():
                if not old or old['dir'] or content_hash(target)!=old['hash']:raise CloudOperationError('本地文件已变化，保留下载副本，等待冲突处理。')
                archive=safe_local(rule['local_root'],Path(record['archive']).relative_to(rule['local_root']).as_posix());archive.parent.mkdir(parents=True,exist_ok=True)
                if not archive.exists():shutil.copy2(target,archive)
                if content_hash(target)!=old['hash']:raise CloudOperationError('本地文件仍在写入，请稍后重试。')
            elif old:raise CloudOperationError('本地文件已删除，未自动恢复。')
            target.parent.mkdir(parents=True,exist_ok=True)
            # No-clobber publish when the target was absent. Replacement keeps a local archive.
            if not target.exists():os.link(cached,target);cached.unlink()
            else:os.replace(cached,target)
        record['phase']='publishing';self.save(record)
        await asyncio.to_thread(publish);record['phase']='published';self.save(record);return expected
    async def compare(self,rule,item,owner,current,progress):
        stage=safe_local(rule['local_root'],'.clouddock-stage/'+rule['id']+'/'+uuid4().hex);stage.mkdir(parents=True)
        result=await self.download(item,stage,owner,current,progress)
        digest=await asyncio.to_thread(content_hash,result);result.unlink();return digest
