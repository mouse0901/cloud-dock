"""Export an allowlist of counters, never raw logs, paths or exception messages."""
from collections import Counter
import datetime,platform
SAFE_STATES={'queued','paused','completed','cancelled','failed','downloading','uploading','preparing','verifying','committing','running','done'}
def report(bridge):
    result={'product':'Cloud Dock','version':'0.6.2','schema':1,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'runtime':{'python':platform.python_version(),'system':platform.system(),'release':platform.release(),'machine':platform.machine()},
            'workspace':{'initializing':bool(bridge.deferred),'initialization_failed':bool(bridge.initialization_error)},
            'accounts':{name:{'connected':bool(p.api.authenticated),'busy':bool(p.browser_busy)} for name,p in bridge.panels.items() if name in ('baidu','quark')},
            'privacy':'No credentials, file names, directory paths, account identifiers, share links or raw logs are included.'}
    f=bridge.features
    if f:
        tasks=f.store.all('transfer');rules=f.sync.rules()
        states=Counter(row.get('status') if row.get('status') in SAFE_STATES else 'other' for row in tasks)
        result['transfers']={'count':len(tasks),'states':dict(states)}
        result['sync']={'rules':len(rules),'enabled':sum(bool(x.get('enabled')) for x in rules),
                        'pending':len(f.store.all('sync_pending')),'issues':len(f.store.all('sync_issue')),
                        'failures':sum(bool(x.get('failures')) for x in rules)}
        result['backups']={'rules':len(f.backup.rules())}
    return result
