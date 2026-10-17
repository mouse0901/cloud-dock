"""Optional account metadata. Authentication and transfers never depend on it.

Only quota is serialized to the desktop. Membership remains in process memory;
missing or unfamiliar fields are unknown, never implicitly a free account.
"""
from __future__ import annotations
import asyncio
import json
import time
from app.cloud_operations import CloudOperationError


def quota_values(total, used):
    def number(value):
        if isinstance(value, str) and value.isascii() and value.isdigit():
            value = int(value)
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise ValueError('Invalid capacity')
        return value
    total, used = number(total), number(used)
    # A downgraded account may legitimately be over quota.
    return {'total': total, 'used': used, 'free': max(0, total-used), 'over': max(0, used-total)}


def membership(provider, body):
    if provider == 'quark':
        value = body.get('member_type')
        return ('free' if value == 'NORMAL' else 'paid' if value in
                {'SUPER_VIP', 'EXP_SVIP', 'Z_VIP', 'VIP'} else 'unknown')
    flags = [body.get(k) for k in ('is_vip', 'is_svip', 'is_evip')]
    if any(type(x) in (int, str) and str(x) == '1' for x in flags):
        return 'paid'
    if all(type(x) in (int, str) and str(x) == '0' for x in flags):
        return 'free'
    return 'unknown'


class AccountStatus:
    TTL = 600
    RETRY = 60

    def __init__(self, ops):
        self.ops = ops
        self.entries = {}
        self.tasks = {}
        self.closed = False

    def entry(self, name):
        stamp = self.ops[name].stamp()
        old = self.entries.get(name)
        if old is None or old['stamp'] != stamp:
            old = self.entries[name] = {'stamp': stamp, 'quota': None,
                'member': 'unknown', 'checked': 0, 'attempt': 0,
                'failed': False, 'dirty': False}
            task = self.tasks.get(name)
            if task and not task.done(): task.cancel()
            self.tasks.pop(name, None)
        return old

    def schedule(self, name, force=False):
        entry = self.entry(name)
        task = self.tasks.get(name)
        if self.closed or not entry['stamp'] or task and not task.done(): return
        age = time.monotonic() - entry['attempt']
        interval = self.RETRY if entry['failed'] or entry['dirty'] else self.TTL
        if entry['attempt'] and age < (5 if force else interval): return
        self.tasks[name] = asyncio.create_task(self.refresh(name, entry))

    def invalidate(self, name):
        self.entry(name)['dirty'] = True
        self.schedule(name, force=True)

    async def refresh(self, name, entry):
        ops = self.ops[name]
        entry['attempt'] = time.monotonic()
        entry['member'] = 'unknown'
        entry['dirty'] = False
        try:
            async with asyncio.timeout(12):
                api = ops.api
                if name == 'baidu':
                    response = await api.request('https://pan.baidu.com/api/quota',
                        {**api._web_params(), 'checkfree': 1, 'checkexpire': 1})
                    body = api.parse_response(response, 'quota')
                    api._check_service(body, 'quota')
                    quota = quota_values(body.get('total'), body.get('used'))
                else:
                    body = await api._request('GET', 'https://pan.quark.cn/1/clouddrive/member',
                        'capacity', params={'pr': 'ucpro', 'fr': 'pc'})
                    body = api._success(body, stage='capacity')
                    quota = quota_values(body.get('total_capacity'), body.get('use_capacity'))
                    entry['member'] = membership(name, body)
                ops.guard(entry['stamp'])
                entry.update(quota=quota, checked=time.time(), failed=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            entry['failed'] = True
        # The optional query cannot hide a successfully read capacity.
        if name == 'baidu' and ops.stamp() == entry['stamp']:
            try:
                async with asyncio.timeout(6):
                    response = await api.request('https://pan.baidu.com/api/gettemplatevariable',
                        {**api._web_params(), 'fields': json.dumps(['is_vip', 'is_svip', 'is_evip'])})
                    body = api.parse_response(response, 'account')
                    api._check_service(body, 'account')
                    ops.guard(entry['stamp'])
                    entry['member'] = membership(name, body.get('result', {}))
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    def public(self, name):
        self.schedule(name)
        entry = self.entry(name)
        if not entry['stamp']: return {'state': 'disconnected'}
        task = self.tasks.get(name)
        loading = bool(task and not task.done())
        quota = entry['quota']
        return {'state': ('stale' if entry['failed'] or entry['dirty'] else 'ready') if quota is not None
                else 'loading' if loading else 'unavailable',
                'loading': loading, 'checked': entry['checked'], **(quota or {})}

    def internal_membership(self, name):
        return self.entry(name)['member']

    def check_upload(self, name, required):
        """Best-effort preflight, never substitute a guess for unknown capacity."""
        entry = self.entry(name)
        if entry['quota'] is None or entry['failed'] or entry['dirty'] or time.time()-entry['checked'] > 60:
            self.schedule(name, force=True)
            return
        if required > entry['quota']['free']:
            raise CloudOperationError('网盘剩余空间不足，请释放空间后再上传。')

    async def close(self):
        self.closed = True
        for task in self.tasks.values(): task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
