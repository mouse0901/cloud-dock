// Activity feedback is delivered by the native host even when the WebView is suspended.
(()=>{
 const native=window.chrome?.webview;
 const defaults={download_sound:true,upload_sound:true,error_sound:false,download_notice:true,upload_notice:true,error_notice:true,sync_notice:true,desktop_notifications:true,notification_filenames:false,sound_volume:30,quiet_enabled:false,quiet_start:'22:00',quiet_end:'08:00'};
 const prefs=()=>({...defaults,...live?.feedback?.preferences});
 const reduced=()=>document.body.classList.contains('reduced')||matchMedia('(prefers-reduced-motion: reduce)').matches;
 const pulse=(el,frames,duration=180)=>{if(el&&!reduced())el.animate(frames,{duration,easing:'cubic-bezier(.2,.75,.25,1)'});};
 const entry=document.createElement('button');entry.id='notifications';entry.className='nav';entry.innerHTML='<svg class="icon" viewBox="0 0 24 24"><path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5Zm5 3h4"/></svg><span>通知</span><small id="notificationCount"></small>';
 $('#settings').before(entry);entry.onclick=showNotifications;
 $('.search').title='筛选当前页文件（Ctrl + F）';$('#search').setAttribute('aria-keyshortcuts','Control+F');
 let toastTimer,historyKey='',knownTasks=null,lastLocation='',lastMode=state.mode,navPending='',loadTimer;
 const positions=new Map();
 const notice=document.createElement('div');notice.className='feedback-toast';notice.hidden=true;notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');document.body.append(notice);

 async function savePreference(key,value){
  const r=await api('feedback_preferences',{preferences:{[key]:value}});
  if(live){live.feedback=live.feedback||{};live.feedback.preferences=r.preferences;}
  return r.preferences;
 }
 function settingRow(key,label,help){const p=prefs();return `<div class="setting-line"><span>${label}<small>${help}</small></span><button class="switch ${p[key]?'on':''}" role="switch" aria-checked="${!!p[key]}" aria-label="${label}" data-feedback-setting="${key}"></button></div>`;}
 const oldSettings=settings;
 settings=function(){
  oldSettings();$('#completionSound')?.closest('.setting-line')?.remove();
  const target=$('[data-settings-panel="feedback"]');if(!target)return;
  const p=prefs();target.innerHTML='<h3 class="setting-group-title">提示音</h3><p class="settings-description">云屿原创短音。同一批任务合并提醒，自动同步成功保持安静。</p>'+
   settingRow('download_sound','下载完成声音','文件已保存到本机后提醒')+settingRow('upload_sound','上传完成声音','网盘确认上传成功后提醒')+settingRow('error_sound','异常提醒声音','默认关闭；失败详情仍保留在任务和通知中')+
   `<div class="setting-line"><label for="feedbackVolume">提示音音量 <output id="volumeValue">${p.sound_volume}%</output></label><input id="feedbackVolume" type="range" min="0" max="100" value="${p.sound_volume}" aria-label="提示音音量"></div><div class="sound-previews">${[['download','试听下载'],['upload','试听上传'],['error','试听提醒']].map(([k,l])=>`<button class="quiet" data-sound-preview="${k}" ${native?'':'disabled'}>${l}</button>`).join('')}</div><h3 class="setting-group-title">通知</h3>`+
   settingRow('download_notice','下载完成通知','在窗口内提醒，后台时显示桌面卡片')+settingRow('upload_notice','上传完成通知','可直接查看上传到的网盘位置')+settingRow('error_notice','传输与账号异常','下载失败、空间不足或登录失效时提醒')+settingRow('sync_notice','同步待处理','合并提醒冲突和需要确认的项目')+settingRow('desktop_notifications','后台桌面通知','云屿在后台时显示；关闭此项不影响声音和通知记录')+settingRow('notification_filenames','通知显示文件名','默认只显示数量；关闭后清除通知记录中的文件名')+
   '<h3 class="setting-group-title">免打扰</h3>'+settingRow('quiet_enabled','定时免打扰','时段内不响铃、不弹出，通知记录照常保留')+
   `<div class="quiet-hours"><label>从 <input id="quietStart" type="time" value="${p.quiet_start}" aria-label="免打扰开始时间"></label><label>到 <input id="quietEnd" type="time" value="${p.quiet_end}" aria-label="免打扰结束时间"></label></div><p class="settings-description">相同时间表示全天免打扰。全屏或系统报告不宜打扰时也保持安静；手动试听除外。</p>`;
  target.querySelectorAll('[data-feedback-setting]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const value=b.getAttribute('aria-checked')!=='true';await savePreference(b.dataset.feedbackSetting,value);b.classList.toggle('on',value);b.setAttribute('aria-checked',String(value));}catch(e){report(e);}finally{b.disabled=false;}});
  $('#feedbackVolume').oninput=e=>$('#volumeValue').textContent=e.target.value+'%';
  $('#feedbackVolume').onchange=async e=>{const b=e.target;b.disabled=true;try{await savePreference('sound_volume',Number(b.value));}catch(err){b.value=prefs().sound_volume;$('#volumeValue').textContent=b.value+'%';report(err);}finally{b.disabled=false;}};
  target.querySelectorAll('[data-sound-preview]').forEach(b=>b.onclick=()=>native?.postMessage({type:'previewFeedbackSound',kind:b.dataset.soundPreview,volume:Number($('#feedbackVolume').value)}));
  for(const [id,key]of [['quietStart','quiet_start'],['quietEnd','quiet_end']])$('#'+id).onchange=async e=>{const b=e.target;try{await savePreference(key,b.value);}catch(err){b.value=prefs()[key];report(err);}};
  const about=$('[data-settings-panel="about"]'),author=document.createElement('div');author.className='author-card';
  author.innerHTML='<img src="cloud-dock.ico" width="44" height="44" alt="云屿"><div><strong>云屿 Cloud Dock</strong><p>作者：马蹄疾</p><button id="authorBilibili" class="quiet">哔哩哔哩主页 ↗</button><small>space.bilibili.com/3546747290782061</small></div>';
  about.prepend(author);$('#authorBilibili').onclick=()=>{if(native)native.postMessage({type:'openAuthor'});else toast('作者主页：https://space.bilibili.com/3546747290782061');};
 };$('#settings').onclick=settings;

 async function activate(item,quick=false){
  if($('#sheet').open&&sheetKind!=='notifications'){toast('当前窗口还有操作，请先完成或关闭后再查看通知。');return;}
  notice.hidden=true;
  if(item.view==='notifications'){showNotifications();return;}
  if($('#sheet').open)$('#sheet').close();
  const task=live?.tasks?.find(t=>t.id===item.task&&t.provider===item.provider);
  if(quick&&task?.status==='completed'){
   try{if(task.direction==='download'){await api('task',{provider:task.provider,id:task.id,command:'open'});return;}
    if(!task.archived&&live.providers[task.provider]?.connected){await go(task.provider,task.path.slice(0,task.path.lastIndexOf('/'))||'/');return;}
   }catch(e){report(e);}
  }
  if(item.view==='accounts'){accounts();return;}
  if(item.view==='backups'){changeView('backups');return;}
  if(item.view==='files'&&names[item.provider]){await go(item.provider,'/');return;}
  changeView('transfers');const filter=item.kind==='error'?'attention':'completed';$(`[data-transfer-filter="${filter}"]`)?.click();
  const row=$$('[data-task]').find(r=>r.dataset.task===item.task);row?.scrollIntoView({block:'center'});pulse(row,[{opacity:.45},{opacity:1}],220);
 }
 function notify(item){
  clearTimeout(toastTimer);const task=live?.tasks?.find(t=>t.id===item.task),quick=task?.status==='completed';
  notice.innerHTML=`<span class="feedback-mark ${item.kind==='error'?'issue':''}">${item.kind==='error'?'!':'✓'}</span><div><strong>${esc(item.title)}</strong>${item.name?'<small>'+esc(item.name)+'</small>':' '}<button class="quiet" data-feedback-open>${quick?(task.direction==='upload'?'查看网盘位置':'打开文件夹'):item.view==='backups'?'处理同步':'查看详情'}</button></div><button class="icon-button" aria-label="关闭通知" data-feedback-close>×</button>`;
  notice.hidden=false;notice.querySelector('[data-feedback-open]').onclick=()=>activate(item,quick);notice.querySelector('[data-feedback-close]').onclick=()=>notice.hidden=true;
  pulse(notice,[{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'none'}],200);
  toastTimer=setTimeout(()=>{if(!notice.matches(':hover')&&!notice.contains(document.activeElement))notice.hidden=true;},6500);
 }
 function drawNotifications(){
  const host=$('#notificationItems');if(!host)return;
  const rows=live?.feedback?.items||[],key=JSON.stringify(rows);if(key===historyKey)return;historyKey=key;
  host.innerHTML=rows.length?rows.map(r=>`<article class="notification-row"><span class="feedback-mark ${r.kind==='error'||r.kind==='sync'?'issue':''}">${r.kind==='error'||r.kind==='sync'?'!':'✓'}</span><div><strong>${esc(r.title)}</strong>${r.name?'<p>'+esc(r.name)+'</p>':''}<small>${esc(names[r.provider]||'云屿')} · ${new Date(r.at*1000).toLocaleString('zh-CN')}</small></div><button class="quiet" data-notification="${esc(r.id)}">查看</button></article>`).join(''):'<div class="notification-empty">这里会保留最近 100 条传输、同步与账号提醒。</div>';
  host.querySelectorAll('[data-notification]').forEach(b=>b.onclick=()=>activate(rows.find(r=>r.id===b.dataset.notification)));
 }
 async function showNotifications(){
  if($('#sheet').open&&sheetKind!=='notifications'){toast('请先完成或关闭当前窗口，再查看通知。');return;}
  sheetKind='notifications';sheet('通知','<div class="notification-toolbar"><span>仅保存在本机 · 最近 100 条</span><button class="quiet" id="clearNotifications">清空记录</button></div><div id="notificationItems"></div>');
  $('#sheet').classList.add('notification-sheet');historyKey='';drawNotifications();
  $('#clearNotifications').onclick=async()=>{try{await api('feedback_clear');await refreshState();historyKey='';drawNotifications();}catch(e){report(e);}};
  try{await api('feedback_read');await refreshState();drawNotifications();}catch(e){report(e);}
 }
 $('#sheet').addEventListener('close',()=>$('#sheet').classList.remove('notification-sheet'));
 native?.addEventListener('message',e=>{if(e.data?.type==='feedbackEvent')notify(e.data.item);if(e.data?.type==='feedbackOpen')activate(e.data.item);});

 function updateFeedback(){
  const count=live?.feedback?.unread||0;$('#notificationCount').textContent=count?String(Math.min(count,99))+(count>99?'+':''):'';entry.setAttribute('aria-label',count?`通知，${count} 条未读`:'通知');
  if(sheetKind==='notifications')drawNotifications();
  if(!live?.initializing){
   const current=new Map((live?.tasks||[]).map(t=>[t.id,t.status]));
   if(knownTasks){let added=0;for(const t of live?.tasks||[]){if(!knownTasks.has(t.id)&&!t.archived)added++;if(t.status==='completed'&&knownTasks.has(t.id)&&knownTasks.get(t.id)!=='completed'){const row=$$('[data-task]').find(r=>r.dataset.task===t.id);pulse(row,[{opacity:.5,transform:'translateY(3px)'},{opacity:1,transform:'none'}],220);}}
    if(added)pulse($('[data-view="transfers"]'),[{background:'var(--selected)',transform:'scale(.97)'},{background:'var(--selected)',transform:'scale(1)'},{background:'transparent',transform:'none'}],300);
   }knownTasks=current;
  }
  const p=providerState();const busy=state.view==='files'&&p?.connected&&p.busy;
  $('#fileList').setAttribute('aria-busy',String(!!busy));
  if(busy&&!loadTimer)loadTimer=setTimeout(()=>{loadTimer=null;if(state.view==='files'&&providerState()?.busy&&!$('#fileList .file-row'))$('#fileList').classList.add('loading-skeleton');},180);
  if(!busy){clearTimeout(loadTimer);loadTimer=null;$('#fileList').classList.remove('loading-skeleton');if(navPending&&state.view==='files'&&p?.path===state.path){$('#fileList').scrollTop=positions.get(navPending)||0;pulse($('#fileList'),[{opacity:.65,transform:'translateY(4px)'},{opacity:1,transform:'none'}],170);navPending='';}}
 }
 const oldRefresh=refreshState;refreshState=async function(){await oldRefresh();updateFeedback();};
 const oldRender=render;render=function(animate=false){
  const list=$('#fileList'),key=state.provider+'|'+state.path+'|'+(providerState()?.page||0),changed=state.view==='files'&&key!==lastLocation;
  if(lastLocation&&changed)positions.set(lastLocation,list.scrollTop);if(positions.size>150)positions.delete(positions.keys().next().value);
  const modeChanged=state.mode!==lastMode;const before=new Map();
  if(modeChanged&&!reduced())list.querySelectorAll('.file-row').forEach(el=>{const r=el.getBoundingClientRect();if(r.bottom>100&&r.top<innerHeight&&before.size<50)before.set(el.dataset.id,r);});
  oldRender(animate);if(state.view==='files'){lastLocation=key;if(changed){navPending=key;list.scrollTop=positions.get(key)||0;}syncSelection();}
  if(modeChanged&&!reduced())for(const el of list.querySelectorAll('.file-row')){const a=before.get(el.dataset.id);if(!a)continue;const b=el.getBoundingClientRect();if(b.top>innerHeight)continue;pulse(el,[{transform:`translate(${a.left-b.left}px,${a.top-b.top}px)`,opacity:.6},{transform:'none',opacity:1}],190);}
  lastMode=state.mode;list.classList.remove('entering');
  if(animate&&!changed&&!modeChanged)pulse(state.view==='transfers'?$('#transferList'):list,[{opacity:.7},{opacity:1}],140);
 };
 const oldSelection=syncSelection;syncSelection=function(){oldSelection();const selected=currentFiles().filter(f=>state.selected.has(f.id));const count=selected.length;
  if(state.view==='files'&&$('#footerHint'))$('#footerHint').textContent=count?'已选择 '+count+' 项'+(selected.some(f=>f.type!=='folder')?' · 文件大小 '+bytes(selected.filter(f=>f.type!=='folder').reduce((n,f)=>n+f.size,0)):''):'';
 };
 // Public only inside the trusted page; used by deterministic UI acceptance fixtures.
 window.cloudDockFeedback={notify,showNotifications,update:updateFeedback,activate};
 updateFeedback();
})();
