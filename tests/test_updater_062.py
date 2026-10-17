import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path

from app.updater import UpdateClient, UpdateSecurityError, UpdateService, is_newer, verify_rs256

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


if __name__ == "__main__": unittest.main()
