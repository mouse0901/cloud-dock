// Product experience rounds 2–3. Operations still use the original guarded plans.
(()=>{
 const dialog=$('#sheet');
 const reduced=()=>document.body.classList.contains('reduced')||matchMedia('(prefers-reduced-motion: reduce)').matches;
 const account=p=>live?.providers?.[p]?.account_name||'当前账号';
 // One navigation entry, with direct paste and encrypted saved links together.
 $('#linkInbox').hidden=false;$('#linkInbox span').textContent='链接库';$('#openLinks span').textContent='打开链接';$('#openLinks').title='粘贴并打开网盘链接（Ctrl + Shift + L）';
 const oldShare=window.openShareWorkspace;
 window.openShareWorkspace=function(...args){oldShare(...args);const draft=window.cloudDockLinkDraft;if(!args[0]&&draft){const input=$('#shareInput');if(input)input.value=draft.text;if($('#sharePassword'))$('#sharePassword').value=draft.password;}};
 // Account controls are separate siblings of the navigation button, never nested.
 for(const nav of $$('.nav.provider')){const p=nav.dataset.provider,wrap=document.createElement('div');wrap.className='provider-entry';nav.before(wrap);wrap.append(nav);const b=document.createElement('button');b.className='provider-account icon-button';b.setAttribute('aria-label',names[p]+'的账号与空间');b.title='账号与空间';b.textContent='···';b.onclick=()=>accounts(p);wrap.append(b);}
 // Keep secondary task actions accessible by keyboard, without a wall of buttons.
 function compactTask(node,t){
  const actions=node.querySelector('.task-actions'),extra=node.querySelector('.task-extra');if(!actions)return;
  const sig=JSON.stringify([t.status,t.error_code,t.committed,t.restart,t.archived,t.priority]);
  if(node.dataset.compact!==sig||!node.querySelector('.task-more')){
   node.dataset.compact=sig;
   const previous=node.querySelector('.task-more');if(previous){for(const b of previous.querySelectorAll('[data-command], [data-copy-target]'))actions.append(b);previous.remove();}
   const more=document.createElement('details');more.className='task-more';more.innerHTML='<summary aria-label="更多任务操作">···</summary><div class="task-more-menu"></div>';const menu=more.lastElementChild;
   const buttons=[...actions.querySelectorAll('button')];
   const preferred=t.status==='completed'?'open':['paused','failed'].includes(t.status)?'resume':'pause';
   let primary=buttons.find(b=>b.dataset.command===preferred||b.dataset.extra===preferred);
   if(!primary&&extra){primary=extra.querySelector('[data-extra="'+preferred+'"]');if(primary)actions.prepend(primary);}
   const disk=extra?.querySelector('.task-disk');if(disk&&t.status==='failed'){if(primary)menu.append(primary);primary=disk;disk.textContent='更换下载位置';actions.prepend(disk);}
   for(const b of buttons)if(b!==primary)menu.append(b);
   if(extra){for(const el of [...extra.children])if(!el.classList.contains('eta')){if(primary&&el.dataset.extra===preferred)el.remove();else menu.append(el);}}
   if(t.status==='failed'&&/auth|login|session/.test(t.error_code||'')){const b=document.createElement('button');b.className='quiet';b.textContent='重新连接账号';b.onclick=()=>accounts(t.provider);actions.prepend(b);if(primary)menu.prepend(primary);}
   if(t.status==='completed'&&t.direction==='upload'){const b=document.createElement('button');b.className='quiet';b.textContent='查看网盘位置';b.onclick=async()=>{if(!t.account_current){await accounts(t.provider);return;}await go(t.provider,t.path?.slice(0,t.path.lastIndexOf('/'))||'/');};actions.prepend(b);}
   const copy=node.querySelector('.task-path');if(copy)copy.classList.add('compact-path');
   actions.append(more);more.hidden=!menu.children.length;
   more.addEventListener('toggle',()=>{if(more.open)for(const other of $$('.task-more[open],.group-more[open]'))if(other!==more)other.open=false;});
  }
  const label=node.querySelector('.task-status');const stages={queued:'等待空闲连接',resolving:'获取下载地址',preparing:'检查本地文件',retrying:'正在重连',committing:'正在提交到网盘',verifying:'正在校验文件'};
  if(label&&stages[t.status])label.textContent=stages[t.status];
  if(label&&t.status==='downloading'&&t.total>0&&t.done>=t.total)label.textContent='正在核对并保存';
  const error=node.querySelector('.task-error');if(error&&!error.closest('details')&&error.textContent){const d=document.createElement('details');d.className='task-error-detail';const summary=document.createElement('summary');summary.textContent=error.textContent.slice(0,60)+(error.textContent.length>60?'…':'')+' · 详情';d.append(summary);error.before(d);d.append(error);}
 }
 const priorTasks=renderTasks;renderTasks=function(){priorTasks();if(state.view!=='transfers')return;for(const node of $$('#transferList [data-task]')){const t=live?.tasks?.find(t=>t.id===node.dataset.task);if(t)compactTask(node,t);}
  for(const group of $$('.transfer-group')){let primary=group.querySelector('.group-primary');if(!primary){primary=document.createElement('button');primary.className='quiet group-primary';group.querySelector('.group-more').before(primary);group.querySelector('.group-toggle').addEventListener('click',()=>{const body=group.querySelector('.group-body');if(!body.hidden&&!reduced())body.animate([{opacity:.4},{opacity:1}],{duration:150});});}const candidates=[...group.querySelectorAll('[data-group-command]')],next=candidates.find(b=>b.dataset.groupCommand==='pause'&&!b.disabled)||candidates.find(b=>b.dataset.groupCommand==='resume'&&!b.disabled)||candidates.find(b=>b.dataset.groupCommand==='retry_failed'&&!b.disabled);primary.hidden=!next;if(next){primary.textContent=next.textContent;primary.onclick=()=>next.click();}}
 };
 document.addEventListener('click',e=>{if(!e.target.closest('.task-more,.group-more'))for(const d of $$('.task-more[open],.group-more[open]'))d.open=false;});
 document.addEventListener('toggle',e=>{const d=e.target;if(!d.matches?.('.task-more,.group-more')||!d.open)return;for(const other of $$('.task-more[open],.group-more[open]'))if(other!==d)other.open=false;const menu=d.lastElementChild,box=d.querySelector('summary').getBoundingClientRect();menu.style.position='fixed';menu.style.maxHeight=Math.max(100,innerHeight-24)+'px';menu.style.overflowY='auto';menu.style.right='auto';menu.style.left=Math.max(12,Math.min(innerWidth-menu.offsetWidth-12,box.right-menu.offsetWidth))+'px';menu.style.top=Math.max(12,Math.min(innerHeight-menu.offsetHeight-12,box.bottom+4))+'px';},true);
 document.addEventListener('scroll',e=>{if(e.target.closest?.('.task-more-menu,.group-actions'))return;for(const d of $$('.task-more[open],.group-more[open]'))d.open=false;},true);
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){const d=$('.task-more[open],.group-more[open]');if(d){e.preventDefault();e.stopPropagation();d.open=false;d.querySelector('summary').focus();}}},true);
 // Reuse the same sync-detail region; the original DOM retains scroll and focus.
 let syncHome=null,syncId=null,syncScroll=0,syncFocus=null;
 const originalDetail=syncDetail;syncDetail=async function(id){syncHome=null;syncId=id;await originalDetail(id);if(sheetKind!=='syncDetail')return;const root=document.createElement('div');root.id='syncHome';while($('#sheetBody').firstChild)root.append($('#sheetBody').firstChild);$('#sheetBody').append(root);syncHome=root;};
 window.cloudDockSyncPane=function(title,html){if(!syncHome||!dialog.open||!syncHome.isConnected)return false;if(!syncHome.hidden){syncScroll=$('#sheetBody').scrollTop;syncFocus=document.activeElement;}syncHome.hidden=true;$('#syncPane')?.remove();const pane=document.createElement('div');pane.id='syncPane';pane.innerHTML='<h3>'+esc(title)+'</h3>'+html;$('#sheetBody').append(pane);$('#sheetBody').scrollTop=0;return true;};
 window.cloudDockSyncBack=function(){if(!syncHome?.isConnected)return false;window.cloudDockSyncReviewCancel?.();$('#syncPane')?.remove();syncHome.hidden=false;sheetKind='syncDetail';$('#sheetBody').scrollTop=syncScroll;syncFocus?.focus?.({preventScroll:true});return true;};
 const resolve=confirmSyncResolution;confirmSyncResolution=function(id,issue,choice){
  if(!syncHome?.isConnected){resolve(id,issue,choice);return;}
  // Render the existing confirmation, including its guarded actions, in-place.
  const root=syncHome,scroll=$('#sheetBody').scrollTop,focus=document.activeElement;root.remove();resolve(id,issue,choice);const pane=document.createElement('div');pane.id='syncPane';while($('#sheetBody').firstChild)pane.append($('#sheetBody').firstChild);root.hidden=true;$('#sheetBody').append(root,pane);syncHome=root;syncScroll=scroll;syncFocus=focus;
  if($('#syncResolveBack'))$('#syncResolveBack').onclick=()=>window.cloudDockSyncBack();
 };
 dialog.addEventListener('close',()=>{if(!dialog.open){syncHome=null;syncId=null;}});
 // A save-as action remains available even with quick download enabled.
 const context=showContext;showContext=function(file,x,y){context(file,x,y);if(!file||state.view!=='files'||!providerState()?.connected)return;const b=document.createElement('button');b.textContent='下载到…';b.onclick=()=>{closeMenus();queue(state.selected.has(file.id)?visible().filter(f=>state.selected.has(f.id)):[file],{choose:true});};$('#contextMenu').append(b);$('#contextMenu').style.top=Math.max(8,Math.min(y,innerHeight-$('#contextMenu').offsetHeight-12))+'px';};
 // Search navigates to real controls; hidden result rows never duplicate switches.
 const settingsBefore=settings;settings=function(){
  settingsBefore();
  const location=$('#downloadLocation')?.closest('.setting-line');if(location){const row=document.createElement('div');row.className='setting-line';row.innerHTML='<span>直接下载到默认位置<small>仅用于自己网盘中的单个文件；重名、文件夹和分享仍会确认</small></span><button id="quickDownloadSetting" class="switch '+(live?.quick_download?'on':'')+'" role="switch" aria-label="直接下载到默认位置" aria-checked="'+!!live?.quick_download+'"></button>';location.after(row);$('#quickDownloadSetting').onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const r=await api('download_preferences',{quick:b.getAttribute('aria-checked')!=='true'});live.quick_download=r.quick;b.classList.toggle('on',r.quick);b.setAttribute('aria-checked',String(r.quick));}catch(err){toast(err.message);}finally{b.disabled=false;}};}
  const panels=$('.settings-panels'),nav=$('.settings-nav');if(!panels||!nav)return;
  const search=document.createElement('div');search.className='settings-search';search.innerHTML='<input id="settingsSearch" type="search" placeholder="搜索设置…" aria-label="搜索设置"><button id="clearSettingsSearch" class="quiet" aria-label="清除设置搜索" hidden>×</button>';nav.prepend(search);
  const results=document.createElement('div');results.id='settingsResults';results.hidden=true;panels.prepend(results);
  const rows=[...panels.querySelectorAll('.setting-line')].map(row=>({row,panel:row.closest('[data-settings-panel]'),text:row.textContent.trim()})).filter(r=>r.panel);
  const aliases={'开机':'启动','自启':'启动','路径':'位置','声音':'提示音','音效':'提示音','cookie':'登录','最小化':'托盘'};
  const input=$('#settingsSearch');let active=$('[data-settings-tab].active')?.dataset.settingsTab||'run';
  function clear(){input.value='';results.hidden=true;$('#clearSettingsSearch').hidden=true;$('[data-settings-tab="'+active+'"]')?.click();}
  function draw(){const raw=input.value.trim().toLowerCase(),term=aliases[raw]||raw;$('#clearSettingsSearch').hidden=!raw;if(!raw){clear();return;}$$('[data-settings-panel]').forEach(p=>p.hidden=true);results.hidden=false;$$('[data-settings-tab]').forEach(b=>b.classList.remove('active'));const hits=rows.filter(r=>r.text.toLowerCase().includes(term)||r.text.toLowerCase().includes(raw));results.innerHTML=hits.length?'<p>'+hits.length+' 项设置</p>'+hits.map((r,i)=>'<button class="settings-hit" data-setting-hit="'+i+'"><strong>'+esc(r.row.querySelector('span,label')?.childNodes[0]?.textContent||r.text)+'</strong><small>'+esc(r.row.querySelector('small')?.textContent||'前往设置')+'</small></button>').join(''):'<p role="status">没有找到相关设置，试试“声音”“托盘”或“下载”。</p>';results.querySelectorAll('[data-setting-hit]').forEach(b=>b.onclick=()=>{const hit=hits[+b.dataset.settingHit];active=hit.panel.dataset.settingsPanel;clear();hit.row.scrollIntoView({block:'center'});hit.row.querySelector('button,input,select')?.focus({preventScroll:true});if(!reduced())hit.row.animate([{background:'var(--selected)'},{background:'transparent'}],{duration:700});});}
  input.oninput=draw;input.onkeydown=e=>{if(e.key==='Escape'&&input.value){e.preventDefault();e.stopPropagation();clear();}if(e.key==='Enter'){e.preventDefault();results.querySelector('button')?.click();}if(e.key==='ArrowDown'&&input.value){e.preventDefault();results.querySelector('button')?.focus();}};
  $('#clearSettingsSearch').onclick=()=>{clear();input.focus();};
  $$('[data-settings-tab]').forEach(b=>b.addEventListener('click',()=>{active=b.dataset.settingsTab;input.value='';results.hidden=true;$('#clearSettingsSearch').hidden=true;}));
 };$('#settings').onclick=settings;
 // Include the active account where a write destination is confirmed.
 const wizard=drawSyncWizard;drawSyncWizard=function(){wizard();const p=syncWizard?.provider;if(p){const note=document.createElement('p');note.className='destination-context';note.textContent=names[p]+' · '+account(p);$('#sheetBody').prepend(note);}};
 window.cloudDockPolish={compactTask};
})();
