"""Reproducible clean Windows portable build. No personal data is copied."""
from pathlib import Path
import hashlib,importlib.metadata as md,json,shutil,zipfile
ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'version.json').read_text(encoding='utf-8'))['version']
OUT=Path('H:/CodexOutputs/packages/cloud-dock')/VERSION
STAGE=OUT/('CloudDock-'+VERSION+'-Windows-x64')
if STAGE.exists():raise SystemExit('Stage already exists; use a new version rather than overwrite it.')
STAGE.mkdir(parents=True)
for name in ['CloudDock.exe','UpdateLauncher.exe','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll']:
 shutil.copy2(ROOT/name,STAGE/name)
shutil.copytree(ROOT/'app',STAGE/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
shutil.copytree(ROOT/'desktop',STAGE/'desktop',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
(STAGE/'assets/branding').mkdir(parents=True)
for name in ['cloud-dock.ico','cloud-dock.png']:shutil.copy2(ROOT/'assets/branding'/name,STAGE/'assets/branding'/name)
for name in ['version.json','update-source.json']:shutil.copy2(ROOT/name,STAGE/name)
py=STAGE/'runtime/python';py.mkdir(parents=True)
with zipfile.ZipFile(ROOT/'work/desktop-build/python-embed.zip') as z:z.extractall(py)
(py/'python314._pth').write_text('python314.zip\n.\nLib\\site-packages\n..\\..\\\n',encoding='utf-8')
site=py/'Lib/site-packages';site.mkdir(parents=True)
packages=['flet','httpx','httpcore','h11','anyio','idna','certifi','oauthlib','repath','six','msgpack','qrcode','colorama','typing_extensions']
versions={}
for name in packages:
 dist=md.distribution(name);versions[name]=dist.version
 for relative in dist.files or []:
  parts=Path(relative).parts
  if '..' in parts or '__pycache__' in parts or str(relative).endswith('.pyc'):continue
  source=Path(dist.locate_file(relative));target=site/relative
  if source.is_file():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
license_dir=STAGE/'licenses';license_dir.mkdir()
for pattern in ['LICENSE*','*.txt']:
 for p in (ROOT/'work/desktop-build/webview2').glob(pattern):
  if p.is_file():shutil.copy2(p,license_dir/('WebView2-'+p.name))
shutil.copy2(ROOT/'work/desktop-build/sources.json',license_dir/'runtime-sources.json')
(STAGE/'START.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\nstart "" "%~dp0CloudDock.exe"\r\n',encoding='ascii')
(STAGE/'使用说明.txt').write_text((ROOT/'installer/usage.txt').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
manifest={'version':VERSION,'architecture':'windows-x64','dependencies':versions,'entry':'CloudDock.exe','personal_data_included':False}
(STAGE/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
print('STAGE='+str(STAGE))
