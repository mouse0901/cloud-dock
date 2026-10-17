"""Finalize the already validated clean stage; hash every archived file."""
from pathlib import Path
import hashlib,json,zipfile

ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'version.json').read_text(encoding='utf-8'))['version']
OUT=Path('H:/CodexOutputs/packages/cloud-dock')/VERSION
STAGE=OUT/('CloudDock-'+VERSION+'-Windows-x64')
assert STAGE.is_dir()
for private in ('data','downloads','work','.venv'):
    assert not (STAGE/private).exists(),f'Private directory in package: {private}'
for folder in ('app','desktop'):
    for source in (ROOT/folder).rglob('*'):
        if source.is_file() and '__pycache__' not in source.parts and source.suffix!='.pyc':
            relative=source.relative_to(ROOT)
            assert (STAGE/relative).read_bytes()==source.read_bytes(),f'Stale staged source: {relative}'
assert (STAGE/'CloudDock.exe').read_bytes()==(ROOT/'work/desktop-build'/('CloudDock'+VERSION.replace('.','')+'.exe')).read_bytes()
hashes={p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(STAGE.rglob('*')) if p.is_file() and p.name!='CHECKSUMS.sha256'}
(STAGE/'CHECKSUMS.sha256').write_text(''.join(f'{digest}  {path}\n' for path,digest in hashes.items()),encoding='utf-8')
archive=OUT/(STAGE.name+'.zip')
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
    for source in sorted(STAGE.rglob('*')):
        if source.is_file():bundle.write(source,STAGE.name+'/'+source.relative_to(STAGE).as_posix())
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
    for path,digest in hashes.items():assert hashlib.sha256(bundle.read(STAGE.name+'/'+path)).hexdigest()==digest
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(OUT/(archive.name+'.sha256')).write_text(digest+'  '+archive.name+'\n',encoding='ascii')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(hashes)+1,'sha256':digest,'verified':True}))
