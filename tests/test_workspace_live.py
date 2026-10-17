"""Daily workflow regressions using isolated sessions and mock network traffic."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import httpx
from app.baidu_web_panel import BaiduWebPanel
from app.quark_panel import QuarkPanel
from app.domain import CloudFile
from app.direct_download import DownloadSource
from app.session_vault import SessionVault
from app.workspace import Workspace
from app.ui import CloudDock
from app.providers.demo import DemoBaiduProvider, PendingQuarkProvider
from app.local_files import open_local_folder
from test_ui_smoke import PageStub

ROOT = Path(__file__).resolve().parents[1]

class LiveWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'work/test-runs')
        self.root = Path(self.tmp.name)
        self.page = PageStub()
        self.panels = []

    async def asyncTearDown(self):
        for panel in self.panels:
            await panel.shutdown()
        self.tmp.cleanup()

    def make_panel(self,provider='quark',connected=True):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'data')))
        cls = QuarkPanel if provider=='quark' else BaiduWebPanel
        panel = cls(self.page,client=client,diagnostic=lambda _:None,session_store=SessionVault(self.root/'sessions'))
        self.panels.append(panel)
        panel.set_download_root(self.root/'downloads')
        if connected:
            panel.api.authenticated = True
            panel.api.session_id = 'fixture'
            panel.reader = SimpleNamespace(root='/',list_page=AsyncMock(return_value=[]))
            panel.download_destination = panel.new_download_destination(provider)
            panel.api.download_source = AsyncMock(return_value=DownloadSource(
                'https://pdds.quark.cn/fixture' if provider=='quark' else 'https://download.baidu.com/fixture',4))
        return panel

    def rows(self,provider='quark'):
        return [CloudFile('folder',provider,'/资料',True),CloudFile('a',provider,'/A.pdf',False,4),
                CloudFile('b',provider,'/B.png',False,4),CloudFile('c',provider,'/C.txt',False,4)]

    async def load(self,panel,rows):
        panel.reader.list_page.return_value = rows
        await panel.load_directory('/',0)

    async def test_filter_select_and_batch_download_real_bytes_for_both_drives(self):
        for provider in ('quark','baidu'):
            panel=self.make_panel(provider)
            await self.load(panel,self.rows(provider))
            self.assertFalse(panel.account_box.visible)
            panel.browser_type.value='document'
            await panel.filter_browser()
            await panel.select_browser_all()
            self.assertEqual(panel.browser_selected,{'a','c'})
            await panel.download_browser_selection()
            await panel._live_worker
            self.assertEqual([job.item.id for job in panel.jobs],['a','c'])
            self.assertTrue(all(job.state=='completed' and job.target.read_bytes()==b'data' for job in panel.jobs))
            self.assertFalse(panel.browser_selected)
            self.assertTrue(panel.browser_download.disabled)
            self.assertNotIn('folder',[job.item.id for job in panel.jobs])

    async def test_filter_hides_selection_and_does_not_request_network(self):
        panel=self.make_panel()
        await self.load(panel,self.rows())
        await panel.select_browser_all()
        panel.browser_search.value='b.PNG'
        await panel.filter_browser()
        self.assertEqual(panel.browser_selected,{'b'})
        self.assertEqual([x.title.value for x in panel.files.controls],['B.png'])
        self.assertEqual(panel.reader.list_page.await_count,1)
        panel.browser_search.value='missing'
        await panel.filter_browser()
        self.assertFalse(panel.browser_selected)
        self.assertTrue(panel.browser_download.disabled)

    async def test_loading_and_failed_navigation_clear_stale_selection(self):
        panel=self.make_panel()
        await self.load(panel,self.rows())
        await panel.select_browser_all()
        pending=asyncio.Event()
        async def delayed(*args):
            pending.set()
            await asyncio.Event().wait()
        panel.reader.list_page.side_effect=delayed
        task=asyncio.create_task(panel.load_directory('/资料',0))
        await pending.wait()
        self.assertFalse(panel.browser_selected)
        self.assertTrue(panel.browser_download.disabled)
        await panel.download_browser_selection()
        self.assertFalse(panel.jobs)
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        self.assertFalse(panel.browser_selected)
        self.assertTrue(panel.next_button.disabled)

    async def test_queued_duplicate_skipped_and_destination_change_does_not_move_jobs(self):
        panel=self.make_panel()
        await self.load(panel,self.rows())
        panel._ensure_live_worker=Mock()
        await panel.select_browser_all()
        await panel.download_browser_selection()
        original=[job.destination for job in panel.jobs]
        panel.set_download_root(self.root/'other')
        await panel.select_browser_all()
        await panel.download_browser_selection()
        self.assertEqual(len(panel.jobs),3)
        self.assertEqual([job.destination for job in panel.jobs],original)
        self.assertEqual(panel.download_destination.parent,self.root/'other')

    async def test_saved_only_startup_with_no_session_does_not_start_qr(self):
        for provider in ('baidu','quark'):
            panel=self.make_panel(provider,connected=False)
            panel.begin=AsyncMock()
            await panel.startup(saved_only=True)
            panel.begin.assert_not_awaited()
            self.assertFalse(panel.started)
            self.assertIsNone(panel.task)
            await panel.startup()
            panel.begin.assert_awaited_once()

    async def test_main_restores_both_accounts_without_forcing_scan_and_persists_directory(self):
        ui=CloudDock(self.page,DemoBaiduProvider(self.root/'demo.json'),PendingQuarkProvider(),
                     Workspace(self.root),initial_section='overview')
        ui.baidu_panel=self.make_panel('baidu',connected=False)
        ui.quark_panel=self.make_panel('quark',connected=False)
        for panel in self.panels: panel.startup=AsyncMock()
        ui.render_shell()
        await ui.restore_saved_accounts()
        for panel in self.panels: panel.startup.assert_awaited_once_with(saved_only=True)
        folder=self.root/'new downloads'
        self.assertTrue(await ui.change_download_directory(str(folder)))
        self.assertEqual(Workspace(self.root).settings['download_dir'],str(folder))
        self.assertTrue(all(panel.download_root==folder for panel in self.panels))
        self.assertFalse(await ui.change_download_directory('relative-folder'))
        self.assertEqual(ui.workspace.settings['download_dir'],str(folder))

    async def test_open_completed_folder_never_executes_file_and_missing_folder_is_reported(self):
        panel=self.make_panel()
        await self.load(panel,self.rows())
        await panel.start_download(self.rows()[1])
        await panel._live_worker
        job=panel.jobs[0]
        with patch('app.local_files.os.startfile') as start:
            await panel.open_completed_folder(job)
            start.assert_called_once_with(str(job.target.parent.resolve()))
        with patch('app.live_download_panel.open_local_folder',side_effect=OSError()):
            await panel.open_completed_folder(job)
            self.assertIn('无法打开',self.page.dialogs[-1].content.value)
        with patch('app.local_files.os.startfile') as start:
            with self.assertRaises(OSError): open_local_folder(job.target)
            start.assert_not_called()

    async def test_logout_clears_account_selection_and_reveals_login_controls(self):
        panel=self.make_panel()
        await self.load(panel,self.rows())
        await panel.select_browser_all()
        await panel.cancel()
        self.assertFalse(panel.browser_rows)
        self.assertFalse(panel.browser_selected)
        self.assertTrue(panel.account_box.visible)
        self.assertFalse(panel.results.visible)
