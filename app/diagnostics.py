"""Export an allowlist of counters, never raw logs, paths or exception messages."""
from collections import Counter
import datetime,json,platform
from pathlib import Path
SAFE_STATES={'queued','paused','completed','cancelled','failed','downloading','uploading','preparing','verifying','committing','running','done'}
def report(bridge):
    from app.operation_diagnostics import recent
    version=json.loads((Path(__file__).resolve().parents[1]/'version.json').read_text(encoding='utf-8'))['version']
    result={'product':'Cloud Dock','version':version,'schema':1,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'runtime':{'python':platform.python_version(),'system':platform.system(),'release':platform.release(),'machine':platform.machine()},
            'workspace':{'initializing':bool(bridge.deferred),'initialization_failed':bool(bridge.initialization_error)},
            'accounts':{name:{'connected':bool(p.api.authenticated),'busy':bool(p.browser_busy)} for name,p in bridge.panels.items() if name in ('baidu','quark')},
            'privacy':'No credentials, file names, directory paths, account identifiers, share links or raw logs are included.'}
    result['recent_errors']=recent()
    if hasattr(bridge,'health'):result['performance']=bridge.health.report()
    if hasattr(bridge,'profiles'):result['saved_accounts']={p:len(v['rows']) for p,v in bridge.profiles.catalog['providers'].items()}
    result['update']={'status':bridge.updates.state['status'] if getattr(bridge,'updates',None) else 'initializing'}
    f=bridge.features
    if f:
        tasks=f.store.all('transfer');rules=f.sync.rules()
        states=Counter(row.get('status') if row.get('status') in SAFE_STATES else 'other' for row in tasks)
        result['transfers']={'count':len(tasks),'states':dict(states)}
        result['sync']={'rules':len(rules),'enabled':sum(bool(x.get('enabled')) for x in rules),
                        'pending':len(f.store.all('sync_pending')),'issues':len(f.store.all('sync_issue')),
                        'failures':sum(bool(x.get('failures')) for x in rules)}
    return result

async def check(bridge):
    import asyncio,shutil,secrets
    def disk():
        path=bridge.workspace.data_dir/'work'/('health-'+secrets.token_hex(16)+'.tmp');created=False
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as f:created=True;f.write(b'Cloud Dock health check')
            free=shutil.disk_usage(bridge.workspace.data_dir).free
            return {'writable':True,'free_bytes':free}
        except OSError:return {'writable':False,'free_bytes':0}
        finally:
            if created:path.unlink(missing_ok=True)
    result=report(bridge);result['storage']=await asyncio.to_thread(disk)
    try:result['database_ok']=not bridge.features or bridge.features.store.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    except Exception:result['database_ok']=False
    result['advice']=[]
    if not result['storage']['writable']:result['advice'].append('data_unwritable')
    elif result['storage']['free_bytes']<512*1024*1024:result['advice'].append('disk_low')
    if not result['database_ok']:result['advice'].append('database_check_failed')
    if bridge.initialization_error:result['advice'].append('workspace_failed')
    if getattr(bridge,'profiles',None) and bridge.profiles.error:result['advice'].append('account_catalog_failed')
    return result
