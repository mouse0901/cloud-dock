"""Headless callback smoke tests. These do not control the live desktop window."""
from pathlib import Path
from types import SimpleNamespace
import asyncio
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import flet as ft
from app.ui import CloudDock
from app.domain import CloudFile
from app.providers.demo import DemoBaiduProvider, PendingQuarkProvider
from app.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]

class PageStub:
    platform_brightness = ft.Brightness.LIGHT
    def __init__(self):
        self.controls = []
        self.dialogs = []
    def add(self, control): self.controls.append(control)
    def update(self): pass
    def show_dialog(self, dialog): self.dialogs.append(dialog)
    def pop_dialog(self): return self.dialogs.pop() if self.dialogs else None

class UiSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / 'work' / 'test-runs')
        self.root = Path(self.tmp.name)
        self.page = PageStub()
        self.disk = DemoBaiduProvider(self.root / 'demo-files.json')
        self.workspace = Workspace(self.root)
        self.ui = CloudDock(self.page, self.disk, PendingQuarkProvider(), self.workspace)
        self.update_patch = patch.object(ft.Control, 'update', return_value=None)
        self.update_patch.start()
        await self.ui.load_files(False)
        self.ui.render_shell()

    async def asyncTearDown(self):
        if self.ui.search_task:
            self.ui.search_task.cancel()
            await asyncio.gather(self.ui.search_task, return_exceptions=True)
        await self.ui.disconnect(None)
        self.update_patch.stop()
        self.tmp.cleanup()

    async def test_all_pages_construct_in_both_themes(self):
        for theme in ['light', 'dark']:
            await self.ui.set_setting('theme', theme)
            for section in ['overview', 'baidu', 'baidu_live', 'quark', 'transfers', 'settings']:
                await self.ui.navigate(section)
                self.assertIsNotNone(self.ui.body.content)
                self.assertEqual(self.ui.search.visible, section == 'baidu')

    async def test_folder_history_and_breadcrumb(self):
        await self.ui.visit('/我的文档')
        await self.ui.visit('/我的文档/会议记录')
        self.assertEqual(self.ui.files.items[0].name, '周会纪要.md')
        await self.ui.history(-1)
        self.assertEqual(self.ui.nav.current, '/我的文档')
        await self.ui.history(1)
        self.assertEqual(self.ui.nav.current, '/我的文档/会议记录')
        await self.ui.visit('/')
        self.assertEqual(self.ui.nav.current, '/')

    async def test_latest_search_wins_and_filters(self):
        await self.ui.on_search(SimpleNamespace(control=SimpleNamespace(value='品牌')))
        await self.ui.on_search(SimpleNamespace(control=SimpleNamespace(value='海岸')))
        await self.ui.search_task
        self.assertEqual(self.ui.files.total, 2)
        await self.ui.filter_files('image')
        self.assertEqual([f.name for f in self.ui.files.items], ['秋日海岸.jpg'])

    async def test_paging_clears_selection(self):
        self.disk.files = [CloudFile(str(i), 'baidu', f'/item-{i:03}.txt') for i in range(72)]
        await self.ui.load_files()
        await self.ui.select_all(None)
        self.assertEqual(len(self.ui.selected), 50)
        await self.ui.paginate(1)
        self.assertEqual(self.ui.selected, set())
        self.assertEqual(len(self.ui.files.items), 22)

    async def test_create_and_rename_dialog_callbacks(self):
        await self.ui.new_folder(None)
        dialog = self.page.dialogs[-1]
        dialog.content.content.controls[0].value = '烟测文件夹'
        await dialog.actions[-1].on_click(None)
        folder = next(f for f in self.disk.files if f.name == '烟测文件夹')
        await self.ui.name_dialog(folder)
        dialog = self.page.dialogs[-1]
        dialog.content.content.controls[0].value = '烟测已改名'
        await dialog.actions[-1].on_click(None)
        self.assertTrue(any(f.name == '烟测已改名' for f in self.disk.files))

    async def test_invalid_name_keeps_dialog_open(self):
        await self.ui.new_folder(None)
        dialog = self.page.dialogs[-1]
        dialog.content.content.controls[0].value = '../bad'
        await dialog.actions[-1].on_click(None)
        self.assertIs(self.page.dialogs[-1], dialog)
        self.assertTrue(dialog.content.content.controls[1].value)

    async def test_download_queue_controls_and_persistence(self):
        item = next(f for f in self.ui.files.items if not f.is_dir)
        await self.ui.select(item, None)
        await self.ui.download_selected(None)
        self.assertEqual(len(self.workspace.transfers), 1)
        task = self.workspace.transfers[0]
        self.workspace.tick()
        await self.ui.navigate('transfers')
        self.assertIn(task.id, self.ui.progress_refs)
        await self.ui.transfer_action(task.id, 'pause')
        self.assertEqual(task.state, 'paused')
        await self.ui.transfer_action(task.id, 'resume')
        self.assertEqual(task.state, 'queued')
        await self.ui.transfer_action(task.id, 'cancel')
        self.assertEqual(task.state, 'cancelled')
        await self.ui.switch_transfer_tab('completed')
        self.assertIn(task.id, self.ui.progress_refs)
        self.assertEqual(Workspace(self.root).transfers[0].state, 'cancelled')

    async def test_view_and_theme_survive_restart(self):
        await self.ui.set_view('grid')
        await self.ui.set_setting('theme', 'dark')
        await self.ui.disconnect(None)
        restored = Workspace(self.root)
        self.assertEqual(restored.settings['view'], 'grid')
        self.assertEqual(restored.settings['theme'], 'dark')

if __name__ == '__main__': unittest.main()
