"""Conservative Baidu-only Range experiment with a full-stream fallback."""
from app.direct_download import DirectDownload, DownloadError
from app.parallel_download import ParallelDownload,DownloadGate
import httpx


def account_connections(member):
    # Membership tunes the budget, never authorization or download URLs.
    return 8 if member == 'paid' else 4


class BaiduAdaptiveDownload(ParallelDownload):
    def __init__(self,*args,allow_parallel=None,on_fallback=None,**kwargs):
        kwargs.setdefault('gate',DownloadGate(16))
        super().__init__(*args,**kwargs)
        self.allow_parallel=allow_parallel
        self.on_fallback=on_fallback
        self._parallel_expirations=0
        self._native_expirations=0
        self._using_native=False
        self._web_connections=self.connections

    def _validate_range(self,response,start,end,*,probe=False):
        if response.status_code==200:
            raise DownloadError('download_range_unsupported','此下载线路不支持分片，正在切换普通下载')
        return super()._validate_range(response,start,end,probe=probe)

    def segment_size(self):
        if self._using_native:return 4*1024*1024
        # Long-lived ranges avoid repeatedly reopening the signed CDN link.
        # Written offsets still permit partial-range resume after interruption.
        return max(512*1024,(self.total+self.connections-1)//self.connections)

    async def _transfer(self, source):
        from app.providers.baidu_locate import NATIVE_UA
        previously_native=self._using_native
        self._using_native=getattr(source,'headers',{}).get('User-Agent')==NATIVE_UA
        if previously_native and not self._using_native:
            self._reset_partial();self.connections=self._web_connections;self._probe_supported=None
        if self._using_native:
            if self._native_expirations>=2:
                raise DownloadError('download_expired','下载路线暂不可用，请稍后重试。')
            self.connections=min(16,self.gate.maximum)
            try:
                if await super()._transfer(source):return True
            except DownloadError as error:
                if error.code not in ('download_http','download_network','download_range_unsupported'):raise
            except httpx.HTTPError:
                pass
            rejected=getattr(self.resolve_source,'__dict__',{}).get('reject_native')
            if rejected is not None:rejected(self.item)
            self._native_expirations+=1
            if self._native_expirations==1:return False
            # Two native candidates failed. Drop partial bytes before switching
            # protocol/ETag, and use the original web route exactly once.
            return await self._transfer(await self._resolve())
        if self.segments is None and self.allow_parallel and not self.allow_parallel():
            self.connections=1
        if self.connections == 1 and self.segments is None:
            self.transfer_mode = 'single'
            return await DirectDownload._transfer(self, source)
        try:
            completed = await super()._transfer(source)
            if completed: return True
            self._parallel_expirations+=1
            if self._parallel_expirations==1:return False
            # A successful one-byte probe does not mean this CDN accepts parallel
            # authenticated ranges. All workers have been joined by the parent.
        except DownloadError as error:
            # Keep validated partial bytes after transient connection failures.
            if error.code not in ('download_http','download_range_unsupported'):
                raise
        self._reset_partial()
        self.connections = 1
        if self.on_fallback:self.on_fallback()
        self.transfer_mode = 'single'
        source = await self._resolve()
        return await DirectDownload._transfer(self, source)

    def _reset_partial(self):
        self._check_owner()
        self._check_part()
        self._remove_part()
        if self.part is not None and self.part.exists():
            raise DownloadError('download_partial', '临时文件已变化，未切换下载方式')
        self._part_identity = None
        self._validator = None
        self._content_type = ''
        self._probe_supported = False
        self._sample_done = self.done = 0
        self.speed = 0
