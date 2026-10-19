"""Bounded task controls; queue metadata never changes cloud ownership."""
import asyncio
from pathlib import Path
from app.cloud_operations import CloudOperationError
from app.local_files import prepare_download_root
from desktop.experience import Experience

ACTIVE = {'queued', 'retrying', 'resolving', 'preparing', 'uploading', 'downloading'}


class TransferManagement:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lock = asyncio.Lock()

    def live_job(self, id):
        for name, p in self.bridge.panels.items():
            for j in [*p.jobs, *p.upload_jobs]:
                if getattr(j, '_desktop_id', None) == id: return name, p, j
        return None

    async def action(self, d):
        f = self.bridge.features
        if not f: raise CloudOperationError('工作空间正在准备。')
        async with self.lock:
            if d['action'] == 'transfers_destination': return await self.destination(d)
            ids = d.get('ids')
            command = d.get('command')
            if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(x, str) for x in ids):
                raise CloudOperationError('一次请选择 1–500 个传输任务。')
            if command not in ('pause', 'resume', 'retry_failed', 'priority'):
                raise CloudOperationError('未知传输操作。')
            priority = d.get('priority')
            if command == 'priority' and (type(priority) is not int or priority not in (0, 1, 2)):
                raise CloudOperationError('优先级无效。')
            # Hold both queues before awaiting cancellation: an active job finishing
            # must not start the next selected job halfway through a group pause.
            panels = list(self.bridge.panels.values())
            for p in panels: p._queue_hold = True
            changed, skipped, errors = 0, 0, []
            try:
                for id in dict.fromkeys(ids):
                    try:
                        live = self.live_job(id)
                        row = f.store.get('transfer', id) if not live else None
                        status = live[2].state if live else row.get('status') if row else None
                        if status is None or status in ('completed', 'cancelled'):
                            skipped += 1; continue
                        if command == 'priority':
                            if live:
                                live[2]._priority = priority
                                f.capture(live[0], force=True)
                            else:
                                row['priority'] = priority; f.store.put('transfer', id, row)
                        elif command == 'pause':
                            if not live or status not in ACTIVE or getattr(live[2], 'commit_started', False):
                                skipped += 1; continue
                            _, p, job = live
                            if getattr(job, 'direction', 'download') == 'upload': await job.pause()
                            else: await p.pause_download(job)
                            f.capture(live[0], force=True)
                        else:
                            allowed = ('failed',) if command == 'retry_failed' else ('paused',)
                            # An interrupted record is presented as paused after restart.
                            if not live and status not in ('failed', 'paused') and command == 'resume': status = 'paused'
                            if status not in allowed or (live and live[2].requires_restart):
                                skipped += 1; continue
                            if live:
                                name, p, job = live
                                account = await f.ops[name].account()
                                if getattr(job, '_persistent_account', account) != account:
                                    raise CloudOperationError('请连接原账号后继续。')
                                if getattr(job, 'direction', 'download') == 'upload': await p.retry_upload(job)
                                else: await p.resume_download(job)
                            else: await f.resume(row)
                        changed += 1
                    except Exception as error:
                        from app.operation_diagnostics import public_error
                        errors.append({'id': id, 'message': str(error) if isinstance(error, CloudOperationError) else public_error('task', error)})
            finally:
                for p in panels:
                    p._queue_hold = False
                    p._ensure_live_worker(); p._ensure_upload_worker()
            return {'changed': changed, 'skipped': skipped, 'errors': errors}

    async def destination(self, d):
        f = self.bridge.features
        live = self.live_job(d.get('id'))
        row = f.store.get('transfer', d.get('id'))
        if live:
            name, p, old = live
            code = getattr(old, 'error_code', '') or p._live_errors.get(old, '')
            if getattr(old, 'direction', 'download') != 'download' or old.state != 'failed' or code not in ('download_disk', 'download_space'):
                raise CloudOperationError('只有磁盘写入失败的下载可以更换位置。')
            account = await f.ops[name].account()
            if getattr(old, '_persistent_account', account) != account:
                raise CloudOperationError('请连接原账号后继续。')
        else:
            if not row or row['direction'] != 'download' or row.get('error_code') not in ('download_disk', 'download_space'):
                raise CloudOperationError('请重新选择磁盘写入失败的任务。')
        destination = prepare_download_root(self.bridge.selected_paths(d.get('selection'), 'directory')[0])
        total = old.total if live else row['total']
        Experience.check_disk(destination, total)
        if live:
            if destination.resolve() == Path(old.destination).resolve():
                raise CloudOperationError('请另选位置；原位置释放空间后可以直接重试。')
            # Reuse the bound resolver (including share sessions), never an arbitrary URL.
            resolver = old.resolve_source
            previous = old.state; old.state = 'cancelled'
            try:
                job = await p.start_download(old.item, destination=destination, notify=False,
                    resolver_override=resolver, share_key=getattr(old, '_share_key', ''))
                if job is None: raise CloudOperationError('文件已有传输任务。')
            finally: old.state = previous
            try: await old.cancel()
            except OSError:
                p.jobs.remove(job)
                raise CloudOperationError('原临时文件被占用，请关闭占用程序后重试。') from None
            for key in ('_desktop_id', '_download_group', '_group_id', '_priority', '_persistent_account'):
                if hasattr(old, key): setattr(job, key, getattr(old, key))
            p.jobs.remove(old); f.capture(name, force=True); p._ensure_live_worker()
        else:
            if destination.resolve() == Path(row['destination']).resolve():
                raise CloudOperationError('请另选位置。')
            updated = {**row, 'destination': str(destination), 'checkpoint': None}
            await f.resume(updated)
        return {'destination': str(destination), 'message': '已在新位置重新下载。已完成文件保留；历史任务的旧临时文件可在确认完成后清理。'}
