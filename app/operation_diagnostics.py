"""Small sanitized error record: no messages, payloads, paths or credentials."""
import datetime,json,re,traceback,threading
from pathlib import Path
from app.runtime_paths import work_dir
_lock=threading.Lock()

def record(operation,error):
    def label(value):
        return value if isinstance(value,str) and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',value) else 'unknown'
    frames=traceback.extract_tb(error.__traceback__)
    code=label(getattr(error,'code','unexpected'))
    row={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
         'operation':label(operation),'type':label(type(error).__name__),'code':code,
         'frames':[{'module':label(Path(f.filename).stem),'function':label(f.name),'line':f.lineno} for f in frames[-5:]]}
    try:
        with _lock:
            path=work_dir()/'operation-errors.jsonl'
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists() and path.stat().st_size>65536:path.unlink()
            with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row)+'\n')
    except OSError:pass
    return code

def public_error(operation,error):
    from app.cloud_operations import CloudOperationError
    from app.providers.baidu_api import BaiduApiError
    from app.providers.quark_web import QuarkApiError
    from app.upload import UploadError
    from app.direct_download import DownloadError
    code=record(operation,error)
    if isinstance(error,CloudOperationError):return str(error)
    if isinstance(error,(BaiduApiError,QuarkApiError,UploadError,DownloadError)):
        return str(error)+f'（{code}）'
    if isinstance(error,TimeoutError):return '操作等待超时，请先刷新查看结果，避免重复提交。'
    if isinstance(error,OSError):return '无法访问本地文件或下载磁盘，请检查保存位置和剩余空间。'
    return '程序未完成此操作，已记录诊断信息。请重试；若仍失败，请反馈操作名称。'

def recent():
    try:
        path=work_dir()/'operation-errors.jsonl'
        if not path.exists() or path.stat().st_size>131072:return []
        result=[]
        for line in path.read_text(encoding='utf-8').splitlines()[-12:]:
            try:
                row=json.loads(line)
                values={key:row[key] for key in ('operation','type','code')}
                if all(isinstance(v,str) and re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}',v) for v in values.values()):result.append(values)
            except (ValueError,KeyError,TypeError):continue
        return result
    except OSError:return []
