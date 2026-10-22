"""Isolated share cookies, validated directories and account-bound download tickets."""
import json,re
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlsplit,unquote
from app.domain import CloudFile
from app.direct_download import DownloadSource,DownloadError
from app.providers.baidu_web import UA,valid_path
from app.providers.baidu_web_download import checked_download_url
from app.cloud_operations import CloudOperationError

class BaiduShare:
    def __init__(self,client,link):
        self.client=client;self.link=link;self.short=urlsplit(link['url']).path.split('/')[-1][1:]
        self.id='';self.uk='';self.sekey='';self.files={};self.directories={'/':None};self.initial=[]
    async def request(self,route,params=None,data=None):
        response=await self.client.request('POST' if data is not None else 'GET','https://pan.baidu.com/'+route,
            params=params,data=data,headers={'User-Agent':UA,'Referer':self.link['url']},timeout=20,follow_redirects=False)
        if response.status_code!=200 or len(response.content)>4*1024*1024:
            raise CloudOperationError('百度暂未允许读取分享，请稍后重试或在官方页面完成验证。')
        return response
    @staticmethod
    def success(body):
        if not isinstance(body,dict):raise CloudOperationError('分享响应无法识别，请稍后重试。')
        code=body.get('errno',0)
        if code not in (0,'0'):
            if str(code) in ('-9','-12'):raise CloudOperationError('提取码不正确，请修改后重试。')
            if str(code) in ('-62','-20'):raise CloudOperationError('百度要求额外验证，请在官方页面完成后重试。')
            raise CloudOperationError('分享已失效、被取消或暂时无法访问。')
        return body
    async def open(self):
        if self.link['password']:
            body=self.success((await self.request('share/verify',{'surl':self.short,'web':1,'clienttype':0},
                {'pwd':self.link['password'],'vcode':'','vcode_str':''})).json())
            self.sekey=body.get('randsk','')
            if not isinstance(self.sekey,str) or not 1<=len(self.sekey)<=4096:raise CloudOperationError('提取码验证信息不完整。')
            self.client.cookies.set('BDCLND',self.sekey,domain='pan.baidu.com',path='/')
        response=await self.request('s/1'+self.short)
        body=None
        for match in re.finditer(r'(?:locals\.mset|yunData\.setData)\s*\(',response.text):
            try:
                candidate,_=json.JSONDecoder().raw_decode(response.text[match.end():].lstrip())
                if isinstance(candidate,dict) and 'file_list' in candidate:body=candidate;break
            except (ValueError,TypeError):continue
        if body is None:raise CloudOperationError('分享需要提取码、已失效或要求官方验证，请检查后重试。')
        self.success(body)
        self.id=str(body.get('shareid',''));self.uk=str(body.get('share_uk',''))
        if not re.fullmatch(r'\d{1,25}',self.id) or not re.fullmatch(r'\d{1,25}',self.uk):raise CloudOperationError('分享信息不完整。')
        self.initial=self.decode(body.get('file_list'),'/')
        return self.initial[:50]
    def decode(self,rows,parent):
        if not isinstance(rows,list) or len(rows)>10000:raise CloudOperationError('分享目录过大或响应不完整。')
        output=[]
        for row in rows:
            try:
                fid=str(row['fs_id']);name=row['server_filename'];amount=row['size'];directory=str(row['isdir'])
                if not re.fullmatch(r'\d{1,25}',fid) or not isinstance(name,str) or name in ('','.','..') or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):raise ValueError()
                if directory not in ('0','1') or type(amount) is not int or amount<0:raise ValueError()
                # A shared file's service-side path can be an opaque marker.
                # We never use it for display or downloading: construct the
                # visible path from its validated name and parent instead.
                # Directory paths, by contrast, become the next list request.
                raw=row.get('path') if directory=='1' else None
                if directory=='1' and not valid_path(raw):raise ValueError()
                path=parent.rstrip('/')+'/'+name
                item=CloudFile(fid,'baidu',path,directory=='1',amount,
                    datetime.fromtimestamp(int(row.get('server_mtime',0))).strftime('%Y-%m-%d %H:%M'),str(row.get('md5','')))
                self.files[fid]=item
                if item.is_dir:self.directories[path]=raw
                output.append(item)
            except (KeyError,ValueError,TypeError,OverflowError,OSError):raise CloudOperationError('分享文件信息无法确认，已停止读取。') from None
        return output
    async def list_page(self,path='/',page=0):
        if type(page) is not int or not 0<=page<200 or path not in self.directories:raise CloudOperationError('请从分享目录逐层打开。')
        if path=='/':return self.initial[page*50:(page+1)*50]
        body=self.success((await self.request('share/list',{'shareid':self.id,'uk':self.uk,'dir':self.directories[path],
            'page':page+1,'num':50,'order':'name','desc':0,'web':1,'clienttype':0})).json())
        return self.decode(body.get('list'),path)
    async def source(self,item):
        try:
            if item.is_dir or self.files.get(item.id)!=item:raise CloudOperationError('请重新打开分享文件。')
            params={'shareid':self.id,'uk':self.uk,'fields':'sign,timestamp','web':1,'channel':'chunlei','app_id':250528,'clienttype':0}
            body=self.success((await self.request('share/tplconfig',params)).json()).get('data',{})
            sign=body.get('sign');stamp=str(body.get('timestamp',''))
            if not isinstance(sign,str) or not re.fullmatch('[A-Za-z0-9_-]{8,256}',sign) or not stamp.isdigit():raise CloudOperationError('分享下载授权暂不可用，请重试。')
            params.update(sign=sign,timestamp=stamp)
            data={'encrypt':0,'product':'share','type':'nolimit','primaryid':self.id,'uk':self.uk,
                  'fid_list':json.dumps([int(item.id)]),'extra':json.dumps({'sekey':unquote(self.sekey)})}
            body=self.success((await self.request('api/sharedownload',params,data)).json());rows=body.get('list')
            if isinstance(rows,str):
                # Some large shares return an opaque string to the web client
                # profile. A second, bounded request using the established
                # client download profile may return the ordinary file list.
                # Never decode or trust the opaque payload itself.
                params['clienttype']=12
                alternate=(await self.request('api/sharedownload',params,data)).json()
                if isinstance(alternate,dict) and str(alternate.get('errno'))=='9019':
                    raise DownloadError('share_ticket_unavailable','百度未提供可用的分享下载地址。')
                body=self.success(alternate);rows=body.get('list')
            if isinstance(rows,str):
                raise DownloadError('share_ticket_unavailable','百度返回了无法使用的分享下载票据。')
            if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict) or str(rows[0].get('fs_id'))!=item.id or rows[0].get('size')!=item.size:
                raise CloudOperationError('分享下载信息与所选文件不一致，请重新打开后核对。')
            if item.revision and rows[0].get('md5') and rows[0]['md5']!=item.revision:
                raise CloudOperationError('分享文件已变化，请重新打开。')
            return DownloadSource(checked_download_url(rows[0].get('dlink')),item.size,{'User-Agent':'pan.baidu.com','Referer':'https://pan.baidu.com/'})
        except CloudOperationError as error:raise DownloadError('share_download',str(error)) from None
