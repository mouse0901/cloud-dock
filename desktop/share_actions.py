"""Explicit, single-use confirmations for share creation and cloud-side saving."""
import asyncio,json,re,secrets,time
from pathlib import PurePosixPath
from app.cloud_operations import CloudOperationError
from app.share_links import parse_links
from app.sync_model import remote_mark,remote_equal
from desktop.productivity import cloud_path

class ShareActions:
    def __init__(self,bridge):self.b=bridge;self.plans={};self.active=0
    async def baidu(self,ops,route,data,extra=None,client=None):
        from app.providers.baidu_web import UA
        await ops.baidu_token();stamp=ops.stamp()
        params={**ops.api._web_params(),**(extra or {})}
        r=await (client or ops.api.client).post('https://pan.baidu.com/share/'+route,params=params,data=data,
              headers={'User-Agent':UA,'Referer':'https://pan.baidu.com/','Origin':'https://pan.baidu.com'},timeout=25,follow_redirects=False)
        ops.guard(stamp)
        if r.status_code!=200 or len(r.content)>4*1024*1024:raise CloudOperationError('百度分享请求结果待核对，未自动重试。')
        body=r.json()
        if not isinstance(body,dict) or body.get('errno') not in (0,'0'):raise CloudOperationError('百度未完成分享操作，请在官方页面检查权限或验证要求。')
        return body
    async def quark_task(self,ops,task,field):
        if not isinstance(task,str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}',task):raise CloudOperationError('分享任务标识无法确认，请到官方页面核对。')
        for index in range(16):
            body=await ops.call('task',params={'task_id':task,'retry_index':index})
            if body.get(field):return body[field]
            await asyncio.sleep(.7)
        raise CloudOperationError('分享任务仍未确认，请先到官方页面核对结果；未重复提交。')
    async def action(self,d):
        a=d['action'];self.plans={k:v for k,v in self.plans.items() if time.monotonic()-v['at']<300}
        if a=='sharing_prepare':
            kind=d.get('kind');session=None
            if kind=='save':
                session=self.b.shares.session(d.get('session'));name=session['provider'];p=session['panel'];ops=session['ops']
                account=await ops.account();stamp=ops.stamp();rows=session['rows']
                if d.get('context')!=str(session['revision']):raise CloudOperationError('分享目录已变化。')
            elif kind=='create':
                name,p,ops,account,stamp=await self.b.productivity.owner(d);rows=p.browser_rows
                if p.browser_busy or d.get('context')!=self.b.context(p):raise CloudOperationError('目录已变化，请重新选择。')
            else:raise CloudOperationError('分享操作无效。')
            ids=d.get('ids');items=[x for x in rows if isinstance(ids,list) and x.id in ids]
            if not items or len(items)>50 or len(items)!=len(set(ids)):raise CloudOperationError('一次选择 1–50 个已列出的项目。')
            plan={'kind':kind,'provider':name,'account':account,'stamp':stamp,'items':items,'at':time.monotonic(),'session':d.get('session')}
            if session:plan['share_path']=session['path'];plan['share_page']=session['page']
            if kind=='create':
                pwd=d.get('password','');days=d.get('days',7)
                if not isinstance(pwd,str) or not re.fullmatch('[A-Za-z0-9]{4}',pwd):raise CloudOperationError('请设置 4 位字母或数字提取码。')
                if days not in (1,7,30) or type(days) is not int:raise CloudOperationError('请选择有效期。')
                # Quark's documented web profile supports a seven-day private link.
                if name=='quark' and days!=7:raise CloudOperationError('夸克当前只接入 7 天有效期。')
                plan.update(password=pwd,days=days)
            else:
                path=cloud_path(d.get('target','/'));await ops.list(path,limit=5000)
                folder='转存-'+time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
                root=None if path=='/' else next((x for x in await ops.list(str(PurePosixPath(path).parent)) if x.path==path and x.is_dir),None)
                if path!='/' and not root:raise CloudOperationError('目标文件夹不存在。')
                plan.update(path=path,folder=folder,root_id=root.id if root else 'root')
            ops.guard(stamp);token=secrets.token_hex(24);self.plans[token]=plan
            return {'token':token,'provider':name,'kind':kind,'names':[x.name for x in items],
                    'target':plan.get('path','').rstrip('/')+'/'+plan['folder'] if kind=='save' else '',
                    'days':plan.get('days'),'password':plan.get('password')}
        if a!='sharing_commit':raise CloudOperationError('分享操作无效。')
        plan=self.plans.pop(d.get('token'),None)
        if not plan:raise CloudOperationError('确认已过期或已提交，请核对结果。')
        ops=self.b.features.ops[plan['provider']];ops.guard(plan['stamp'])
        if await ops.account()!=plan['account']:raise CloudOperationError('账号已改变。')
        self.active+=1
        try:
            async with ops.lock:
                ops.guard(plan['stamp'])
                if plan['kind']=='create':return await self.create(plan,ops)
                return await self.save(plan,ops)
        finally:self.active-=1
    async def create(self,plan,ops):
        for item in plan['items']:
            fresh=next((x for x in await ops.list(item.parent) if x.id==item.id and x.path==item.path),None)
            if not fresh or not remote_equal(remote_mark(fresh),remote_mark(item)):raise CloudOperationError('所选文件已变化，未创建分享。')
        ids=[x.id for x in plan['items']];pwd=plan['password']
        ops.guard(plan['stamp'])
        if plan['provider']=='baidu':
            data=await self.baidu(ops,'set',{'fid_list':json.dumps(ids),'schannel':'4','channel_list':'[]','period':str(plan['days']),'pwd':pwd,'eflag_disable':'true'})
            url=data.get('link') or data.get('shorturl','')
        else:
            data=await ops.call('share',{'fid_list':ids,'title':plan['items'][0].name,'url_type':2,'expired_type':3,'passcode':pwd})
            share_id=await self.quark_task(ops,data.get('task_id'),'share_id')
            data=await ops.call('share/password',{'share_id':share_id});url=data.get('share_url','')
            if data.get('passcode')!=pwd:raise CloudOperationError('服务返回的分享密码与设置不一致，请在夸克官方页面核对后再使用。')
        links=parse_links(url)
        if len(links)!=1 or links[0]['provider']!=plan['provider']:raise CloudOperationError('分享地址无法确认，请到官方“我的分享”核对。')
        return {'url':links[0]['url'].split('?')[0],'password':pwd,'days':plan['days']}
    async def save(self,plan,ops):
        row=self.b.shares.session(plan['session']);share=row['share'];path=plan['path']
        fresh=await row['list'](plan['share_path'],plan['share_page']);ops.guard(plan['stamp'])
        if any(not any(x.id==item.id and x.path==item.path and remote_equal(remote_mark(x),remote_mark(item)) for x in fresh) for item in plan['items']):raise CloudOperationError('分享文件已变化，请重新打开后选择。')
        if path!='/':
            root=next((x for x in await ops.list(str(PurePosixPath(path).parent)) if x.path==path and x.is_dir),None)
            if not root or root.id!=plan['root_id']:raise CloudOperationError('目标目录已变化。')
        folder=await ops.mkdir(path,plan['folder'],exclusive=True)
        ids=[x.id for x in plan['items']]
        try:
            if plan['provider']=='baidu':
                await self.baidu(ops,'transfer',{'fsidlist':json.dumps(ids),'path':folder.path},
                                 {'shareid':share.id,'from':share.uk},row['client'])
            else:
                data=await ops.call('share/sharepage/save',{'fid_list':ids,'fid_token_list':[share.file_tokens[x] for x in ids],
                  'to_pdir_fid':folder.id,'pwd_id':share.id,'stoken':share.token,'pdir_fid':share.directories[plan['share_path']],
                  'pdir_save_all':False,'exclude_fids':[],'scene':'link'})
                await self.quark_task(ops,data.get('task_id'),'save_as')
            for attempt in range(8):
                rows=await ops.list(folder.path)
                if all(any(x.name==item.name and x.is_dir==item.is_dir and (x.is_dir or x.size==item.size) for x in rows) for item in plan['items']):
                    return {'path':folder.path,'count':len(ids)}
                await asyncio.sleep(.6)
            raise CloudOperationError('服务已接收，但目标文件尚未全部确认。')
        except Exception:
            raise CloudOperationError('转存结果待核对，请打开 '+folder.path+' 查看；未自动重复提交。') from None
