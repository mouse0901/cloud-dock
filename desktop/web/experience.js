// Desktop workflow improvements; provider account metadata never enters this UI.
(()=>{
 let preferencesLoaded=false,capacityKey='',favoriteKey='',transferFilter='active',transferRenderKey='';
 state.sortDesc=false;
 const size=n=>n===0?'0 B':n>=1e12?(n/1e12).toFixed(2)+' TB':bytes(n);
 const labels={name:'名称',size:'大小',date:'修改日期'};
 const crumb=$('#breadcrumbs'),footerHint=document.createElement('span');
 footerHint.id='footerHint';crumb.replaceWith(footerHint);$('.location').append(crumb);
 const up=document.createElement('button');up.className='icon-button';up.id='upLevel';up.title='上一级（Alt + ↑）';up.setAttribute('aria-label','上一级');up.innerHTML='<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20V4m-6 6 6-6 6 6"/></svg>';$('.history').append(up);
 up.onclick=()=>{const p=state.path.split('/').filter(Boolean);p.pop();go(state.provider,'/'+p.join('/'))};
 const pin=document.createElement('button');pin.id='pinFolder';pin.className='quiet';pin.textContent='固定此位置';$('#normalActions').append(pin);
 pin.onclick=async()=>{try{await api('favorite_toggle',{context:providerState()?.context});await refreshState();}catch(e){report(e)}};
 const favorites=document.createElement('div');favorites.id='favorites';$('[data-provider="quark"]').after(favorites);
 for(const provider of Object.keys(names)){
  const button=document.createElement('button');button.className='capacity-button';button.dataset.capacity=provider;button.hidden=true;
  $(`[data-provider="${provider}"]`).after(button);button.onclick=()=>showCapacity(provider);
 }
 function capacityText(q){return q?.free!==undefined?'剩余 '+size(q.free)+(q.state==='stale'?' · 待刷新':''):q?.state==='loading'?'正在读取空间…':'空间暂时无法读取'}
 function showCapacity(provider){
  sheetKind='capacity';sheet(names[provider]+' · 存储空间','<div id="capacityDetail"></div><button class="quiet" id="refreshCapacity">刷新空间</button>');
  $('#sheet').dataset.capacity=provider;renderCapacityDetail();$('#refreshCapacity').onclick=async()=>{try{await api('capacity_refresh',{provider});await refreshState();renderCapacityDetail();}catch(e){report(e)}};
 }
 function renderCapacityDetail(){const provider=$('#sheet').dataset.capacity,q=live?.capacities?.[provider];if(!$('#capacityDetail'))return;
  $('#capacityDetail').innerHTML=q?.total!==undefined?`<strong class="quota-number">${size(q.free)}<small>剩余空间</small></strong><div class="quota-track"><i style="width:${q.total?Math.min(100,100*q.used/q.total):100}%"></i></div><div class="quota-labels"><span>已用 ${size(q.used)}</span><span>总计 ${size(q.total)}</span></div><p>${q.over?'已超出容量 '+size(q.over)+'。':q.state==='stale'?'显示上次结果，稍后自动刷新。':'空间由网盘返回；回收站占用及释放时间以网盘为准。'}</p><small>更新时间：${q.checked?new Date(q.checked*1000).toLocaleString('zh-CN'):'—'}</small>`:`<p>${q?.state==='disconnected'?'账号未连接':capacityText(q)}</p><p>这不会影响目录浏览和已有传输。</p>`;
  $('#refreshCapacity').disabled=!!q?.loading||q?.state==='disconnected';
 }
 function refreshExtras(){
  if(!live)return;
  if(!preferencesLoaded&&live.file_view){preferencesLoaded=true;state.mode=live.file_view.view;state.sort=live.file_view.sort_field;state.sortDesc=live.file_view.sort_desc;render();}
  const key=JSON.stringify(live.capacities);if(key!==capacityKey){capacityKey=key;for(const provider of Object.keys(names)){const q=live.capacities?.[provider],b=$(`[data-capacity="${provider}"]`);b.hidden=!live.providers[provider]?.connected;b.textContent=capacityText(q);b.title='查看'+names[provider]+'存储空间';b.classList.toggle('quota-low',q?.total>0&&q.free/q.total<.05);}}
  if(sheetKind==='capacity')renderCapacityDetail();
  const fk=JSON.stringify(live.favorites);if(fk!==favoriteKey){favoriteKey=fk;favorites.innerHTML=live.favorites?.length?'<div class="nav-label">常用位置</div>'+live.favorites.map(f=>`<button class="favorite-place" data-favorite="${esc(f.id)}" title="${esc(names[f.provider]+' / '+f.path)}">${icon('folder')}<span>${esc(f.name)}</span><small>${f.provider==='baidu'?'百度':'夸克'}</small></button>`).join(''):'';favorites.querySelectorAll('button').forEach(b=>b.onclick=()=>{const f=live.favorites.find(x=>x.id===b.dataset.favorite);if(f)go(f.provider,f.path)});}
  const p=providerState();pin.hidden=state.view!=='files'||!p?.connected||!!p.share;pin.disabled=p?.busy||p?.path!==state.path;
  pin.textContent=live.favorites?.some(f=>f.provider===state.provider&&f.path===state.path)?'取消固定':'固定此位置';
 }
 const oldControls=updateLiveControls;updateLiveControls=function(){oldControls();refreshExtras()};
 const oldRender=render;render=function(animate=false){oldRender(animate);up.hidden=state.view!=='files';up.disabled=state.path==='/'||!providerState()?.connected||providerState()?.busy;crumb.hidden=state.view!=='files';$('#locationLabel').hidden=state.view==='files';footerHint.textContent=state.view==='files'?(providerState()?.share?'分享目录':'双击打开 · Ctrl / Shift 多选'):state.view==='transfers'?'退出后保留传输记录':'云屿 · 个人工作空间';updateSortHeaders();};
 visible=function(){const term=$('#search').value.toLocaleLowerCase();return currentFiles().filter(x=>(state.filter==='all'||x.type===state.filter)&&x.name.toLocaleLowerCase().includes(term)).sort((a,b)=>{const folders=Number(b.type==='folder')-Number(a.type==='folder');if(folders)return folders;let n=state.sort==='size'?a.size-b.size:state.sort==='date'?String(a.date).localeCompare(String(b.date)):a.name.localeCompare(b.name,'zh-Hans-CN',{numeric:true});return (state.sortDesc?-1:1)*n||a.name.localeCompare(b.name,'zh-Hans-CN',{numeric:true})})};
 function persistView(){api('file_view',{view:state.mode,sort_field:state.sort,sort_desc:state.sortDesc}).catch(report)}
 function sortBy(field){state.sortDesc=state.sort===field?!state.sortDesc:false;state.sort=field;render();persistView()}
 function updateSortHeaders(){Object.keys(labels).forEach((field,i)=>{const cell=$('.table-head').children[i+1];cell.setAttribute('role','columnheader');cell.setAttribute('aria-sort',state.sort===field?(state.sortDesc?'descending':'ascending'):'none');cell.innerHTML=`<button data-sort-field="${field}">${labels[field]}${state.sort===field?(state.sortDesc?' ↓':' ↑'):''}</button>`;cell.firstElementChild.onclick=()=>sortBy(field)});$('#sortLabel').textContent=labels[state.sort]+(state.sortDesc?' ↓':' ↑')}
 $('#sort').title='当前页排序';$('#sort').onclick=e=>{const r=e.currentTarget.getBoundingClientRect();menu($('#filterMenu'),Object.entries(labels).map(([k,label])=>({label,run:()=>sortBy(k)})),r.left,r.bottom+4)};
 $$('[data-mode]').forEach(b=>b.onclick=()=>{state.mode=b.dataset.mode;render();persistView()});
 const oldSyncSelection=syncSelection;syncSelection=function(){oldSyncSelection();const files=visible(),selected=currentFiles().filter(x=>state.selected.has(x.id));$('#download').disabled=!selected.length;$('#download').title=selected.some(x=>x.type==='folder')?'扫描并下载所选文件夹':'加入下载队列';$('#selectAll').checked=!!files.length&&files.every(x=>state.selected.has(x.id));$('#selectAll').indeterminate=files.some(x=>state.selected.has(x.id))&&!$('#selectAll').checked;};
 $('#selectAll').onchange=()=>{state.selected=$('#selectAll').checked?new Set(visible().map(x=>x.id)):new Set();syncSelection()};
 document.addEventListener('keydown',e=>{if($('#sheet').open||e.target.matches('input,textarea'))return;if(e.altKey&&e.key==='ArrowUp'&&state.view==='files'){e.preventDefault();up.click()}if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='a'&&['files','recent'].includes(state.view)){e.preventDefault();state.selected=new Set(visible().map(x=>x.id));syncSelection()}if(e.key==='F5'&&state.view==='files'){e.preventDefault();$('#refresh').click()}});
 const previousQueue=queue;
 queue=async function(files){if(!files.some(x=>x.type==='folder'))return previousQueue(files);const p=providerState();if(state.view!=='files'||!p?.connected||p.busy||p.share){toast('请在本人网盘目录选择文件夹');return;}
  let token='',finished=false,provider=state.provider;
  try{
   const result=await api('folder_preview',{provider,context:p.context,ids:files.map(x=>x.id)});token=result.token;sheetKind='folderDownload';
   sheet('下载文件夹','<p id="folderScanStatus">正在扫描文件夹…</p><p id="folderDestination"></p><div class="account-actions"><button class="quiet" id="cancelFolderDownload">取消</button><button class="primary" id="confirmFolderDownload" disabled>开始下载</button></div>');
   const cancel=()=>{if(!finished)api('folder_cancel',{provider,token}).catch(()=>{});finished=true;};
   $('#sheet').addEventListener('close',cancel,{once:true});$('#cancelFolderDownload').onclick=()=>$('#sheet').close();
   $('#confirmFolderDownload').onclick=async()=>{const b=$('#confirmFolderDownload');b.disabled=true;try{const r=await api('folder_confirm',{provider,token});finished=true;$('#sheet').close();toast(r.count?'已加入 '+r.count+' 个文件':'文件夹已准备；文件可能已在队列中');await refreshState();changeView('transfers')}catch(e){report(e);$('#folderScanStatus').textContent=e.message;}};
   while(!finished&&$('#sheet').open&&sheetKind==='folderDownload'){
    const s=await api('folder_status',{provider,token});if(finished||!$('#folderScanStatus'))break;
    $('#folderScanStatus').textContent=s.error||`${s.state==='scanning'?'正在扫描… ':''}${s.count} 个文件 · ${s.folders} 个文件夹 · ${size(s.bytes)}`;
    $('#folderDestination').textContent='保存到：'+s.destination;$('#confirmFolderDownload').disabled=s.state!=='ready';
    if(s.state!=='scanning')break;await new Promise(r=>setTimeout(r,600));
   }
  }catch(e){if(token)api('folder_cancel',{provider,token}).catch(()=>{});report(e);}
 };
 const oldContext=showContext;showContext=function(file,x,y){oldContext(file,x,y);if(file.type==='folder'&&state.view==='files'&&!providerState()?.share){const b=document.createElement('button');b.innerHTML=icon('download')+'下载文件夹';b.onclick=()=>{closeMenus();queue([file])};$('#contextMenu').prepend(b)}};
 const active=t=>['queued','resolving','preparing','uploading','downloading','committing','verifying'].includes(t.status);
 const category=t=>active(t)?'active':['completed','cancelled'].includes(t.status)?'completed':'attention';
 renderTasks=function(){
  const all=live?.tasks||[],tasks=all.filter(t=>transferFilter==='all'||category(t)===transferFilter),list=$('#transferList');
  const key=transferFilter+JSON.stringify(all.map(t=>[t.id,t.status,t.error,t.warning,t.target,t.group,t.restart,t.committed]));
  if(key!==transferRenderKey||!$('#transferTabs')){transferRenderKey=key;const scroll=list.scrollTop;
   list.innerHTML=`<div class="transfer-title">传输<small id="overallSpeed"></small></div><div class="transfer-tabs" id="transferTabs" role="tablist">${[['active','进行中'],['attention','待处理'],['completed','已结束'],['all','全部']].map(([key,label])=>`<button role="tab" aria-selected="${transferFilter===key}" data-transfer-filter="${key}">${label}<small>${all.filter(t=>key==='all'||category(t)===key).length}</small></button>`).join('')}<button class="quiet" id="clearFinished">清理已结束记录</button></div><div id="syncTransfers"></div>`+
    (tasks.length?[...tasks].reverse().map(t=>`<article class="task ${category(t)==='completed'?'task-compact':''}" data-task="${esc(t.id)}"><div class="task-top">${icon(t.direction==='upload'?'upload':'download')}<span class="task-name" title="${esc(t.name)}">${esc(t.name)}</span><span class="task-status">${statusNames[t.status]||t.status}</span><span class="task-actions">${taskActions(t)}${t.status==='completed'&&t.direction==='download'?'<button class="quiet" data-command="copy">复制路径</button>':''}</span></div>${category(t)!=='completed'?'<div class="task-progress"><i></i></div>':''}<div class="task-foot"><span>${names[t.provider]} · ${t.direction==='upload'?'上传':'下载'}${t.group?' · '+esc(t.group):''}</span><span class="task-percent"></span></div>${t.error?`<p class="task-error">${esc(t.error)}</p>`:''}${t.warning?`<p class="task-error">${esc(t.warning)}</p>`:''}</article>`).join(''):`<div class="empty transfer-empty">${icon('transfer')}<strong>${transferFilter==='active'?'当前没有进行中的传输':transferFilter==='attention'?'没有需要处理的任务':'这里还没有传输记录'}</strong><p>在网盘中选中文件或文件夹，即可开始下载。</p></div>`);
   list.scrollTop=scroll;$$('[data-transfer-filter]').forEach(b=>b.onclick=()=>{transferFilter=b.dataset.transferFilter;renderTasks()});
   $('#clearFinished').disabled=!all.some(t=>category(t)==='completed');$('#clearFinished').onclick=()=>{const ids=all.filter(t=>category(t)==='completed').slice(0,500).map(t=>t.id);sheetKind='history';sheet('清理已结束记录','<p>仅清理传输列表中的记录，已下载的本地文件与云端文件都会保留。</p><button class="primary" id="clearHistoryConfirm">清理 '+ids.length+' 条记录</button>');$('#clearHistoryConfirm').onclick=()=>api('history_clear',{ids}).then(async()=>{$('#sheet').close();await refreshState()}).catch(report)};
   $$('[data-task] [data-command]').forEach(b=>b.onclick=async()=>{const t=all.find(t=>t.id===b.closest('[data-task]').dataset.task);b.disabled=true;try{if(b.dataset.command==='copy'){await navigator.clipboard.writeText(t.target);toast('路径已复制')}else{await api('task',{provider:t.provider,id:t.id,command:b.dataset.command});await refreshState()}}catch(e){report(e)}finally{b.disabled=false}});
  }
  for(const t of tasks){const row=$(`[data-task="${t.id}"]`);if(!row)continue;const bar=row.querySelector('.task-progress i');if(bar)bar.style.width=(t.total?Math.min(100,t.done/t.total*100):0)+'%';row.querySelector('.task-percent').textContent=category(t)==='completed'?size(t.total):size(t.done)+' / '+size(t.total)+(t.speed>0?' · '+size(t.speed)+'/s':'');}
  const totalSpeed=all.filter(active).reduce((n,t)=>n+t.speed,0);$('#overallSpeed').textContent=totalSpeed?size(totalSpeed)+'/s':all.length+' 个任务';$('#itemCount').textContent=tasks.length+' / '+all.length+' 个任务';
  const sync=(live?.sync_rules||[]).filter(r=>r.active),box=$('#syncTransfers');box.hidden=!sync.length||!['all','active'].includes(transferFilter);box.innerHTML=sync.length?'<button class="quiet" id="openSyncTransfers">管理正在同步的任务</button>'+sync.map(r=>`<p>${esc(r.active.name)} · ${size(r.active.done)} / ${size(r.active.total)}</p>`).join(''):'';if($('#openSyncTransfers'))$('#openSyncTransfers').onclick=()=>changeView('backups');
 };
 const oldSheet=sheet;sheet=function(title,html){$('#sheet').classList.remove('settings-sheet');oldSheet(title,html)};
 const oldSettings=settings;settings=function(){oldSettings();const body=$('#sheetBody'),nodes=[...body.children],nav=document.createElement('nav'),panels=document.createElement('div');nav.className='settings-nav';panels.className='settings-panels';$('#sheet').classList.add('settings-sheet');
  const groups=[['general','运行'],['appearance','外观'],['storage','传输与存储'],['convenience','便捷操作'],['about','关于与更新']],sections={};for(const [key,label]of groups){const s=document.createElement('section');s.dataset.settingsPanel=key;s.setAttribute('aria-label',label);sections[key]=s;panels.append(s);const b=document.createElement('button');b.textContent=label;b.onclick=()=>selectSettings(key);b.dataset.settingsTab=key;nav.append(b)}
  let group='general';for(const node of nodes){if(node.matches('.setting-group-title')){group=({'运行与同步':'general','外观':'appearance','传输与存储':'storage','便捷操作':'convenience'})[node.textContent]||'about';continue;}if(node.querySelector('#releaseNotes,#exportDiagnostics,#updateCard')||node.id==='updateCard'||node.classList.contains('setting-about'))group='about';sections[group].append(node)}
  body.replaceChildren(nav,panels);function selectSettings(key){nav.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.settingsTab===key));Object.entries(sections).forEach(([k,s])=>s.hidden=k!==key)}selectSettings('general');
 };$('#settings').onclick=settings;$('#sheet').addEventListener('close',()=>$('#sheet').classList.remove('settings-sheet'));
 render();refreshExtras();
})();
