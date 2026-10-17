"""Exclusive process lifetime lock, including during graceful shutdown."""
import os
class ProcessLock:
    def __init__(self,path):self.path=path;self.stream=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=open(self.path,'a+b');self.stream.seek(0)
        if self.path.stat().st_size==0:self.stream.write(b'0');self.stream.flush();self.stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    def close(self):
        if self.stream:self.stream.close();self.stream=None
