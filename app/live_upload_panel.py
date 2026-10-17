"""Native file selection and per-account upload queues."""
from __future__ import annotations
import asyncio
from functools import partial
from pathlib import Path
import flet as ft
from app.domain import human_size
from app.upload import UploadJob, UploadError
from app.providers.web_upload import BaiduUploader, QuarkUploader


class LiveUploadMixin:
    def init_live_uploads(self):
        self.upload_jobs = []
        self._upload_worker = None
        self._uploads_stopping = False
        self._picking_upload = False
        self.upload_status = ft.Text('', size=12, selectable=True)
        self._upload_host = ft.Column(spacing=10)
        self.browser_upload = ft.Button(content='上传文件', icon=ft.Icons.UPLOAD_ROUNDED,
            disabled=True, on_click=self.choose_upload)

    def upload_allowed(self):
        return (not self.closed and not self._uploads_stopping and not self._picking_upload and self.reader is not None
                and getattr(self.api, 'authenticated', False) and getattr(self, 'share', None) is None
                and bool(self.path) and not getattr(self, 'browser_busy', False))

    def upload_snapshot(self):
        api, generation = self.api, self.generation
        session = getattr(api, 'session_id', None)
        owner = (id(api), generation, session)
        def current_owner():
            return owner if (not self.closed and self.api is api and self.generation == generation
                and api.authenticated and getattr(api, 'session_id', None) == session) else None
        return api, self.path, owner, current_owner

    async def choose_upload(self, e=None):
        if not self.upload_allowed(): return
        snapshot = self.upload_snapshot()
        self._picking_upload = True
        self.update()
        try:
            files = await ft.FilePicker().pick_files(dialog_title='选择要上传的文件', allow_multiple=True)
        except Exception:
            self.upload_status.value = '文件选择窗口未能打开，请重试。'
            self._uploads_changed()
            return
        finally:
            self._picking_upload = False
            self.update()
        if not files: return
        if snapshot[3]() != snapshot[2] or getattr(self, 'share', None) is not None:
            self.upload_status.value = '账号或目录视图已切换，请重新选择上传文件。'
            self._uploads_changed()
            return
        paths = [f.path for f in files if f.path]
        if len(paths) != len(files):
            self.upload_status.value = '请在 Windows 桌面程序中选择本地文件上传。'
            self._uploads_changed()
            return
        await self.queue_upload_paths(paths, snapshot)

    async def queue_upload_paths(self, paths, snapshot=None):
        if not self.upload_allowed(): return
        api, directory, owner, current_owner = snapshot or self.upload_snapshot()
        if current_owner() != owner: return
        added, skipped, errors = 0, 0, []
        for path in paths:
            try:
                local = Path(path).resolve(strict=True)
                if any(j.local == local and j.directory == directory and j.owner == owner
                       and j.state not in {'completed', 'cancelled'} for j in self.upload_jobs):
                    skipped += 1
                    continue
                adapter = (BaiduUploader if self.session_provider == 'baidu' else QuarkUploader)(api)
                async def progress(done, total): self._uploads_changed()
                job = UploadJob(local, self.session_provider, directory, adapter, owner=owner,
                                current_owner=current_owner, progress=progress)
                self.upload_jobs.append(job)
                added += 1
            except (OSError, UploadError):
                errors.append('有文件无法读取，请确认文件仍在本机且未被占用。')
        self.upload_status.value = (f'已加入 {added} 个上传任务 · 目标：{directory}' if added else '没有新增上传任务。')
        if skipped: self.upload_status.value += f' · {skipped} 个文件已在队列中'
        if errors: self.upload_status.value += ' · ' + errors[0]
        self._uploads_changed()
        self._ensure_upload_worker()

    def _uploads_changed(self):
        if self.closed: return
        self._upload_host.controls = self.render_live_uploads().controls
        self.update()
        callback = getattr(self, 'on_transfer_change', None)
        if callback: callback()

    def _ensure_upload_worker(self):
        if self.closed or self._uploads_stopping: return
        if self._upload_worker is None or self._upload_worker.done():
            self._upload_worker = asyncio.create_task(self._drive_upload_queue())

    async def _drive_upload_queue(self):
        try:
            while not self.closed and not self._uploads_stopping:
                job = next((j for j in self.upload_jobs if j.state == 'queued'), None)
                if job is None: break
                try:
                    await asyncio.create_task(job.run())
                    if job.state == 'completed':
                        self.save_remembered()
                        if (self.reader is not None and getattr(self, 'share', None) is None
                                and self.path == job.directory and job.current_owner() == job.owner
                                and not self.browser_busy):
                            await self.load_directory(self.path, self.page_index)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling(): raise
                except UploadError:
                    pass
                finally:
                    if job in self.upload_jobs: self._uploads_changed()
        finally:
            if self._upload_worker is asyncio.current_task(): self._upload_worker = None

    async def retry_upload(self, job, e=None):
        if (self.closed or self._uploads_stopping or job not in self.upload_jobs
                or job.state != 'failed' or job.requires_restart): return
        job.state, job.error = 'queued', ''
        self._uploads_changed()
        self._ensure_upload_worker()

    async def cancel_one_upload(self, job, e=None):
        if job in self.upload_jobs and not job.commit_started:
            await job.cancel()
            self._uploads_changed()

    async def cancel_download(self, e=None):
        # The existing logout/shutdown path awaits this method before resetting
        # cookies, so in-flight writes cannot escape into a replacement account.
        if hasattr(self, 'upload_jobs'):
            self._uploads_stopping = True
            try:
                worker = self._upload_worker
                if worker is not None and worker is not asyncio.current_task():
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)
                for job in list(self.upload_jobs): await job.cancel()
                self.upload_jobs.clear()
                self.upload_status.value = ''
                self._upload_host.controls.clear()
                self._upload_worker = None
            finally: self._uploads_stopping = False
        await super().cancel_download(e)

    def upload_controls(self):
        if getattr(self, 'on_transfer_navigation', None) is not None:
            return [self.upload_status]
        return [self.upload_status, self._upload_host]

    def render_live_uploads(self, *, completed=None):
        states = {'queued':'等待上传', 'preparing':'检查本地文件', 'uploading':'正在上传',
            'committing':'正在提交', 'verifying':'核对上传结果', 'completed':'上传完成',
            'cancelled':'已取消', 'failed':'上传未完成'}
        rows = []
        for job in reversed(self.upload_jobs):
            ended = job.state in {'completed', 'cancelled'}
            if completed is not None and ended != completed: continue
            label = '百度网盘' if job.item.provider == 'baidu' else '夸克网盘'
            detail = f'{label} · 上传 · {human_size(job.done)} / {human_size(job.total)}'
            if job.state == 'uploading': detail += f' · {human_size(int(job.speed))}/s'
            actions = []
            if job.state == 'failed' and not job.requires_restart:
                actions.append(ft.TextButton(content='核对结果' if job.commit_started else '重试',
                    on_click=partial(self.retry_upload, job)))
            if not ended and not job.commit_started:
                actions.append(ft.TextButton(content='取消', on_click=partial(self.cancel_one_upload, job)))
            body = [ft.Row([ft.Icon(ft.Icons.UPLOAD_ROUNDED, size=18),
                ft.Text(job.item.name, size=13, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(states.get(job.state, job.state), size=12)]), ft.Text(detail, size=11),
                ft.ProgressBar(value=job.done/job.total if job.total else int(job.state == 'completed'), height=4),
                ft.Text('目标：'+job.item.path, size=11, selectable=True)]
            if job.error: body.append(ft.Text(job.error, size=12, color='#B45309', selectable=True))
            if job.state == 'completed': body.append(ft.Text('已在网盘目录确认文件，大小核对通过。', size=11))
            if actions: body.append(ft.Row(actions, spacing=4))
            rows.append(ft.Container(ft.Column(body, spacing=8), padding=12,
                border=ft.Border.all(1, '#CBD5E1'), border_radius=10))
        return ft.Column(rows, spacing=10)
