"""Explicit local-folder actions, never launch a downloaded file."""
import os
from pathlib import Path
import tempfile


def prepare_download_root(value):
    if not isinstance(value,str) or not value.strip() or "\x00" in value:
        raise ValueError("请选择有效的下载文件夹。")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("下载位置必须是完整路径。")
    path = path.resolve()
    path.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryFile(dir=path):
        pass
    return path


def open_local_folder(path):
    directory = Path(path).resolve()
    if not directory.is_dir():
        raise OSError("文件夹不存在，可能已被移动或删除。")
    os.startfile(str(directory))
