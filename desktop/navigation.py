"""Account-bound paged navigation and locating retained search results."""
import asyncio
from app.cloud_operations import CloudOperationError
from desktop.productivity import cloud_path


class BrowserNavigation:
    def __init__(self, bridge):
        self.bridge = bridge
        self.locks = {}

    async def action(self, d):
        name, p, ops, account, stamp = await self.bridge.productivity.owner(d)
        generation = str(p.generation)
        if d.get('session') != generation:
            raise CloudOperationError('账号已变化，请重新打开这个位置。')
        async with self.locks.setdefault(name, asyncio.Lock()):
            ops.guard(stamp)
            page = d.get('page', 0)
            if type(page) is not int or not 0 <= page < 1000:
                raise CloudOperationError('页码无效。')
            path = cloud_path(d.get('path', '/'))
            selected = ''
            if d['action'] == 'browse_locate':
                row = self.bridge.productivity.jobs.get(d.get('token'))
                if not row or row['kind'] != 'search' or row['provider'] != name or not self.bridge.productivity.valid(row):
                    raise CloudOperationError('搜索结果已过期，请重新搜索。')
                found = next((x for x in row['rows'] if x['id'] == d.get('id') and x['parent'] == path), None)
                if not found:
                    raise CloudOperationError('搜索结果已变化，请重新搜索。')
                async with asyncio.timeout(60):
                    for page in range(1000):
                        ops.guard(stamp)
                        files = await ops.api._fetch_files(path, page)
                        ops.guard(stamp)
                        if any(x.id == found['id'] and x.path == found['path'] for x in files):
                            selected = found['id']; break
                        if len(files) < 50: break
                if not selected:
                    raise CloudOperationError('文件已移动或删除，请重新搜索。')
            await p.load_directory(path, page)
            ops.guard(stamp)
            if getattr(p, 'directory_load_error', ''):
                raise CloudOperationError(p.directory_load_error)
            if str(p.generation) != generation or p.path != path or p.page_index != page or p.browser_busy:
                raise CloudOperationError('目录已变化，请重新打开。')
            if selected and not any(x.id == selected for x in p.browser_rows):
                raise CloudOperationError('目录内容已变化，请重新定位。')
            return {'path': path, 'page': page, 'selected': selected, 'session': generation,
                    'files': self.bridge.files(p), 'context': self.bridge.context(p)}
