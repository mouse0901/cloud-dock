"""Share previews independent of the user's current drive view."""
import copy,secrets,time
from http.cookiejar import CookieJar
from app.cloud_operations import CloudOperationError
from app.share_links import parse_links
from app.http_pool import client

def file_info(item):
    return {'id':item.id,'name':item.name,'path':item.path,'size':item.size,'type':'folder' if item.is_dir else item.category,'date':item.modified}

class ShareWorkspace:
    def __init__(self,bridge):self.bridge=bridge;self.sessions={}
    def session(self,token):
        row=self.sessions.get(token)
        if not row or time.monotonic()-row['at']>3600:raise CloudOperationError('分享已过期，请重新打开链接。')
        row['ops'].guard(row['stamp']);return row
    async def action(self,d):
        action=d['action']
        if action=='links_parse':return {'links':parse_links(d.get('text',''))}
        if action=='links_open':
            links=parse_links(d.get('link',''))
            if len(links)!=1:raise CloudOperationError('请选择一个百度或夸克分享链接。')
            link=links[0];password=d.get('password',link['password'])
            import re
            if not isinstance(password,str) or password and not re.fullmatch('[A-Za-z0-9]{4,8}',password):raise CloudOperationError('提取码应为 4–8 位字母或数字。')
            link['password']=password
            if not self.bridge.features:raise CloudOperationError('工作空间正在准备。')
            p=self.bridge.panels[link['provider']];ops=self.bridge.features.ops[link['provider']]
            account=await ops.account();stamp=ops.stamp();ops.guard(stamp)
            self.bridge.features.accounts[link['provider']]=account
            for key,row in list(self.sessions.items()):
                active=any(getattr(job,'_share_key','')==key and job.state not in ('completed','cancelled') for job in row['panel'].jobs)
                if not active and (row['stamp']!=row['ops'].stamp() or time.monotonic()-row['at']>600):
                    self.sessions.pop(key)
                    if row['client']:await row['client'].aclose()
            if len(self.sessions)>=12:raise CloudOperationError('打开的分享较多，请先完成已有分享下载，稍后再打开新链接。')
            owned=None
            try:
                if link['provider']=='baidu':
                    from app.providers.baidu_share import BaiduShare
                    jar=CookieJar()
                    for cookie in p.api.client.cookies.jar:jar.set_cookie(copy.copy(cookie))
                    owned=client(cookies=jar,timeout=20,follow_redirects=False)
                    share=BaiduShare(owned,link);rows=await share.open()
                    listing=share.list_page;resolver=share.source
                else:
                    share=await p.api.acquire_share(link['url'],password)
                    rows=await p.api.list_share(share)
                    async def listing(path='/',page=0):return await p.api.list_share(share,parent_id=share.directories[path],path=path,page=page)
                    async def resolver(item):return await p.api.share_download_source(item,share)
                ops.guard(stamp)
                token=secrets.token_hex(24)
                self.sessions[token]={'provider':link['provider'],'ops':ops,'stamp':stamp,'panel':p,'share':share,
                    'list':listing,'source':resolver,'client':owned,'at':time.monotonic(),'used':False,'path':'/','page':0,'rows':rows,'revision':0,'link':link,'inbox_id':d.get('inbox_id')}
                if d.get('inbox_id'):
                    saved=next((x for x in self.bridge.inbox.rows() if x['id']==d['inbox_id'] and x.get('url')==link['url'].split('?')[0]),None)
                    if not saved:self.sessions[token]['inbox_id']=None
                return self.snapshot(token)
            except BaseException:
                if owned:await owned.aclose()
                raise
        row=self.session(d.get('session'))
        if action!='links_list':raise CloudOperationError('分享操作无效。')
        path=d.get('path','/');page=d.get('page',0)
        if type(page) is not int or not 0<=page<200 or path not in row['share'].directories:raise CloudOperationError('分享目录无效。')
        row['revision']+=1;revision=row['revision']
        rows=await row['list'](path,page);row['ops'].guard(row['stamp'])
        if row['revision']!=revision:raise CloudOperationError('已切换到另一个分享目录。')
        row.update(path=path,page=page,rows=rows)
        if row.get('inbox_id') and self.bridge.inbox.store.get('link_inbox',row['inbox_id']):
            self.bridge.inbox.save(row['link'],path=path,page=page)
        return self.snapshot(d['session'])
    def snapshot(self,token):
        row=self.session(token)
        return {'session':token,'provider':row['provider'],'path':row['path'],'page':row['page'],
                'context':str(row['revision']),'files':[file_info(x) for x in row['rows']],
                'previous':row['page']>0,'next':len(row['rows'])==50}
    async def close(self):
        for row in self.sessions.values():
            if row['client']:await row['client'].aclose()
        self.sessions.clear()
