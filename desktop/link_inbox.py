"""Explicitly accepted links only. Link text and passcodes stay DPAPI encrypted."""
import base64,hashlib,json,re,time
from app.share_links import parse_links
from app.session_vault import crypt,SessionStoreError
from app.cloud_operations import CloudOperationError

class LinkInbox:
    def __init__(self,bridge):self.b=bridge
    @property
    def store(self):return self.b.features.store
    def rows(self):
        result=[]
        for record in self.store.all('link_inbox'):
            try:row=json.loads(crypt(base64.b64decode(record['sealed']),'link-inbox',decrypt=True));result.append(row)
            except Exception:result.append({'id':record['id'],'unavailable':True,'at':record['at'],'title':'此记录无法由当前 Windows 账号解密'})
        return sorted(result,key=lambda r:r['at'],reverse=True)
    def save(self,link,*,title='',path=None,page=None):
        url=link['url'].split('?')[0];key=hashlib.sha256(url.encode()).hexdigest()
        old=next((x for x in self.rows() if x['id']==key and not x.get('unavailable')),None)
        row={'id':key,'url':url,'provider':link['provider'],'password':link['password'],
             'title':title or (old or {}).get('title','') or url,
             'path':path if path is not None else (old or {}).get('path','/'),
             'page':page if page is not None else (old or {}).get('page',0),'at':time.time()}
        sealed=base64.b64encode(crypt(json.dumps(row,ensure_ascii=False).encode(),'link-inbox')).decode()
        self.store.put('link_inbox',key,{'id':key,'sealed':sealed,'at':row['at']})
        for record in sorted(self.store.all('link_inbox'),key=lambda x:x['at'],reverse=True)[100:]:self.store.remove('link_inbox',record['id'])
        return row
    async def action(self,d):
        if not self.b.features:raise CloudOperationError('工作空间正在准备。')
        a=d['action']
        if a=='inbox_list':return {'rows':self.rows()}
        if a=='inbox_remove':self.store.remove('link_inbox',d.get('id'));return {}
        if a=='inbox_clear':
            for row in self.store.all('link_inbox'):self.store.remove('link_inbox',row['id'])
            return {}
        if a in ('inbox_add','inbox_edit'):
            links=parse_links(d.get('text',''))
            if len(links)!=1:raise CloudOperationError('一次收下一条有效分享链接。')
            link=links[0];password=d.get('password',link['password']);title=d.get('title','')
            if not isinstance(password,str) or password and not re.fullmatch('[A-Za-z0-9]{4,8}',password):raise CloudOperationError('提取码应为 4–8 位字母或数字。')
            if not isinstance(title,str) or len(title)>200:raise CloudOperationError('备注最多 200 个字。')
            link['password']=password
            try:return self.save(link,title=title)
            except SessionStoreError:raise CloudOperationError('无法加密保存链接，请检查当前 Windows 用户权限。') from None
        raise CloudOperationError('链接收件箱操作无效。')
