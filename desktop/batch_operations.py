"""Account-bound previews and durable, item-by-item batch outcomes."""
import asyncio
import secrets
import stat
import threading
import time
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from app.batch_names import choose_name, policy, renamed
from app.cloud_operations import CloudOperationError
from app.upload import Fingerprint, UploadJob
from app.providers.web_upload import BaiduUploader, QuarkUploader
from desktop.productivity import cloud_path, file_name

LIMIT = 10000

def ordinary(path):
    for part in (path, *path.parents):
        s = part.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise CloudOperationError('链接或联接不参与上传，请选择真实文件夹。')

def local_tree(paths, stop=None):
    rows = []
    seen = set()
    deadline = time.monotonic() + 60
    def visit(path, rel, depth):
        if stop is not None and stop.is_set(): raise CloudOperationError('扫描已取消。')
        if len(rows) >= LIMIT or depth > 32 or time.monotonic() > deadline:
            raise CloudOperationError('本次选择过大，请分批选择（最多 10000 项、32 层）。')
        ordinary(path)
        s = path.stat()
        directory = stat.S_ISDIR(s.st_mode)
        if not directory and not stat.S_ISREG(s.st_mode):
            raise CloudOperationError('包含无法上传的特殊文件。')
        file_name(path.name)
        row = {'source': str(path), 'rel': rel, 'directory': directory,
               'size': 0 if directory else s.st_size}
        if not directory: row['fingerprint'] = asdict(Fingerprint.read(path))
        rows.append(row)
        if directory:
            for child in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
                visit(child, rel + '/' + child.name, depth + 1)
    roots = [Path(p).absolute() for p in paths]
    for root in roots:
        if root in seen: continue
        if any(other != root and other in root.parents for other in roots): continue
        seen.add(root)
        visit(root, root.name, 0)
    return rows

class BatchOperations:
    def __init__(self, bridge):
        self.bridge = bridge
        self.plans = {}
        self.tasks = {}
        self.active = set()
        self.closed = False
        self.records = None

    def records_loaded(self):
        if self.records is None:
            store = self.bridge.features.store
            self.records = {r['token']: r for r in store.all('batch')}
            for r in self.records.values():
                r['_persisted'] = True
                for i, item in enumerate(r['rows']): item['_index'] = i
            for change in store.all('batch_item'):
                row = self.records.get(change['batch'])
                if row and 0 <= change['index'] < len(row['rows']): row['rows'][change['index']].update(change['item'])
            for meta in store.all('batch_meta'):
                if meta['token'] in self.records: self.records[meta['token']]['state'] = meta['state']
        return self.records

    def guard(self, row):
        self.bridge.features.ops[row['provider']].guard(row['_stamp'])
        if self.closed: raise CloudOperationError('程序正在关闭。')

    def save(self, row, item=None):
        store = self.bridge.features.store
        records = self.records_loaded()
        if not row.get('_persisted'):
            clean = {k: v for k, v in row.items() if not k.startswith('_') and k != 'rows'}
            clean['rows'] = [{k:v for k,v in x.items() if not k.startswith('_')} for x in row['rows']]
            store.put('batch', row['token'], clean)
            row['_persisted'] = True
            for i, x in enumerate(row['rows']): x['_index'] = i
        records[row['token']] = row
        if item is not None:
            store.put('batch_item', row['token']+':'+str(item['_index']), {
                'batch':row['token'], 'index':item['_index'], 'item':{k:v for k,v in item.items() if not k.startswith('_')}})
        if row.get('_saved_state') != row['state']:
            store.put('batch_meta', row['token'], {'token':row['token'],'state':row['state']})
            row['_saved_state'] = row['state']

    def counts(self, row):
        result = {}
        for item in row['rows']:
            state = item['state']; result[state] = result.get(state, 0) + 1
        return result

    def public(self, row, offset=0):
        keys = ('source', 'target', 'size', 'directory', 'state', 'message', 'job')
        return {k: row[k] for k in ('token', 'provider', 'kind', 'state', 'title', 'at', 'error')} | {
            'count': len(row['rows']), 'counts': self.counts(row),
            'bytes': sum(x.get('size', 0) for x in row['rows'] if x['state'] != 'skipped'),
            'offset': offset, 'rows': [{k: x[k] for k in keys if k in x} for x in row['rows'][offset:offset+100]]}

    def reconcile(self, row):
        live = {getattr(j, '_desktop_id', ''): j for p in self.bridge.panels.values() for j in p.upload_jobs}
        changed = False
        for item in row['rows']:
            if item.get('job'):
                job = live.get(item['job'])
                saved = self.bridge.features.store.get('transfer', item['job']) if job is None else None
                status = job.state if job else saved.get('status') if saved else item['state']
                message = job.error if job else saved.get('error', '') if saved else item.get('message', '')
                if job is None and status not in ('completed', 'cancelled', 'failed'):
                    status, message = 'paused', '已保留上传任务，请在传输列表继续。'
                if (status, message) != (item['state'], item.get('message', '')):
                    item.update(state=status, message=message); self.save(row,item); changed = True
            elif row['token'] not in self.active and row['state'] == 'running' and item['state'] in ('pending', 'running'):
                item.update(state='uncertain' if item['state'] == 'running' else 'not_started',
                            message='上次操作中断，请核对云端结果。' if item['state'] == 'running' else '上次操作中断，尚未执行。')
                changed = True
                self.save(row,item)
        if row['token'] not in self.active and row['state'] == 'running':
            row['state'] = 'submitted' if any(x.get('job') for x in row['rows']) else 'finished'; changed = True
        if changed: self.save(row)
        return row

    def summaries(self):
        if not self.bridge.features: return []
        result = []
        for row in sorted(self.records_loaded().values(), key=lambda r: r['at'], reverse=True):
            ops = self.bridge.features.ops.get(row['provider'])
            if not ops or ops.identity_stamp != ops.stamp() or ops.identity != row['account']: continue
            row = self.reconcile(row)
            result.append({k: v for k, v in self.public(row).items() if k != 'rows'})
            if len(result) == 30: break
        return result

    async def action(self, d):
        name, panel, ops, account, stamp = await self.bridge.productivity.owner(d)
        self.bridge.features.accounts[name] = account
        action = d['action']
        if action == 'batch_result':
            row = self.records_loaded().get(d.get('token'))
            if not row or row['provider'] != name or row['account'] != account: raise CloudOperationError('请使用原账号查看操作记录。')
            offset = d.get('offset', 0)
            if type(offset) is not int or offset < 0: raise CloudOperationError('页码无效。')
            return self.public(self.reconcile(row), offset)
        if action == 'batch_prepare':
            if d.get('context') != self.bridge.context(panel) or panel.browser_busy: raise CloudOperationError('目录已变化，请重新选择。')
            if sum(not t.done() for t in self.tasks.values()) >= 2: raise CloudOperationError('请先完成正在处理的批量操作。')
            self.plans = {k: v for k, v in self.plans.items() if time.monotonic()-v['_time'] < 600}
            self.tasks = {k: t for k, t in self.tasks.items() if not t.done() or k in self.plans}
            if len(self.plans) >= 12: raise CloudOperationError('待确认任务过多，请先关闭已有任务。')
            kind = d.get('kind'); mode = policy(d.get('policy', 'rename'))
            if kind not in ('upload', 'move', 'rename'): raise CloudOperationError('批量操作无效。')
            token = secrets.token_hex(24)
            row = {'token': token, 'provider': name, 'account': account, 'kind': kind, 'state': 'scanning',
                   'title': {'upload': '批量上传', 'move': '批量移动', 'rename': '批量重命名'}[kind],
                   'at': time.time(), '_time': time.monotonic(), '_stamp': stamp, 'rows': [], 'error': '', 'policy': mode}
            if kind == 'upload':
                destination = panel.path
                if d.get('target_id') is not None:
                    folder=next((x for x in panel.browser_rows if x.id==d['target_id'] and x.is_dir),None)
                    if folder is None or folder.parent!=panel.path:raise CloudOperationError('目标文件夹已变化，请重新拖入。')
                    destination=folder.path
                paths = self.bridge.selected_paths(d.get('selection'), 'upload')
                task = lambda: self.plan_upload(row, paths, destination, ops)
            else:
                ids = d.get('ids')
                if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(x, str) for x in ids): raise CloudOperationError('请选择当前目录的项目。')
                by_id = {x.id: x for x in panel.browser_rows}
                items = [by_id[key] for key in dict.fromkeys(ids) if key in by_id]
                if len(items) != len(set(ids)): raise CloudOperationError('选择已变化，请重新选择。')
                target = cloud_path(d.get('target', panel.path)) if kind == 'move' else panel.path
                task = lambda: self.plan_cloud(row, items, target, d.get('rename', {}), ops)
            self.plans[token] = row
            self.tasks[token] = asyncio.create_task(self.prepare(row, task))
            return self.public(row)
        row = self.plans.get(d.get('token'))
        if not row or row['provider'] != name or row['account'] != account or time.monotonic()-row['_time'] > 600:
            raise CloudOperationError('确认已过期，请重新预览。')
        self.guard(row)
        if action == 'batch_status':
            offset = d.get('offset', 0)
            if type(offset) is not int or offset < 0: raise CloudOperationError('页码无效。')
            return self.public(row, offset)
        if action == 'batch_cancel':
            if row['state'] == 'running': raise CloudOperationError('已提交的操作正在执行，请查看结果。')
            self.tasks[row['token']].cancel(); self.plans.pop(row['token'], None); return {}
        if action != 'batch_commit' or row['state'] != 'ready': raise CloudOperationError('清单未就绪或已经提交。')
        row['state'] = 'running'; self.save(row); self.active.add(row['token'])
        self.tasks[row['token']] = asyncio.create_task(self.execute(row, panel, ops))
        self.plans.pop(row['token'])
        return self.public(row)

    async def prepare(self, row, task):
        try:
            await task(); self.guard(row); row['state'] = 'ready'
        except asyncio.CancelledError: raise
        except Exception as error:
            row.update(state='failed', error=str(error) if isinstance(error, (CloudOperationError, OSError)) else '扫描未完成，请重新选择。')

    async def plan_upload(self, row, paths, target, ops):
        row['title'] = '上传 · ' + (Path(paths[0]).name if len(paths)==1 else str(len(paths))+' 个项目')
        stop = threading.Event()
        try: local = await asyncio.to_thread(local_tree, paths, stop)
        finally: stop.set()
        self.guard(row)
        parents = {'': target}; existing = {target}; occupied = {}
        for item in local:
            rel = PurePosixPath(item['rel']); parent_rel = str(rel.parent); parent_rel = '' if parent_rel == '.' else parent_rel
            parent = parents.get(parent_rel)
            result = dict(item, state='pending', message='')
            if parent is None:
                result.update(target='', state='skipped', message='上层文件夹已跳过。'); row['rows'].append(result)
                if item['directory']: parents[item['rel']] = None
                continue
            if parent not in occupied:
                occupied[parent] = {x.name.casefold() for x in await ops.list(parent, limit=LIMIT)} if parent in existing else set()
                self.guard(row)
            name = choose_name(rel.name, occupied[parent], row['policy'], item['directory'])
            result['target'] = parent.rstrip('/') + '/' + (name or rel.name)
            if name is None: result.update(state='skipped', message='目标已有同名项目。')
            elif name != rel.name: result['message'] = '保留两份，使用新名称。'
            if parent_rel == '' and name:
                self.bridge.productivity.overlap(row['provider'], row['account'], [result['target']])
            row['rows'].append(result)
            if item['directory']: parents[item['rel']] = result['target'] if name else None
            if len(row['rows']) % 100 == 0: await asyncio.sleep(0)

    async def plan_cloud(self, row, items, target, options, ops):
        occupied = {x.name.casefold() for x in await ops.list(target, limit=LIMIT)}; self.guard(row)
        source_names = {}
        for index, item in enumerate(items):
            name = file_name(renamed(item.name, options, index, item.is_dir)) if row['kind'] == 'rename' else item.name
            result = {'source': item.path, 'target': target.rstrip('/')+'/'+name, 'size': item.size,
                      'directory': item.is_dir, 'state': 'pending', 'message': '', '_item': item}
            if result['target'] == item.path: result.update(state='skipped', message='位置和名称未变化。')
            else:
                if item.is_dir and (target == item.path or target.startswith(item.path+'/')): raise CloudOperationError('不能移动到自身或子文件夹。')
                if row['provider']=='quark' and row['kind']=='move' and target!=item.parent and row['policy']=='rename' and name.casefold() in occupied:
                    # The intermediate rename must also be free at the source.
                    if item.parent not in source_names:
                        source_names[item.parent] = {x.name.casefold() for x in await ops.list(item.parent, limit=LIMIT)}
                        self.guard(row)
                    chosen = choose_name(name, occupied | source_names[item.parent], row['policy'], item.is_dir)
                    occupied.add(chosen.casefold()); source_names[item.parent].add(chosen.casefold())
                else: chosen = choose_name(name, occupied, row['policy'], item.is_dir)
                if chosen is None: result.update(state='skipped', message='目标已有同名项目。')
                else:
                    result['target'] = target.rstrip('/')+'/'+chosen
                    if chosen != name: result['message'] = '保留两份，使用新名称。'
            self.bridge.productivity.overlap(row['provider'], row['account'], [item.path, result['target']])
            row['rows'].append(result)

    async def execute(self, row, panel, ops):
        blocked = []
        occupied = {}
        try:
            async with ops.lock:
                for item in row['rows']:
                    if item['state'] == 'skipped': continue
                    self.guard(row)
                    if any(item['target'].startswith(x+'/') for x in blocked):
                        item.update(state='not_started', message='上层文件夹未创建，未上传。'); self.save(row,item); continue
                    item['state'] = 'running'; self.save(row,item)
                    try:
                        target = PurePosixPath(item['target']); parent = str(target.parent)
                        self.bridge.productivity.overlap(row['provider'], row['account'], [item['target']] + ([] if row['kind']=='upload' else [item['source']]))
                        self.guard(row)
                        if row['kind'] == 'upload':
                            local = Path(item['source']); ordinary(local)
                            if parent not in occupied:
                                occupied[parent] = {x.name.casefold() for x in await ops.list(parent, limit=LIMIT)}
                            queued = {j.item.path.casefold() for j in panel.upload_jobs if j.state not in ('completed','cancelled')}
                            if target.name.casefold() in occupied[parent] or item['target'].casefold() in queued:
                                item.update(state='skipped', message='预览后出现同名项目，已跳过，请重新选择。')
                                if item['directory']: blocked.append(item['target'])
                                self.save(row,item); continue
                            self.guard(row)
                            if item['directory']:
                                if not local.is_dir(): raise CloudOperationError('本地文件夹已变化。')
                                await ops.mkdir(parent, target.name, exclusive=True); item.update(state='completed', message='文件夹已创建。')
                                occupied.setdefault(item['target'], set())
                            else:
                                if asdict(Fingerprint.read(local)) != item['fingerprint']: raise CloudOperationError('预览后本地文件已变化，请重新选择。')
                                api, _, owner, current = panel.upload_snapshot()
                                job = UploadJob(local, row['provider'], parent, (BaiduUploader if row['provider']=='baidu' else QuarkUploader)(api),
                                                owner=owner, current_owner=current, remote_name=target.name,
                                                progress=lambda done,total: self.progress(panel))
                                if asdict(job.fingerprint) != item['fingerprint']: raise CloudOperationError('本地文件已变化。')
                                job._desktop_id = secrets.token_hex(12); job._download_group = row['title']; job._group_id = row['token']; job._persistent_account = row['account']
                                panel.upload_jobs.append(job); item.update(state='queued', job=job._desktop_id, message='已加入上传队列。')
                                self.bridge.features.capture(row['provider'], force=True)
                            occupied[parent].add(target.name.casefold())
                        else:
                            source = item['_item']
                            for job in [*panel.jobs, *panel.upload_jobs]:
                                if job.state not in ('completed','cancelled') and (job.item.path==source.path or job.item.path.startswith(source.path+'/')):
                                    raise CloudOperationError('所选项目仍有传输任务，请先完成或取消。')
                            if row['provider']=='quark' and parent!=source.parent and target.name!=source.name:
                                # Quark requires two requests. Persist the intermediate location before moving.
                                intermediate=await ops.relocate(source, source.parent, target.name)
                                item.update(message='源项目已改名，正在移动。', current=intermediate.path); self.save(row,item)
                                source=intermediate
                            await ops.relocate(source, parent, target.name)
                            item.update(state='completed', message='操作完成。')
                    except asyncio.CancelledError:
                        item.update(state='uncertain', message='操作中断，请核对目标位置。'); self.save(row,item); raise
                    except Exception as error:
                        item.update(state='failed', message=(str(error) if isinstance(error,CloudOperationError) else '操作未完成，请核对结果后重试。') + (' 源项目已改名为 '+item['current'] if item.get('current') else ''))
                        if item['directory'] and row['kind']=='upload': blocked.append(item['target'])
                    self.save(row,item)
                    if row['kind']=='upload': panel._uploads_changed(); panel._ensure_upload_worker()
                    await asyncio.sleep(0)
        except (Exception, asyncio.CancelledError):
            for item in row['rows']:
                if item['state']=='pending':
                    item.update(state='not_started', message='操作停止，尚未执行。'); self.save(row,item)
        finally:
            row['state']='submitted' if row['kind']=='upload' else 'finished'; self.save(row); self.active.discard(row['token'])
            if row['kind']=='upload': panel._uploads_changed(); panel._ensure_upload_worker()
            self.bridge.features.account_status.invalidate(row['provider'])
            if not self.closed:
                try: await panel.load_directory(panel.path,panel.page_index)
                except Exception: pass

    async def progress(self, panel): panel._uploads_changed()

    async def close(self):
        self.closed=True
        for task in self.tasks.values(): task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
