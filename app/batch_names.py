"""Shared non-destructive name policy for user-confirmed batch operations."""
from pathlib import PurePosixPath
from app.cloud_operations import CloudOperationError

def policy(value):
    if value not in ('skip', 'rename'):
        raise CloudOperationError('请选择跳过或保留两份。')
    return value

def choose_name(name, occupied, mode, directory=False):
    policy(mode)
    if name.casefold() not in occupied:
        occupied.add(name.casefold())
        return name
    if mode == 'skip':
        return None
    suffix = '' if directory else PurePosixPath(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    for number in range(1, 10001):
        candidate = f'{stem} ({number}){suffix}'
        if len(candidate) > 200:
            raise CloudOperationError('重名后的名称过长，请缩短名称。')
        if candidate.casefold() not in occupied:
            occupied.add(candidate.casefold())
            return candidate
    raise CloudOperationError('同名项目过多，请选择其他位置。')

def renamed(name, options, index, directory=False):
    if not isinstance(options, dict):
        raise CloudOperationError('重命名规则无效。')
    suffix = '' if directory else PurePosixPath(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    find, replace = options.get('find', ''), options.get('replace', '')
    prefix, postfix = options.get('prefix', ''), options.get('suffix', '')
    if any(not isinstance(x, str) or len(x) > 100 for x in (find, replace, prefix, postfix)):
        raise CloudOperationError('重命名文字过长或无效。')
    if find: stem = stem.replace(find, replace)
    start = options.get('start')
    if start is not None and (type(start) is not int or not 1 <= start <= 999999):
        raise CloudOperationError('起始序号应为 1–999999。')
    return prefix + stem + postfix + (f'_{start + index:03d}' if start is not None else '') + suffix
