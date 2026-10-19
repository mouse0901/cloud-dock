"""Local operational counters. Never record data from a request or an exception."""
import os,time
class Health:
    def __init__(self):self.started=time.monotonic();self.stages={};self.actions={};self.snapshots=0;self.last_snapshot_ms=0
    def mark(self,key):self.stages[key]=round((time.monotonic()-self.started)*1000,1)
    def action(self,ok):key='succeeded' if ok else 'failed';self.actions[key]=self.actions.get(key,0)+1
    def report(self):
        memory=0
        if os.name=='nt':
            try:
                import ctypes
                class Counters(ctypes.Structure):
                    _fields_=[('cb',ctypes.c_ulong),('PageFaultCount',ctypes.c_ulong)]+[(x,ctypes.c_size_t) for x in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
                value=Counters();value.cb=ctypes.sizeof(value)
                dll=ctypes.WinDLL('psapi');dll.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong]
                if dll.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(value),value.cb):memory=value.WorkingSetSize
            except Exception:pass
        return {'uptime_seconds':int(time.monotonic()-self.started),'stages_ms':dict(self.stages),'actions':dict(self.actions),'snapshots':self.snapshots,'last_snapshot_ms':self.last_snapshot_ms,'backend_working_set_bytes':memory}
