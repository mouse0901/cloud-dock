from __future__ import annotations
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
import httpx
from app.domain import CloudFile
from app.providers.demo import DemoBaiduProvider, validate_name
from app.providers.baidu_api import BaiduApi, BaiduApiError
from app.workspace import Workspace, Navigation

TEST_ROOT = Path(__file__).resolve().parents[1] / "work" / "test-runs"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

class DiskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.root = Path(self.tmp.name)
        self.disk = DemoBaiduProvider(self.root / "demo.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_pagination_does_not_drop_or_duplicate_items(self):
        self.disk.files = [CloudFile(str(i), "baidu", f"/item-{i:03}.txt") for i in range(127)]
        pages = [await self.disk.list_files(page=i) for i in range(3)]
        self.assertEqual([len(p.items) for p in pages], [50, 50, 27])
        self.assertEqual(len({f.id for p in pages for f in p.items}), 127)

    async def test_search_finds_nested_file_and_respects_category(self):
        result = await self.disk.list_files(query="纪要", category="document")
        self.assertEqual(result.total, 1)
        self.assertEqual(result.items[0].name, "周会纪要.md")
        self.assertEqual((await self.disk.list_files(query="纪要", category="image")).total, 0)

    async def test_rename_folder_preserves_child_ids_and_paths(self):
        folder = next(f for f in self.disk.files if f.path == "/我的文档")
        child = next(f for f in self.disk.files if f.name == "周会纪要.md")
        await self.disk.rename(folder.id, "个人文档")
        updated = next(f for f in self.disk.files if f.id == child.id)
        self.assertEqual(updated.path, "/个人文档/会议记录/周会纪要.md")
        restored = DemoBaiduProvider(self.root / "demo.json")
        self.assertEqual(restored.files, self.disk.files)

    async def test_conflicting_rename_leaves_disk_unchanged(self):
        folder = next(f for f in self.disk.files if f.path == "/我的文档")
        before = list(self.disk.files)
        with self.assertRaises(ValueError):
            await self.disk.rename(folder.id, "设计素材")
        self.assertEqual(before, self.disk.files)

    async def test_delete_subtree_does_not_match_similar_prefix(self):
        a = await self.disk.create_folder("/", "A")
        await self.disk.create_folder("/", "AB")
        await self.disk.create_folder("/A", "child")
        await self.disk.delete([a.id])
        self.assertTrue(any(f.path == "/AB" for f in self.disk.files))
        self.assertFalse(any(f.path == "/A" or f.path.startswith("/A/") for f in self.disk.files))

    async def test_invalid_names_cannot_escape_directory(self):
        for name in ["../escape", "a/b", "a\\b", "..", "bad:name", ""]:
            with self.assertRaises(ValueError, msg=name):
                validate_name(name)

    async def test_directory_creation_checks_parent_and_duplicates(self):
        with self.assertRaises(ValueError):
            await self.disk.create_folder("/不存在", "child")
        await self.disk.create_folder("/", "Unique")
        with self.assertRaises(ValueError):
            await self.disk.create_folder("/", "unique")

    async def test_transfer_concurrency_pause_and_restart(self):
        workspace = Workspace(self.root)
        files = [f for f in self.disk.files if not f.is_dir][:5]
        self.assertEqual(workspace.enqueue(files), 5)
        self.assertEqual(workspace.enqueue(files), 0)
        workspace.tick()
        self.assertEqual(sum(t.state == "running" for t in workspace.transfers), 3)
        task = workspace.transfers[0]
        workspace.transition(task.id, "pause")
        previous = task.progress
        workspace.tick()
        self.assertEqual(task.progress, previous)
        await workspace.save()
        restored = Workspace(self.root)
        self.assertTrue(all(t.state == "paused" for t in restored.transfers))
        self.assertTrue(all(t.simulation for t in restored.transfers))
        self.assertEqual(list(self.root.glob("*.part")), [])

    async def test_finished_transfer_cannot_resume(self):
        workspace = Workspace(self.root)
        workspace.enqueue([f for f in self.disk.files if not f.is_dir][:1])
        for _ in range(45):
            workspace.tick()
        task = workspace.transfers[0]
        self.assertEqual(task.state, "done")
        workspace.transition(task.id, "resume")
        self.assertEqual(task.state, "done")
        self.assertEqual(task.progress, 1)

class NavigationTests(unittest.TestCase):
    def test_back_forward_and_new_branch(self):
        nav = Navigation()
        nav.visit("/A")
        nav.visit("/A/B")
        self.assertEqual(nav.step(-1), "/A")
        self.assertEqual(nav.step(1), "/A/B")
        nav.step(-1)
        nav.visit("/C")
        self.assertEqual(nav.paths, ["/", "/A", "/C"])

class ApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_device_request_includes_response_type(self):
        def handle(request):
            self.assertEqual(request.url.host, "openapi.baidu.com")
            self.assertEqual(request.url.params["response_type"], "device_code")
            return httpx.Response(200, json={"device_code": "test", "user_code": "ABCD", "verification_url": "https://openapi.baidu.com/device", "qrcode_url": "https://openapi.baidu.com/device/qrcode/test/user", "interval": 5, "expires_in": 300})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            grant = await BaiduApi(client).device_code("test-app")
            self.assertEqual(grant.interval, 5)
            self.assertEqual(grant.user_code, "ABCD")

    async def test_list_maps_remote_fields_and_preserves_pagination(self):
        def handle(request):
            self.assertEqual(request.url.params["start"], "50")
            self.assertEqual(request.url.params["limit"], "50")
            return httpx.Response(200, json={"errno": 0, "list": [{"fs_id": 123, "path": "/A/report.pdf", "server_filename": "report.pdf", "isdir": 0, "size": 512}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            files = await BaiduApi(client).list_files("test-only", "/A", 50, 50)
            self.assertEqual(files[0].id, "123")
            self.assertEqual(files[0].provider, "baidu")
            self.assertFalse(files[0].is_dir)

    async def test_pending_auth_remains_distinguishable(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"error": "authorization_pending"}))) as client:
            with self.assertRaises(BaiduApiError) as caught:
                await BaiduApi(client).exchange_device("app", "test-secret", "device")
            self.assertEqual(caught.exception.code, "authorization_pending")
            self.assertNotIn("test-secret", str(caught.exception))

    async def test_business_error_is_not_an_empty_success(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"errno": -6}))) as client:
            with self.assertRaises(BaiduApiError):
                await BaiduApi(client).list_files("test-only")

if __name__ == "__main__":
    unittest.main()
