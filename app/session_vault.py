"""Only Cloud Dock's own login cookies; encrypted with current-user Windows DPAPI."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
from http.cookiejar import Cookie, CookieJar
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

MAX_BYTES = 512 * 1024
MAGIC = b'CloudDockSession1\0'
DOMAINS = {'baidu': 'baidu.com', 'quark': 'quark.cn'}


class SessionStoreError(Exception):
    pass


class SessionConflict(SessionStoreError):
    pass


def crypt(data, provider, *, decrypt=False):
    if os.name != 'nt':
        raise SessionStoreError('当前系统不支持 Windows 登录信息加密')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    def blob(value):
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        return Blob(len(value), buffer), buffer
    source, source_buffer = blob(data)
    entropy, entropy_buffer = blob(('CloudDock/session/v1/' + provider).encode())
    result = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    operation = api.CryptUnprotectData if decrypt else api.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN only. Never use LOCAL_MACHINE: the Windows user owns this blob.
    if not operation(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(result)):
        raise SessionStoreError('无法使用当前 Windows 账号读取或保存登录信息')
    try:
        if result.size > MAX_BYTES:
            raise SessionStoreError('登录信息大小异常')
        return ctypes.string_at(result.data, result.size)
    finally:
        if result.data:
            ctypes.memset(result.data, 0, result.size)
            kernel.LocalFree(result.data)


def allowed(cookie, provider):
    domain = cookie.domain.lower().lstrip('.')
    suffix = DOMAINS[provider]
    return (domain == suffix or domain.endswith('.' + suffix)) and not cookie.is_expired()


def encode_cookies(jar, provider):
    fields = ('version','name','value','port','port_specified','domain','domain_specified',
              'domain_initial_dot','path','path_specified','secure','expires','discard','comment','comment_url','rfc2109')
    rows = []
    for cookie in jar:
        if allowed(cookie, provider):
            row = {name: getattr(cookie,name) for name in fields}
            row['rest'] = {k:v for k,v in cookie._rest.items() if k.lower() in ('httponly','samesite')}
            rows.append(row)
    return rows


def decode_cookies(rows, provider):
    if not isinstance(rows,list) or len(rows)>256:
        raise ValueError()
    jar = CookieJar()
    for row in rows:
        if not isinstance(row,dict): raise ValueError()
        for field in ('name','value','domain','path'):
            value = row.get(field)
            if not isinstance(value,str) or len(value)>16384 or any(ord(c)<32 or ord(c)==127 for c in value):
                raise ValueError()
        if not re.fullmatch(r"[^\s=;,]+",row['name']) or not row['path'].startswith('/'):
            raise ValueError()
        for field in ('port_specified','domain_specified','domain_initial_dot','path_specified','secure','discard','rfc2109'):
            if type(row.get(field)) is not bool: raise ValueError()
        if type(row.get('version')) is not int or row['version'] not in (0,1): raise ValueError()
        if row.get('expires') is not None and type(row['expires']) is not int: raise ValueError()
        rest = row.get('rest')
        if not isinstance(rest,dict) or any(k.lower() not in ('httponly','samesite') or v is not None and not isinstance(v,str) for k,v in rest.items()):
            raise ValueError()
        cookie = Cookie(**row)
        if allowed(cookie,provider): jar.set_cookie(cookie)
    return jar


class SessionVault:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else __import__('app.runtime_paths',fromlist=['data_dir']).data_dir()/'sessions'
        self.revisions = {}
        self.preferences = {}

    def _path(self, provider):
        if provider not in DOMAINS: raise SessionStoreError('不支持的网盘账号')
        return self.directory / (provider + '.session')

    def _read(self, provider):
        path = self._path(provider)
        if not path.exists(): return None
        if path.is_symlink() or path.stat().st_size > MAX_BYTES:
            raise SessionStoreError('保存的登录信息无效，请重新登录')
        return path.read_bytes()

    @staticmethod
    def _revision(data):
        return hashlib.sha256(data).digest() if data is not None else None

    @contextmanager
    def _lock(self, provider):
        import msvcrt
        self.directory.mkdir(parents=True,exist_ok=True)
        with self._path(provider).with_suffix('.lock').open('a+b') as handle:
            if handle.tell() == 0: handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                raise SessionStoreError('其他窗口正在更新登录信息，请稍后重试') from None
            try: yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)

    def load(self, provider):
        try:
            raw = self._read(provider)
            self.revisions[provider] = self._revision(raw)
            if raw is None:
                self.preferences[provider] = True
                return CookieJar()
            if not raw.startswith(MAGIC): raise ValueError()
            body = json.loads(crypt(raw[len(MAGIC):],provider,decrypt=True))
            if body.get('version') != 1 or body.get('provider') != provider: raise ValueError()
            if type(body.get('remember', True)) is not bool: raise ValueError()
            self.preferences[provider] = body.get('remember', True)
            return decode_cookies(body['cookies'],provider)
        except SessionStoreError: raise
        except (OSError,ValueError,TypeError,KeyError,RecursionError,AttributeError):
            raise SessionStoreError('保存的登录信息不可用，请重新登录') from None

    def _write(self, provider, rows, *, replace=False, remember=True):
        body = json.dumps({'version':1,'provider':provider,'cookies':rows,'remember':remember},separators=(',',':')).encode()
        if len(body)>MAX_BYTES or len(rows)>256: raise SessionStoreError('登录信息过大，未保存')
        encrypted = MAGIC + crypt(body,provider)
        try:
            with self._lock(provider):
                if not replace and self._revision(self._read(provider)) != self.revisions.get(provider):
                    raise SessionConflict('其他窗口已更新登录信息，本窗口未覆盖它')
                target = self._path(provider)
                temp = self.directory / (provider + '.' + uuid4().hex + '.tmp')
                try:
                    with temp.open('xb') as output:
                        output.write(encrypted)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temp,target)
                    self.revisions[provider] = self._revision(encrypted)
                    self.preferences[provider] = remember
                finally:
                    if temp.exists(): temp.unlink()
        except OSError:
            raise SessionStoreError('无法保存登录信息，请检查本地目录权限') from None

    def save(self, provider, jar):
        self._write(provider,encode_cookies(jar,provider))

    def forget(self, provider, *, remember=True, replace=True):
        # A credential-free tombstone prevents an older open window from
        # silently writing a logged-out account back when it closes.
        self._write(provider,[],replace=replace,remember=remember)
