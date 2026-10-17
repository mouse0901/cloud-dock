"""Offline, non-destructive portable-profile import used by Setup."""
from __future__ import annotations
import argparse,ctypes,json,os,shutil,sqlite3,sys,uuid,hashlib
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.process_lock import ProcessLock

def ordinary(path):
    path=Path(path)
    for p in (path,*path.parents):
        if p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise ValueError('Linked directories are not supported')
    return path

def import_profile(source,target):
    source=ordinary(Path(source).absolute());target=ordinary(Path(target).absolute())
    if source==target or target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('Overlapping locations')
    if not (source/'data/preferences.json').is_file():raise ValueError('Not a portable profile')
    if target.exists() and any(target.iterdir()):raise ValueError('Destination already contains data')
    target.parent.mkdir(parents=True,exist_ok=True)
    stage=target.parent/('.clouddock-import-'+uuid.uuid4().hex)
    kernel=None;mutex=None;leases=[]
    try:
        if os.name=='nt':
            from ctypes import wintypes
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            name='Local\\CloudDock-'+hashlib.sha256((str(source)+'\\').lower().encode()).hexdigest().upper()
            mutex=kernel.CreateMutexW(None,False,name)
            if not mutex or ctypes.get_last_error()==183:raise ValueError('Portable app is running')
        for location in (source/'data/.app-lock',source/'data/.backend-lock'):
            lease=ProcessLock(location);lease.acquire();leases.append(lease)
        stage.mkdir()
        for name in ('preferences.json','demo-transfers.json','tray-hint-seen'):
            path=ordinary(source/'data'/name)
            if path.is_file():
                if path.stat().st_size>8*1024*1024:raise ValueError('Profile file too large')
                shutil.copy2(path,stage/name)
        sessions=ordinary(source/'data/sessions')
        if sessions.is_dir():
            (stage/'sessions').mkdir()
            for path in sessions.iterdir():
                ordinary(path)
                if path.is_file() and path.stat().st_size<2*1024*1024:shutil.copy2(path,stage/'sessions'/path.name)
                else:raise ValueError('Invalid session data')
        database=ordinary(source/'data/activity.sqlite3');paused=0
        if database.is_file():
            with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(stage/'activity.sqlite3')) as dst:
                src.backup(dst)
                for kind,id,raw in dst.execute("SELECT kind,id,payload FROM records WHERE kind IN ('sync_rule','backup')").fetchall():
                    row=json.loads(raw);paused+=bool(row.get('enabled'));row.update(enabled=False,status='已导入并暂停，请核对位置后启用')
                    dst.execute('UPDATE records SET payload=? WHERE kind=? AND id=?',(json.dumps(row,ensure_ascii=False),kind,id))
                dst.execute("DELETE FROM records WHERE kind='sync_resolution'")
                dst.commit()
                if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid database')
        # Absolute download/sync locations and encrypted login bytes are preserved.
        (stage/'migration.json').write_text(json.dumps({'version':'0.6.2','paused':paused,'source_preserved':True}),encoding='utf-8')
        if target.exists():target.rmdir()  # only the already-checked empty directory
        stage.rename(target)
        return {'paused':paused,'source_preserved':True}
    finally:
        for lease in leases:lease.close()
        if mutex:kernel.CloseHandle(mutex)
        # Never delete the original profile or a partially migrated staging directory.

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--target',required=True);a=p.parse_args()
    try:import_profile(a.source,a.target)
    except Exception:sys.exit(2)
