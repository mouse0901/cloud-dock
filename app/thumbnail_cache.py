"""Bounded provider-only thumbnail cache; URLs and credentials never reach the UI."""
import asyncio,hashlib,time
from pathlib import Path
from urllib.parse import urlsplit,urljoin
from app.providers.baidu_web import UA
class ThumbnailCache:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.slots=asyncio.Semaphore(4);self.inflight={};self.failed={}
    @staticmethod
    def key(account,item):return hashlib.sha256((account+':'+item.id+':'+item.revision+':'+str(item.size)+':320').encode()).hexdigest()
    @staticmethod
    def allowed(url,provider):
        p=urlsplit(url);h=p.hostname or ''
        suffixes=('baidu.com','baidupcs.com','bdstatic.com') if provider=='baidu' else ('quark.cn',)
        return p.scheme=='https' and p.port in (None,443) and not p.username and not p.password and not p.fragment and any(h==s or h.endswith('.'+s) for s in suffixes)
    async def get(self,account,item,client,valid):
        key=self.key(account,item);path=self.root/(key+'.thumb')
        if not valid():return None
        if path.exists():return await asyncio.to_thread(path.read_bytes)
        if time.monotonic()-self.failed.get(key,-1000)<300:return None
        if key in self.inflight:return await asyncio.shield(self.inflight[key])
        async def fetch():
            try:
                async with self.slots:
                    url=item.thumbnail
                    for _ in range(5):
                        if not valid() or not self.allowed(url,item.provider):return None
                        async with client.stream('GET',url,headers={'User-Agent':UA,'Referer':'https://pan.baidu.com/' if item.provider=='baidu' else 'https://pan.quark.cn/'},follow_redirects=False,timeout=12) as response:
                            if response.is_redirect:url=urljoin(url,response.headers.get('location',''));continue
                            if response.status_code!=200:return None
                            data=bytearray()
                            async for block in response.aiter_bytes():
                                data.extend(block)
                                if len(data)>2*1024*1024:return None
                            data=bytes(data)
                            if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or data[:4]==b'RIFF' and data[8:12]==b'WEBP'):return None
                            if not valid():return None
                            await asyncio.to_thread(path.write_bytes,data)
                            await asyncio.to_thread(self.trim)
                            return data
            except Exception:return None
            finally:self.failed[key]=time.monotonic()
        task=asyncio.create_task(fetch());self.inflight[key]=task
        try:return await asyncio.shield(task)
        finally:self.inflight.pop(key,None)
    def trim(self):
        files=sorted(self.root.glob('*.thumb'),key=lambda p:p.stat().st_mtime,reverse=True);total=0
        for p in files:
            total+=p.stat().st_size
            if total>256*1024*1024:p.unlink(missing_ok=True)
    async def clear(self):
        for task in self.inflight.values():task.cancel()
        await asyncio.gather(*list(self.inflight.values()),return_exceptions=True)
        for p in self.root.glob('*.thumb'):p.unlink(missing_ok=True)
        self.failed.clear()
