"""Conservative Baidu-only Range experiment with a full-stream fallback."""
from app.direct_download import DirectDownload, DownloadError
from app.parallel_download import ParallelDownload


def account_connections(member):
    # Optional metadata must never put an unknown account on an experimental route.
    return 4 if member == 'free' else 1


class BaiduAdaptiveDownload(ParallelDownload):
    def __init__(self,*args,allow_parallel=None,on_fallback=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.allow_parallel=allow_parallel
        self.on_fallback=on_fallback

    async def _transfer(self, source):
        if self.segments is None and self.allow_parallel and not self.allow_parallel():
            self.connections=1
        if self.connections == 1 and self.segments is None:
            self.transfer_mode = 'single'
            return await DirectDownload._transfer(self, source)
        try:
            completed = await super()._transfer(source)
            if completed: return True
            # A successful one-byte probe does not mean this CDN accepts parallel
            # authenticated ranges. All workers have been joined by the parent.
        except DownloadError as error:
            if error.code not in ('download_http', 'download_network', 'download_incomplete'):
                raise
        self._check_owner()
        self._check_part()
        self._remove_part()
        if self.part is not None and self.part.exists():
            raise DownloadError('download_partial', '临时文件已变化，未切换下载方式')
        self._part_identity = None
        self._validator = None
        self._content_type = ''
        self._probe_supported = False
        self.connections = 1
        if self.on_fallback:self.on_fallback()
        self.transfer_mode = 'single'
        self._sample_done = self.done = 0
        self.speed = 0
        # This uses the same account and version-pinned source resolver. No
        # third-party tickets, alternative identity, or unvalidated destination.
        source = await self._resolve()
        return await DirectDownload._transfer(self, source)
