"""Filesystem snapshots and three-way decisions; no cloud mutations."""
from __future__ import annotations
import fnmatch,hashlib,os,re,stat
from pathlib import Path,PurePosixPath
from app.cloud_operations import CloudOperationError
from app.upload import Fingerprint
from dataclasses import asdict

INTERNAL='.clouddock-'
LIMIT=50000
def valid_name(name):
    return bool(name and name not in ('.','..') and not re.search(r'[<>:"/\\|?*\x00-\x1f]',name)
                and name==name.rstrip(' .') and name.split('.')[0].upper() not in
                {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]})
def ignored(rel,patterns=()):
    parts=PurePosixPath(rel).parts
    return any(p.lower().startswith(INTERNAL) or p in ('.git','$RECYCLE.BIN','System Volume Information') or p.startswith('~$') for p in parts) or rel.lower().endswith(('.tmp','.temp','.part','.crdownload','.swp')) or any(fnmatch.fnmatchcase(rel.casefold(),p.casefold()) or any(fnmatch.fnmatchcase(x.casefold(),p.casefold()) for x in parts) for p in patterns)
def ordinary(path):
    return not path.is_symlink() and not getattr(path,'is_junction',lambda:False)()
def safe_local(root,rel):
    base=Path(root);parts=PurePosixPath(rel).parts
    if not parts or any(not valid_name(x) for x in parts):raise CloudOperationError('云端文件名无法映射到 Windows，请先改名。')
    target=base
    for part in parts:
        target=target/part
        if not ordinary(target):raise CloudOperationError('同步路径包含链接或联接，已暂停。')
    if not target.resolve().is_relative_to(base.resolve()):raise CloudOperationError('同步路径越界。')
    return target
def identity(path):
    path=Path(path)
    if not ordinary(path) or not path.is_dir():raise CloudOperationError('本地位置不可用，请检查磁盘与文件夹。')
    for parent in path.parents:
        if not ordinary(parent):raise CloudOperationError('本地位置包含链接或联接。')
    s=path.stat();return [s.st_dev,s.st_ino]
def content_hash(path):
    before=Fingerprint.read(path);h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while chunk:=f.read(1024*1024):h.update(chunk)
    if Fingerprint.read(path)!=before:raise CloudOperationError('文件仍在写入，稍后重试。')
    return h.hexdigest()
def local_snapshot(rule,cache=None,metadata_only=False):
    base=Path(rule['local_root']);cache=cache or {};result={};names={}
    if identity(base)!=rule['local_identity']:raise CloudOperationError('本地文件夹身份变化，已暂停；请重新选择位置。')
    def visit(path,rel):
        if not ordinary(path):raise CloudOperationError('同步范围出现链接或联接，请排除后重试。')
        key=rel.casefold()
        if key in names and names[key]!=rel:raise CloudOperationError('存在大小写重名，需先处理。')
        names[key]=rel
        if path.is_dir():
            s=path.stat();result[rel]={'dir':True,'identity':[s.st_dev,s.st_ino]};return
        if not path.is_file():raise CloudOperationError('同步范围包含无法读取的项目。')
        fp=asdict(Fingerprint.read(path));old=cache.get(rel,{})
        result[rel]={'dir':False,'fp':fp,'hash':None if metadata_only else old.get('hash') if old.get('fp')==fp else content_hash(path)}
    if rule['kind']=='file':
        p=safe_local(base,rule['local_name'])
        if p.exists():visit(p,rule['local_name'])
    else:
        def fail(error):raise error
        for parent,dirs,files in os.walk(base,followlinks=False,onerror=fail):
            for name in list(dirs):
                p=Path(parent)/name;rel=p.relative_to(base).as_posix()
                if ignored(rel,rule['exclude']):dirs.remove(name);continue
                safe_local(base,rel);visit(p,rel)
            for name in files:
                p=Path(parent)/name;rel=p.relative_to(base).as_posix()
                if not ignored(rel,rule['exclude']):safe_local(base,rel);visit(p,rel)
            if len(result)>LIMIT:raise CloudOperationError('单个同步任务最多 50000 项，请拆分范围。')
    return result
def remote_mark(item):
    return {'dir':item.is_dir,'id':item.id,'revision':item.revision,'size':item.size}
def local_equal(a,b):
    if a is None or b is None:return a is b
    return a['dir']==b['dir'] and (a['dir'] or a.get('hash')==b.get('hash'))
def remote_equal(a,b):
    if a is None or b is None:return a is b
    return a['dir']==b['dir'] and a['id']==b['id'] and (a['dir'] or (a['revision'],a['size'])==(b['revision'],b['size']))
def decide(local,remote,baseline,mode):
    """All deletions require a recorded common baseline. Conflict never means overwrite."""
    if baseline and baseline.get('ignored'):
        if local_equal(local,baseline.get('local')) and remote_equal(remote,baseline.get('remote')):return 'none'
        return 'conflict'
    if baseline is None:
        if local and remote:
            if local['dir'] and remote['dir']:return 'baseline'
            return 'compare' if local['dir']==remote['dir'] else 'conflict'
        if local:return 'upload' if mode in ('two_way','upload') else 'unmanaged'
        if remote:return 'download' if mode in ('two_way','download') else 'unmanaged'
        return 'none'
    a=not local_equal(local,baseline.get('local'));b=not remote_equal(remote,baseline.get('remote'))
    if not a and not b:return 'none'
    if local is None and remote is None:return 'baseline'
    if a and b:return 'compare' if local and remote and not local['dir'] and not remote['dir'] else 'conflict'
    if a:
        if mode=='download':return 'conflict'
        return 'upload' if local else 'delete_remote'
    if mode=='upload':return 'conflict'
    return 'download' if remote else 'delete_local'
