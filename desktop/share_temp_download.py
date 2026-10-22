"""Journaled Baidu share fallback: temporary own-drive copy, then local download."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path

from app.cloud_operations import CloudOperationError


ROOT = '/CloudDock 临时下载'


class ShareTempDownload:
    def __init__(self, bridge):
        self.bridge = bridge
        self.running = set()

    @property
    def store(self):
        return self.bridge.features.store

    def record(self, key, **changes):
        row = self.store.get('share_temp', key) or {'id': key, 'at': time.time()}
        row.update(changes)
        self.store.put('share_temp', key, row)
        return row

    async def stage(self, plan, item):
        """Copy exactly one checked share file into its own app-created folder."""
        if plan['provider'] != 'baidu' or not plan['share_key'] or item.is_dir:
            raise CloudOperationError('此文件不能使用临时转存下载。')
        share_row = self.bridge.shares.session(plan['share_key'])
        share = share_row['share']
        if share.files.get(item.id) != item:
            raise CloudOperationError('分享文件已变化，请重新打开。')
        ops = plan['ops']
        account = await ops.account()
        try:
            self.bridge.features.account_status.check_upload('baidu', item.size)
        except CloudOperationError:
            raise CloudOperationError('百度网盘剩余空间不足，无法临时转存此文件。') from None
        key = secrets.token_hex(16)
        name = '下载-' + time.strftime('%Y%m%d-%H%M%S') + '-' + key[:8]
        path = ROOT + '/' + name
        # Persist intent before the first remote mutation. A crash may leave an
        # unknown folder, but it must never cause deletion by name alone.
        self.record(key, provider='baidu', account=account, state='intent',
                    folder_path=path, folder_id='', file_id='', file_path='',
                    file_name=item.name, file_size=item.size, job_id='')
        try:
            async with ops.lock:
                ops.guard(plan['stamp'])
                if await ops.account() != account:
                    raise CloudOperationError('账号已变化，未转存。')
                await ops.ensure(ROOT)
                folder = await ops.mkdir(ROOT, name, exclusive=True)
                self.record(key, state='transferring', folder_id=folder.id)
                await self.bridge.sharing.baidu(ops, 'transfer',
                    {'fsidlist': json.dumps([int(item.id)]), 'path': folder.path},
                    {'shareid': share.id, 'from': share.uk}, share_row['client'])
                for _ in range(12):
                    files = await ops.list(path)
                    if len(files) == 1:
                        target = files[0]
                        if (not target.is_dir and target.name == item.name
                                and target.size == item.size):
                            self.record(key, state='ready', file_id=target.id,
                                        file_path=target.path)
                            return target, key
                    await asyncio.sleep(.6)
                raise CloudOperationError('转存已提交，但文件尚未核对完成。')
        except Exception:
            self.record(key, state='needs_review')
            self.bridge.features.feedback.publish('error',
                '临时转存结果待核对，未加入下载队列', provider='baidu', view='transfers')
            raise CloudOperationError('临时转存结果待核对。请检查百度网盘中的 '
                                      + path + '；云屿未自动删除或重复转存。') from None

    def queued(self, key, job):
        row = self.record(key, state='downloading', job_id=job._desktop_id)
        job._temporary_share = key
        self.bridge.features.feedback.publish('sync',
            '分享文件已临时转存，开始下载到本机', provider='baidu', view='transfers')
        return row

    def schedule_completed(self, row):
        key = row.get('temporary_share')
        if not key or key in self.running:
            return
        record = self.store.get('share_temp', key)
        if not record or record.get('state') not in ('downloading', 'cleanup_retry'):
            return
        if row.get('status') != 'completed':
            return
        self.running.add(key)
        task = asyncio.create_task(self.cleanup(key, row), name='share-temp-cleanup:'+key)
        task.add_done_callback(lambda _: self.running.discard(key))

    async def cleanup(self, key, transfer):
        record = self.store.get('share_temp', key)
        if not record or record.get('state') not in ('downloading', 'cleanup_retry'):
            return
        ops = self.bridge.features.ops.get(record['provider'])
        try:
            if not ops:
                return
            try:current_account=await ops.account()
            except CloudOperationError:return
            if current_account != record['account']:
                # The original account can be reconnected later. Never touch
                # a different account's same-named path.
                return
            target = Path(transfer['target'])
            if (transfer.get('status') != 'completed' or not target.is_file()
                    or target.stat().st_size != record['file_size']
                    or record.get('job_id') != transfer.get('id')):
                raise CloudOperationError('本地完成文件无法核对。')
            async with ops.lock:
                folders = await ops.list(ROOT)
                folder = next((f for f in folders if f.id == record['folder_id']
                               and f.path == record['folder_path'] and f.is_dir), None)
                if folder is None:
                    raise CloudOperationError('临时目录已被移动或删除。')
                files = await ops.list(folder.path)
                if (len(files) != 1 or files[0].id != record['file_id']
                        or files[0].path != record['file_path']
                        or files[0].size != record['file_size']):
                    raise CloudOperationError('临时目录内容已变化。')
                await ops.delete([folder])
            self.record(key, state='recycled', cleaned_at=time.time())
            self.bridge.features.feedback.publish('download',
                '下载完成，临时云端副本已移入回收站', provider=record['provider'], view='transfers')
        except Exception:
            self.record(key, state='cleanup_failed')
            self.bridge.features.feedback.publish('error',
                '下载已完成，临时云端副本未清理，请在网盘中检查',
                provider=record['provider'], view='transfers')

    async def retry_cleanup(self, key, transfer):
        record=self.store.get('share_temp',key)
        if not record or record.get('state')!='cleanup_failed' or record.get('job_id')!=transfer.get('id'):
            raise CloudOperationError('没有可重试的临时副本清理任务。')
        self.record(key,state='cleanup_retry')
        await self.cleanup(key,transfer)
        if (self.store.get('share_temp',key) or {}).get('state')!='recycled':
            raise CloudOperationError('临时副本仍未能清理，请检查账号和临时目录内容。')

    async def close(self):
        # Cleanup tasks finish or are cancelled before DurableStore closes.
        for task in list(asyncio.all_tasks()):
            if task is not asyncio.current_task() and task.get_name().startswith('share-temp-cleanup:'):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
