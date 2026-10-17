"""Signed, resumable application updates. No account credentials are involved."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))["version"]
ACTIVE_STATES = {"queued", "resolving", "preparing", "uploading", "downloading", "committing", "verifying"}
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$")
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


class UpdateError(Exception):
    pass


class UpdateSecurityError(UpdateError):
    pass


class UpdateCancelled(UpdateError):
    pass


def _version(value):
    match = _VERSION.fullmatch(value or "")
    if not match:
        raise UpdateSecurityError("更新版本号无效。")
    core = tuple(int(x) for x in match.groups()[:3])
    pre = match.group(4)
    # Stable builds sort after prereleases with the same numeric core.
    return core + ((1, "") if pre is None else (0, pre))


def is_newer(candidate, current=VERSION):
    return _version(candidate) > _version(current)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateSecurityError("更新清单包含重复字段。")
        result[key] = value
    return result


def verify_rs256(payload, signature, key):
    """Verify PKCS#1 v1.5 SHA-256 without adding a runtime crypto dependency."""
    try:
        modulus = int.from_bytes(base64.b64decode(key["modulus"], validate=True), "big")
        exponent = int.from_bytes(base64.b64decode(key["exponent"], validate=True), "big")
        raw = base64.b64decode(signature.strip(), validate=True)
    except (KeyError, ValueError, TypeError) as error:
        raise UpdateSecurityError("更新签名配置无效。") from error
    size = (modulus.bit_length() + 7) // 8
    if size < 256 or exponent < 3 or len(raw) != size:
        raise UpdateSecurityError("更新签名长度无效。")
    decoded = pow(int.from_bytes(raw, "big"), exponent, modulus).to_bytes(size, "big")
    expected = b"\x00\x01" + b"\xff" * (size - len(_DIGEST_INFO) - 35) + b"\x00" + _DIGEST_INFO + hashlib.sha256(payload).digest()
    if not __import__("hmac").compare_digest(decoded, expected):
        raise UpdateSecurityError("更新签名验证失败，已停止下载。")


def _https(url, hosts=None, prefix=None):
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise UpdateSecurityError("更新地址无效。") from error
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise UpdateSecurityError("更新地址不受信任。")
    if hosts and parsed.hostname.lower() not in hosts:
        raise UpdateSecurityError("更新地址不受信任。")
    if prefix and not url.startswith(prefix):
        raise UpdateSecurityError("安装包不属于配置的发布仓库。")
    return parsed


def load_source(path=None):
    source = Path(path) if path else ROOT / "update-source.json"
    try:
        config = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, ValueError, TypeError) as error:
        raise UpdateSecurityError("更新源配置无效。") from error
    if config.get("public_key", {}).get("algorithm") != "RS256":
        raise UpdateSecurityError("不支持此更新签名算法。")
    return config


class UpdateClient:
    def __init__(self, config=None, opener=None):
        self.config = config or load_source()
        self.opener = opener or urllib.request.urlopen

    def _read(self, url, limit):
        _https(url, {"raw.githubusercontent.com"})
        request = urllib.request.Request(url, headers={"User-Agent": "CloudDock-Updater/" + VERSION, "Accept": "application/octet-stream"})
        try:
            with self.opener(request, timeout=15) as response:
                if getattr(response, "status", response.getcode()) != 200:
                    raise UpdateError("更新服务暂时不可用。")
                data = response.read(limit + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UpdateError("无法连接更新服务，请稍后重试。") from error
        if len(data) > limit:
            raise UpdateSecurityError("更新响应超过安全限制。")
        return data

    def check(self, channel):
        if not self.config.get("enabled"):
            return None
        if channel not in ("stable", "beta"):
            raise UpdateSecurityError("更新通道无效。")
        entry = self.config.get("channels", {}).get(channel) or {}
        manifest_url, signature_url = entry.get("manifest", ""), entry.get("signature", "")
        repository = self.config.get("repository", "").strip("/")
        expected = "https://raw.githubusercontent.com/" + repository + "/main/channels/" + channel
        if manifest_url != expected + ".json" or signature_url != expected + ".sig":
            raise UpdateSecurityError("更新通道地址与发布仓库不匹配。")
        payload = self._read(manifest_url, 256 * 1024)
        signature = self._read(signature_url, 16 * 1024)
        verify_rs256(payload, signature, self.config["public_key"])
        try:
            manifest = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeError, ValueError, TypeError) as error:
            raise UpdateSecurityError("更新清单格式无效。") from error
        self.validate(manifest, channel, repository)
        manifest["_signed_payload"] = base64.b64encode(payload).decode("ascii")
        manifest["_signature"] = signature.decode("ascii").strip()
        return manifest

    @staticmethod
    def validate(manifest, channel, repository):
        if manifest.get("schema") != 1 or manifest.get("product") != "cloud-dock" or manifest.get("channel") != channel:
            raise UpdateSecurityError("更新清单与当前产品不匹配。")
        _version(manifest.get("version"))
        notes = manifest.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 8000:
            raise UpdateSecurityError("更新说明无效。")
        artifact = manifest.get("artifact")
        if not isinstance(artifact, dict) or artifact.get("kind") != "installer" or artifact.get("architecture") != "windows-x64":
            raise UpdateSecurityError("更新包不适用于当前程序。")
        digest = artifact.get("sha256", "")
        size = artifact.get("size")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or type(size) is not int or not 1 <= size <= 512 * 1024 * 1024:
            raise UpdateSecurityError("更新包校验信息无效。")
        prefix = "https://github.com/" + repository + "/releases/download/"
        _https(artifact.get("url", ""), {"github.com"}, prefix)

    def download(self, manifest, destination, cancel, progress=None):
        artifact = manifest["artifact"]
        final = Path(destination)
        part = final.with_suffix(final.suffix + ".part")
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.is_file() and final.stat().st_size == artifact["size"] and _sha256(final) == artifact["sha256"]:
            return final
        existing = part.stat().st_size if part.is_file() else 0
        if existing > artifact["size"]:
            part.unlink(); existing = 0
        free = shutil.disk_usage(final.parent).free
        if free < artifact["size"] - existing + 64 * 1024 * 1024:
            raise UpdateError("磁盘空间不足，无法下载更新。")
        headers = {"User-Agent": "CloudDock-Updater/" + VERSION, "Accept": "application/octet-stream"}
        if existing:
            headers["Range"] = "bytes=" + str(existing) + "-"
        request = urllib.request.Request(artifact["url"], headers=headers)
        try:
            with self.opener(request, timeout=30) as response:
                status = getattr(response, "status", response.getcode())
                final_url = response.geturl()
                _https(final_url, {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"})
                if existing and status != 206:
                    existing = 0
                elif existing and not response.headers.get("Content-Range", "").startswith("bytes " + str(existing) + "-"):
                    raise UpdateSecurityError("服务器返回了无效的续传范围。")
                mode = "ab" if existing else "wb"
                done = existing
                with part.open(mode) as output:
                    while True:
                        if cancel.is_set():
                            raise UpdateCancelled("已暂停更新下载。")
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        done += len(block)
                        if done > artifact["size"]:
                            raise UpdateSecurityError("下载内容超过清单大小。")
                        output.write(block)
                        if progress:
                            progress(done, artifact["size"])
                    output.flush(); os.fsync(output.fileno())
        except UpdateError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UpdateError("更新下载中断，可稍后继续。") from error
        if part.stat().st_size != artifact["size"]:
            raise UpdateError("更新下载尚未完成，可稍后继续。")
        if _sha256(part) != artifact["sha256"]:
            part.unlink(missing_ok=True)
            raise UpdateSecurityError("更新包校验失败，损坏文件已删除。")
        os.replace(part, final)
        return final


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class UpdateService:
    def __init__(self, workspace, busy, client=None):
        self.workspace = workspace
        self.busy = busy
        self.client = client or UpdateClient()
        self.directory = workspace.data_dir / "updates"
        self.state_path = self.directory / "state.json"
        self.task = None
        self.cancel = threading.Event()
        self.closed = False
        self.state = {"status": "disabled" if not self.client.config.get("enabled") else "idle", "current": VERSION,
                      "channel": workspace.settings.get("update_channel", "beta"), "progress": 0, "downloaded": 0,
                      "total": 0, "error": "", "available": "", "notes": "", "install_when_idle": False}
        self.manifest = None
        self._restore()

    def _restore(self):
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            manifest = saved["manifest"]
            payload = base64.b64decode(manifest["_signed_payload"], validate=True)
            signature = manifest["_signature"].encode("ascii")
            verify_rs256(payload, signature, self.client.config["public_key"])
            verified = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
            channel = self.workspace.settings.get("update_channel", "beta")
            UpdateClient.validate(verified, channel, self.client.config.get("repository", ""))
            verified["_signed_payload"] = manifest["_signed_payload"]
            verified["_signature"] = manifest["_signature"]
            if saved.get("version") == verified.get("version") and saved.get("artifact") == verified.get("artifact") and is_newer(saved["version"]):
                artifact = saved.get("artifact", {})
                path = self.directory / ("CloudDock-" + saved["version"] + "-Setup-x64.exe")
                if path.is_file() and path.stat().st_size == artifact.get("size") and _sha256(path) == artifact.get("sha256"):
                    self.manifest = verified
                    self.state.update(status="downloaded", available=saved["version"], notes=self.manifest.get("notes", ""),
                                      downloaded=artifact["size"], total=artifact["size"], progress=1)
        except (OSError, ValueError, TypeError, UnicodeError, KeyError, UpdateError):
            pass

    def snapshot(self):
        result = dict(self.state)
        result["enabled"] = bool(self.client.config.get("enabled"))
        result["auto_check"] = self.workspace.settings.get("update_auto_check", True)
        result["auto_download"] = self.workspace.settings.get("update_auto_download", False)
        result["busy"] = bool(self.busy())
        result["install_supported"] = (ROOT / "installed-data.txt").is_file()
        result["can_install"] = result["status"] == "downloaded" and not result["busy"] and result["install_supported"]
        result["skipped"] = self.workspace.settings.get("update_skipped", "")
        result["last_checked"] = self.workspace.settings.get("update_last_checked", 0)
        return result

    async def start(self, delay=20):
        if not self.client.config.get("enabled") or not self.workspace.settings.get("update_auto_check", True):
            return
        if time.time() - self.workspace.settings.get("update_last_checked", 0) < 24 * 3600:
            return
        await asyncio.sleep(delay)
        if not self.closed:
            await self.check(False)

    async def preferences(self, data):
        for key in ("auto_check", "auto_download"):
            if key in data:
                if type(data[key]) is not bool:
                    raise UpdateError("更新设置无效。")
                self.workspace.settings["update_" + key] = data[key]
        if "channel" in data:
            if data["channel"] not in ("stable", "beta"):
                raise UpdateError("更新通道无效。")
            self.workspace.settings["update_channel"] = data["channel"]
            self.workspace.settings["update_skipped"] = ""
            self.state.update(channel=data["channel"], status="idle", available="", notes="", error="")
            self.manifest = None
        await self.workspace.save()

    async def check(self, manual=True):
        if not self.client.config.get("enabled"):
            raise UpdateError("更新源尚未启用。")
        if self.task and not self.task.done():
            return
        self.state.update(status="checking", error="", install_when_idle=False)
        self.task = asyncio.create_task(self._check(manual))

    async def _check(self, manual):
        try:
            channel = self.workspace.settings.get("update_channel", "beta")
            manifest = await asyncio.to_thread(self.client.check, channel)
            now = int(time.time()); self.workspace.settings["update_last_checked"] = now
            await self.workspace.save()
            skipped = self.workspace.settings.get("update_skipped", "")
            if manifest and is_newer(manifest["version"]) and manifest["version"] != skipped:
                self.manifest = manifest
                self.state.update(status="available", available=manifest["version"], notes=manifest.get("notes", ""),
                                  total=manifest["artifact"]["size"], downloaded=0, progress=0, error="")
                if self.workspace.settings.get("update_auto_download", False):
                    await self.download()
            else:
                self.manifest = None
                self.state.update(status="latest", available="", notes="", total=0, downloaded=0, progress=0, error="")
        except UpdateError as error:
            self.state.update(status="error", error=str(error))
        except Exception:
            self.state.update(status="error", error="检查更新时发生异常，请稍后重试。")

    async def download(self):
        if not self.manifest:
            raise UpdateError("请先检查更新。")
        if self.task and not self.task.done() and self.state["status"] not in ("available", "error"):
            return
        self.cancel.clear(); self.state.update(status="downloading", error="", install_when_idle=False)
        self.task = asyncio.create_task(self._download())

    async def _download(self):
        manifest = self.manifest
        target = self.directory / ("CloudDock-" + manifest["version"] + "-Setup-x64.exe")
        loop = asyncio.get_running_loop()
        def progress(done, total):
            loop.call_soon_threadsafe(self.state.update, {"downloaded": done, "total": total, "progress": done / total})
        try:
            path = await asyncio.to_thread(self.client.download, manifest, target, self.cancel, progress)
            self.state.update(status="downloaded", downloaded=manifest["artifact"]["size"], total=manifest["artifact"]["size"], progress=1, error="")
            self.directory.mkdir(parents=True, exist_ok=True)
            saved = {"version": manifest["version"], "artifact": manifest["artifact"], "manifest": manifest}
            temporary = self.state_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.state_path)
            return path
        except UpdateCancelled as error:
            self.state.update(status="available", error=str(error), install_when_idle=False)
        except UpdateError as error:
            self.state.update(status="error", error=str(error), install_when_idle=False)
        except Exception:
            self.state.update(status="error", error="更新下载未完成，可稍后继续。", install_when_idle=False)

    def stop_download(self):
        if self.state["status"] == "downloading":
            self.cancel.set()

    def skip(self):
        if not self.manifest:
            raise UpdateError("当前没有可跳过的版本。")
        self.workspace.settings["update_skipped"] = self.manifest["version"]
        self.state.update(status="idle", available="", notes="", install_when_idle=False)

    def install(self):
        if not self.manifest or self.state["status"] != "downloaded":
            raise UpdateError("更新包尚未准备完成。")
        if not (ROOT / "installed-data.txt").is_file():
            raise UpdateError("便携版暂不自动替换程序；请使用安装包升级并保留原数据目录。")
        if self.busy():
            raise UpdateError("仍有传输、同步或备份任务，请等待任务安全结束。")
        artifact = self.manifest["artifact"]
        path = self.directory / ("CloudDock-" + self.manifest["version"] + "-Setup-x64.exe")
        if not path.is_file() or path.stat().st_size != artifact["size"] or _sha256(path) != artifact["sha256"]:
            raise UpdateSecurityError("更新包已变化，请重新下载。")
        self.state["install_when_idle"] = False
        return {"installer": str(path), "sha256": artifact["sha256"], "version": self.manifest["version"]}

    def install_when_idle(self):
        if not self.manifest or self.state["status"] != "downloaded":
            raise UpdateError("更新包尚未准备完成。")
        self.state["install_when_idle"] = True

    async def close(self):
        self.closed = True; self.cancel.set()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
