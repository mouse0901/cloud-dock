"""Local, batched activity notifications. No credentials or download URLs are stored."""
from __future__ import annotations
import secrets,time
from datetime import datetime

DEFAULTS = dict(download_sound=True, upload_sound=True, error_sound=False,
                download_notice=True, upload_notice=True, error_notice=True, sync_notice=True,
                desktop_notifications=True, notification_filenames=False, sound_volume=30,
                quiet_enabled=False, quiet_start='22:00', quiet_end='08:00')

def preferences(saved):
    result=dict(DEFAULTS)
    if saved.get('completion_sound') is False:
        result.update(download_sound=False,upload_sound=False)
    value=saved.get('feedback',{})
    if isinstance(value,dict):
        for key,default in DEFAULTS.items():
            v=value.get(key)
            if type(default) is bool and type(v) is bool:result[key]=v
            elif key=='sound_volume' and type(v) is int and 0<=v<=100:result[key]=v
            elif key in ('quiet_start','quiet_end') and valid_time(v):result[key]=v
    return result

def valid_time(value):
    return isinstance(value,str) and len(value)==5 and value[2]==':' and value[:2].isdigit() and value[3:].isdigit() and int(value[:2])<24 and int(value[3:])<60

def quiet(p,now=None):
    if not p['quiet_enabled']:return False
    current=(now or datetime.now()).strftime('%H:%M')
    start,end=p['quiet_start'],p['quiet_end']
    return start<=current<end if start<end else current>=start or current<end

class Feedback:
    def __init__(self,workspace,store,clock=time.monotonic):
        self.workspace=workspace;self.store=store;self.clock=clock
        self.states={};self.pending={};self.conditions={};self.delivery=[]

    def rows(self):
        return sorted(self.store.all('notification'),key=lambda r:r['at'],reverse=True)[:100]

    def snapshot(self):
        rows=self.rows()
        return {'preferences':preferences(self.workspace.settings),'items':rows,'unread':sum(not r['read'] for r in rows)}

    async def configure(self,patch):
        if not isinstance(patch,dict) or not patch or any(k not in DEFAULTS for k in patch):raise ValueError('通知设置无效。')
        for k,v in patch.items():
            d=DEFAULTS[k]
            if type(d) is bool and type(v) is not bool:raise ValueError('开关值无效。')
            if k=='sound_volume' and (type(v) is not int or not 0<=v<=100):raise ValueError('音量应在 0 到 100 之间。')
            if k in ('quiet_start','quiet_end') and not valid_time(v):raise ValueError('请填写有效的时间。')
        p=preferences(self.workspace.settings);p.update(patch);self.workspace.settings['feedback']=p
        await self.workspace.save()
        if not p['notification_filenames']:
            with self.store.db:
                self.store.db.execute("UPDATE records SET payload=json_remove(payload,'$.name') WHERE kind='notification'")
        return p

    def tick(self,tasks):
        now=self.clock();current=set()
        for t in tasks:
            if t.get('archived'):continue
            key=t['id'];current.add(key);old=self.states.get(key);status=t['status'];self.states[key]=status
            if status==old or status not in ('completed','failed'):continue
            group=(t['provider'],t['direction']);batch=self.pending.setdefault(group,{'first':now,'last':now,'tasks':{}})
            batch['last']=now;batch['tasks'][key]=dict(t)
        self.states={k:v for k,v in self.states.items() if k in current}
        for group,batch in list(self.pending.items()):
            # A retried/cancelled job must not produce a stale failure notification.
            batch['tasks']={k:t for k,t in batch['tasks'].items() if k in current and self.states.get(k)==t['status']}
            if now-batch['last']<3 and now-batch['first']<15:continue
            self.pending.pop(group)
            rows=list(batch['tasks'].values())
            if not rows:continue
            success=sum(t['status']=='completed' for t in rows);failed=len(rows)-success
            provider,direction=group;verb='上传' if direction=='upload' else '下载'
            detail=(f'完成 {success} 项，失败 {failed} 项' if failed and success else f'{failed} 项{verb}未完成' if failed else f'{success} 项{verb}完成')
            self.publish('error' if failed else direction,detail,provider=provider,success=success,failed=failed,
                         task=rows[0]['id'] if len(rows)==1 else '',name=rows[0]['name'] if len(rows)==1 else '',view='transfers')

    def condition(self,key,active,title,kind='error',provider='',view='transfers'):
        if active and not self.conditions.get(key):self.publish(kind,title,provider=provider,view=view)
        self.conditions[key]=bool(active)

    def publish(self,kind,title,**details):
        p=preferences(self.workspace.settings)
        row={'id':secrets.token_hex(12),'at':time.time(),'read':False,'kind':kind,'title':title,**details}
        if not p['notification_filenames']:row.pop('name',None)
        self.store.put('notification',row['id'],row)
        all_rows=sorted(self.store.all('notification'),key=lambda r:r['at'],reverse=True)
        for old in all_rows[100:]:self.store.remove('notification',old['id'])
        self.delivery.append(row['id']);self.delivery=self.delivery[-100:]
        return row

    def poll(self,ack=()):
        ack=set(ack) if isinstance(ack,list) else set()
        self.delivery=[key for key in self.delivery if key not in ack]
        p=preferences(self.workspace.settings);events=[]
        for key in self.delivery:
            row=self.store.get('notification',key)
            if not row:continue
            category=row['kind'];audible=p.get(category+'_sound',False)
            events.append({**row,'notice':p.get(category+'_notice',False),'audible':audible})
        return {'events':events,'preferences':p,'quiet':quiet(p)}

    def read(self,clear=False):
        with self.store.db:
            if clear:self.store.db.execute("DELETE FROM records WHERE kind='notification'")
            else:self.store.db.execute("UPDATE records SET payload=json_set(payload,'$.read',json('true')) WHERE kind='notification'")
        # Opening the inbox acknowledges pending popups as well.
        self.delivery.clear()
