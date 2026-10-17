"""Verify production panel wiring and direct-only download pool, without login."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from app.parallel_download import ParallelDownload
from app.direct_download import DownloadSource
from app.domain import CloudFile
from app.quark_panel import QuarkPanel
from app.session_vault import SessionVault
from test_quark_panel import PageStub
from test_parallel_download import DATA, MD5, Service, URL

ROOT = Path(__file__).resolve().parents[1]

class QuarkPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_clients_share_scoped_jar_and_downloads_ignore_proxy(self):
        folder = tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs')
        self.addCleanup(folder.cleanup)
        with patch.dict(os.environ, {'HTTPS_PROXY':'http://127.0.0.1:9','ALL_PROXY':'http://127.0.0.1:9','NO_PROXY':''}):
            panel = QuarkPanel(PageStub(),diagnostic=lambda _:None,session_store=SessionVault(folder.name))
        self.addAsyncCleanup(panel.shutdown)
        self.assertIsNot(panel.client, panel.download_client)
        self.assertIs(panel.client.cookies.jar, panel.download_client.cookies.jar)
        download_pool = panel.download_client._transport_for_url(httpx.URL(URL))._pool
        api_pool = panel.client._transport_for_url(httpx.URL(URL))._pool
        self.assertNotIn('Proxy', type(download_pool).__name__)
        self.assertIn('Proxy', type(api_pool).__name__)
        self.assertEqual(download_pool._max_connections,128)
        panel.client.cookies.set('account','own',domain='.quark.cn',path='/')
        self.assertEqual(panel.download_client.cookies.get('account'),'own')
        panel.download_client.cookies.set('renewed','value',domain='.quark.cn',path='/')
        self.assertEqual(panel.client.cookies.get('renewed'),'value')
        await panel.cancel()
        self.assertEqual(len(panel.download_client.cookies),0)
        await panel.shutdown()
        self.assertTrue(panel.client.is_closed)
        self.assertTrue(panel.download_client.is_closed)

    async def test_production_queue_uses_parallel_job_and_injected_transport(self):
        service = Service()
        client = httpx.AsyncClient(transport=httpx.MockTransport(service))
        panel = QuarkPanel(PageStub(),client=client,diagnostic=lambda _:None)
        self.addAsyncCleanup(panel.shutdown)
        self.assertIs(panel.client,panel.download_client)
        item = CloudFile('sample','quark','/sample.bin',False,len(DATA))
        panel.reader = object()
        panel.api.authenticated,panel.api.session_id = True,'fixture-session'
        panel.api.download_source = AsyncMock(return_value=DownloadSource(URL,len(DATA),md5=MD5))
        with tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs') as folder:
            panel.download_destination = Path(folder)
            await panel.start_download(item)
            await asyncio.wait_for(panel._live_worker,5)
            job = panel.jobs[0]
            self.assertIsInstance(job,ParallelDownload)
            self.assertIs(job.gate,panel.download_gate)
            self.assertEqual(job.state,'completed')
            self.assertEqual(job.target.read_bytes(),DATA)
            self.assertGreater(service.peak,1)
            job.state, job.active_connections = 'downloading',7
            row = panel.render_live_downloads().controls[0]
            self.assertIn('分片连接 7/128', row.content.controls[1].value)
            job.transfer_mode = 'single'
            row = panel.render_live_downloads().controls[0]
            self.assertIn('单连接', row.content.controls[1].value)
            self.assertNotIn('/128', row.content.controls[1].value)
            job.state = 'completed'
            self.assertGreater(job.elapsed_seconds,0)
            self.assertGreater(job.average_speed,0)
            row = panel.render_live_downloads().controls[0]
            self.assertIn('平均',row.content.controls[1].value)
            self.assertIn('用时',row.content.controls[1].value)
