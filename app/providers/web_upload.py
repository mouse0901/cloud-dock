"""Provider upload protocols. Credentials/tickets stay in memory, with no raw logging."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import mimetypes
import re
from email.utils import formatdate
from urllib.parse import quote, urlencode, urlsplit, urljoin
import httpx
from app.upload import UploadError, BLOCK_SIZE
from app.providers.baidu_web import UA as BAIDU_UA
from app.providers.quark_web import DRIVE, PARAMS, UA as QUARK_UA, QuarkApiError

async def response_bytes(client, method, url, *, headers=None, **kwargs):
    try:
        async with client.stream(method,url,headers=headers,follow_redirects=False,
                                 timeout=httpx.Timeout(120,connect=15),**kwargs) as response:
            raw=bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw)>2*1024*1024:raise UploadError('upload_response','上传服务返回数据过大，已停止处理。')
            if response.is_redirect:raise UploadError('upload_address','上传服务要求未验证的跳转，已停止传输。')
            if response.status_code not in (200,201,203,204):
                raise UploadError('upload_http',f'上传请求未成功（HTTP {response.status_code}），请重试。')
            return bytes(raw),response.headers
    except httpx.HTTPError:
        raise UploadError('upload_network','上传连接中断或超时，请检查网络后重试。') from None

async def json_response(*args,**kwargs):
    raw,headers=await response_bytes(*args,**kwargs)
    try:
        body=json.loads(raw)
        if not isinstance(body,dict):raise ValueError()
        return body
    except (ValueError,UnicodeError):raise UploadError('upload_response','上传服务返回的数据无法识别。') from None

def field(data,key,pattern=r'[\x21-\x7e]{1,8192}'):
    value=data.get(key)
    if not isinstance(value,str) or not re.fullmatch(pattern,value):
        raise UploadError('upload_response','上传服务返回的参数不完整。')
    return value

class UploadAdapter:
    def __init__(self,api):self.api=api
    async def lookup(self,job):
        # Always read the live directory, bypassing the login prefetch. Scan all
        # pages so a name on page two cannot be silently overwritten.
        for page in range(10000):
            job.guard()
            rows=await self.api._fetch_files(job.directory,page)
            job.guard()
            match=next((f for f in rows if f.name==job.item.name),None)
            if match:return match
            if len(rows)<50:return None
        raise UploadError('upload_directory','目录过大，暂不能完成重名检查。')
    async def preflight(self,job):
        if not self.api.authenticated:raise UploadError('upload_account','请先登录对应网盘。')
        check=getattr(self.api,'capacity_check',None)
        if check:
            from app.cloud_operations import CloudOperationError
            try:check(job.total)
            except CloudOperationError as error:raise UploadError('upload_capacity',str(error)) from None
        if await self.lookup(job):raise UploadError('upload_conflict','目标目录已有同名项目；未覆盖，请重命名本地文件后上传。')
    async def verify(self,job):
        for attempt in range(4):
            item=await self.lookup(job)
            if item and job.remote_id is not None and item.id != job.remote_id:
                raise UploadError('upload_verify','目标目录的文件标识发生变化，上传结果待核对。')
            if item and not item.is_dir and item.size==job.total:
                changed=getattr(self.api,'capacity_changed',None)
                if changed:changed()
                return item
            if item:raise UploadError('upload_verify','目标目录的文件大小与本地不同，上传结果待核对。')
            if attempt<3:await asyncio.sleep(.5*(attempt+1))
        raise UploadError('upload_verify','尚未在目标目录确认文件，请稍后点击“核对结果”。')

class BaiduUploader(UploadAdapter):
    async def call(self,route,params=None,data=None):
        p=self.api._web_params();p.update(params or {})
        body=await json_response(self.api.client,'POST' if data is not None else 'GET','https://pan.baidu.com/api/'+route,
            params=p,data=data,headers={'User-Agent':BAIDU_UA,'Referer':'https://pan.baidu.com/','Origin':'https://pan.baidu.com'})
        code=body.get('errno')
        self.api.emit({'stage':'upload_'+route,'service_code':str(code) if str(code).lstrip('-').isdigit() else 'unknown'})
        if code not in (0,'0'):
            safe=str(code) if re.fullmatch(r'-?\d{1,10}',str(code)) else 'unknown'
            if route in ('gettemplatevariable','filemanager','recycle/list','recycle/restore'):
                from app.cloud_operations import CloudOperationError
                if code in (-6,'-6'):
                    raise CloudOperationError('百度文件操作授权未通过（代码 '+safe+'），请重新连接账号后重试。')
                labels={'gettemplatevariable':'账号核对','filemanager':'文件操作','recycle/list':'回收站读取','recycle/restore':'回收站还原'}
                raise CloudOperationError('百度未完成'+labels[route]+'（代码 '+safe+'）。请刷新目录及回收站核对结果，未自动重复提交。')
            if code in (-8,'-8'):raise UploadError('upload_conflict','目标已有同名文件，未覆盖，请重命名后上传。')
            raise UploadError('upload_service','百度未完成上传请求（代码 '+safe+'）。')
        return body

    async def upload(self,job):
        await self.preflight(job)
        if not self.api._bdstoken:
            # Directory access can work before the official Passport handoff has
            # scoped STOKEN to pan.baidu.com. Follow only this fixed login route.
            url='https://pan.baidu.com/disk/main'
            for _ in range(6):
                p=urlsplit(url)
                allowed=((p.hostname=='pan.baidu.com' and p.path=='/disk/main') or
                         (p.hostname=='passport.baidu.com' and p.path=='/v3/login/api/auth/'))
                if not allowed or p.scheme!='https' or p.username or p.password or p.port not in (None,443):
                    raise UploadError('upload_session','百度登录同步地址无法验证，请重新连接账号。')
                job.guard()
                response=await self.api.request(url)
                if not response.is_redirect:break
                url=urljoin(url,response.headers.get('location',''))
            else:raise UploadError('upload_session','百度登录同步尚未完成，请重试。')
            body=await self.call('gettemplatevariable',{'fields':json.dumps(['bdstoken'])})
            self.api._bdstoken=field(body.get('result',{}),'bdstoken',r'[A-Za-z0-9_-]{8,128}')
        job.guard()
        form={'path':job.item.path,'size':str(job.total),'isdir':'0','autoinit':'1','rtype':'0',
              'block_list':json.dumps(job.hashes['blocks']),'content-md5':job.hashes['md5'],
              'slice-md5':hashlib.md5(await job.read(0,min(job.total,256*1024))).hexdigest()}
        job.commit_started=True
        try:
            info=await self.call('precreate',data=form)
        except UploadError as exc:
            if exc.code in {'upload_service','upload_conflict'}:job.commit_started=False
            raise
        if info.get('return_type')==2:
            if info.get('fs_id') is not None:job.remote_id=str(info['fs_id'])
            return
        upload_id=field(info,'uploadid')
        needed=info.get('block_list')
        if not isinstance(needed,list) or any(type(n) is not int or not 0<=n<len(job.hashes['blocks']) for n in needed):
            raise UploadError('upload_response','百度返回的上传分片列表无法识别。')
        job.commit_started=False
        await job.stage('uploading')
        # PCS is a fixed Baidu-owned endpoint. The cookie jar applies scopes;
        # BDUSS is required by the PCS protocol and never appears in diagnostics.
        cookies=self.api.client.build_request('GET','https://pan.baidu.com/').headers.get('cookie','')
        match=re.search(r'(?:^|;\s*)BDUSS=([^;]+)',cookies)
        params={'method':'upload',**self.api._web_params(),'path':job.item.path,'uploadid':upload_id,'uploadsign':'0'}
        if match:params['BDUSS']=match[1]
        for number in needed:
            job.guard();offset=number*BLOCK_SIZE;data=await job.read(offset,BLOCK_SIZE)
            if hashlib.md5(data).hexdigest()!=job.hashes['blocks'][number]:raise UploadError('upload_changed','本地文件已改变，请重新选择。')
            boundary='CloudDock'+hashlib.sha256(upload_id.encode()).hexdigest()[:24]
            head=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="blob"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
            tail=f'\r\n--{boundary}--\r\n'.encode()
            async def multipart():
                yield head
                async for chunk in job.stream(data,offset):yield chunk
                yield tail
            answer=await json_response(self.api.client,'POST','https://d.pcs.baidu.com/rest/2.0/pcs/superfile2',
                params={**params,'partseq':number},content=multipart(),headers={'User-Agent':BAIDU_UA,'Referer':'https://pan.baidu.com/',
                'Content-Type':'multipart/form-data; boundary='+boundary,'Content-Length':str(len(head)+len(data)+len(tail))})
            if answer.get('md5')!=job.hashes['blocks'][number]:raise UploadError('upload_checksum','百度上传分片校验未通过，请重试。')
        await job.stage('committing');job.commit_started=True
        created=await self.call('create',{'a':'commit'},data={'path':job.item.path,'size':str(job.total),'isdir':'0','rtype':'0',
            'uploadid':upload_id,'block_list':json.dumps(job.hashes['blocks'])})
        if created.get('fs_id') is not None:job.remote_id=str(created['fs_id'])
        job.guard()

class QuarkUploader(UploadAdapter):
    def __init__(self,api,storage_client=None):super().__init__(api);self.storage_client=storage_client
    async def call(self,route,data):
        try:
            body=await self.api._request('POST',DRIVE+'/'+route,'upload',params=PARAMS,data=data)
            return self.api._success(body,stage='upload'),body.get('metadata',{})
        except QuarkApiError as exc:
            if exc.service_code==32003:
                raise UploadError('upload_capacity','夸克网盘容量已超出额度，无法上传新文件；请先在官方网盘处理容量后重试。') from None
            if exc.service_code==23008:
                raise UploadError('upload_conflict','目标目录已有同名文件；未覆盖，请重命名后上传。') from None
            raise UploadError('upload_service',str(exc)) from None
    @staticmethod
    def storage_url(info):
        bucket=field(info,'bucket',r'[a-z0-9-]{1,63}');key=field(info,'obj_key',r'[A-Za-z0-9/_.-]{1,4096}')
        if any(p in ('','.','..') for p in key.split('/')):raise UploadError('upload_address','夸克上传对象路径无法识别。')
        value=field(info,'upload_url')
        p=urlsplit(value if '://' in value else 'https://'+value)
        host=p.hostname or ''
        if host=='pds.quark.cn':host=bucket+'.'+host
        if (host!=bucket+'.pds.quark.cn' or p.scheme not in ('http','https') or p.port not in (None,80,443)
                or p.username or p.password or p.query or p.fragment or p.path not in ('','/')):
            raise UploadError('upload_address','夸克返回了未验证的上传服务地址，已停止传输。')
        return 'https://'+host+'/'+quote(key,safe='/'),bucket,key

    async def upload(self,job):
        await self.preflight(job)
        parent=self.api._directories.get(job.directory)
        if parent is None:raise UploadError('upload_path','请重新打开目标文件夹后上传。')
        mime=mimetypes.guess_type(job.item.name)[0] or 'application/octet-stream'
        stamp=job.fingerprint.modified_ns//1000000
        info,metadata=await self.call('file/upload/pre',{'ccp_hash_update':True,'parallel_upload':True,
            'pdir_fid':parent,'dir_name':'','size':job.total,'file_name':job.item.name,'format_type':mime,
            'l_created_at':stamp,'l_updated_at':stamp})
        if not isinstance(info,dict):raise UploadError('upload_response','夸克返回的上传任务不完整。')
        task_id=field(info,'task_id');job.guard();job.commit_started=True
        hashes,_=await self.call('file/update/hash',{'task_id':task_id,'md5':job.hashes['md5'],'sha1':job.hashes['sha1']})
        if isinstance(hashes,dict) and hashes.get('finish'):return
        job.commit_started=False
        url,bucket,key=self.storage_url(info)
        upload_id=field(info,'upload_id');auth_info=field(info,'auth_info');callback=info.get('callback')
        if not isinstance(callback,dict):raise UploadError('upload_response','夸克上传回调信息不完整。')
        part_size=metadata.get('part_size',BLOCK_SIZE) if isinstance(metadata,dict) else BLOCK_SIZE
        if type(part_size) is not int or not 1<=part_size<=16*1024*1024:raise UploadError('upload_response','夸克上传分片大小不受支持。')
        sdk='aliyun-sdk-js/6.6.1'
        async def signed(method,content_type,query,extra=None,md5=''):
            job.guard();date=formatdate(usegmt=True)
            headers={'x-oss-date':date,'x-oss-user-agent':sdk,**(extra or {})}
            canonical=''.join(k+':'+headers[k]+'\n' for k in sorted(headers))
            meta=method+'\n'+md5+'\n'+content_type+'\n'+date+'\n'+canonical+'/'+bucket+'/'+key+'?'+query
            auth,_=await self.call('file/upload/auth',{'task_id':task_id,'auth_info':auth_info,'auth_meta':meta})
            result={**headers,'Authorization':field(auth,'auth_key',r'OSS [A-Za-z0-9_+/=:.-]{1,4096}'),'Content-Type':content_type,'Referer':'https://pan.quark.cn/'}
            if md5:result['Content-MD5']=md5
            return result
        client=self.storage_client or httpx.AsyncClient(trust_env=False)
        etags=[]
        try:
            await job.stage('uploading')
            for number,offset in enumerate(range(0,max(1,job.total),part_size),1):
                data=await job.read(offset,part_size)
                query=urlencode({'partNumber':number,'uploadId':upload_id})
                headers=await signed('PUT',mime,query);headers['Content-Length']=str(len(data))
                _,response_headers=await response_bytes(client,'PUT',url+'?'+query,content=job.stream(data,offset),headers=headers)
                etag=response_headers.get('etag','').strip('"')
                if not re.fullmatch(r'[a-fA-F0-9]{32}',etag):raise UploadError('upload_checksum','夸克没有返回有效的分片校验结果。')
                if etag.lower()!=hashlib.md5(data).hexdigest():raise UploadError('upload_checksum','夸克上传分片校验未通过。')
                etags.append(etag)
            await job.stage('committing');job.commit_started=True
            xml=('<?xml version="1.0" encoding="UTF-8"?>\n<CompleteMultipartUpload>'+''.join(
                f'<Part><PartNumber>{i}</PartNumber><ETag>"{etag}"</ETag></Part>' for i,etag in enumerate(etags,1))+'</CompleteMultipartUpload>').encode()
            checksum=base64.b64encode(hashlib.md5(xml).digest()).decode()
            callback64=base64.b64encode(json.dumps(callback,separators=(',',':')).encode()).decode()
            query=urlencode({'uploadId':upload_id});headers=await signed('POST','application/xml',query,{'x-oss-callback':callback64},checksum)
            await response_bytes(client,'POST',url+'?'+query,content=xml,headers=headers)
            job.guard()
            await self.call('file/upload/finish',{'task_id':task_id,'obj_key':key})
        finally:
            if self.storage_client is None:await client.aclose()
