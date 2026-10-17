"""Opt-in integration acceptance: only unique newly-created test folders."""
import asyncio,json,sys,time
from pathlib import Path
from uuid import uuid4
import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.session_vault import SessionVault
from app.providers.baidu_web import BaiduWebApi
from app.providers.quark_web import QuarkWebApi
from app.cloud_operations import CloudOperations
from app.sync_engine import SyncEngine
from app.durable_store import DurableStore
from app.providers.web_upload import BaiduUploader
from app.upload import UploadJob

async def main():
    target=ROOT/'work/sync-live'/uuid4().hex;target.mkdir(parents=True);result={}
    vault=SessionVault(Path('H:/CodexOutputs/projects/cloud-dock/data/sessions'))
    for provider,cls in [('baidu',BaiduWebApi),('quark',QuarkWebApi)]:
        row={};result[provider]=row;created=None;ops=None;engine=None;store=None
        async with httpx.AsyncClient(trust_env=False,timeout=45) as client:
            try:
                for cookie in vault.load(provider):client.cookies.jar.set_cookie(cookie)
                api=cls(client,diagnostic=lambda event:None);await api.restore_login();ops=CloudOperations(provider,api);await ops.account()
                created=await ops.mkdir('/','CloudDock-sync-acceptance-'+time.strftime('%Y%m%d')+'-'+uuid4().hex[:8])
                if provider=='quark':
                    child=await ops.mkdir(created.path,'before');child=await ops.relocate(child,created.path,'renamed');row['folder_rename']=True
                    dest=await ops.mkdir(created.path,'destination');await ops.relocate(child,dest.path,child.name);row['folder_move']=True
                    row['upload']='not_retested_account_capacity';continue
                local=target/'local';local.mkdir();file=local/'sync.txt';file.write_bytes(b'Cloud Dock initial version\n')
                store=DurableStore(target/'activity.db');engine=SyncEngine(store,{provider:ops})
                rule=await engine.preview(local,provider,created.path,'folder','two_way');engine.create(rule);rid=rule['id']
                async def settle():
                    for _ in range(3):
                        await engine.check(rid,True)
                        engine.observed[rid]={k:(v[0],v[1]-10) for k,v in engine.observed.get(rid,{}).items()}
                    saved=store.get('sync_rule',rid)
                    if saved['failures']:raise RuntimeError(saved['status'])
                await settle();assert any(x.name=='sync.txt' for x in await ops.list(created.path));row['initial_upload']=True
                file.write_bytes(b'Cloud Dock updated version\n');await settle();row['same_name_update']=True
                history=await ops.list(created.path+'/.clouddock-history/'+rid);assert history;row['old_version_retained']=True
                file.rename(local/'renamed.txt');await settle();rows=await ops.list(created.path);assert any(x.name=='renamed.txt' for x in rows) and not any(x.name=='sync.txt' for x in rows);row['rename_propagated']=True
                # Create a cloud-only file in the unique test root and verify downloaded bytes.
                fixture=target/'cloud-only.txt';fixture.write_bytes(b'Cloud to local acceptance\n')
                owner=ops.stamp();await UploadJob(fixture,provider,created.path,BaiduUploader(api),owner=owner,current_owner=ops.stamp).run()
                await settle();assert (local/fixture.name).read_bytes()==fixture.read_bytes();row['cloud_download']=True
                (local/'renamed.txt').unlink();await settle();assert any(x.name=='renamed.txt' for x in await ops.list(created.path));row['delete_waits_for_confirmation']=True
                await engine.resolve(rid,'renamed.txt','delete');await settle();assert not any(x.name=='renamed.txt' for x in await ops.list(created.path));row['confirmed_recycle']=True
                await engine.close();engine=SyncEngine(store,{provider:ops});await settle();assert not (local/'renamed.txt').exists();row['restart_no_resurrection']=True
            except Exception as error:
                row['error_type']=type(error).__name__
                row['stage_error']=str(error) if type(error).__name__ in ('CloudOperationError','UploadError','DownloadError','RuntimeError') else 'acceptance check failed'
            finally:
                if engine:await engine.close()
                if store:store.close()
                if created and ops:
                    try:
                        fresh=next((x for x in await ops.list('/') if x.id==created.id),None)
                        if fresh:await ops.delete([fresh]);row['probe_folder_recycled']=True
                    except Exception:row['probe_folder_recycled']=False
                (ROOT/'work/sync-live-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
asyncio.run(main())
