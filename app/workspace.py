from __future__ import annotations
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
from app.domain import Transfer
from app.providers.demo import atomic_json


class Workspace:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.settings = {"theme": "light", "view": "list", "concurrency": 3,
                         "download_dir": str((data_dir / "downloads" if (data_dir.parent / "installed-data.txt").exists() or __import__("os").environ.get("CLOUD_DOCK_DATA_DIR") else data_dir.parent / "downloads").resolve()), "reduce_motion": False,
                         "close_to_tray": True, "clipboard_links": True,
                         "update_auto_check": True, "update_auto_download": False,
                         "update_channel": "beta", "update_last_checked": 0,
                         "update_skipped": ""}
        self.transfers: list[Transfer] = []
        self._save_lock = asyncio.Lock()
        try:
            saved = json.loads((data_dir / "preferences.json").read_text(encoding="utf-8"))
            download_dir = saved.get("download_dir")
            if isinstance(download_dir,str) and "\x00" not in download_dir and Path(download_dir).is_absolute():
                self.settings["download_dir"] = download_dir
            if type(saved.get("completion_sound")) is bool:self.settings["completion_sound"]=saved["completion_sound"]
            if type(saved.get("close_to_tray")) is bool:self.settings["close_to_tray"]=saved["close_to_tray"]
            if type(saved.get("clipboard_links")) is bool:self.settings["clipboard_links"]=saved["clipboard_links"]
            if type(saved.get("update_auto_check")) is bool:self.settings["update_auto_check"]=saved["update_auto_check"]
            if type(saved.get("update_auto_download")) is bool:self.settings["update_auto_download"]=saved["update_auto_download"]
            if saved.get("update_channel") in {"stable", "beta"}:self.settings["update_channel"]=saved["update_channel"]
            if type(saved.get("update_last_checked")) in {int, float} and saved["update_last_checked"] >= 0:self.settings["update_last_checked"]=saved["update_last_checked"]
            if isinstance(saved.get("update_skipped"),str) and len(saved["update_skipped"])<=64:self.settings["update_skipped"]=saved["update_skipped"]
            if type(saved.get("reduce_motion")) is bool:
                self.settings["reduce_motion"] = saved["reduce_motion"]
            if saved.get("theme") in {"light", "dark", "system"}:
                self.settings["theme"] = saved["theme"]
            if saved.get("view") in {"list", "grid"}:
                self.settings["view"] = saved["view"]
            if saved.get("concurrency") in {1, 2, 3}:
                self.settings["concurrency"] = saved["concurrency"]
        except (OSError, ValueError, AttributeError):
            pass
        try:
            rows = json.loads((data_dir / "demo-transfers.json").read_text(encoding="utf-8"))
            self.transfers = [Transfer(**x) for x in rows if x.get("simulation") is True]
            for t in self.transfers:
                if t.state in {"running", "queued"}:
                    t.state = "paused"
        except (OSError, ValueError, TypeError, AttributeError):
            self.transfers = []

    async def save(self):
        async with self._save_lock:
            prefs = dict(self.settings)
            transfers = [asdict(t) for t in self.transfers]
            await asyncio.to_thread(atomic_json, self.data_dir / "preferences.json", prefs)
            await asyncio.to_thread(atomic_json, self.data_dir / "demo-transfers.json", transfers)

    def enqueue(self, files):
        added = 0
        for f in files:
            if f.is_dir or any(t.file_id == f.id and t.provider == f.provider and t.state not in {"done", "cancelled"} for t in self.transfers):
                continue
            self.transfers.append(Transfer(uuid4().hex, f.provider, f.id, f.name, f.size))
            added += 1
        return added

    def tick(self):
        active = sum(t.state == "running" for t in self.transfers)
        for t in self.transfers:
            if t.state == "queued" and active < self.settings["concurrency"]:
                t.state = "running"
                active += 1
            if t.state == "running":
                t.progress = min(1.0, t.progress + 0.025)
                if t.progress >= 1.0:
                    t.state = "done"
                    active -= 1

    def transition(self, transfer_id, action):
        t = next((x for x in self.transfers if x.id == transfer_id), None)
        if not t:
            return
        if action == "pause" and t.state in {"running", "queued"}:
            t.state = "paused"
        elif action == "resume" and t.state == "paused":
            t.state = "queued"
        elif action == "cancel" and t.state not in {"done", "cancelled"}:
            t.state = "cancelled"


class Navigation:
    def __init__(self):
        self.paths = ["/"]
        self.index = 0

    @property
    def current(self):
        return self.paths[self.index]

    def visit(self, path):
        if path != self.current:
            self.paths = self.paths[:self.index + 1] + [path]
            self.index += 1

    def step(self, delta):
        self.index = max(0, min(len(self.paths) - 1, self.index + delta))
        return self.current
