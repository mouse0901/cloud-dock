"""Bounded Range downloads, independently implemented for the Quark adapter.

Design evidence: docs/QUARK-SPEED-REVIEW.md. Session-local segment progress is
kept separately from preallocated file length. No URLs or credentials persist.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin

import httpx
from app.direct_download import DirectDownload, DownloadError

# Same-file live measurements: 128 improved throughput; 256 added latency only.
DEFAULT_CONNECTIONS = 128
MAX_CONNECTIONS = 128


class DownloadGate:
    """A real shared connection limit with explicit throttle reduction/recovery."""
    def __init__(self, maximum=DEFAULT_CONNECTIONS):
        self.maximum = max(1, min(int(maximum), MAX_CONNECTIONS))
        self.limit = self.maximum
        self.active = 0
        self.peak = 0
        self.last_throttle = 0.0
        self.condition = asyncio.Condition()

    @asynccontextmanager
    async def slot(self):
        async with self.condition:
            if self.limit < self.maximum and time.monotonic() - self.last_throttle >= 30:
                self.limit += 1
                self.last_throttle = time.monotonic()
            await self.condition.wait_for(lambda: self.active < self.limit)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            yield
        finally:
            async with self.condition:
                self.active -= 1
                self.condition.notify_all()

    async def throttled(self):
        async with self.condition:
            self.limit = max(1, int(self.limit * 0.7))
            self.last_throttle = time.monotonic()
            self.condition.notify_all()


@dataclass
class Segment:
    start: int
    end: int
    written: int = 0

    @property
    def size(self):
        return self.end - self.start + 1


class _ExpiredLink(Exception):
    pass


class _RetrySegment(Exception):
    pass


class ParallelDownload(DirectDownload):
    """One account-bound file, bounded parallel connections; existing run lifecycle."""
    def __init__(self, *args, connections=DEFAULT_CONNECTIONS, gate=None, diagnostic=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.connections = max(1, min(int(connections), MAX_CONNECTIONS))
        self.gate = gate or DownloadGate(self.connections)
        self.segments: list[Segment] | None = None
        self.active_connections = 0
        self.peak_connections = 0
        self.transfer_mode = 'single'
        self.retry_count = 0
        self._probe_supported = None
        self.diagnostic = diagnostic
        self._sample_at = 0.0
        self._sample_done = 0
        self._diagnostic_at = 0.0
        self.elapsed_seconds = 0.0
        self.average_speed = 0.0

    def _check_part(self):
        if self.segments is None:
            return super()._check_part()
        if self.part is None or not self.part.exists():
            raise DownloadError('download_partial', '下载临时文件已丢失，请重新创建任务')
        stat = self.part.lstat()
        if (self.part.is_symlink() or not self.part.is_file()
                or (stat.st_dev, stat.st_ino) != self._part_identity
                or stat.st_size != self.total
                or self.done != sum(part.written for part in self.segments)):
            raise DownloadError('download_partial', '分片进度或临时文件已改变，请重新创建任务')

    def _remove_part(self):
        super()._remove_part()
        self.segments = None

    async def _open_range(self, source, start, end):
        url = source.url
        for redirects in range(6):
            self._check_owner()
            request = self._request(url, source)
            request.headers['Range'] = f'bytes={start}-{end}'
            if self._validator is not None:
                request.headers['If-Range'] = self._validator[1]
            # A stalled range is recoverable independently; do not hold all other
            # ranges for the single-stream 60 second read timeout.
            request.extensions['timeout'] = httpx.Timeout(15, connect=10).as_dict()
            response = await self.client.send(request, auth=None, stream=True, follow_redirects=False)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response
            try:
                location = response.headers.get('location')
                if not location or redirects == 5:
                    raise DownloadError('download_redirect', '分片下载地址跳转失败')
                url = self._approved_url(urljoin(url, location))
            finally:
                await response.aclose()
        raise DownloadError('download_redirect', '分片下载地址跳转次数过多')

    def _validate_range(self, response, start, end, *, probe=False):
        if response.status_code != 206:
            raise DownloadError('download_range', '服务端未按要求返回文件分片，已停止写入')
        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
        if not match or tuple(map(int, match.groups())) != (start, end, self.total):
            raise DownloadError('download_range', '服务端返回的分片范围不一致，已停止写入')
        length = response.headers.get('content-length')
        if length is not None and (not re.fullmatch(r'\d+', length) or int(length) != end-start+1):
            raise DownloadError('download_length', '服务端返回的分片长度不一致')
        if response.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
            raise DownloadError('download_encoding', '分片下载不支持压缩传输')
        if self._validator is not None:
            name, value = self._validator
            if response.headers.get(name) != value:
                raise DownloadError('download_changed', '文件版本已变化，请重新创建下载任务')
        elif probe:
            etag = response.headers.get('etag')
            if etag and not etag.startswith('W/'):
                self._validator = ('etag', etag)
            elif response.headers.get('last-modified'):
                self._validator = ('last-modified', response.headers['last-modified'])
        if probe:
            self._content_type = response.headers.get('content-type', '').lower()
            if 'text/html' in self._content_type and Path(self.item.name).suffix.lower() not in ('.html', '.htm'):
                raise DownloadError('download_content', '服务端返回网页，未作为文件下载')

    async def _probe(self, source):
        async with self.gate.slot():
            response = await self._open_range(source, 0, 0)
            try:
                self._check_owner()
                if response.status_code in (401, 403, 412):
                    raise _ExpiredLink()
                if response.status_code == 200:
                    if self.segments is not None:
                        raise DownloadError('download_range', '服务端不再支持分片续传，已保留进度')
                    return False
                if response.status_code != 206:
                    raise DownloadError('download_http', f'下载探测失败（HTTP {response.status_code}），请稍后重试')
                self._validate_range(response, 0, 0, probe=True)
                count = 0
                async for chunk in response.aiter_bytes():
                    count += len(chunk)
                    if count > 1:
                        raise DownloadError('download_range', '文件分片探测响应异常')
                if count != 1:
                    raise DownloadError('download_incomplete', '文件分片探测响应不完整')
                # Avoid mixing unversioned sources. A strong content digest can
                # also validate the final result; advisory Quark MD5 cannot.
                return self._validator is not None or bool(source.md5 and source.md5_is_content)
            finally:
                await response.aclose()

    def _prepare_segments(self):
        # Keep the plan fixed for this task, including pause/link refresh.
        # More workers need enough pieces on medium files. Keep the tested
        # 512 KiB plan for <=64 workers; 128 workers use 256 KiB work units.
        minimum = (256 if self.connections > 64 else 512) * 1024
        size = max(minimum, (self.total + 4095) // 4096)
        plan = [Segment(start, min(start+size, self.total)-1) for start in range(0, self.total, size)]
        with self.part.open('xb') as stream:
            stat = os.fstat(stream.fileno())
            self._part_identity = (stat.st_dev, stat.st_ino)
            try:
                stream.truncate(self.total)
            except BaseException:
                stream.close()
                self.part.unlink()
                self._part_identity = None
                raise
        self.segments = plan

    async def _read_segment(self, source, segment, stream):
        for attempt in range(4):
            self._check_owner()
            if segment.written == segment.size:
                return
            try:
                async with self.gate.slot():
                    self.active_connections += 1
                    self.peak_connections = max(self.peak_connections, self.active_connections)
                    try:
                        start = segment.start + segment.written
                        response = await self._open_range(source, start, segment.end)
                        try:
                            self._check_owner()
                            if response.status_code in (401, 403, 412):
                                raise _ExpiredLink()
                            if response.status_code in (429, 503):
                                await self.gate.throttled()
                                raise _RetrySegment()
                            if response.status_code in (500, 502, 504):
                                raise _RetrySegment()
                            self._validate_range(response, start, segment.end)
                            async for chunk in response.aiter_bytes(chunk_size=64*1024):
                                self._check_owner()
                                if segment.written + len(chunk) > segment.size:
                                    self._restart_required = True
                                    raise DownloadError('download_size', '分片内容超过范围，请重新创建任务')
                                # No await between seek/write/counters: one event
                                # loop owns this file handle, ranges never overlap.
                                stream.seek(segment.start + segment.written)
                                if stream.write(chunk) != len(chunk):
                                    raise OSError('incomplete local write')
                                segment.written += len(chunk)
                                self.done += len(chunk)
                                await self._notify()
                            if segment.written != segment.size:
                                raise _RetrySegment()
                            return
                        finally:
                            await response.aclose()
                    finally:
                        self.active_connections -= 1
            except (httpx.HTTPError, _RetrySegment):
                if attempt == 3:
                    raise DownloadError('download_network', '部分下载连接中断，已保留分片进度，可继续下载') from None
                self.retry_count += 1
                await asyncio.sleep(0.5 * 2**attempt)
        raise DownloadError('download_incomplete', '分片下载未完成')

    async def _transfer(self, source):
        if self.segments is None and (self.connections == 1 or self.total < 5*1024*1024 or self._probe_supported is False):
            return await super()._transfer(source)
        try:
            supported = await self._probe(source)
            if not supported:
                if self.segments is not None:
                    raise DownloadError('download_changed', '文件版本信息缺失，已停止分片续传')
                self._probe_supported = False
                return await super()._transfer(source)
            self._probe_supported = True
            if self.segments is None:
                self._prepare_segments()
            self._check_part()
            queue = asyncio.Queue()
            for segment in self.segments:
                if segment.written < segment.size:
                    queue.put_nowait(segment)
            self.state, self.transfer_mode = 'downloading', 'parallel'
            with self.part.open('r+b', buffering=0) as stream:
                stat = os.fstat(stream.fileno())
                if self._part_identity != (stat.st_dev, stat.st_ino):
                    raise DownloadError('download_partial', '下载临时文件已被替换')
                async def worker():
                    while not queue.empty():
                        segment = queue.get_nowait()
                        await self._read_segment(source, segment, stream)
                tasks = [asyncio.create_task(worker()) for _ in range(min(self.connections, queue.qsize()))]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    stream.flush()
                    os.fsync(stream.fileno())
            if self.done != self.total:
                raise DownloadError('download_incomplete', '下载分片尚未完整，可继续下载')
            return True
        except _ExpiredLink:
            return False

    def _record_transfer(self, outcome):
        if self.diagnostic is None:
            return
        elapsed = max(0, time.monotonic() - self._started)
        try:
            self.diagnostic({
                'stage': 'download_transfer', 'route': self.transfer_mode,
                'outcome': outcome, 'transferred_bytes': self.done,
                'peak_connections': self.peak_connections,
                'active_connections': self.active_connections,
                'retry_count': self.retry_count,
                'elapsed_ms': int(elapsed * 1000),
                'bytes_per_second': int(self.speed),
                'average_bytes_per_second': int(self.average_speed),
                'active_elapsed_ms': int(self.elapsed_seconds * 1000),
            })
        except Exception:
            pass  # Diagnostics must not interrupt a transfer.

    async def _notify(self, *, force=False):
        now = time.monotonic()
        elapsed = now - self._sample_at
        if elapsed >= 0.5:
            instantaneous = (self.done - self._sample_done) / elapsed
            weight = 1 - 2.718281828 ** (-elapsed / 1.5)
            self.speed = instantaneous if not self.speed else self.speed + weight * (instantaneous - self.speed)
            self._sample_at, self._sample_done = now, self.done
        if force or now - self._last_notify >= 0.5:
            self._last_notify = now
            await self.on_progress(self.done, self.total)
        if now - self._diagnostic_at >= 5:
            self._diagnostic_at = now
            self._record_transfer(self.state)

    async def run(self):
        # Retrying a task keeps its range plan, but starts fresh speed samples.
        if self._task is not None or self.state in ('completed', 'cancelled'):
            return await super().run()
        self.speed = 0.0
        self._sample_at = self._diagnostic_at = time.monotonic()
        self._sample_done = self.done
        before = time.monotonic()
        try:
            return await super().run()
        finally:
            self.elapsed_seconds += time.monotonic() - before
            self.average_speed = self.done / self.elapsed_seconds if self.elapsed_seconds else 0.0
            self._record_transfer(self.state)
