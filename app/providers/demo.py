from __future__ import annotations
import asyncio
import json
import re
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4
from app.domain import CloudFile, FilePage, ProviderInfo


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def validate_name(name: str) -> str:
    name = name.strip()
    if not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("请输入有效名称，不要包含 / \\ : * ? 等字符")
    if len(name) > 200 or name.endswith("."):
        raise ValueError("名称过长或以句点结尾")
    return name


def seed_files() -> list[CloudFile]:
    items = [
        ("/我的文档", True, 0, "2026-10-15 18:30"),
        ("/设计素材", True, 0, "2026-10-15 16:42"),
        ("/视频项目", True, 0, "2026-10-14 10:20"),
        ("/工作归档", True, 0, "2026-10-12 09:18"),
        ("/2026 产品规划.pdf", False, 2457600, "2026-10-16 09:30"),
        ("/品牌视觉规范.pdf", False, 8243200, "2026-10-15 17:46"),
        ("/秋日海岸.jpg", False, 5832704, "2026-10-15 14:12"),
        ("/素材清单.xlsx", False, 348160, "2026-10-14 11:05"),
        ("/项目资料.zip", False, 1342177280, "2026-10-13 20:30"),
        ("/云端使用指南.md", False, 6144, "2026-10-12 08:40"),
        ("/我的文档/会议记录", True, 0, "2026-10-15 11:00"),
        ("/我的文档/产品需求.docx", False, 1245184, "2026-10-15 10:30"),
        ("/我的文档/出行计划.xlsx", False, 235520, "2026-10-14 14:00"),
        ("/我的文档/会议记录/周会纪要.md", False, 10240, "2026-10-15 11:00"),
        ("/设计素材/界面参考.png", False, 3460300, "2026-10-15 15:30"),
        ("/设计素材/字体与图标.zip", False, 247463936, "2026-10-13 08:00"),
        ("/视频项目/海岸日落.mp4", False, 858993459, "2026-10-14 18:30"),
        ("/视频项目/分镜脚本.pdf", False, 4505600, "2026-10-13 12:30"),
    ]
    return [CloudFile(str(i + 1), "baidu", p, d, s, m) for i, (p, d, s, m) in enumerate(items)]


class DemoBaiduProvider:
    """Local fixture disk. Never accesses or changes a cloud account."""
    info = ProviderInfo("baidu", "百度网盘", "demo", frozenset({"list", "search", "mkdir", "rename", "delete", "simulate_download"}))

    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.files = seed_files()
        self._lock = asyncio.Lock()
        if state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.files = [CloudFile(**item) for item in payload]
            except (ValueError, TypeError, KeyError):
                # Keep the original file available for diagnosis; don't overwrite it.
                self.state_path = state_path.with_name("demo-recovered.json")

    async def _save(self):
        await asyncio.to_thread(atomic_json, self.state_path, [asdict(x) for x in self.files])

    async def list_files(self, path="/", query="", category="all", page=0, page_size=50, sort="name"):
        if page < 0 or page_size < 1 or page_size > 100:
            raise ValueError("分页参数无效")
        # Search always covers this provider; browsing remains scoped to the current folder.
        files = [f for f in self.files if (query.casefold() in f.name.casefold() if query else f.parent == path)]
        if category != "all":
            files = [f for f in files if f.category == category]
        files.sort(key=lambda f: f.modified if sort == "modified" else f.name.casefold(), reverse=sort == "modified")
        files.sort(key=lambda f: not f.is_dir)
        return FilePage(files[page * page_size:(page + 1) * page_size], len(files), page, page_size)

    def _item(self, file_id):
        return next((f for f in self.files if f.id == file_id), None)

    def _target(self, parent, name, except_id=None):
        name = validate_name(name)
        target = str(PurePosixPath(parent) / name)
        if any(f.path.casefold() == target.casefold() and f.id != except_id for f in self.files):
            raise ValueError("这里已经有同名文件或文件夹")
        return target

    async def create_folder(self, parent, name):
        async with self._lock:
            if parent != "/" and not any(f.path == parent and f.is_dir for f in self.files):
                raise ValueError("目标文件夹不存在")
            item = CloudFile(uuid4().hex, "baidu", self._target(parent, name), True, modified=datetime.now().strftime("%Y-%m-%d %H:%M"))
            self.files.append(item)
            await self._save()
            return item

    async def rename(self, file_id, name):
        async with self._lock:
            item = self._item(file_id)
            if not item:
                raise ValueError("文件已不存在")
            target = self._target(item.parent, name, file_id)
            self.files = [replace(f, path=target + f.path[len(item.path):]) if f.id == file_id or f.path.startswith(item.path + "/") else f for f in self.files]
            await self._save()

    async def delete(self, file_ids):
        async with self._lock:
            paths = [f.path for f in self.files if f.id in file_ids]
            self.files = [f for f in self.files if not any(f.path == p or f.path.startswith(p + "/") for p in paths)]
            await self._save()


class PendingQuarkProvider:
    info = ProviderInfo("quark", "夸克网盘", "pending", frozenset())

    async def list_files(self, *args, **kwargs):
        raise NotImplementedError("夸克尚未接入")
