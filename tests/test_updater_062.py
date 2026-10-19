import asyncio
import base64
import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path

from app.updater import UpdateClient, UpdateError, UpdateSecurityError, UpdateService, is_newer, verify_rs256

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures"


class Response:
    def __init__(self, data, url, status=200, headers=None):
        self.data = data
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.offset = 0

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def getcode(self): return self.status
    def geturl(self): return self.url
    def read(self, size=-1):
        if size < 0: size = len(self.data) - self.offset
        result = self.data[self.offset:self.offset + size]
        self.offset += len(result)
        return result


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.payload = (FIXTURE / "update-beta.json").read_bytes()
        self.signature = (FIXTURE / "update-beta.sig").read_bytes()
        self.source = json.loads((ROOT / "update-source.json").read_text(encoding="utf-8"))

    def test_version_order(self):
        self.assertTrue(is_newer("0.6.3", "0.6.2"))
        self.assertTrue(is_newer("0.6.2", "0.6.2-beta.1"))
        self.assertFalse(is_newer("0.6.2-beta.1", "0.6.2"))
        with self.assertRaises(UpdateSecurityError): is_newer("v0.6.3", "0.6.2")

    def test_signature_accepts_fixture_and_rejects_tamper(self):
        verify_rs256(self.payload, self.signature, self.source["public_key"])
        with self.assertRaises(UpdateSecurityError):
            verify_rs256(self.payload.replace(b"9.9.9", b"9.9.8"), self.signature, self.source["public_key"])

    def test_signed_channel_check(self):
        config = json.loads(json.dumps(self.source))
        config["enabled"] = True; config["repository"] = "example/cloud-dock-releases"
        base = "https://raw.githubusercontent.com/example/cloud-dock-releases/main/channels/beta"
        config["channels"]["beta"] = {"manifest": base + ".json", "signature": base + ".sig"}
        replies = {base + ".json": self.payload, base + ".sig": self.signature}
        def open_fixture(request, timeout=0): return Response(replies[request.full_url], request.full_url)
        manifest = UpdateClient(config, open_fixture).check("beta")
        self.assertEqual(manifest["version"], "9.9.9")
        self.assertEqual(manifest["artifact"]["kind"], "installer")

    def test_signed_channel_falls_back_to_github_contents_api(self):
        config = json.loads(json.dumps(self.source))
        config["enabled"] = True; config["repository"] = "example/cloud-dock-releases"
        raw = "https://raw.githubusercontent.com/example/cloud-dock-releases/main/channels/beta"
        config["channels"]["beta"] = {"manifest": raw + ".json", "signature": raw + ".sig"}
        files = {"channels/beta.json": self.payload, "channels/beta.sig": self.signature}
        def api_only(request, timeout=0):
            if request.full_url.startswith(raw):
                raise urllib.error.URLError("raw blocked")
            path = request.full_url.split("/contents/", 1)[1].split("?", 1)[0]
            body = json.dumps({"type":"file","path":path,"encoding":"base64",
                               "content":base64.b64encode(files[path]).decode()}).encode()
            return Response(body, request.full_url)
        client = UpdateClient(config, api_only)
        manifest = client.check("beta")
        self.assertEqual(manifest["version"], "9.9.9")
        self.assertTrue(client.last_route.startswith("GitHub API"))

    def test_proxy_modes_are_validated_and_auto_prefers_active_local_proxy(self):
        client = UpdateClient(self.source, proxy_mode="auto", probe=lambda port: port == 19828)
        self.assertEqual(client._routes[0][0], "本机代理 127.0.0.1:19828")
        client.configure_proxy("manual", "http://127.0.0.1:19828")
        self.assertEqual(client.proxy_url, "http://127.0.0.1:19828")
        with self.assertRaises(UpdateError): client.configure_proxy("manual", "http://name:secret@127.0.0.1:19828")
        with self.assertRaises(UpdateError): client.configure_proxy("unknown", "")

    def test_resumable_download_and_hash(self):
        content = b"hello world!"
        digest = hashlib.sha256(content).hexdigest()
        manifest = {"version":"9.9.9","artifact":{"url":"https://github.com/example/cloud-dock-releases/releases/download/v9.9.9/CloudDock-9.9.9-Setup-x64.exe","size":len(content),"sha256":digest}}
        def resume(request, timeout=0):
            self.assertEqual(request.headers.get("Range"), "bytes=5-")
            return Response(content[5:], "https://release-assets.githubusercontent.com/example", 206, {"Content-Range":"bytes 5-11/12"})
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "CloudDock-9.9.9-Setup-x64.exe"
            target.with_suffix(".exe.part").write_bytes(content[:5])
            result = UpdateClient(self.source, resume).download(manifest, target, threading.Event())
            self.assertEqual(result.read_bytes(), content)
            self.assertFalse(target.with_suffix(".exe.part").exists())

    def test_download_retries_next_network_route(self):
        content = b"proxy route content"
        digest = hashlib.sha256(content).hexdigest()
        manifest = {"version":"9.9.9","artifact":{"url":"https://github.com/example/cloud-dock-releases/releases/download/v9.9.9/CloudDock-9.9.9-Setup-x64.exe","size":len(content),"sha256":digest}}
        def unavailable(request, timeout=0): raise urllib.error.URLError("direct blocked")
        def proxied(request, timeout=0): return Response(content, "https://release-assets.githubusercontent.com/example")
        client = UpdateClient(self.source, unavailable)
        client._routes = [("直接连接", unavailable), ("本机代理 127.0.0.1:19828", proxied)]
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "CloudDock-9.9.9-Setup-x64.exe"
            self.assertEqual(client.download(manifest, target, threading.Event()).read_bytes(), content)
            self.assertIn("127.0.0.1:19828", client.last_route)

    def test_manifest_rejects_wrong_repository(self):
        manifest = json.loads(self.payload)
        with self.assertRaises(UpdateSecurityError):
            UpdateClient.validate(manifest, "beta", "another/repository")

    def test_unsigned_persisted_state_is_not_restored(self):
        class Workspace:
            async def save(self): pass
        content=b"local executable";digest=hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            workspace=Workspace();workspace.data_dir=Path(folder);workspace.settings={"update_channel":"beta"}
            updates=workspace.data_dir/"updates";updates.mkdir()
            artifact={"kind":"installer","architecture":"windows-x64","url":"https://github.com/example/cloud-dock-releases/releases/download/v9.9.9/CloudDock-9.9.9-Setup-x64.exe","size":len(content),"sha256":digest}
            (updates/"CloudDock-9.9.9-Setup-x64.exe").write_bytes(content)
            (updates/"state.json").write_text(json.dumps({"version":"9.9.9","artifact":artifact,"manifest":{"version":"9.9.9","artifact":artifact}}),encoding="utf-8")
            config=json.loads(json.dumps(self.source));config.update(enabled=True,repository="example/cloud-dock-releases")
            service=UpdateService(workspace,lambda:False,UpdateClient(config))
            self.assertEqual(service.state["status"],"idle")
            self.assertIsNone(service.manifest)

    def test_service_saves_manual_proxy_without_credentials(self):
        class Workspace:
            def __init__(self, folder):
                self.data_dir=Path(folder);self.settings={"update_channel":"beta","update_proxy_mode":"auto","update_proxy_url":""}
            async def save(self): self.saved=True
        with tempfile.TemporaryDirectory() as folder:
            workspace=Workspace(folder);client=UpdateClient(self.source, lambda *args,**kwargs: None)
            service=UpdateService(workspace,lambda:False,client)
            asyncio.run(service.preferences({"proxy_mode":"manual","proxy_url":"http://127.0.0.1:19828"}))
            self.assertEqual(workspace.settings["update_proxy_url"],"http://127.0.0.1:19828")
            self.assertEqual(service.snapshot()["proxy_mode"],"manual")

    def test_channel_save_failure_restores_previous_choice(self):
        class Workspace:
            def __init__(self, folder):
                self.data_dir=Path(folder);self.settings={"update_channel":"stable"}
            async def save(self): raise OSError("disk full")
        with tempfile.TemporaryDirectory() as folder:
            workspace=Workspace(folder);service=UpdateService(workspace,lambda:False,UpdateClient(self.source))
            with self.assertRaises(OSError):asyncio.run(service.preferences({"channel":"beta"}))
            self.assertEqual(workspace.settings["update_channel"],"stable")
            self.assertEqual(service.snapshot()["channel"],"stable")

    def test_previous_channel_check_cannot_replace_new_selection(self):
        class Workspace:
            def __init__(self, folder):
                self.data_dir=Path(folder);self.settings={"update_channel":"stable"}
            async def save(self):pass
        config=json.loads(json.dumps(self.source));config["enabled"]=True
        with tempfile.TemporaryDirectory() as folder:
            started=threading.Event();release=threading.Event()
            client=UpdateClient(config)
            def slow_check(channel):
                started.set();release.wait(2);return None
            client.check=slow_check
            workspace=Workspace(folder);service=UpdateService(workspace,lambda:False,client)
            async def run():
                await service.check();old=service.task
                self.assertTrue(await asyncio.to_thread(started.wait,1))
                await service.preferences({"channel":"beta"});release.set();await old
                self.assertEqual(service.snapshot()["channel"],"beta")
                self.assertEqual(service.snapshot()["status"],"idle")
            asyncio.run(run())


if __name__ == "__main__": unittest.main()
