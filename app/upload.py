"""Upload lifecycle with immutable destinations, local-change detection and safe retry."""
from __future__ import annotations
import asyncio
import hashlib
import os
import stat
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from app.domain import CloudFile

BLOCK_SIZE = 4 * 1024 * 1024

class UploadError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

@dataclass(frozen=True)
class Fingerprint:
    size: int
    modified_ns: int
    device: int
    inode: int
    @classmethod
    def read(cls, path):
        s = path.stat()
        if not stat.S_ISREG(s.st_mode):
            raise UploadError('upload_local', '请选择本地普通文件；文件夹上传尚未接入。')
        return cls(s.st_size, s.st_mtime_ns, s.st_dev, s.st_ino)

class UploadJob:
    direction = 'upload'
    def __init__(self, local, provider, directory, adapter, *, owner=None, current_owner=None, progress=None, remote_name=None):
        self.local = Path(local).resolve(strict=True)
        self.fingerprint = Fingerprint.read(self.local)
        name = self.local.name if remote_name is None else remote_name
        if not name or name in ('.', '..') or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):
            raise UploadError('upload_name', '文件名不能用于网盘，请重命名后上传。')
        if (not isinstance(directory,str) or not directory.startswith('/') or '\\' in directory
            or any(ord(c)<32 for c in directory) or (directory!='/' and any(x in ('','.','..') for x in directory[1:].split('/')))):
            raise UploadError('upload_path', '请重新进入要上传到的文件夹。')
        self.directory = directory
        self.item = CloudFile(uuid4().hex, provider, directory.rstrip('/')+'/'+name, False, self.fingerprint.size)
        self.adapter, self.owner, self.current_owner, self.progress = adapter, owner, current_owner, progress
        self.total, self.done, self.speed = self.item.size, 0, 0.0
        self.state, self.error, self.error_code = 'queued', '', ''
        self.requires_restart = False
        self.commit_started = False
        self.remote_file = None
        self.remote_id = None
        self._stop_reading = threading.Event()
        self.hashes = None
        self._task = None
        self._last_notice = 0.0
        self._started = 0.0
        self.elapsed_seconds = 0.0

    def guard(self):
        if self.state=='cancelled' or (self.current_owner and self.current_owner()!=self.owner):
            raise UploadError('upload_account', '账号会话已改变，上传已停止。')
        try:
            if Fingerprint.read(self.local)!=self.fingerprint:
                raise UploadError('upload_changed', '本地文件已改变，请重新选择后上传。')
        except OSError:
            raise UploadError('upload_local', '本地文件无法读取，请检查文件是否移动或被占用。') from None

    async def notify(self, *, force=False):
        now=time.monotonic()
        if self.progress and (force or now-self._last_notice>=.2):
            self._last_notice=now
            await self.progress(self.done,self.total)

    async def stage(self, state):
        self.guard()
        self.state=state
        await self.notify(force=True)

    async def sent(self, count):
        self.guard()
        self.done=min(count,self.total)
        self.speed=self.done/max(.001,time.monotonic()-self._started)
        await self.notify()

    async def fingerprint_content(self):
        await self.stage('preparing')
        def calculate():
            md5,sha1=hashlib.md5(),hashlib.sha1();blocks=[]
            with self.local.open('rb') as stream:
                while block:=stream.read(BLOCK_SIZE):
                    if self._stop_reading.is_set(): raise UploadError('upload_cancelled', '上传已取消。')
                    md5.update(block);sha1.update(block);blocks.append(hashlib.md5(block).hexdigest())
            return {'md5':md5.hexdigest(),'sha1':sha1.hexdigest(),'blocks':blocks or [hashlib.md5(b'').hexdigest()]}
        result=await asyncio.to_thread(calculate)
        self.guard()
        return result

    async def read(self, offset, size):
        self.guard()
        def read_block():
            with self.local.open('rb') as stream:
                stream.seek(offset)
                return stream.read(size)
        data=await asyncio.to_thread(read_block)
        self.guard()
        if len(data)!=min(size,self.total-offset):
            raise UploadError('upload_changed','本地文件长度已改变，请重新选择后上传。')
        return data

    async def stream(self, data, offset):
        for start in range(0,len(data),65536):
            self.guard()
            chunk=data[start:start+65536]
            yield chunk
            await self.sent(offset+start+len(chunk))

    async def run(self):
        if self.state=='cancelled': return None
        self._stop_reading.clear();self._pause_requested=False
        self._task=asyncio.current_task();self._started=time.monotonic();self.error='';self.error_code=''
        try:
            self.guard()
            if self.commit_started:
                # A lost commit response may already have created the file. Never
                # retry the write until its outcome is known.
                await self.stage('verifying')
                self.remote_file=await self.adapter.verify(self)
            else:
                self.done=0
                self.hashes=await self.fingerprint_content()
                await self.adapter.upload(self)
                await self.stage('verifying')
                self.remote_file=await self.adapter.verify(self)
            self.guard()
            self.done=self.total;self.state='completed'
            self.elapsed_seconds=time.monotonic()-self._started
            return self.remote_file
        except asyncio.CancelledError:
            self._stop_reading.set()
            if self.commit_started:
                self.state='failed';self.error_code='upload_uncertain';self.error='上传提交结果待核对，请重新连接后检查目标目录。'
            else:self.state='paused' if getattr(self,'_pause_requested',False) else 'cancelled'
            self.speed=0.0
            raise
        except UploadError as exc:
            self.state='failed';self.error_code=exc.code;self.error=str(exc)
            self.requires_restart=exc.code in {'upload_changed','upload_account','upload_local','upload_conflict'}
            raise
        except Exception:
            self.state='failed';self.error_code='upload_failed'
            self.error='上传结果尚未确认，请核对结果。' if self.commit_started else '上传未完成，请重试。'
            raise UploadError(self.error_code,self.error) from None
        finally:
            self._task=None
            await self.notify(force=True)

    async def pause(self):
        if self.commit_started or self.state in ('completed','cancelled'):return False
        self._pause_requested=True;self._stop_reading.set()
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel();await asyncio.gather(self._task,return_exceptions=True)
        else:self.state='paused'
        self.speed=0.0
        return True

    async def cancel(self):
        self._pause_requested=False
        if self.state=='completed':return
        self._stop_reading.set()
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task,return_exceptions=True)
        elif not self.commit_started:self.state='cancelled'
