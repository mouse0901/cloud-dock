"""Live acceptance: upload only generated fixtures using Cloud Dock's saved sessions."""
import asyncio,hashlib,json,sys,secrets
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from app.session_vault import SessionVault
from app.providers.baidu_web import BaiduWebApi
from app.providers.quark_web import QuarkWebApi
from app.providers.web_upload import BaiduUploader,QuarkUploader
from app.upload import UploadJob,UploadError
from app.direct_download import DirectDownload

async def main():
    out=ROOT/'work/upload-live-check';out.mkdir(parents=True,exist_ok=True)
    run_id=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
    fixture=out/('cloud-dock-upload-check-'+run_id+'.txt')
    payload=('Cloud Dock upload acceptance fixture. No private user data. 云屿上传测试。\n'.encode()*400)+secrets.token_bytes(32).hex().encode()
    if '--multipart' in sys.argv:payload=payload*128+secrets.token_bytes(32)
    fixture.write_bytes(payload);results=[]
    for provider,cls,adapter_cls in [('quark',QuarkWebApi,QuarkUploader),('baidu',BaiduWebApi,BaiduUploader)]:
        if len(sys.argv)>1 and provider!=sys.argv[1]:continue
        result={'provider':provider,'fixture':fixture.name,'bytes':len(payload)};events=[]
        jar=SessionVault().load(provider)
        if not jar:result['outcome']='no_saved_session';results.append(result);print(json.dumps(result),flush=True);continue
        async with httpx.AsyncClient(cookies=jar) as client:
            api=cls(client,diagnostic=lambda e:events.append({k:v for k,v in e.items() if k in ('stage','http_status','service_code','entry_count')}))
            try:
                await api.restore_login();result['login']='restored';print(provider+': session restored',flush=True)
                job=UploadJob(fixture,provider,'/',adapter_cls(api));item=await job.run();result['upload_state']=job.state;result['remote_size']=item.size
                print(provider+': uploaded; verifying bytes',flush=True)
                async def progress(done,total):pass
                download=DirectDownload(item,out/provider,client,api.download_source,api.validate_download_url,progress)
                await download.run();actual=hashlib.sha256(download.target.read_bytes()).hexdigest();expected=hashlib.sha256(payload).hexdigest()
                result.update(outcome='roundtrip_pass' if actual==expected else 'roundtrip_mismatch',sha256=actual,hash_match=actual==expected)
            except Exception as e:
                result.update(outcome='failed',error_type=type(e).__name__,code=getattr(e,'code','unknown'))
                if type(e).__name__ in ('UploadError','QuarkApiError','BaiduApiError'):result['message']=str(e)
            result['events']=events[-16:]
        results.append(result);print(json.dumps(result,ensure_ascii=True),flush=True)
        (out/('result-'+run_id+'.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT_FILE='+str(out/('result-'+run_id+'.json')),flush=True)
asyncio.run(main())
