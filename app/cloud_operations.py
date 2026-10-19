"""Account-scoped file operations. No permanently-delete or overwrite operation."""
from __future__ import annotations
import asyncio,hashlib,json,re
from pathlib import PurePosixPath
from urllib.parse import urlsplit,urljoin
from app.domain import CloudFile
from app.providers.web_upload import BaiduUploader
from app.providers.quark_web import DRIVE,PARAMS
class CloudOperationError(Exception):pass
class CloudOperations:
    def __init__(self,provider,api):
        self.provider,self.api=provider,api
        self.identity='';self.identity_stamp=None
        self.lock=asyncio.Lock()
    def stamp(self):
        # Identity cache is invalidated by login/logout; never keyed only by provider.
        names={'BDUSS','BDUSS_BFESS'} if self.provider=='baidu' else {'__pus','__uid'}
        rows=sorted((c.domain,c.name,c.value) for c in self.api.client.cookies.jar if c.name in names)
        return hashlib.sha256(repr(rows).encode()).hexdigest() if rows and self.api.authenticated else None
    async def account(self):
        stamp=self.stamp()
        if not stamp:raise CloudOperationError('请先连接网盘账号。')
        if self.identity and stamp==self.identity_stamp:return self.identity
        if self.provider=='baidu':
            await self.baidu_token()
            body=await BaiduUploader(self.api).call('gettemplatevariable',{'fields':json.dumps(['uk'])})
            uid=str(body.get('result',{}).get('uk',''))
        else:
            body=await self.api._request('GET','https://pan.quark.cn/account/info','account',params={'fr':'pc','platform':'pc'})
            if body.get('success') is not True:raise CloudOperationError('无法确认当前账号，自动备份已暂停。')
            uids={c.value for c in self.api.client.cookies.jar if c.name=='__uid' and c.domain.lstrip('.') in ('quark.cn','pan.quark.cn')}
            uid=uids.pop() if len(uids)==1 else ''
        if not uid or uid in ('0','None') or self.stamp()!=stamp:raise CloudOperationError('无法确认当前账号，自动备份已暂停。')
        self.identity=hashlib.sha256((self.provider+':'+uid).encode()).hexdigest();self.identity_stamp=stamp
        return self.identity
    def guard(self,stamp):
        if not stamp or self.stamp()!=stamp:raise CloudOperationError('账号已切换，操作已停止。')
    async def baidu_token(self):
        if self.api._bdstoken:return
        url='https://pan.baidu.com/disk/main'
        for _ in range(6):
            p=urlsplit(url)
            if p.scheme!='https' or (p.hostname,p.path) not in [('pan.baidu.com','/disk/main'),('passport.baidu.com','/v3/login/api/auth/')]:
                raise CloudOperationError('登录同步地址无法确认。')
            response=await self.api.request(url)
            if not response.is_redirect:break
            url=urljoin(url,response.headers.get('location',''))
        b=await BaiduUploader(self.api).call('gettemplatevariable',{'fields':json.dumps(['bdstoken'])})
        token=b.get('result',{}).get('bdstoken','')
        if not re.fullmatch('[A-Za-z0-9_-]{8,128}',token):raise CloudOperationError('无法确认文件操作授权。')
        self.api._bdstoken=token
    async def call(self,route,data=None,params=None):
        stamp=self.stamp();self.guard(stamp)
        if self.provider=='baidu':
            await self.baidu_token();self.guard(stamp)
            result=await BaiduUploader(self.api).call(route,params,data)
        else:
            body=await self.api._request('POST' if data is not None else 'GET',DRIVE+'/'+route,'files',params={**PARAMS,**(params or {})},data=data)
            result=self.api._success(body,stage='files')
        self.guard(stamp)
        return result or {}
    async def list(self,path,limit=None):
        stamp=self.stamp();self.guard(stamp)
        if self.provider=='quark' and path not in self.api._directories:
            parent=str(PurePosixPath(path).parent)
            await self.list(parent,limit=limit)
        result=[]
        for page in range(10000):
            self.guard(stamp);rows=await self.api._fetch_files(path,page);self.guard(stamp)
            result.extend(rows)
            if limit is not None and len(result)>limit:raise CloudOperationError('文件夹内容超过本次扫描上限，请分批选择更小的文件夹。')
            if len(rows)<50:return result
        raise CloudOperationError('文件夹过大，请缩小备份范围。')
    async def mkdir(self,parent,name,*,exclusive=False):
        stamp=self.stamp();self.guard(stamp)
        if not name or name in ('.','..') or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):raise CloudOperationError('文件夹名称无效。')
        rows=await self.list(parent);found=next((x for x in rows if x.name==name or exclusive and x.name.casefold()==name.casefold()),None)
        if found:
            if exclusive:raise CloudOperationError('目标已出现同名项目，请重新预览。')
            if not found.is_dir:raise CloudOperationError('云端有同名文件，未覆盖。')
            return found
        self.guard(stamp)
        path=parent.rstrip('/')+'/'+name
        if self.provider=='baidu':await self.call('create',{'path':path,'isdir':'1','size':'0','block_list':'[]','rtype':'0'},{'a':'commit'})
        else:await self.call('file',{'pdir_fid':self.api._directories[parent],'file_name':name,'dir_init_lock':False,'dir_path':''})
        found=None
        for _ in range(8):
            rows=await self.list(parent);found=next((x for x in rows if x.name==name and x.is_dir),None)
            if found:break
            await asyncio.sleep(.5)
        if not found:raise CloudOperationError('文件夹创建结果待核对。')
        return found
    async def ensure(self,path):
        current='/'
        for part in PurePosixPath(path).parts[1:]:current=(await self.mkdir(current,part)).path
        return current
    async def relocate(self,item,directory,name):
        """Move/rename an identified item without replacing an existing target."""
        if not name or name in ('.','..') or any(c in name for c in '/\\') or any(ord(c)<32 for c in name):raise CloudOperationError('文件名无效。')
        stamp=self.stamp();self.guard(stamp)
        fresh=next((x for x in await self.list(item.parent) if x.id==item.id),None)
        if fresh is None or fresh.path!=item.path or fresh.size!=item.size or fresh.revision!=item.revision:raise CloudOperationError('云端文件已变化，请重新核对。')
        target=directory.rstrip('/')+'/'+name
        if target==item.path:return fresh
        if any(x.name.casefold()==name.casefold() for x in await self.list(directory)):raise CloudOperationError('目标已有同名项目，未替换。')
        self.guard(stamp)
        if self.provider=='baidu':
            value={'path':item.path,'newname':name}
            if directory!=item.parent:value['dest']=directory
            await self.call('filemanager',{'filelist':json.dumps([value]),'async':'0','ondup':'fail'},
                            {'opera':'rename' if directory==item.parent else 'move','onnest':'fail'})
        else:
            # Move and rename separately. Callers journal each step when both differ.
            if directory!=item.parent and name!=item.name:raise CloudOperationError('请分步移动和改名。')
            if directory==item.parent:await self.call('file/rename',{'fid':item.id,'file_name':name})
            else:await self.call('file/move',{'action_type':1,'filelist':[item.id],'to_pdir_fid':self.api._directories[directory],'exclude_fids':[]})
        for _ in range(8):
            self.guard(stamp);found=next((x for x in await self.list(directory) if x.id==item.id and x.name==name),None)
            if found:return found
            await asyncio.sleep(.5)
        raise CloudOperationError('改名或移动结果待核对，未重复提交。')
    async def copy(self,item,directory):
        if self.provider!='baidu':raise CloudOperationError('夸克暂未接入云端复制。')
        stamp=self.stamp();self.guard(stamp)
        fresh=next((x for x in await self.list(item.parent) if x.id==item.id),None)
        if not fresh or (fresh.path,fresh.size,fresh.revision)!=(item.path,item.size,item.revision):raise CloudOperationError('源文件已变化，请刷新。')
        if item.is_dir and (directory==item.path or directory.startswith(item.path+'/')):raise CloudOperationError('不能复制到自身或子目录。')
        if any(x.name.casefold()==item.name.casefold() for x in await self.list(directory)):raise CloudOperationError('目标已有同名项目，未覆盖。')
        self.guard(stamp)
        await self.call('filemanager',{'filelist':json.dumps([{'path':item.path,'dest':directory,'newname':item.name}]),'async':'0','ondup':'fail'},{'opera':'copy','onnest':'fail'})
        for _ in range(8):
            self.guard(stamp)
            found=next((x for x in await self.list(directory) if x.name==item.name and x.is_dir==item.is_dir and (item.is_dir or x.size==item.size)),None)
            if found:return found
            await asyncio.sleep(.5)
        raise CloudOperationError('复制结果尚未确认，请刷新核对；未重复提交。')
    async def trash(self,page=0):
        if type(page) is not int or page<0:raise CloudOperationError('页码无效。')
        body=await self.call('recycle/list' if self.provider=='baidu' else 'file/recycle/list',params=
            {'start':page*50,'num':50,'dir':'/','order':'time','desc':1} if self.provider=='baidu' else {'_page':page+1,'_size':50})
        result=[]
        for row in body.get('list',[]):
            if self.provider=='baidu':
                result.append({'id':str(row['fs_id']),'fid':str(row['fs_id']),'name':row.get('server_filename') or PurePosixPath(row.get('path','')).name,
                    'path':row.get('path',''),'directory':bool(row.get('isdir')),'size':row.get('size',0),'remaining':row.get('leftTime'),'deleted':row.get('server_mtime',0)})
            else:
                result.append({'id':str(row['record_id']),'fid':str(row['fid']),'name':row.get('file_name',''),
                    'path':row.get('file_path',''),'parent_id':str(row.get('pdir_fid','')),'directory':row.get('file_type')==0,'size':row.get('size',0),'remaining':None,'deleted':row.get('move_recycle_at',0)})
        return result
    async def delete(self,items):
        if not items or len(items)>50:raise CloudOperationError('一次最多选择 50 项。')
        # Re-read authoritative IDs before path-addressed operations.
        parents={}
        for item in items:
            if item.parent not in parents:parents[item.parent]=await self.list(item.parent)
            if not any(f.id==item.id and f.path==item.path and f.size==item.size and (not item.revision or f.revision==item.revision) for f in parents[item.parent]):raise CloudOperationError('文件已经变化，请刷新后再删除。')
        if self.provider=='baidu':await self.call('filemanager',{'filelist':json.dumps([x.path for x in items]),'async':'0'},{'opera':'delete'})
        else:await self.call('file/delete',{'action_type':1,'exclude_fids':[],'filelist':[x.id for x in items]})
        for _ in range(10):
            rows=await self.trash();ids={x['fid'] for x in rows}
            if all(x.id in ids for x in items):return
            await asyncio.sleep(.6)
        raise CloudOperationError('删除结果尚未在回收站确认，请刷新核对；未重复删除。')
    async def restore(self,row):
        path=row.get('path','')
        if self.provider=='quark':
            parent=row.get('parent_id')
            if not parent:raise CloudOperationError('无法确认原文件夹，请到夸克官方回收站还原此项。')
            for page in range(10000):
                listing=await self.call('file/sort',params={'pdir_fid':parent,'_page':page+1,'_size':50})
                rows=listing.get('list',[])
                if any(x.get('file_name')==row['name'] for x in rows):raise CloudOperationError('原位置已有同名项目，请先处理重名后再还原。')
                if len(rows)<50:break
            else:raise CloudOperationError('无法完成原位置重名检查。')
        if path.startswith('/'):
            try:existing=await self.list(str(PurePosixPath(path).parent))
            except Exception:raise CloudOperationError('无法检查原位置，请稍后重试或到官方回收站还原。') from None
            if existing is not None and any(x.name==PurePosixPath(path).name for x in existing):raise CloudOperationError('原位置已有同名项目，请先处理重名后再还原。')
        if self.provider=='baidu':await self.call('recycle/restore',{'fidlist':json.dumps([int(row['id'])])})
        else:await self.call('file/recycle/recover',{'select_mode':2,'record_list':[row['id']]})
        for _ in range(10):
            if not any(x['id']==row['id'] for x in await self.trash()):return
            await asyncio.sleep(.6)
        raise CloudOperationError('还原结果尚未确认，请刷新回收站核对。')
