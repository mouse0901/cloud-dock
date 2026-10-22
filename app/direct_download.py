"""Account-bound, single-file direct downloads with resumable partial files.

Only opaque, in-memory source tickets contain URLs or credentials. This module
never writes a URL, a cookie, headers, or an account identifier to disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx

from app.domain import CloudFile


@dataclass(frozen=True, repr=False)
class DownloadSource:
    url: str
    size: int
    headers: dict[str, str] = field(default_factory=dict)
    md5: str | None = None
    # Some provider metadata is not a reliable digest of the served bytes.
    # It still pins source identity across resume. Strict is the default.
    md5_is_content: bool = True


class DownloadError(Exception):
    """Safe public errors: messages never include transport exception details."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip(' .')
    if not name:
        name = 'download'
    if name.split('.')[0].upper() in {
        'CON', 'PRN', 'AUX', 'NUL',
        *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)],
    }:
        name = '_' + name
    suffix = Path(name).suffix[:20]
    stem = name[:-len(Path(name).suffix)] if Path(name).suffix else name
    return stem[:140] + suffix


def _known_error_payload(payload) -> bool:
    """Recognize provider error envelopes, not arbitrary user JSON fields.

    A matching upstream checksum takes precedence at the call site. Without a
    checksum, generic keys such as code/errno/success alone prove nothing about
    whether the body is an error or an ordinary downloaded JSON document.
    """
    if not isinstance(payload, dict):
        return False

    def number(value):
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r'-?\d{1,12}', value):
            return int(value)
        return None

    baidu_error = any(number(payload.get(key)) not in (None, 0)
                      for key in ('errno', 'err_no', 'error_code'))
    baidu_envelope = (isinstance(payload.get('request_id'), (str, int))
                      or isinstance(payload.get('show_msg'), str))
    status, code = number(payload.get('status')), number(payload.get('code'))
    quark_error = status is not None and 400 <= status <= 599 and code not in (None, 0)
    return (baidu_error and baidu_envelope) or quark_error

class DirectDownload:
    """One file owned by one session; call run() again after pause/failure.

    pause() and cancel() are asynchronous and wait until the active stream has
    closed. Cancelling run() externally is equivalent to pausing. A cancelled
    transfer is terminal; completed files are never removed by cancel().
    """

    def __init__(
        self, item: CloudFile, destination: str | Path,
        client: httpx.AsyncClient,
        resolve_source: Callable[[CloudFile], Awaitable[DownloadSource]],
        validate_url: Callable[[str], str],
        on_progress: Callable[[int, int], Awaitable[None]], *,
        owner: str | None = None,
        current_owner: Callable[[], str | None] | None = None,
    ):
        if item.is_dir or not item.id or type(item.size) is not int or item.size < 0:
            raise DownloadError('download_item', '请选择有效的单个文件')
        self.item = item
        self.destination = Path(destination).resolve()
        self.client = client
        self.resolve_source = resolve_source
        self.validate_url = validate_url
        self.on_progress = on_progress
        self.state = 'queued'
        self.done = 0
        self.total = item.size
        self.target: Path | None = None
        self.part: Path | None = None
        self.error = ''
        self.warning = ''
        self.checksum_verified = False
        self.speed = 0.0
        self._owner = owner
        self._current_owner = current_owner
        self._task: asyncio.Task | None = None
        self._stop_mode = ''
        self._source_md5: str | None = None
        self._md5_is_content: bool | None = None
        self._validator: tuple[str, str] | None = None
        self._part_identity: tuple[int, int] | None = None
        self._content_type = ''
        self._rejected_status = None
        self._restart_required = False
        self._last_notify = 0.0
        self._started = 0.0
        self._start_done = 0

    @property
    def requires_restart(self):
        return self._restart_required

    def _check_owner(self):
        if self._current_owner is not None and self._current_owner() != self._owner:
            raise DownloadError('download_account', '账号会话已改变，请在原账号下重新创建下载任务')

    def _approved_url(self, value: str) -> str:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != 'https' or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment
                    or parsed.port not in (None, 443)):
                raise ValueError()
            result = self.validate_url(value)
            # A validator cannot rewrite the destination behind our back.
            if result != value:
                raise ValueError()
            return value
        except Exception:
            raise DownloadError('download_address', '下载地址不属于支持的 HTTPS 服务，已停止请求') from None

    def _prepare_paths(self):
        self.destination.mkdir(parents=True, exist_ok=True)
        if self.target is None:
            name = _safe_name(self.item.name)
            candidate = self.destination / name
            number = 1
            while candidate.exists() or candidate.is_symlink():
                candidate = self.destination / f'{Path(name).stem} ({number}){Path(name).suffix}'
                number += 1
            self.target = candidate
            self.part = self.destination / f'.cloud-dock-{uuid4().hex}.part'

    def _check_part(self):
        if self.part is None or not self.part.exists():
            if self._part_identity is not None:
                raise DownloadError('download_partial', '下载临时文件已丢失，请取消任务后重新下载')
            return
        stat = self.part.lstat()
        if (self.part.is_symlink() or not self.part.is_file()
                or self._part_identity != (stat.st_dev, stat.st_ino)
                or stat.st_size != self.done):
            raise DownloadError('download_partial', '下载临时文件已被改动，请取消任务后重新下载')

    async def _notify(self, *, force=False):
        now = time.monotonic()
        elapsed = now - self._started
        self.speed = (self.done - self._start_done) / elapsed if elapsed > 0 else 0.0
        if force or now - self._last_notify >= 0.5:
            self._last_notify = now
            await self.on_progress(self.done, self.total)

    async def _resolve(self) -> DownloadSource:
        self._check_owner()
        self.state = 'resolving'
        try:
            source = await self.resolve_source(self.item)
        except (DownloadError, asyncio.CancelledError):
            raise
        except Exception:
            raise DownloadError('download_source', '暂时无法获取下载地址，请重试') from None
        self._check_owner()
        if (not isinstance(source, DownloadSource) or type(source.size) is not int
                or source.size != self.total):
            raise DownloadError('download_changed', '文件大小已变化，请重新选择文件下载')
        if source.md5 is not None and not re.fullmatch(r'[0-9a-fA-F]{32}', source.md5):
            raise DownloadError('download_metadata', '文件校验信息无效，已停止下载')
        if type(source.md5_is_content) is not bool:
            raise DownloadError('download_metadata', '文件校验信息无效，已停止下载')
        if self._md5_is_content is not None and self._md5_is_content != source.md5_is_content:
            raise DownloadError('download_changed', '文件校验方式已变化，请重新创建下载任务')
        self._md5_is_content = source.md5_is_content
        digest = source.md5.lower() if source.md5 else None
        if self._source_md5 is not None and digest != self._source_md5:
            raise DownloadError('download_changed', '文件校验信息已变化，请重新选择文件下载')
        if digest is not None:
            self._source_md5 = digest
        self._approved_url(source.url)
        return source

    def _request(self, url: str, source: DownloadSource) -> httpx.Request:
        # Only provider-specified public headers are accepted. In particular,
        # ticket/default Cookie and Authorization headers cannot travel to CDNs.
        headers = {'User-Agent': 'CloudDock/0.1', 'Accept-Encoding': 'identity'}
        for key, value in source.headers.items():
            if key.lower() == 'user-agent':
                headers['User-Agent'] = value
            elif key.lower() == 'referer':
                parsed = urlsplit(value)
                if (parsed.scheme != 'https' or not parsed.hostname or parsed.query
                        or parsed.fragment or parsed.username or parsed.password):
                    raise DownloadError('download_headers', '下载请求配置无效')
                headers['Referer'] = value
        if self.done:
            headers['Range'] = f'bytes={self.done}-'
            if self._validator is not None:
                headers['If-Range'] = self._validator[1]
        # Construct a fresh request so client-wide query parameters, basic auth,
        # and raw Cookie defaults cannot leak. CookieJar still enforces domains.
        return httpx.Request('GET', self._approved_url(url), headers=headers,
                             cookies=self.client.cookies,
                             extensions={'timeout': httpx.Timeout(60, connect=10).as_dict()})

    def _check_response(self, response: httpx.Response, offset: int):
        if response.status_code not in (200, 206):
            raise DownloadError('download_http', f'下载失败（HTTP {response.status_code}），请重试')
        if offset and response.status_code != 206:
            raise DownloadError('download_range', '服务端未接受断点续传，请取消任务后重新下载')
        expected = self.total - offset
        if response.status_code == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
            if (not match or tuple(map(int, match.groups())) != (offset, self.total - 1, self.total)):
                raise DownloadError('download_range', '服务端返回的文件分段不一致，已停止写入')
        length = response.headers.get('content-length')
        if length is not None and (not re.fullmatch(r'\d+', length) or int(length) != expected):
            raise DownloadError('download_length', '服务端返回的文件长度不一致，已停止写入')
        if response.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
            raise DownloadError('download_encoding', '服务端返回了不支持的压缩传输，已停止写入')
        if offset and self._validator is not None:
            name, value = self._validator
            if response.headers.get(name) != value:
                raise DownloadError('download_changed', '文件版本已变化，请取消任务后重新下载')
        if not offset:
            etag = response.headers.get('etag')
            if etag and not etag.startswith('W/'):
                self._validator = ('etag', etag)
            elif response.headers.get('last-modified'):
                self._validator = ('last-modified', response.headers['last-modified'])
            self._content_type = response.headers.get('content-type', '').lower()
            if ('text/html' in self._content_type
                    and Path(self.item.name).suffix.lower() not in ('.htm', '.html')):
                raise DownloadError('download_content', '服务端返回了网页，未作为文件保存')

    async def _transfer(self, source: DownloadSource) -> bool:
        """Return False only for an expired ticket (one bounded refresh)."""
        url = source.url
        for redirect_count in range(6):
            self._check_owner()
            request = self._request(url, source)
            response = await self.client.send(request, auth=None, stream=True, follow_redirects=False)
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location or redirect_count == 5:
                        raise DownloadError('download_redirect', '下载地址跳转失败，请重试')
                    url = self._approved_url(urljoin(url, location))
                    continue
                if response.status_code in (401, 403):
                    self._rejected_status = response.status_code
                    return False
                offset = self.done
                self._check_response(response, offset)
                self._check_part()
                self.state = 'downloading'
                mode = 'ab' if self._part_identity is not None else 'xb'
                with self.part.open(mode) as output:
                    stat = os.fstat(output.fileno())
                    identity = (stat.st_dev, stat.st_ino)
                    if self._part_identity is not None and identity != self._part_identity:
                        raise DownloadError('download_partial', '下载临时文件已被改动，已停止写入')
                    self._part_identity = identity
                    async for chunk in response.aiter_bytes():
                        self._check_owner()
                        if self.done + len(chunk) > self.total:
                            self._restart_required = True
                            raise DownloadError('download_size', '下载内容超过文件大小，请取消任务后重新下载')
                        output.write(chunk)
                        self.done += len(chunk)
                        await self._notify()
                    output.flush()
                    os.fsync(output.fileno())
                if self.done != self.total:
                    raise DownloadError('download_incomplete', '下载连接提前结束，可继续下载剩余部分')
                return True
            finally:
                await response.aclose()
        raise DownloadError('download_redirect', '下载地址跳转次数过多')

    async def _verify_and_publish(self) -> Path:
        self._check_owner()
        self._check_part()
        if self.part is None or self.done != self.total or not self.part.exists():
            raise DownloadError('download_size', '下载文件尚未完整，无法完成任务')
        with self.part.open('rb') as data:
            if self._source_md5:
                digest = hashlib.md5()
                while chunk := data.read(1024 * 1024):
                    digest.update(chunk)
                    self._check_owner()
                    # Keep large-file verification cancellable and the UI alive.
                    await asyncio.sleep(0)
                self.checksum_verified = digest.hexdigest() == self._source_md5
                if not self.checksum_verified:
                    if self._md5_is_content:
                        self._restart_required = True
                        raise DownloadError('download_checksum', '文件校验未通过，请取消任务后重新下载')
                    # Some providers publish an MD5-shaped metadata value
                    # that is not specified as the bytes' content digest.
                    # A mismatch is therefore not evidence of corruption.
                    # Size and server-error-payload checks still apply.
            data.seek(0)
            if self.total <= 65536 and not self.checksum_verified:
                try:
                    payload = json.loads(data.read())
                except (ValueError, UnicodeDecodeError):
                    payload = None
                if _known_error_payload(payload):
                    self._restart_required = True
                    raise DownloadError('download_content', '服务端返回了错误数据，未作为文件保存')
        # Windows rename refuses an existing destination. On POSIX hard-linking
        # provides the same no-overwrite atomic publication guarantee.
        if os.name == 'nt':
            self.part.rename(self.target)
        else:
            os.link(self.part, self.target)
            self.part.unlink()
        return self.target

    async def run(self) -> Path:
        if self._task is not None:
            raise DownloadError('download_running', '下载任务已在运行')
        if self.state == 'completed':
            return self.target
        if self.state == 'cancelled':
            raise DownloadError('download_cancelled', '下载任务已取消，请重新创建任务')
        if self._restart_required:
            raise DownloadError('download_restart', '文件内容异常，请取消任务后重新下载')
        self._task = asyncio.current_task()
        self._stop_mode = ''
        self.error = ''
        self.warning = ''
        self.checksum_verified = False
        self._started = time.monotonic()
        self._start_done = self.done
        self._last_notify = 0.0
        try:
            self._check_owner()
            self._prepare_paths()
            self._check_part()
            self.state = 'resolving'
            await self._notify(force=True)
            for attempt in range(2):
                source = await self._resolve()
                if self._part_identity is not None and self.done == self.total:
                    break
                if await self._transfer(source):
                    break
                if attempt == 1:
                    raise DownloadError('download_expired', f'下载服务器拒绝请求（HTTP {self._rejected_status}）；刷新下载地址后仍失败，请稍后重试。')
            saved = await self._verify_and_publish()
            self.state = 'completed'
            try:
                await self._notify(force=True)
            except (Exception, asyncio.CancelledError):
                # Publication is already complete; a closed UI cannot undo it.
                pass
            return saved
        except asyncio.CancelledError:
            self.state = 'cancelled' if self._stop_mode == 'cancel' else 'paused'
            if self.state == 'cancelled':
                self._remove_part()
            self.speed = 0.0
            raise
        except DownloadError as exc:
            self.state, self.error, self.speed = 'failed', str(exc), 0.0
            raise
        except httpx.HTTPError:
            self.state, self.error, self.speed = 'failed', '下载连接中断，可继续下载剩余部分', 0.0
            raise DownloadError('download_network', self.error) from None
        except FileExistsError:
            self.state, self.error, self.speed = 'failed', '目标文件已存在，未覆盖已有文件', 0.0
            raise DownloadError('download_exists', self.error) from None
        except OSError:
            self.state, self.error, self.speed = 'failed', '无法写入下载目录，请检查磁盘空间与目录权限', 0.0
            raise DownloadError('download_disk', self.error) from None
        except Exception:
            self.state, self.error, self.speed = 'failed', '下载暂时无法继续，请重试', 0.0
            raise DownloadError('download_failed', self.error) from None
        finally:
            self._task = None

    def _remove_part(self):
        if self.part is not None and self._part_identity is not None:
            try:
                stat = self.part.lstat()
            except FileNotFoundError:
                return
            if not self.part.is_symlink() and (stat.st_dev, stat.st_ino) == self._part_identity:
                self.part.unlink()
        self.done = 0

    async def _stop(self, mode: str):
        task = self._task
        if self.state == 'completed':
            return
        if task is not None:
            if task is asyncio.current_task():
                raise DownloadError('download_callback', '请在下载回调之外暂停或取消任务')
            self._stop_mode = mode
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, DownloadError):
                pass
        elif mode == 'cancel':
            self._remove_part()
            self.state = 'cancelled'
        elif self.state != 'cancelled':
            self.state = 'paused'
        self.speed = 0.0

    async def pause(self):
        await self._stop('pause')

    async def cancel(self):
        await self._stop('cancel')
