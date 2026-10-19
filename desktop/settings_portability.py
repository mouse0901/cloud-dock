"""Portable preferences contain no accounts, credentials, paths or automatic work."""
import json,secrets,time
from app.cloud_operations import CloudOperationError
from app.layout_preferences import DEFAULTS as LAYOUT_DEFAULTS, clean as clean_layout

ENUMS={'theme':{'light','dark','system'},'view':{'list','grid'},'density':{'comfortable','compact'},'grid_size':{'small','medium','large'},'sort_field':{'name','size','date'}}
BOOLS={'full_names','sort_desc','reduce_motion','close_to_tray','clipboard_links','clipboard_wake','completion_sound'}
FEEDBACK_BOOLS={'download_sound','upload_sound','error_sound','download_notice','upload_notice','error_notice','sync_notice','desktop_notifications','notification_filenames','quiet_enabled'}

def clean(settings,strict=False):
    if not isinstance(settings,dict):raise CloudOperationError('设置文件格式无效。')
    allowed=set(ENUMS)|BOOLS|{'feedback'}|set(LAYOUT_DEFAULTS)
    if strict and set(settings)-allowed:raise CloudOperationError('设置文件含未支持字段，未导入。请使用云屿导出的设置文件。')
    layout={k:v for k,v in settings.items() if k in LAYOUT_DEFAULTS}
    out=clean_layout(layout)
    if set(layout)!=set(out):raise CloudOperationError('布局尺寸或显示选项无效。')
    for k,values in ENUMS.items():
        if k in settings:
            if not isinstance(settings[k],str) or settings[k] not in values:raise CloudOperationError('设置选项无效：'+k)
            out[k]=settings[k]
    for k in BOOLS:
        if k in settings:
            if type(settings[k]) is not bool:raise CloudOperationError('设置开关无效：'+k)
            out[k]=settings[k]
    if 'feedback' in settings:
        f=settings['feedback'];result={}
        if not isinstance(f,dict):raise CloudOperationError('声音设置无效。')
        if strict and set(f)-(FEEDBACK_BOOLS|{'sound_volume','quiet_start','quiet_end'}):raise CloudOperationError('声音设置包含未支持字段。')
        for k in FEEDBACK_BOOLS:
            if k in f:
                if type(f[k]) is not bool:raise CloudOperationError('声音开关无效。')
                result[k]=f[k]
        if 'sound_volume' in f:
            if type(f['sound_volume']) is not int or not 0<=f['sound_volume']<=100:raise CloudOperationError('音量无效。')
            result['sound_volume']=f['sound_volume']
        import re
        for k in ('quiet_start','quiet_end'):
            if k in f:
                if not isinstance(f[k],str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',f[k]):raise CloudOperationError('免打扰时间无效。')
                result[k]=f[k]
        out['feedback']=result
    return out

class SettingsPortability:
    def __init__(self,bridge):self.b=bridge;self.plans={}
    async def action(self,d):
        a=d['action'];self.plans={k:v for k,v in self.plans.items() if time.monotonic()-v['at']<300}
        if a=='settings_export':return {'product':'Cloud Dock','schema':1,'settings':clean(self.b.workspace.settings)}
        if a=='settings_preview':
            raw=d.get('text')
            if not isinstance(raw,str) or len(raw.encode())>65536:raise CloudOperationError('设置文件不能超过 64 KB。')
            try:
                def unique(pairs):
                    result={}
                    for key,value in pairs:
                        if key in result:raise ValueError()
                        result[key]=value
                    return result
                value=json.loads(raw,object_pairs_hook=unique)
                if set(value)!={'product','schema','settings'} or value['product']!='Cloud Dock' or type(value['schema']) is not int or value['schema']!=1:raise ValueError()
            except (ValueError,TypeError,RecursionError):raise CloudOperationError('不是受支持的云屿设置文件。') from None
            fields=clean(value['settings'],True)
            if not fields:raise CloudOperationError('设置文件没有可导入的选项。')
            token=secrets.token_hex(24);self.plans[token]={'at':time.monotonic(),'fields':fields}
            return {'token':token,'fields':fields,'count':len(fields)}
        if a=='settings_apply':
            plan=self.plans.pop(d.get('token'),None)
            if not plan:raise CloudOperationError('设置预览已过期，请重新选择文件。')
            if self.b.update_busy():raise CloudOperationError('请在传输和同步暂停后导入设置。')
            import copy
            previous=copy.deepcopy(self.b.workspace.settings);fields=plan['fields']
            try:
                if 'feedback' in fields:self.b.workspace.settings['feedback']={**previous.get('feedback',{}),**fields['feedback']}
                self.b.workspace.settings.update({k:v for k,v in fields.items() if k!='feedback'})
                await self.b.workspace.save()
            except Exception:self.b.workspace.settings=previous;raise
            if fields.get('feedback',{}).get('notification_filenames') is False and self.b.features:
                with self.b.features.store.db:self.b.features.store.db.execute("UPDATE records SET payload=json_remove(payload,'$.name') WHERE kind='notification'")
            return {'applied':list(fields),'theme':self.b.workspace.settings['theme'],'reduced':self.b.workspace.settings['reduce_motion'],'file_view':{k:self.b.workspace.settings.get(k,v) for k,v in {'view':'list','sort_field':'name','sort_desc':False,'density':'comfortable','grid_size':'medium','full_names':False,**LAYOUT_DEFAULTS}.items()}}
        raise CloudOperationError('设置操作无效。')
