"""Shared live download queue and controls for independently logged-in drives."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from uuid import uuid4
from functools import partial

import flet as ft

from app.direct_download import DirectDownload, DownloadError
from app.domain import human_size
from app.local_files import open_local_folder


class LiveDownloadMixin:
    def init_live_downloads(self):
        self.download_root = Path(__file__).resolve().parents[1] / 'downloads'
        self.on_transfer_navigation = None
        self.jobs: list[DirectDownload] = []
        self._live_worker: asyncio.Task | None = None
        self._downloads_stopping = False
        self._live_errors: dict[DirectDownload, str] = {}
        self._live_host = ft.Column(spacing=10)

    def new_download_destination(self,provider):
        return self.download_root / provider / uuid4().hex[:12]

    def set_download_root(self,path):
        self.download_root = Path(path)
        if self.download_destination is not None:
            # Already queued jobs keep their own destination unchanged.
            self.download_destination = self.download_root / self.session_provider / self.download_destination.name

    async def open_completed_folder(self,job,e=None):
        if job not in self.jobs or job.state != 'completed' or job.target is None:
            return
        try:
            await asyncio.to_thread(open_local_folder,job.target.parent)
        except OSError:
            if hasattr(self.page,'show_dialog'):
                self.page.show_dialog(ft.SnackBar(content=ft.Text('保存文件夹无法打开，可能已被移动或删除。')))

    async def view_transfers(self,e=None):
        if self.on_transfer_navigation:
            await self.on_transfer_navigation()

    def get_download_resolver(self):
        """Called at enqueue time; a sharing panel may return a bound closure."""
        factory = getattr(self.api, "new_download_resolver", None)
        return factory() if factory else self.api.download_source

    def download_controls(self):
        if not hasattr(self, 'jobs'):
            self.init_live_downloads()
        if self.on_transfer_navigation is not None:
            return [ft.Row([ft.Container(self.download_status,expand=True),
                ft.TextButton(content='查看传输中心',icon=ft.Icons.SWAP_VERT_ROUNDED,on_click=self.view_transfers)])]
        return [self.download_status, self._live_host, ft.Text(
            '下载直接保存到本机；当前支持本次会话内暂停和继续。'
            '退出账号或关闭窗口会清除未完成任务，需要重新创建下载。',
            size=11, color=ft.Colors.ON_SURFACE_VARIANT)]

    def _downloads_changed(self):
        if not hasattr(self, 'jobs') or self.closed:
            return
        self._live_host.controls = self.render_live_downloads().controls
        self.update()
        callback = getattr(self, 'on_transfer_change', None)
        if callback:
            callback()

    def _ensure_live_worker(self):
        if self.closed or self._downloads_stopping:
            return
        if self._live_worker is None or self._live_worker.done():
            self._live_worker = asyncio.create_task(self._drive_download_queue())

    def make_download(self, item, destination, client, resolver, validate, progress, **kwargs):
        return DirectDownload(item, destination, client, resolver, validate, progress, **kwargs)

    async def start_download(self, item, e=None):
        if self.closed or self._downloads_stopping or self.reader is None:
            return
        if item.is_dir:
            self.download_status.value = '请进入文件夹后选择具体文件下载。'
            self._downloads_changed()
            return
        if self.download_destination is None:
            self.download_status.value = '本次会话的下载目录尚未准备好，请重新连接账号。'
            self._downloads_changed()
            return
        if any(job.item.id == item.id and job.item.path == item.path
               and job.state not in {'completed', 'cancelled'} for job in self.jobs):
            self.download_status.value = '此文件已在任务列表中，可继续或重试原任务。'
            self._downloads_changed()
            return
        api, reader, generation = self.api, self.reader, self.generation
        session = getattr(api, 'session_id', None)
        resolver = self.get_download_resolver()
        owner = f'{generation}:{id(reader)}:{id(api)}'
        reader_is_view = getattr(self, 'download_reader_is_view', False)

        def current_owner():
            current = (not self.closed and self.api is api and self.reader is not None
                       and (reader_is_view or self.reader is reader)
                       and self.generation == generation
                       and getattr(api, 'session_id', None) == session)
            return owner if current else None

        async def bound_resolver(file):
            if current_owner() != owner:
                raise DownloadError('download_account', '账号会话已改变，请重新创建下载任务')
            result = await resolver(file)
            if current_owner() != owner:
                raise DownloadError('download_account', '账号会话已改变，请重新创建下载任务')
            return result

        async def progress(done, total):
            if current_owner() == owner and job in self.jobs:
                self._downloads_changed()

        job = self.make_download(item, self.download_destination, api.client,
                             bound_resolver, api.validate_download_url, progress,
                             owner=owner, current_owner=current_owner)
        job._share_download=bool(getattr(self, "share", None))
        self.jobs.append(job)
        self.download_status.value = '已加入真实下载队列，文件保存在：' + str(self.download_destination)
        self._downloads_changed()
        self._ensure_live_worker()

    async def _run_queued(self, job):
        if job.state == 'queued':
            return await job.run()

    async def _drive_download_queue(self):
        try:
            while not self.closed and not self._downloads_stopping:
                job = next((entry for entry in self.jobs if entry.state == 'queued'), None)
                if job is None:
                    return
                task = asyncio.create_task(self._run_queued(job))
                try:
                    await task
                except asyncio.CancelledError:
                    # Cancelling an individual transfer must not cancel its queue.
                    if asyncio.current_task().cancelling():
                        raise
                except DownloadError as exc:
                    job.state, job.error = 'failed', str(exc)
                    self._live_errors[job] = (exc.code if re.fullmatch(r'[a-z0-9_]{1,64}', exc.code)
                                              else 'download_failed')
                except Exception:
                    job.state, job.error = 'failed', '下载未完成，请重试。'
                    self._live_errors[job] = 'download_failed'
                finally:
                    if job in self.jobs:
                        self._downloads_changed()
        finally:
            # No task starts here: logout owns the stopping flag and cleanup.
            if self._live_worker is asyncio.current_task():
                self._live_worker = None

    async def pause_download(self, job, e=None):
        if job in self.jobs and job.state not in {'completed', 'cancelled'}:
            await job.pause()
            self._downloads_changed()

    async def resume_download(self, job, e=None):
        if (job not in self.jobs or self.closed or self._downloads_stopping
                or job.state not in {'paused', 'failed'} or job.requires_restart):
            return
        # run() revalidates the bound session and fetches a fresh source ticket.
        job.state, job.error = 'queued', ''
        self._live_errors.pop(job, None)
        self._downloads_changed()
        self._ensure_live_worker()

    async def cancel_one_download(self, job, e=None):
        if job in self.jobs and job.state != 'completed':
            await job.cancel()
            self._live_errors.pop(job, None)
            self._downloads_changed()

    async def cancel_download(self, e=None):
        if not hasattr(self, 'jobs'):
            return
        self._downloads_stopping = True
        try:
            worker = self._live_worker
            if worker is not None and worker is not asyncio.current_task():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            for job in list(self.jobs):
                if job.state != 'completed':
                    try:
                        await job.cancel()
                    except OSError:
                        # A locked local partial must not keep the account alive.
                        pass
            self.jobs.clear()
            self._live_errors.clear()
            self._live_host.controls.clear()
            self.download_status.value = ''
            self.download_task = None  # Legacy base-panel compatibility.
            self._live_worker = None
        finally:
            self._downloads_stopping = False
            self._downloads_changed()

    def render_live_downloads(self, *, completed=None):
        rows = []
        names = {'queued': '等待下载', 'resolving': '获取下载地址',
                 'downloading': '正在下载', 'paused': '已暂停',
                 'failed': '下载未完成', 'completed': '下载完成', 'cancelled': '已取消'}
        for job in reversed(self.jobs):
            ended = job.state in {'completed', 'cancelled'}
            if completed is not None and ended != completed:
                continue
            label = '百度网盘' if job.item.provider == 'baidu' else '夸克网盘'
            details = f'{label} · 真实下载 · {human_size(job.done)} / {human_size(job.total)}'
            if job.state == 'downloading':
                details += f' · {human_size(int(job.speed))}/s'
                if getattr(job, 'transfer_mode', None) == 'parallel':
                    details += f' · 分片连接 {job.active_connections}/{job.connections}'
                elif hasattr(job, 'transfer_mode'):
                    details += ' · 单连接'
            if job.state == 'completed' and getattr(job, 'elapsed_seconds', 0) > 0:
                seconds = job.elapsed_seconds
                duration = (f'{seconds:.1f} 秒' if seconds < 60
                            else f'{int(seconds)//60} 分 {int(seconds)%60} 秒')
                details += f' · 用时 {duration} · 平均 {human_size(int(job.average_speed))}/s'
            controls = []
            if job.state == 'completed':
                controls.append(ft.TextButton(content='打开所在文件夹',icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                    on_click=partial(self.open_completed_folder,job)))
            if job.state in {'queued', 'resolving', 'downloading'}:
                controls.append(ft.TextButton(content='暂停', on_click=partial(self.pause_download, job)))
            elif job.state in {'paused', 'failed'} and not job.requires_restart:
                controls.append(ft.TextButton(content='重试' if job.state == 'failed' else '继续',
                    on_click=partial(self.resume_download, job)))
            if not ended:
                controls.append(ft.TextButton(content='取消', on_click=partial(self.cancel_one_download, job)))
            body = [
                ft.Row([ft.Text(job.item.name, size=13, expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS), ft.Text(names.get(job.state, job.state), size=12)]),
                ft.Text(details, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.ProgressBar(value=(job.done / job.total if job.total else int(job.state == 'completed')),
                    height=4),
            ]
            if job.error:
                stage = self._live_errors.get(job, 'download_failed')
                body.append(ft.Text(f'{job.error}（阶段：{stage}）', size=12, color='#B45309', selectable=True))
            if job.state == 'completed':
                if job.warning:
                    body.append(ft.Text(job.warning, size=12, color='#B45309', selectable=True))
                check = 'MD5核对通过' if job.checksum_verified else '文件大小核对通过'
                body.append(ft.Text('已保存（' + check + '）：' + str(job.target), size=11, selectable=True))
            if controls:
                body.append(ft.Row(controls, spacing=4))
            rows.append(ft.Container(ft.Column(body, spacing=8), padding=12,
                                    border=ft.Border.all(1, '#CBD5E1'), border_radius=10))
        return ft.Column(rows, spacing=10)