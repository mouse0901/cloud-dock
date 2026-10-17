"""Opt-in real-account probe; only touches a newly created unique test folder."""
import asyncio,json,sys,time
from pathlib import Path
from uuid import uuid4
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.session_vault import SessionVault
from app.providers.baidu_web import BaiduWebApi
from app.providers.quark_web import QuarkWebApi
from app.cloud_operations import CloudOperations
from app.providers.web_upload import BaiduUploader,QuarkUploader
from app.upload import UploadJob

async def main():
    results={};vault=SessionVault(Path('H:/CodexOutputs/projects/cloud-dock/data/sessions'))
    fixture=ROOT/'work/sync-probe';fixture.mkdir(exist_ok=True);local=fixture/'sync-probe.txt';local.write_text('Cloud Dock synchronization protocol check\n'+uuid4().hex,encoding='utf-8')
    for provider,cls,adapter in [('baidu',BaiduWebApi,BaiduUploader),('quark',QuarkWebApi,QuarkUploader)]:
        row={};results[provider]=row;created=None;ops=None
        async with httpx.AsyncClient(trust_env=False,timeout=30) as client:
            try:
                jar=vault.load(provider)
                for cookie in jar:client.cookies.jar.set_cookie(cookie)
                api=cls(client,diagnostic=lambda event:None);await api.restore_login();ops=CloudOperations(provider,api);await ops.account();row['login']=True
                name='CloudDock-sync-check-'+time.strftime('%Y%m%d')+'-'+uuid4().hex[:8]
                created=await ops.mkdir('/',name);row['mkdir']=True
                stamp=ops.stamp();job=UploadJob(local,provider,created.path,adapter(api),owner=stamp,current_owner=ops.stamp)
                item=await job.run();row['upload']=True
                item=await ops.relocate(item,created.path,'renamed.txt');row['rename']=True
                target=await ops.mkdir(created.path,'archive');item=await ops.relocate(item,target.path,item.name);row['move']=True
                await ops.delete([item]);row['trash']=True
            except Exception as error:
                row['error_type']=type(error).__name__;row['error_code']=getattr(error,'code','')
                row['message']=str(error) if type(error).__name__ in ('UploadError','CloudOperationError','SessionStoreError') else 'Login or protocol check failed'
            finally:
                if created and ops:
                    try:
                        fresh=next((x for x in await ops.list('/') if x.id==created.id and x.path==created.path),None)
                        if fresh:await ops.delete([fresh]);row['probe_folder_recycled']=True
                    except Exception:row['probe_folder_recycled']=False
                (fixture/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False))
asyncio.run(main())
