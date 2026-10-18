"""Own-account PCS address negotiation, with explicit web fallback.

Observed against the user's own SVIP session on 2026-10-18. This is not the
official native transfer engine, and does not grant or manufacture membership.
"""
import asyncio
import hashlib
import json
import time
from app.providers.baidu_api import BaiduApiError

# This public protocol identifier must match at negotiation and CDN transfer.
# It does not assert any membership; eligibility comes from the account response.
NATIVE_UA='netdisk;P2SP;3.0.0.8;netdisk;11.12.3;ANG-AN00;android-android;10.0;JSbridge4.4.0;jointBridge;1.1.0;'

def tier(flags):
    if str(flags.get('is_evip')) == '1': return 'evip'
    if str(flags.get('is_svip')) == '1': return 'svip'
    if str(flags.get('is_vip')) == '1': return 'vip'
    if all(str(flags.get(k)) == '0' for k in ('is_vip','is_svip','is_evip')): return 'free'
    return 'unknown'

class BaiduLocate:
    def __init__(self, api):
        self.api=api;self.checked=0;self.member='unknown';self.owner='';self.lock=asyncio.Lock()

    def stamp(self):
        values=sorted((c.domain,c.name,c.value) for c in self.api.client.cookies.jar
                      if c.name in ('BDUSS','BDUSS_BFESS'))
        return hashlib.sha256(repr(values).encode()).hexdigest()

    async def membership(self):
        async with self.lock:
            owner=self.stamp()
            if owner==self.owner and time.monotonic()-self.checked<120:return self.member
            self.member='unknown';self.owner=owner;self.checked=time.monotonic()
            try:
                async with asyncio.timeout(6):
                    response=await self.api.request('https://pan.baidu.com/api/gettemplatevariable',
                        {**self.api._web_params(),'fields':json.dumps(['is_vip','is_svip','is_evip'])})
                    body=self.api.parse_response(response,'account');self.api._check_service(body,'account')
                    if self.stamp()!=owner:return 'unknown'
                    flags=body.get('result',{})
                    self.member=tier(flags) if isinstance(flags,dict) else 'unknown'
            except (Exception,):
                self.member='unknown'
            return self.member

    async def link(self,item,attempt=0):
        if attempt>=2:return None
        if await self.membership() not in ('svip','evip'):return None
        owner=self.stamp()
        try:
            async with asyncio.timeout(8):
                response=await self.api.client.post('https://d.pcs.baidu.com/rest/2.0/pcs/file',
                    params={'app_id':250528,'method':'locatedownload','path':item.path,'ver':'4.0',
                            'check_blue':1,'es':1,'esl':1,'ant':1,'ehps':1},
                    headers={'User-Agent':NATIVE_UA,'Referer':'https://pan.baidu.com/'},
                    timeout=7,follow_redirects=False)
                if response.status_code!=200 or len(response.content)>256*1024:raise ValueError()
                body=response.json()
                if not isinstance(body,dict) or body.get('error_code',0) not in (0,'0') or body.get('errno',0) not in (0,'0'):raise ValueError()
                rows=body.get('urls')
                if not isinstance(rows,list) or not 1<=len(rows)<=16:raise ValueError()
                from app.providers.baidu_web_download import checked_download_url
                links=[]
                for row in rows:
                    if not isinstance(row,dict) or row.get('encrypt') not in (0,'0'):continue
                    try:value=checked_download_url(row.get('url'))
                    except (BaiduApiError,TypeError):continue
                    if value not in links:links.append(value)
                if owner!=self.stamp() or not self.api.authenticated:raise ValueError()
                if not links or attempt>=len(links):raise ValueError()
                self.api.emit({'stage':'download_route','outcome':'locatedownload','entry_count':len(links)})
                return links[attempt]
        except Exception:
            self.api.emit({'stage':'download_route','outcome':'web_fallback'})
            return None
