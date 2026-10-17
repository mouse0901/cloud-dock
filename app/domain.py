from __future__ import annotations
from dataclasses import dataclass, asdict, field
from pathlib import PurePosixPath
from typing import Protocol

@dataclass(frozen=True)
class CloudFile:
    id: str
    provider: str
    path: str
    is_dir: bool = False
    size: int = 0
    modified: str = "2026-10-16 09:30"

    revision: str = ""
    thumbnail: str = field(default="", compare=False, repr=False)

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def parent(self) -> str:
        return str(PurePosixPath(self.path).parent)

    @property
    def category(self) -> str:
        if self.is_dir:
            return "folder"
        ext = PurePosixPath(self.path).suffix.lower()
        if ext in {".jpg", ".png", ".jpeg", ".webp"}:
            return "image"
        if ext in {".mp4", ".mov", ".mkv"}:
            return "video"
        if ext in {".pdf", ".docx", ".xlsx", ".md", ".txt", ".pptx"}:
            return "document"
        return "other"

@dataclass(frozen=True)
class FilePage:
    items: list[CloudFile]
    total: int
    page: int
    page_size: int

@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    state: str
    capabilities: frozenset[str]

class DriveProvider(Protocol):
    info: ProviderInfo
    async def list_files(self, path: str, query: str = "", category: str = "all", page: int = 0, page_size: int = 50, sort: str = "name") -> FilePage: ...
    async def create_folder(self, parent: str, name: str) -> CloudFile: ...
    async def rename(self, file_id: str, name: str) -> None: ...
    async def delete(self, file_ids: list[str]) -> None: ...

@dataclass
class Transfer:
    id: str
    provider: str
    file_id: str
    name: str
    size: int
    progress: float = 0.0
    state: str = "queued"
    simulation: bool = True


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return ""
