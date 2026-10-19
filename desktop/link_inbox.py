"""Private local library of deliberately opened or saved cloud shares."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from app.cloud_operations import CloudOperationError
from app.session_vault import SessionStoreError, crypt
from app.share_links import parse_links


class LinkInbox:
    PAGE_SIZE = 50

    def __init__(self, bridge):
        self.b = bridge
        self.undo = None

    @property
    def store(self):
        return self.b.features.store

    @property
    def search_key(self):
        if not hasattr(self, '_search_key'):
            record = self.store.get('link_search_key', 'v1')
            if record:
                try:self._search_key = crypt(base64.b64decode(record['sealed']), 'link-search-key', decrypt=True)
                except Exception:raise CloudOperationError('当前 Windows 账号无法读取链接库搜索索引。') from None
            else:
                self._search_key = secrets.token_bytes(32)
                sealed = crypt(self._search_key, 'link-search-key')
                self.store.put('link_search_key', 'v1', {'sealed': base64.b64encode(sealed).decode()})
        return self._search_key

    def tokens(self, value):
        value = str(value).casefold()
        return {hmac.new(self.search_key, value[i:i+3].encode(), hashlib.sha256).digest()[:16]
                for i in range(max(0, len(value)-2))}

    def index_terms(self, row):
        fields = [row.get('title', ''), row.get('url', ''), row.get('note', '')]
        fields.extend(row.get('tags', []) if isinstance(row.get('tags'), list) else [])
        return set().union(*(self.tokens(value) for value in fields))

    def ensure_index(self):
        for encrypted in self.store.unindexed_links():
            row = self.decode(encrypted)
            if row.get('unavailable'):
                self.store.put_link(encrypted['id'], encrypted, ())
            else:self.put(row)

    @staticmethod
    def identity(link):
        url = link['url'].split('?')[0].split('#')[0].rstrip('/')
        # Keep v1.0.0 IDs stable: provider is already part of the validated URL host.
        return url, hashlib.sha256(url.encode()).hexdigest()

    def decode(self, record):
        try:
            return json.loads(crypt(base64.b64decode(record['sealed']), 'link-inbox', decrypt=True))
        except Exception:
            return {'id': record['id'], 'unavailable': True, 'at': record.get('at', 0),
                    'title': '此记录无法由当前 Windows 账号解密'}

    def get(self, key):
        record = self.store.get('link_inbox', key)
        return self.decode(record) if record else None

    def rows(self):
        return sorted((self.decode(r) for r in self.store.all('link_inbox')),
                      key=lambda r: (bool(r.get('pinned')), r.get('at', 0)), reverse=True)

    def put(self, row):
        sealed = base64.b64encode(crypt(json.dumps(row, ensure_ascii=False).encode(), 'link-inbox')).decode()
        self.store.put_link(row['id'], {'id': row['id'], 'sealed': sealed,
                                        'at': row.get('at', 0), 'pinned': bool(row.get('pinned')),
                                        'provider': row.get('provider')}, self.index_terms(row))
        return row

    def save(self, link, *, title=None, note=None, tags=None, path=None, page=None,
             status=None, opening=False, source_id=None, pinned=None, replace_password=False):
        url, key = self.identity(link)
        previous = self.get(source_id or key)
        if previous and previous.get('unavailable'):
            raise CloudOperationError('旧记录无法解密，请先核对 Windows 账号。')
        if source_id and source_id != key and self.get(key):
            raise CloudOperationError('这个分享链接已在链接库中，请先核对两条记录再合并。')
        now = time.time()
        row = dict(previous or {})
        title_source = row.get('title_source')
        if not title_source:
            title_source = 'user' if row.get('title') and row.get('title') != row.get('url') else 'url'
        row.update(id=key, url=url, provider=link['provider'],
                   password=link.get('password', '') if replace_password else link.get('password') or row.get('password', ''),
                   title=title if title is not None else row.get('title') or url,
                   note=note if note is not None else row.get('note', ''),
                   tags=tags if tags is not None else row.get('tags', []),
                   path=path if path is not None else row.get('path', '/'),
                   page=page if page is not None else row.get('page', 0),
                   created_at=row.get('created_at', row.get('at', now)),
                   at=now if opening or not previous else row.get('at', now),
                   opened=row.get('opened', 0) + (1 if opening else 0),
                   status=status if status is not None else row.get('status', '未检查'),
                   pinned=pinned if pinned is not None else row.get('pinned', False))
        row['title_source'] = 'user' if title is not None else title_source
        self.put(row)
        if source_id and source_id != key:
            self.store.remove('link_inbox', source_id)
        return row

    def suggest_title(self, key, value):
        row = self.get(key)
        if not row or row.get('unavailable') or row.get('title_source') == 'user':
            return
        if not isinstance(value, str) or not value.strip():
            return
        row['title'] = value.strip()[:200]
        row['title_source'] = 'share'
        self.put(row)

    def mark(self, key, status):
        row = self.get(key)
        if row and not row.get('unavailable'):
            row['status'] = status
            self.put(row)

    def list_page(self, page, query='', provider='all'):
        if type(page) is not int or not 0 <= page < 1000000:
            raise CloudOperationError('页码无效。')
        if not isinstance(query, str) or len(query) > 200 or provider not in ('all', 'baidu', 'quark'):
            raise CloudOperationError('筛选条件无效。')
        needle = query.casefold().strip()
        if needle or provider != 'all':
            self.ensure_index()
            if not needle:
                total = self.store.count_provider('link_inbox', provider)
                records = self.store.page_provider('link_inbox', provider, page*self.PAGE_SIZE, self.PAGE_SIZE)
                return {'rows': [self.decode(r) for r in records], 'total': total, 'page': page}
            if len(needle) >= 3:
                ids = self.store.search_link_ids(self.tokens(needle))
                candidates = [self.get(key) for key in ids]
            else:
                candidates = self.rows()
            filtered = [r for r in candidates if r and (provider == 'all' or r.get('provider') == provider)
                        and any(needle in str(r.get(k, '')).casefold() for k in ('title', 'url', 'note', 'tags'))]
            filtered.sort(key=lambda r: (bool(r.get('pinned')), r.get('at', 0)), reverse=True)
            return {'rows': filtered[page*self.PAGE_SIZE:(page+1)*self.PAGE_SIZE],
                    'total': len(filtered), 'page': page}
        total = self.store.count('link_inbox')
        records = self.store.page('link_inbox', page*self.PAGE_SIZE, self.PAGE_SIZE)
        return {'rows': [self.decode(r) for r in records], 'total': total, 'page': page}

    async def action(self, d):
        if not self.b.features:
            raise CloudOperationError('工作空间正在准备。')
        a = d['action']
        if a == 'inbox_preferences':
            if type(d.get('auto_save')) is not bool:
                raise CloudOperationError('链接保存设置无效。')
            previous = self.b.workspace.settings.get('link_auto_save', True)
            self.b.workspace.settings['link_auto_save'] = d['auto_save']
            try:await self.b.workspace.save()
            except Exception:
                self.b.workspace.settings['link_auto_save'] = previous
                raise
            return {'auto_save': d['auto_save']}
        if a == 'inbox_list':
            return self.list_page(d.get('page', 0), d.get('query', ''), d.get('provider', 'all'))
        if a == 'inbox_pin':
            key = d.get('id')
            row = self.get(key) if isinstance(key, str) else None
            if not row or row.get('unavailable') or type(d.get('pinned')) is not bool:
                raise CloudOperationError('置顶操作无效。')
            row['pinned'] = d['pinned']
            self.put(row)
            return row
        if a in ('inbox_remove', 'inbox_remove_batch'):
            keys = [d.get('id')] if a == 'inbox_remove' else d.get('ids')
            if not isinstance(keys, list) or not keys or len(keys) > 1000 or any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)) or any(not self.get(key) for key in keys):
                raise CloudOperationError('请选择有效的链接记录。')
            records = [self.store.get('link_inbox', key) for key in keys]
            for key in keys:self.store.remove('link_inbox', key)
            self.undo = (records, time.monotonic() + 30)
            return {}
        if a == 'inbox_undo':
            if not self.undo or time.monotonic() > self.undo[1]:
                raise CloudOperationError('撤销时间已过。')
            records = self.undo[0]
            if any(self.store.get('link_inbox', record['id']) for record in records):
                raise CloudOperationError('原位置已有新的链接记录。')
            for record in records:self.store.put('link_inbox', record['id'], record)
            self.undo = None
            return {}
        if a == 'inbox_clear':
            self.store.remove_kind('link_inbox')
            self.undo = None
            return {}
        if a in ('inbox_add', 'inbox_edit', 'inbox_record'):
            links = parse_links(d.get('text', ''))
            if len(links) != 1:
                raise CloudOperationError('一次保存一条有效分享链接。')
            link = links[0]
            password = d.get('password', link['password'])
            title, note, tags = d.get('title'), d.get('note'), d.get('tags')
            if not isinstance(password, str) or password and not re.fullmatch('[A-Za-z0-9]{4,8}', password):
                raise CloudOperationError('提取码应为 4–8 位字母或数字。')
            if title is not None and (not isinstance(title, str) or len(title) > 200 or not title.strip()):
                raise CloudOperationError('名称不能为空且最多 200 个字。')
            if note is not None and (not isinstance(note, str) or len(note) > 2000):
                raise CloudOperationError('备注最多 2000 个字。')
            if tags is not None and (not isinstance(tags, list) or len(tags) > 10 or
                                     any(not isinstance(x, str) or len(x) > 24 for x in tags)):
                raise CloudOperationError('标签格式无效。')
            source_id = d.get('id') if a == 'inbox_edit' else None
            if source_id is not None and not self.get(source_id):
                raise CloudOperationError('原链接记录不存在。')
            link['password'] = password
            try:
                target_id = self.identity(link)[1]
                if d.get('merge') is True and source_id and source_id != target_id:
                    source, target = self.get(source_id), self.get(target_id)
                    if not target or source.get('unavailable') or target.get('unavailable'):
                        raise CloudOperationError('合并目标无法读取。')
                    merged = dict(target)
                    merged.update(title=title.strip() if title is not None else target.get('title'),
                                  note=note if note is not None else target.get('note', ''),
                                  tags=tags if tags is not None else target.get('tags', []),
                                  password=password or target.get('password') or source.get('password', ''),
                                  pinned=bool(source.get('pinned') or target.get('pinned') or d.get('pinned')),
                                  opened=source.get('opened', 0) + target.get('opened', 0),
                                  created_at=min(source.get('created_at', source.get('at', 0)),
                                                 target.get('created_at', target.get('at', 0))))
                    if source.get('at', 0) > target.get('at', 0):
                        merged.update(at=source['at'], path=source.get('path', '/'), page=source.get('page', 0))
                    self.put(merged)
                    self.store.remove('link_inbox', source_id)
                    return merged
                return self.save(link, title=title, note=note, tags=tags, source_id=source_id,
                                 opening=a == 'inbox_record', status='打开未完成' if a == 'inbox_record' else None,
                                 replace_password=a == 'inbox_edit',
                                 pinned=d.get('pinned') if type(d.get('pinned')) is bool else None)
            except SessionStoreError:
                raise CloudOperationError('无法加密保存链接，请检查当前 Windows 用户权限。') from None
        raise CloudOperationError('链接库操作无效。')
