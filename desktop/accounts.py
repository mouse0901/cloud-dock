"""Explicit account switching; all durable tasks retain their original owner."""
import asyncio
from app.session_vault import SessionStoreError
from app.cloud_operations import CloudOperationError

class Accounts:
    def __init__(self,bridge):self.b=bridge;self.lock=asyncio.Lock();self.switching=False
    async def action(self,d):
        catalog=self.b.profiles;name=d.get('provider');a=d['action']
        if a=='profiles_list':return catalog.public()
        catalog.provider(name)
        async with self.lock:
            if a=='profiles_rename':catalog.rename(name,d.get('id'),d.get('name'));return catalog.public()
            if a=='profiles_remove' and d.get('id')!=catalog.provider(name)['active']:
                catalog.remove(name,d.get('id'));return catalog.public()
            if a not in ('profiles_add','profiles_switch','profiles_remove'):raise CloudOperationError('账号操作无效。')
            if self.b.update_busy():raise CloudOperationError('请先暂停传输、跨盘复制和同步，并等待正在提交的操作完成，再切换账号。')
            if a=='profiles_switch':
                catalog.row(name,d.get('id'))
                if d['id']==catalog.provider(name)['active']:return catalog.public()
            if a=='profiles_add':
                catalog.name(d.get('name'))
                if len(catalog.provider(name)['rows'])>=5:raise SessionStoreError('每家网盘最多保存 5 个账号。')
            self.switching=True
            try:
                p=self.b.panels[name];f=self.b.features
                for r in f.sync.rules():
                    if r['provider']==name:
                        if r['enabled']:await f.sync.toggle(r['id'],False)
                        async with f.sync.lock(r['id']):pass
                p._queue_hold=True;p.save_remembered();f.capture(name,force=True)
                # Detach only inactive jobs after persistence; p.cancel must not erase their checkpoints.
                p.jobs.clear();p.upload_jobs.clear();p._closing=True
                try:await p.cancel()
                finally:p._closing=False;p._queue_hold=False
                f.accounts.pop(name,None);f.account_status.invalidate(name)
                f.thumb_items={k:v for k,v in f.thumb_items.items() if v[0]!=name}
                previous=catalog.provider(name)['active']
                if a=='profiles_add':key=catalog.add(name,d['name'])['id']
                elif a=='profiles_remove':
                    rows=[r for r in catalog.provider(name)['rows'] if r['id']!=previous]
                    key=rows[0]['id'] if rows else catalog.add(name,'默认账号')['id']
                else:key=d['id']
                catalog.select(name,key)
                if a=='profiles_remove':catalog.remove(name,previous)
                # Restore asynchronously, same as a cold start. A newly added slot stays logged out.
                await p.startup(saved_only=True)
                return catalog.public()
            except SessionStoreError:raise
            finally:self.switching=False
