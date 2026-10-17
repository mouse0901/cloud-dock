"""Parallel transfer regressions: real files, in-memory HTTP, no user account."""
import asyncio
import hashlib
from pathlib import Path
import random
import re
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from app.direct_download import DownloadError, DownloadSource
from app.domain import CloudFile
from app.parallel_download import DownloadGate, ParallelDownload

ROOT = Path(__file__).resolve().parents[1]
DATA = random.Random(731).randbytes(6 * 1024 * 1024 + 37)
MD5 = hashlib.md5(DATA).hexdigest()
URL = 'https://pdds.quark.cn/fixture'


def validate(url):
    if httpx.URL(url).host not in ('pdds.quark.cn', 'cdn.quark.cn'):
        raise ValueError('unsupported host')
    return url


class Body(httpx.AsyncByteStream):
    def __init__(self, server, data, *, fail_after=None, block=False):
        self.server, self.data = server, data
        self.fail_after, self.block = fail_after, block
        self.active = False
        self.closed = False

    async def __aiter__(self):
        self.active = True
        self.server.active += 1
        self.server.peak = max(self.server.peak, self.server.active)
        for offset in range(0, len(self.data), 65536):
            await asyncio.sleep(0)
            if self.fail_after is not None and offset >= self.fail_after:
                raise httpx.ReadError('private server diagnostic must not leak')
            yield self.data[offset:offset+65536]
            if self.block:
                self.server.blocked.set()
                await self.server.release.wait()

    async def aclose(self):
        self.closed = True
        if self.active:
            self.active = False
            self.server.active -= 1


class Service:
    def __init__(self):
        self.calls, self.bodies = [], []
        self.active = self.peak = 0
        self.blocked, self.release = asyncio.Event(), asyncio.Event()
        self.block = False
        self.ignore_range = False
        self.etag = '"version-one"'
        self.policy = None

    async def __call__(self, request):
        self.calls.append(request)
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', request.headers.get('range', ''))
        start, end = (int(match[1]), int(match[2]) if match[2] else len(DATA)-1) if match else (0,len(DATA)-1)
        assert 0 <= start <= end < len(DATA)
        if self.policy:
            response = await self.policy(request, start, end)
            if response is not None:
                return response
        if self.ignore_range:
            start, end, match = 0, len(DATA)-1, None
        return self.response(start, end, ranged=bool(match))

    def response(self, start, end, *, ranged=True, fail_after=None):
        body = Body(self, DATA[start:end+1], fail_after=fail_after,
                    block=self.block and end > start)
        self.bodies.append(body)
        headers = {'content-length': str(end-start+1), 'content-type': 'application/octet-stream'}
        if self.etag:
            headers['etag'] = self.etag
        if ranged:
            headers['content-range'] = f'bytes {start}-{end}/{len(DATA)}'
        return httpx.Response(206 if ranged else 200, headers=headers, stream=body)


class ParallelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parent = ROOT / 'work/test-runs'
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.service = Service()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.service))
        self.addAsyncCleanup(self.client.aclose)
        self.item = CloudFile('fixture', 'quark', '/sample.bin', False, len(DATA))
        self.source = DownloadSource(URL, len(DATA), md5=MD5,
            headers={'User-Agent':'fixture-desktop', 'Referer':'https://pan.quark.cn/',
                     'Cookie':'do-not-forward', 'Authorization':'do-not-forward'})
        self.resolve = AsyncMock(side_effect=lambda item: self.source)
        self.events = []

    def job(self, **kwargs):
        return ParallelDownload(self.item, self.tmp.name, self.client, self.resolve,
            validate, AsyncMock(), connections=8, diagnostic=self.events.append, **kwargs)

    async def test_parallel_bytes_match_and_cookie_redirect_scope(self):
        self.client.cookies.set('account', 'own', domain='.quark.cn', path='/')
        self.client.cookies.set('private', 'other', domain='other.invalid', path='/')
        async def policy(request, start, end):
            self.assertEqual(request.headers['user-agent'], 'fixture-desktop')
            self.assertEqual(request.headers['cookie'], 'account=own')
            self.assertNotIn('authorization', request.headers)
            if request.url.host == 'pdds.quark.cn':
                return httpx.Response(302, headers={'location':'https://cdn.quark.cn/fixture'})
        self.service.policy = policy
        job = self.job()
        saved = await job.run()
        self.assertEqual(saved.read_bytes(), DATA)
        self.assertTrue(job.checksum_verified)
        self.assertGreater(self.service.peak, 1)
        self.assertLessEqual(self.service.peak, 8)
        self.assertEqual(job.active_connections, 0)
        self.assertEqual(self.service.active, 0)
        self.assertTrue(all(body.closed for body in self.service.bodies))
        self.assertEqual(self.events[-1]['outcome'], 'completed')
        self.assertNotIn('fixture', str(self.events))
        await job.cancel()
        self.assertTrue(saved.exists())

    async def test_default_128_workers_and_small_parts_preserve_content(self):
        data = random.Random(128).randbytes(33 * 1024 * 1024 + 37)
        source = DownloadSource(URL, len(data), md5=hashlib.md5(data).hexdigest())
        item = CloudFile('medium', 'quark', '/medium.bin', False, len(data))
        self.service.block = True
        with patch(__name__ + '.DATA', data):
            job = ParallelDownload(item, self.tmp.name, self.client,
                AsyncMock(return_value=source), validate, AsyncMock())
            task = asyncio.create_task(job.run())
            try:
                async with asyncio.timeout(5):
                    while self.service.active < 128:
                        if task.done():
                            await task
                            self.fail('transfer ended before reaching 128 active streams')
                        await asyncio.sleep(0)
                self.assertEqual(job.connections, 128)
                self.assertEqual(job.gate.maximum, 128)
                self.assertEqual(job.segments[0].size, 256 * 1024)
                self.assertEqual(self.service.peak, 128)
            finally:
                self.service.release.set()
                saved = await asyncio.wait_for(task, 5)
            self.assertEqual(hashlib.sha256(saved.read_bytes()).digest(), hashlib.sha256(data).digest())
            self.assertTrue(job.checksum_verified)
            self.assertEqual(job.active_connections, 0)
            self.assertEqual(job.gate.active, 0)
            self.assertGreater(job.elapsed_seconds, 0)
            self.assertAlmostEqual(job.average_speed, len(data) / job.elapsed_seconds)
            elapsed = job.elapsed_seconds
            await job.run()
            self.assertEqual(job.elapsed_seconds, elapsed)
        self.assertEqual(DownloadGate(999).maximum, 128)
        capped = ParallelDownload(item, self.tmp.name, self.client,
            AsyncMock(return_value=source), validate, AsyncMock(), connections=999)
        self.assertEqual(capped.connections, 128)

    async def test_ignored_range_falls_back_once(self):
        self.service.ignore_range = True
        job = self.job()
        self.assertEqual((await job.run()).read_bytes(), DATA)
        self.assertEqual(len(self.service.calls), 2)
        self.assertEqual(self.service.calls[0].headers['range'], 'bytes=0-0')
        self.assertNotIn('range', self.service.calls[1].headers)
        self.assertEqual(job.transfer_mode, 'single')
        self.assertIsNone(job.segments)

    async def test_unversioned_advisory_digest_falls_back(self):
        self.service.etag = None
        self.source = DownloadSource(URL, len(DATA), md5=MD5, md5_is_content=False)
        job = self.job()
        self.assertEqual((await job.run()).read_bytes(), DATA)
        self.assertEqual(job.transfer_mode, 'single')
        self.assertEqual(len(self.service.calls), 2)

    async def test_unversioned_strict_digest_allows_parallel_and_rejects_corruption(self):
        self.service.etag = None
        self.source = DownloadSource(URL, len(DATA), md5='0'*32)
        job = self.job()
        with self.assertRaises(DownloadError) as error:
            await job.run()
        self.assertEqual(error.exception.code, 'download_checksum')
        self.assertTrue(job.requires_restart)
        self.assertFalse(job.target.exists())

    async def test_wrong_range_and_changed_version_are_not_published(self):
        for change in ('content-range', 'etag', 'content-length', 'content-encoding'):
            async def policy(request, start, end):
                if end == 0: return None
                response = self.service.response(start,end)
                response.headers[change] = {'content-range':'bytes 1-2/3', 'etag':'"changed"',
                    'content-length':'1', 'content-encoding':'gzip'}[change]
                return response
            self.service.policy = policy
            job = self.job()
            with self.assertRaises(DownloadError):
                await job.run()
            self.assertFalse(job.target.exists())
            self.assertEqual(job.done, 0)
            self.assertEqual(self.service.active, 0)
            await job.cancel()

    async def test_pause_preserves_holes_and_resumes_only_remaining_ranges(self):
        self.service.block = True
        job = self.job()
        task = asyncio.create_task(job.run())
        await asyncio.wait_for(self.service.blocked.wait(), 3)
        await job.pause()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(job.state, 'paused')
        self.assertTrue(0 < job.done < job.total)
        self.assertEqual(job.part.stat().st_size, job.total)
        self.assertEqual(self.service.active, 0)
        remaining = {f'bytes={s.start+s.written}-{s.end}' for s in job.segments if s.written<s.size}
        plan = job.segments
        count = len(self.service.calls)
        self.service.block = False
        saved = await job.run()
        self.assertIs(job.segments, plan)
        self.assertEqual(saved.read_bytes(), DATA)
        self.assertEqual({r.headers['range'] for r in self.service.calls[count:]}, remaining | {'bytes=0-0'})

    async def test_partial_network_failure_retries_only_tail(self):
        failed = False
        async def policy(request,start,end):
            nonlocal failed
            if start == 0 and end > 0 and not failed:
                failed = True
                return self.service.response(start,end,fail_after=65536)
        self.service.policy = policy
        job = self.job()
        self.assertEqual((await job.run()).read_bytes(), DATA)
        self.assertEqual(job.retry_count, 1)
        self.assertIn('bytes=65536-524287', [r.headers['range'] for r in self.service.calls])

    async def test_expired_link_refreshes_once_and_closes_other_workers(self):
        failed = False
        async def policy(request,start,end):
            nonlocal failed
            if start == 524288 and not failed:
                failed = True
                await asyncio.sleep(0)
                return httpx.Response(403)
        self.service.policy = policy
        job = self.job()
        self.assertEqual((await job.run()).read_bytes(), DATA)
        self.assertEqual(self.resolve.await_count, 2)
        self.assertEqual(self.service.active, 0)
        self.assertTrue(all(body.closed for body in self.service.bodies))

    async def test_changed_or_unsupported_resume_never_switches_to_append(self):
        self.service.block = True
        job = self.job()
        task = asyncio.create_task(job.run())
        await asyncio.wait_for(self.service.blocked.wait(), 3)
        await job.pause()
        await asyncio.gather(task,return_exceptions=True)
        done = job.done
        self.service.block = False
        self.service.ignore_range = True
        for _ in range(2):
            with self.assertRaises(DownloadError) as error:
                await job.run()
            self.assertEqual(error.exception.code, 'download_range')
            self.assertEqual(job.done, done)
            self.assertEqual(job.part.stat().st_size, job.total)
        self.service.ignore_range = False
        self.service.etag = '"different"'
        with self.assertRaises(DownloadError) as error:
            await job.run()
        self.assertEqual(error.exception.code, 'download_changed')
        self.assertFalse(job.target.exists())
        await job.cancel()

    async def test_account_change_stops_every_worker(self):
        owner = 'a'
        self.service.block = True
        job = self.job(owner='a', current_owner=lambda: owner)
        task = asyncio.create_task(job.run())
        await asyncio.wait_for(self.service.blocked.wait(), 3)
        owner = 'b'
        self.service.release.set()
        with self.assertRaises(DownloadError) as error:
            await task
        self.assertEqual(error.exception.code, 'download_account')
        self.assertEqual(self.service.active, 0)
        await job.cancel()
        self.assertFalse(job.part.exists())

    async def test_cancel_removes_only_owned_partial(self):
        sentinel = Path(self.tmp.name)/'existing.bin'
        sentinel.write_bytes(b'keep')
        self.service.block = True
        job = self.job()
        task = asyncio.create_task(job.run())
        await asyncio.wait_for(self.service.blocked.wait(), 3)
        await job.cancel()
        await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(job.part.exists())
        self.assertEqual(sentinel.read_bytes(), b'keep')
        self.assertEqual(self.service.active, 0)
        self.assertEqual(job.gate.active, 0)

    async def test_retry_budget_and_throttling(self):
        async def policy(request,start,end):
            if end != 0: return httpx.Response(503)
        self.service.policy = policy
        job = self.job()
        with self.assertRaises(DownloadError) as error:
            await asyncio.wait_for(job.run(), 8)
        self.assertEqual(error.exception.code, 'download_network')
        self.assertEqual(job.gate.limit, 1)
        self.assertEqual(job.gate.active, 0)
        self.assertLessEqual(len(self.service.calls), 1+8*4)
        self.assertNotIn('private', str(error.exception))

    async def test_shared_gate_caps_two_simultaneous_files(self):
        gate = DownloadGate(5)
        a, b = self.job(gate=gate), self.job(gate=gate)
        b.item = CloudFile('second', 'quark', '/second.bin', False, len(DATA))
        results = await asyncio.gather(a.run(),b.run())
        self.assertEqual([p.read_bytes() for p in results], [DATA,DATA])
        self.assertLessEqual(self.service.peak, 5)
        self.assertEqual(gate.active, 0)

    async def test_redirect_outside_provider_never_requested(self):
        async def policy(request,start,end):
            return httpx.Response(302,headers={'location':'https://other.invalid/secret'})
        self.service.policy = policy
        job = self.job()
        with self.assertRaises(DownloadError) as error:
            await job.run()
        self.assertEqual(error.exception.code, 'download_address')
        self.assertEqual(len(self.service.calls), 1)


if __name__ == '__main__':
    unittest.main()
