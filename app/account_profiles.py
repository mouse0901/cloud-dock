"""Small encrypted account catalog; credentials remain in independent DPAPI vaults."""
import hashlib,json,os,re,secrets,time
from pathlib import Path
from app.session_vault import SessionVault,SessionStoreError,crypt

class AccountProfiles:
    def __init__(self,directory):
        self.directory=Path(directory);self.path=self.directory/'accounts.catalog';self.vaults={};self.error=''
        self.catalog={'version':1,'providers':{p:{'active':'default','rows':[{'id':'default','name':'默认账号','account':'','used':0}]} for p in ('baidu','quark')}}
        try:
            if self.path.exists():
                if self.path.is_symlink() or self.path.stat().st_size>65536:raise ValueError()
                body=json.loads(crypt(self.path.read_bytes(),'account-catalog',decrypt=True));self.validate(body);self.catalog=body
        except Exception:self.error='账号列表无法读取，未覆盖原记录；请导出诊断后检查本机权限。'
    @staticmethod
    def validate(body):
        if body.get('version')!=1 or set(body['providers'])!={'baidu','quark'}:raise ValueError()
        for v in body['providers'].values():
            rows=v['rows']
            if not isinstance(rows,list) or not 1<=len(rows)<=5:raise ValueError()
            ids=[]
            for r in rows:
                if not re.fullmatch(r'default|[a-f0-9]{32}',r['id']) or not isinstance(r['name'],str) or not 1<=len(r['name'])<=32:raise ValueError()
                if not re.fullmatch(r'|[a-f0-9]{64}',r.get('account','')):raise ValueError()
                ids.append(r['id'])
            if len(set(ids))!=len(ids) or v['active'] not in ids:raise ValueError()
    def provider(self,p):
        if p not in self.catalog['providers']:raise SessionStoreError('网盘类型无效。')
        if self.error:raise SessionStoreError(self.error)
        return self.catalog['providers'][p]
    def row(self,p,key=None):
        v=self.provider(p);r=next((r for r in v['rows'] if r['id']==(key or v['active'])),None)
        if not r:raise SessionStoreError('保存的账号不存在。')
        return r
    def persist(self):
        if self.error:raise SessionStoreError(self.error)
        self.validate(self.catalog);self.directory.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_name('accounts-'+secrets.token_hex(12)+'.tmp')
        try:
            with tmp.open('xb') as f:f.write(crypt(json.dumps(self.catalog,ensure_ascii=False).encode(),'account-catalog'));f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:tmp.unlink(missing_ok=True)
    def vault(self,p,key=None):
        r=self.row(p,key);ident=(p,r['id'])
        if ident not in self.vaults:
            directory=self.directory if r['id']=='default' else self.directory/'profiles'/r['id']
            self.vaults[ident]=SessionVault(directory)
        return self.vaults[ident]
    def add(self,p,name):
        v=self.provider(p)
        if len(v['rows'])>=5:raise SessionStoreError('每家网盘最多保存 5 个账号，请先移除不再使用的账号。')
        name=self.name(name);r={'id':secrets.token_hex(16),'name':name,'account':'','used':0};v['rows'].append(r)
        try:self.persist()
        except Exception:v['rows'].remove(r);raise
        return r
    @staticmethod
    def name(value):
        if not isinstance(value,str) or not 1<=len(value.strip())<=32 or any(ord(c)<32 for c in value):raise SessionStoreError('账号昵称应为 1–32 个字。')
        return value.strip()
    def select(self,p,key):
        self.row(p,key);v=self.provider(p);old=v['active'];v['active']=key
        try:self.persist()
        except Exception:v['active']=old;raise
    def rename(self,p,key,name):
        r=self.row(p,key);old=r['name'];r['name']=self.name(name)
        try:self.persist()
        except Exception:r['name']=old;raise
    def bind(self,p,account):
        r=self.row(p)
        if r.get('account')==account:return
        old=dict(r);r.update(account=account,used=time.time())
        try:self.persist()
        except Exception:r.clear();r.update(old);raise
    def remove(self,p,key):
        v=self.provider(p);r=self.row(p,key)
        if key==v['active']:raise SessionStoreError('请先断开该账号。')
        self.vault(p,key).forget(p);index=v['rows'].index(r);v['rows'].remove(r)
        try:self.persist()
        except Exception:v['rows'].insert(index,r);raise
        self.vaults.pop((p,key),None)
    def public(self):
        if self.error:return {'error':self.error,'providers':{}}
        return {'error':'','providers':{p:{'active':v['active'],'rows':[{'id':r['id'],'name':r['name'],'verified':bool(r.get('account'))} for r in v['rows']]} for p,v in self.catalog['providers'].items()}}
    def label(self,p,account):
        if self.error:return '原账号'
        return next((r['name'] for r in self.provider(p)['rows'] if r.get('account')==account),'原账号')

class ProfileVault:
    def __init__(self,catalog,provider):self.catalog=catalog;self.provider=provider;self.preferences={}
    def load(self,p):
        vault=self.catalog.vault(p);jar=vault.load(p);self.preferences[p]=vault.preferences.get(p,True);return jar
    def save(self,p,jar):self.catalog.vault(p).save(p,jar)
    def forget(self,p,**kwargs):self.catalog.vault(p).forget(p,**kwargs)
