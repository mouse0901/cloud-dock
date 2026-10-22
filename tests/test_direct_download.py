"""Deterministic download acceptance tests; no real account or network access."""
import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from app.direct_download import DirectDownload, DownloadError, DownloadSource
from app.domain import CloudFile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://download.baidu.com/file?sign=private-test-sign'


def trusted_url(url):
    if httpx.URL(url).host not in {'download.baidu.com', 'cdn.baidupcs.com'}:
        raise ValueError('rejected')
    return url


class Pieces(httpx.AsyncByteStream):
    def __init__(self, *pieces, fail=False, block=None):
        self.pieces, self.fail, self.block = pieces, fail, block
        self.closed = False

    async def __aiter__(self):
        for piece in self.pieces:
            yield piece
        if self.block is not None:
            self.block.set()
            await asyncio.Event().wait()
        if self.fail:
            raise httpx.ReadError('unsafe https://private.example/?secret=123')

    async def aclose(self):
        self.closed = True


class DirectDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        (ROOT / 'work' / 'test-runs').mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'work' / 'test-runs')
        self.folder = Path(self.tmp.name)
        self.progress = AsyncMock()
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.aclose()
        self.tmp.cleanup()

    def make(self, handle, *, content=b'hello', name='example.bin', resolver=None,
             headers=None, md5=None, owner=None, current_owner=None, client_headers=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle), headers=client_headers)
        self.clients.append(client)
        item = CloudFile('file-1', 'baidu', '/' + name, size=len(content))
        if resolver is None:
            resolver = AsyncMock(return_value=DownloadSource(URL, len(content), headers or {}, md5))
        transfer = DirectDownload(item, self.folder, client, resolver, trusted_url,
                                  self.progress, owner=owner, current_owner=current_owner)
        return transfer, resolver, client

    async def test_download_checks_md5_and_does_not_overwrite_existing_file(self):
        (self.folder / 'example.bin').write_bytes(b'old')
        transfer, resolver, _ = self.make(lambda req: httpx.Response(200, content=b'hello'),
            md5=hashlib.md5(b'hello').hexdigest())
        saved = await transfer.run()
        self.assertEqual(saved.name, 'example (1).bin')
        self.assertEqual(saved.read_bytes(), b'hello')
        self.assertEqual((self.folder / 'example.bin').read_bytes(), b'old')
        self.assertEqual(transfer.state, 'completed')
        self.assertFalse(transfer.part.exists())
        self.assertEqual(self.progress.await_args_list[0].args, (0, 5))
        self.assertEqual(self.progress.await_args_list[-1].args, (5, 5))
        self.assertEqual(await transfer.run(), saved)
        resolver.assert_awaited_once()
        await transfer.cancel()
        self.assertEqual(saved.read_bytes(), b'hello')

    async def test_pause_keeps_bytes_and_resume_resolves_new_ticket(self):
        waiting = asyncio.Event()
        first = Pieces(b'he', block=waiting)
        requests = []
        def handle(req):
            requests.append(req)
            if len(requests) == 1:
                return httpx.Response(200, headers={'Content-Length': '5', 'ETag': '"v1"'}, stream=first)
            self.assertEqual(req.headers['Range'], 'bytes=2-')
            self.assertEqual(req.headers['If-Range'], '"v1"')
            self.assertEqual(req.url.params['sign'], 'new')
            return httpx.Response(206, headers={'Content-Range': 'bytes 2-4/5', 'ETag': '"v1"'}, content=b'llo')
        resolver = AsyncMock(side_effect=[DownloadSource(URL, 5), DownloadSource(URL.replace('private-test-sign', 'new'), 5)])
        transfer, _, _ = self.make(handle, resolver=resolver)
        task = asyncio.create_task(transfer.run())
        await asyncio.wait_for(waiting.wait(), 2)
        await transfer.pause()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(transfer.state, 'paused')
        self.assertTrue(first.closed)
        self.assertEqual(transfer.part.read_bytes(), b'he')
        saved = await transfer.run()
        self.assertEqual(saved.read_bytes(), b'hello')
        self.assertEqual(resolver.await_count, 2)

    async def test_multiple_disconnects_keep_exact_offset(self):
        requests = []
        def handle(req):
            requests.append(req)
            n = len(requests)
            if n == 1:
                return httpx.Response(200, headers={'Content-Length': '5'}, stream=Pieces(b'h', fail=True))
            if n == 2:
                self.assertEqual(req.headers['Range'], 'bytes=1-')
                return httpx.Response(206, headers={'Content-Range': 'bytes 1-4/5', 'Content-Length': '4'}, stream=Pieces(b'el', fail=True))
            self.assertEqual(req.headers['Range'], 'bytes=3-')
            return httpx.Response(206, headers={'Content-Range': 'bytes 3-4/5'}, content=b'lo')
        transfer, resolver, _ = self.make(handle)
        for expected in (b'h', b'hel'):
            with self.assertRaises(DownloadError) as error:
                await transfer.run()
            self.assertEqual(error.exception.code, 'download_network')
            self.assertNotIn('secret', str(error.exception))
            self.assertEqual(transfer.part.read_bytes(), expected)
            self.assertEqual(transfer.done, len(expected))
        self.assertEqual((await transfer.run()).read_bytes(), b'hello')
        self.assertEqual(resolver.await_count, 3)

    async def test_ignored_range_never_appends_entire_file(self):
        count = 0
        def handle(req):
            nonlocal count
            count += 1
            return httpx.Response(200, stream=Pieces(b'he', fail=True)) if count == 1 else httpx.Response(200, content=b'hello')
        transfer, _, _ = self.make(handle)
        with self.assertRaises(DownloadError):
            await transfer.run()
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_range')
        self.assertEqual(transfer.part.read_bytes(), b'he')
        self.assertFalse(transfer.target.exists())

    async def test_inconsistent_range_or_etag_is_rejected_before_writing(self):
        for bad_headers in ({'Content-Range': 'bytes 1-4/5', 'ETag': '"v1"'},
                            {'Content-Range': 'bytes 2-4/5', 'ETag': '"v2"'},
                            {'Content-Range': 'bytes 2-4/6', 'ETag': '"v1"'}):
            calls = 0
            def handle(req):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return httpx.Response(200, headers={'ETag': '"v1"'}, stream=Pieces(b'he', fail=True))
                return httpx.Response(206, headers=bad_headers, content=b'llo')
            transfer, _, _ = self.make(handle)
            with self.assertRaises(DownloadError):
                await transfer.run()
            with self.assertRaises(DownloadError):
                await transfer.run()
            self.assertEqual(transfer.part.read_bytes(), b'he')
            await transfer.cancel()

    async def test_untrusted_redirect_never_receives_request(self):
        urls = []
        def handle(req):
            urls.append(str(req.url))
            return httpx.Response(302, headers={'location': 'https://attacker.example/?secret=123'})
        transfer, _, _ = self.make(handle)
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_address')
        self.assertEqual(len(urls), 1)
        self.assertNotIn('attacker', transfer.error)
        self.assertEqual(list(self.folder.iterdir()), [])

    async def test_cookie_jar_scopes_redirect_and_default_secrets_are_ignored(self):
        requests = []
        def handle(req):
            requests.append(req)
            self.assertNotIn('authorization', req.headers)
            self.assertNotIn('secret-param', req.url.params)
            self.assertNotIn('raw-secret', req.headers.get('cookie', ''))
            self.assertNotIn('ticket-secret', req.headers.get('cookie', ''))
            self.assertEqual(req.headers['User-Agent'], 'pan.baidu.com')
            if len(requests) == 1:
                self.assertIn('BDUSS=jar-secret', req.headers.get('cookie', ''))
                return httpx.Response(302, headers={'location': 'https://cdn.baidupcs.com/object?sign=cdn-ticket'})
            self.assertNotIn('cookie', req.headers)
            self.assertEqual(req.url.params['sign'], 'cdn-ticket')
            return httpx.Response(200, content=b'hello')
        transfer, _, client = self.make(handle,
            headers={'Cookie': 'ticket-secret', 'Authorization': 'Bearer ticket-secret', 'User-Agent': 'pan.baidu.com', 'Referer': 'https://pan.baidu.com/'},
            client_headers={'Cookie': 'raw-secret', 'Authorization': 'Bearer raw-secret'})
        client.cookies.set('BDUSS', 'jar-secret', domain='.baidu.com')
        client.params = {'secret-param': 'secret'}
        self.assertEqual((await transfer.run()).read_bytes(), b'hello')
        self.assertEqual(len(requests), 2)

    async def test_expired_source_refreshes_only_once(self):
        for fail_forever in (False, True):
            count = 0
            def handle(req):
                nonlocal count
                count += 1
                return httpx.Response(403) if count == 1 or fail_forever else httpx.Response(200, content=b'hello')
            transfer, resolver, _ = self.make(handle)
            if fail_forever:
                with self.assertRaises(DownloadError) as error:
                    await transfer.run()
                self.assertEqual(error.exception.code, 'download_expired')
                self.assertIn('HTTP 403', str(error.exception))
                self.assertNotIn('登录', str(error.exception))
                self.assertNotIn('扫码', str(error.exception))
            else:
                self.assertEqual((await transfer.run()).read_bytes(), b'hello')
            self.assertEqual(count, 2)
            self.assertEqual(resolver.await_count, 2)

    async def test_owner_change_prevents_resolve_and_mid_stream_write(self):
        current = ['A']
        transfer, resolver, _ = self.make(lambda req: httpx.Response(200, content=b'hello'), owner='A', current_owner=lambda: current[0])
        current[0] = 'B'
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_account')
        resolver.assert_not_awaited()
        current[0] = 'A'
        class SwitchOwner(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'he'
                current[0] = 'B'
                yield b'llo'
        transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=SwitchOwner()), owner='A', current_owner=lambda: current[0])
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_account')
        self.assertEqual(transfer.part.read_bytes(), b'he')

    async def test_cancel_closes_stream_deletes_part_and_is_terminal(self):
        waiting = asyncio.Event()
        stream = Pieces(b'he', block=waiting)
        transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=stream))
        task = asyncio.create_task(transfer.run())
        await asyncio.wait_for(waiting.wait(), 2)
        await transfer.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(stream.closed)
        self.assertFalse(transfer.part.exists())
        self.assertEqual(transfer.state, 'cancelled')
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_cancelled')

    async def test_size_and_checksum_errors_are_never_published(self):
        for body, headers, checksum, expected in (
            (b'hell', {'content-length': '5'}, None, 'download_incomplete'),
            (b'hello!', {}, None, 'download_length'),
            (b'hello', {}, '0' * 32, 'download_checksum'),
            (b'hello', {'content-length': 'invalid'}, None, 'download_length'),
        ):
            transfer, _, _ = self.make(lambda req: httpx.Response(200, content=body, headers=headers), md5=checksum)
            with self.assertRaises(DownloadError) as error:
                await transfer.run()
            self.assertEqual(error.exception.code, expected)
            self.assertFalse(transfer.target.exists())
            await transfer.cancel()

    async def test_abnormal_json_and_login_page_are_not_saved(self):
        for content, mime in ((b'{"errno":31360,"request_id":"123"}', 'application/json'),
                              (b'<html>login</html>', 'text/html')):
            transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content, headers={'Content-Type': mime}), content=content)
            with self.assertRaises(DownloadError) as error:
                await transfer.run()
            self.assertEqual(error.exception.code, 'download_content')
            self.assertFalse(transfer.target.exists())
            await transfer.cancel()

    async def test_empty_file_and_actual_json_file_are_supported(self):
        for content in (b'', b'{"name":"test"}'):
            transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content, headers={'Content-Type': 'application/json'}), content=content)
            self.assertEqual((await transfer.run()).read_bytes(), content)

    async def test_real_json_documents_with_status_fields_are_saved_verbatim(self):
        for content in (
            b'{"code":404,"example":"test"}',
            b'{"errno":42,"description":"application settings"}',
            b'{"success":false,"enabled":false}',
            b'{"error_code":17,"errcode":21,"retry":true}',
            b'{"code":404,"status":"draft","message":"documentation example"}',
            b'{"status":404,"code":0,"example":"not a provider error"}',
        ):
            with self.subTest(content=content):
                transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content,
                    headers={'Content-Type': 'application/json'}), content=content, name='real-content.json')
                saved = await transfer.run()
                self.assertEqual(saved.read_bytes(), content)
                self.assertEqual(transfer.state, 'completed')

    async def test_checksum_verified_error_examples_are_valid_user_files(self):
        for content in (
            b'{"errno":31360,"request_id":"123"}',
            b'{"status":403,"code":23018,"message":"example error"}',
        ):
            transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content,
                headers={'Content-Type': 'application/json'}), content=content, name='error-example.json',
                md5=hashlib.md5(content).hexdigest())
            self.assertEqual((await transfer.run()).read_bytes(), content)
            self.assertEqual(transfer.state, 'completed')

    async def test_unverified_known_error_envelopes_are_not_published(self):
        for content in (
            b'{"errno":31360,"request_id":"123"}',
            b'{"errno":"-6","show_msg":"login required"}',
            b'{"error_code":31066,"error_msg":"missing file","request_id":123}',
            b'{"status":403,"code":23018,"message":"permission denied"}',
            b'{"status":401,"code":31001}',
        ):
            transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content,
                headers={'Content-Type': 'application/json'}), content=content, name='wanted.json')
            with self.assertRaises(DownloadError) as error:
                await transfer.run()
            self.assertEqual(error.exception.code, 'download_content')
            self.assertFalse(transfer.target.exists())
            await transfer.cancel()

    async def test_known_error_with_wrong_checksum_is_rejected_by_integrity_check(self):
        content = b'{"errno":31360,"request_id":"123"}'
        transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content,
            headers={'Content-Type': 'application/json'}), content=content, name='wanted.json', md5='0' * 32)
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_checksum')
        self.assertFalse(transfer.target.exists())

    async def test_conflict_created_during_download_is_not_overwritten(self):
        async def progress(done, total):
            if done == total:
                transfer.target.write_bytes(b'concurrent-file')
        transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=Pieces(b'hello')))
        # Force an in-stream update for the race, rather than relying on time.
        original_notify = transfer._notify
        async def notify(*, force=False):
            await original_notify(force=True)
        transfer.on_progress = progress
        transfer._notify = notify
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_exists')
        self.assertEqual(transfer.target.read_bytes(), b'concurrent-file')
        self.assertEqual(transfer.part.read_bytes(), b'hello')

    async def test_size_or_md5_changed_on_resume_is_rejected(self):
        for second in (DownloadSource(URL, 6), DownloadSource(URL, 5, md5='1' * 32)):
            resolver = AsyncMock(side_effect=[DownloadSource(URL, 5, md5='0' * 32), second])
            transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=Pieces(b'he', fail=True)), resolver=resolver)
            with self.assertRaises(DownloadError):
                await transfer.run()
            with self.assertRaises(DownloadError) as error:
                await transfer.run()
            self.assertEqual(error.exception.code, 'download_changed')
            self.assertEqual(transfer.part.read_bytes(), b'he')
            await transfer.cancel()

    async def test_progress_is_throttled_and_source_repr_hides_secrets(self):
        content = b'x' * 100
        transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=Pieces(*[b'x'] * 100)), content=content)
        await transfer.run()
        self.assertLessEqual(self.progress.await_count, 3)
        self.assertNotIn('private-test-sign', repr(DownloadSource(URL, 5, {'Cookie': 'secret'})))

    async def test_unsafe_filename_remains_inside_destination(self):
        for name in ('CON', 'bad:name?.txt', '..\\escape.txt'):
            transfer, _, _ = self.make(lambda req: httpx.Response(200, content=b'hello'), name=name)
            saved = await transfer.run()
            self.assertEqual(saved.parent, self.folder)
            self.assertNotIn(':', saved.name)
            self.assertNotIn('?', saved.name)
            self.assertFalse(saved.name.startswith('CON'))

    async def test_external_cancellation_is_pause(self):
        waiting = asyncio.Event()
        transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=Pieces(b'he', block=waiting)))
        task = asyncio.create_task(transfer.run())
        await asyncio.wait_for(waiting.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(transfer.state, 'paused')
        self.assertEqual(transfer.part.read_bytes(), b'he')

    async def test_resolver_error_does_not_disclose_credentials(self):
        resolver = AsyncMock(side_effect=ValueError('https://private.example/?token=secret'))
        transfer, _, _ = self.make(lambda req: self.fail('no file request expected'), resolver=resolver)
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_source')
        self.assertNotIn('secret', str(error.exception))
        self.assertNotIn('http', transfer.error)

    async def test_completion_callback_failure_does_not_undo_saved_file(self):
        transfer, _, _ = self.make(lambda req: httpx.Response(200, content=b'hello'))
        async def progress(done, total):
            if transfer.state == 'completed':
                raise RuntimeError('closed UI')
        transfer.on_progress = progress
        saved = await transfer.run()
        self.assertEqual(saved.read_bytes(), b'hello')
        self.assertEqual(transfer.state, 'completed')
        self.assertEqual(transfer.error, '')

    async def test_hash_verification_remains_cancellable(self):
        content = b'a' * (3 * 1024 * 1024)
        transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content),
            content=content, md5=hashlib.md5(content).hexdigest())
        original_verify = transfer._verify_and_publish
        started = asyncio.Event()
        async def verify():
            started.set()
            return await original_verify()
        transfer._verify_and_publish = verify
        task = asyncio.create_task(transfer.run())
        await asyncio.wait_for(started.wait(), 2)
        await transfer.pause()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(transfer.state, 'paused')
        self.assertEqual(transfer.done, len(content))
        self.assertFalse(transfer.target.exists())
        self.assertEqual((await transfer.run()).read_bytes(), content)

    async def test_tampered_partial_is_not_appended(self):
        transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=Pieces(b'he', fail=True)))
        with self.assertRaises(DownloadError):
            await transfer.run()
        transfer.part.write_bytes(b'changed')
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_partial')
        self.assertEqual(transfer.part.read_bytes(), b'changed')


    async def test_advisory_md5_mismatch_completes_without_false_corruption_warning(self):
        source = DownloadSource(URL, 5, md5='0' * 32, md5_is_content=False)
        transfer, _, _ = self.make(lambda req: httpx.Response(200, content=b'hello'),
                                   resolver=AsyncMock(return_value=source))
        self.assertEqual((await transfer.run()).read_bytes(), b'hello')
        self.assertEqual(transfer.state, 'completed')
        self.assertFalse(transfer.checksum_verified)
        self.assertEqual(transfer.warning, '')
        self.assertFalse(transfer.requires_restart)
        self.assertFalse(transfer.part.exists())

    async def test_matching_advisory_md5_is_reported_as_verified(self):
        source = DownloadSource(URL, 5, md5=hashlib.md5(b'hello').hexdigest(), md5_is_content=False)
        transfer, _, _ = self.make(lambda req: httpx.Response(200, content=b'hello'),
                                   resolver=AsyncMock(return_value=source))
        await transfer.run()
        self.assertTrue(transfer.checksum_verified)
        self.assertEqual(transfer.warning, '')

    async def test_advisory_mismatch_does_not_publish_error_response(self):
        content = b'{"status":403,"code":23018,"message":"denied"}'
        source = DownloadSource(URL, len(content), md5='0' * 32, md5_is_content=False)
        transfer, _, _ = self.make(lambda req: httpx.Response(200, content=content),
            content=content, resolver=AsyncMock(return_value=source))
        with self.assertRaises(DownloadError) as error:
            await transfer.run()
        self.assertEqual(error.exception.code, 'download_content')
        self.assertFalse(transfer.target.exists())

    async def test_advisory_identity_or_policy_change_still_blocks_resume(self):
        for second in (DownloadSource(URL, 5, md5='1' * 32, md5_is_content=False),
                       DownloadSource(URL, 5, md5='0' * 32, md5_is_content=True)):
            resolver = AsyncMock(side_effect=[DownloadSource(URL, 5, md5='0' * 32, md5_is_content=False), second])
            transfer, _, _ = self.make(lambda req: httpx.Response(200, stream=Pieces(b'he', fail=True)), resolver=resolver)
            with self.assertRaises(DownloadError): await transfer.run()
            with self.assertRaises(DownloadError) as error: await transfer.run()
            self.assertEqual(error.exception.code, 'download_changed')
            self.assertEqual(transfer.part.read_bytes(), b'he')
            await transfer.cancel()


if __name__ == '__main__':
    unittest.main()
