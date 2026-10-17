"""One data location shared by the native shell and Python controllers."""
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def data_dir():
    value=os.environ.get('CLOUD_DOCK_DATA_DIR')
    if not value:
        marker=ROOT/'installed-data.txt'
        if marker.exists():value=marker.read_text(encoding='utf-8-sig').strip()
    path=Path(value) if value else ROOT/'data'
    if not path.is_absolute():raise ValueError('Data location must be absolute')
    return path
def work_dir():
    return data_dir()/'work'
