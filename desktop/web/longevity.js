// Account profiles, portable preferences, and local health/recovery controls.
(()=>{
 const native=window.chrome?.webview;let epoch=0;
 const dialog=$('#sheet');dialog.addEventListener('close',()=>epoch++);
 function panel(title,html){sheetKind='longevity';sheet(title,html);return ++epoch;}
 function alive(id){return id===epoch&&dialog.open&&sheetKind==='longevity';}
 function fail(e){report(e);}
 const labels={sidebar_width:'侧栏宽度',name_width:'名称列宽',size_width:'大小列宽',date_width:'修改日期列宽',show_size:'显示大小列',show_date:'显示日期列',theme:'外观',view:'列表或网格',density:'列表密度',grid_size:'网格大小',sort_field:'排序字段',sort_desc:'排序方向',full_names:'完整文件名',reduce_motion:'减少动态效果',close_to_tray:'关闭到托盘',clipboard_links:'识别新复制链接',clipboard_wake:'链接唤起窗口',completion_sound:'完成声音',feedback:'声音与通知'};
 accounts=async function(providerFocus=null){
  const id=panel('我的账号','<p>每家网盘最多保存 5 个账号，一次连接一个。切换时保留原账号任务；同步会先暂停。</p><div id="profileRows">正在读取…</div>');
  try{
   const value=await api('profiles_list');if(!alive(id))return;
   if(value.error){$('#profileRows').textContent=value.error;return;}
   $('#profileRows').innerHTML=Object.entries(value.providers).map(([provider,p])=>`<section class="profiles-group" data-profile-provider="${provider}"><div class="profiles-heading"><h3>${names[provider]}</h3>${live?.providers[provider]?.connected?`<button class="quiet" data-account-capacity="${provider}">存储空间</button>`:''}<button class="quiet" data-profile-add="${provider}" ${p.rows.length>=5?'disabled':''}>添加账号</button></div>${p.rows.map(r=>`<article class="profile-row"><div><strong>${esc(r.name)}</strong><small>${r.id===p.active?(live?.providers[provider]?.connected?'当前已连接':'当前 · 需要连接'):'已保存'}${r.verified?'':' · 登录后确认身份'}</small></div><div class="profile-actions"><button class="quiet" data-profile-rename="${r.id}" data-provider-name="${provider}">改名</button><button class="quiet" data-profile-remove="${r.id}" data-provider-name="${provider}">移除</button><button class="${r.id===p.active?'quiet':'primary'}" data-profile-switch="${r.id}" data-provider-name="${provider}" data-active="${r.id===p.active}">${r.id===p.active?'管理登录':'切换'}</button></div></article>`).join('')}</section>`).join('');
   if(typeof providerFocus==='string'){$('[data-profile-provider="'+providerFocus+'"]')?.scrollIntoView({block:'nearest'});$('[data-profile-add="'+providerFocus+'"]')?.focus({preventScroll:true});}
   $$('[data-account-capacity]').forEach(b=>b.onclick=()=>cloudDockCapacity(b.dataset.accountCapacity));
   $$('[data-profile-add]').forEach(b=>b.onclick=()=>editProfile(b.dataset.profileAdd));
   $$('[data-profile-rename]').forEach(b=>b.onclick=()=>{const p=b.dataset.providerName;editProfile(p,value.providers[p].rows.find(r=>r.id===b.dataset.profileRename));});
   $$('[data-profile-switch]').forEach(b=>b.onclick=()=>b.dataset.active==='true'?loginSheet(b.dataset.providerName,!live?.providers[b.dataset.providerName]?.connected):confirmProfile('switch',b.dataset.providerName,b.dataset.profileSwitch));
   $$('[data-profile-remove]').forEach(b=>b.onclick=()=>confirmProfile('remove',b.dataset.providerName,b.dataset.profileRemove));
  }catch(e){if(alive(id))$('#profileRows').textContent=e.message;}
 };
 $('#account').onclick=accounts;$('#accountAdd').onclick=accounts;
 function editProfile(provider,row){
  const id=panel(row?'修改账号昵称':'添加'+names[provider]+'账号',`<label class="long-form">账号昵称<input id="profileName" maxlength="32" value="${esc(row?.name||'')}" placeholder="例如：工作、个人"></label><p>${row?'昵称只保存在本机。':'将先保存并断开当前连接，暂停该网盘同步。添加后用新账号扫码；原账号仍保留在列表中。'}</p><div class="long-actions"><button id="profileBack" class="quiet">返回</button><button id="profileSave" class="primary">${row?'保存昵称':'添加并扫码'}</button></div>`);
  $('#profileBack').onclick=accounts;$('#profileSave').onclick=async()=>{const b=$('#profileSave');b.disabled=true;try{await api(row?'profiles_rename':'profiles_add',{provider,id:row?.id,name:$('#profileName').value});if(!alive(id))return;await refreshState();if(row)await accounts();else await loginSheet(provider,true);}catch(e){if(alive(id)){fail(e);b.disabled=false;}}};$('#profileName').focus();
 }
 function confirmProfile(kind,provider,key){
  const id=panel(kind==='remove'?'移除保存的账号':'切换网盘账号',`<p>${kind==='remove'?'清除这个账号保存的登录信息和昵称。网盘文件、下载文件、同步记录和历史任务保留。':'当前传输需要先暂停，正在提交的操作需先完成。同步规则会暂停，切回原账号后可手动继续。'}</p><div class="long-actions"><button class="quiet" id="profileCancel">取消</button><button class="${kind==='remove'?'danger-button':'primary'}" id="profileConfirm">${kind==='remove'?'移除账号':'确认切换'}</button></div>`);
  $('#profileCancel').onclick=accounts;$('#profileConfirm').onclick=async()=>{const b=$('#profileConfirm');b.disabled=true;try{await api('profiles_'+kind,{provider,id:key});if(!alive(id))return;await refreshState();await accounts();toast(kind==='remove'?'账号已移除，文件和任务保留':'已切换账号；原账号任务已保留');}catch(e){if(alive(id)){fail(e);b.disabled=false;}}};
 }
 const priorSettings=settings;
 settings=function(){
  priorSettings();const general=$('[data-settings-panel="general"]'),about=$('[data-settings-panel="about"]');
  if(general){const row=document.createElement('div');row.className='setting-line';row.innerHTML='<span>迁移个人设置<small>导出外观、操作习惯与声音；不含账号、文件路径、自启动和自动任务。</small></span><div class="long-actions"><button id="settingsExport" class="quiet">导出…</button><button id="settingsImport" class="quiet">导入…</button></div>';general.append(row);$('#settingsExport').disabled=$('#settingsImport').disabled=!native;$('#settingsExport').onclick=()=>native.postMessage({type:'exportSettings'});$('#settingsImport').onclick=()=>native.postMessage({type:'importSettings'});}
  if(about){const row=document.createElement('div');row.className='setting-line';row.innerHTML='<span>运行检查与恢复<small>检查本地存储、任务状态和运行开销；查看升级恢复点。</small></span><button id="healthOpen" class="quiet">查看…</button>';about.append(row);$('#healthOpen').onclick=health;}
 };$('#settings').onclick=settings;
 function importPreview(value){
  const id=panel('确认导入设置',`<p>只修改下面列出的选项。账号、文件位置、自启动、更新源与同步规则保持原样。</p><ul>${Object.keys(value.fields).map(k=>`<li>${labels[k]||esc(k)}</li>`).join('')}</ul><div class="long-actions"><button class="quiet" id="settingsCancel">取消</button><button id="settingsApply" class="primary">确认导入</button></div>`);
  $('#settingsCancel').onclick=settings;$('#settingsApply').onclick=async()=>{const b=$('#settingsApply');b.disabled=true;try{const r=await api('settings_apply',{token:value.token});if(!alive(id))return;document.body.classList.toggle('dark',r.theme==='dark'||r.theme==='system'&&matchMedia('(prefers-color-scheme:dark)').matches);document.body.classList.toggle('reduced',r.reduced);const v=r.file_view;if(v){window.cloudDockApplyLayout?.(v);document.body.dataset.density=v.density;document.body.dataset.gridSize=v.grid_size;document.body.classList.toggle('full-names',v.full_names);state.mode=v.view;state.sort=v.sort_field;state.sortDesc=v.sort_desc;render();}window.dispatchEvent(new CustomEvent('clouddock-theme',{detail:{initial:true}}));await refreshState();settings();toast('设置已导入');}catch(e){if(alive(id)){fail(e);b.disabled=false;}}};
 }
 const advice={data_unwritable:'数据位置不可写：检查磁盘连接与目录权限，再重新打开软件。',disk_low:'数据所在磁盘剩余空间较少：先释放空间，再继续传输。',database_check_failed:'任务数据库检查未通过：先退出软件、保留数据目录副本，再导出诊断处理。',workspace_failed:'工作空间初始化失败：先重试，持续失败时使用完整安装包修复。',account_catalog_failed:'账号列表无法解密：确认仍使用保存它的 Windows 用户，原文件未覆盖。'};
 async function health(){
  const id=panel('运行检查与恢复','<p id="healthProgress">正在检查本地状态…</p><div id="healthContent"></div>');
  try{const r=await api('health_check');if(!alive(id))return;$('#healthProgress').textContent='本地检查完成；账号连接状态不等同于实际上传或下载验收。';
   $('#healthContent').innerHTML=`<div class="health-grid"><article><small>任务数据库</small><strong>${r.database_ok?'检查通过':'需要处理'}</strong></article><article><small>数据磁盘剩余</small><strong>${bytes(r.storage.free_bytes)}</strong></article><article><small>后台工作集</small><strong>${r.performance?.backend_working_set_bytes?bytes(r.performance.backend_working_set_bytes):'暂不可用'}</strong></article><article><small>后台就绪</small><strong>${r.performance?.stages_ms?.workspace_ready!=null?(r.performance.stages_ms.workspace_ready/1000).toFixed(2)+' 秒':'准备中'}</strong></article></div><div>${r.advice.length?r.advice.map(k=>`<p>${advice[k]||'请导出诊断后检查。'}</p>`).join(''):'<p>本地存储与数据库未发现异常。</p>'}</div><p class="sync-subtle">后台工作集仅指 Python 服务，不包含界面与 WebView2。启动耗时从后台服务创建开始计时。</p><div id="recoveryStatus" class="recovery-card">正在读取升级恢复点…</div><div class="long-actions"><button class="quiet" id="healthExport">导出诊断…</button><button class="primary" id="healthAgain">重新检查</button></div>`;
   $('#healthExport').disabled=!native;$('#healthExport').onclick=()=>native.postMessage({type:'exportDiagnostics'});$('#healthAgain').onclick=health;
   if(native)native.postMessage({type:'longevityStatus'});else $('#recoveryStatus').textContent='请在 Windows 软件中查看升级恢复点。';
  }catch(e){if(alive(id))$('#healthProgress').textContent=e.message;}
 }
 native?.addEventListener('message',e=>{
  const m=e.data;if(m?.type==='settingsExported')toast('设置已导出');if(m?.type==='settingsFileError')toast(m.message);
  if(m?.type==='recoveryError'){toast(m.message);if($('#recoverConfirm'))$('#recoverConfirm').disabled=false;}
  if(m?.type==='settingsPreview')importPreview(m.preview);
  if(m?.type==='longevityStatus'&&$('#recoveryStatus')&&dialog.open){const r=m.recovery;const status={prepared:'已创建恢复点',installing:'安装中',installed:'等待启动确认',healthy:'新版已正常启动',restoring:'正在恢复',restored:'已恢复旧程序',restore_failed:'恢复未完成，可重新尝试',waiting_health:'启动确认待核对',installation_uncertain:'安装结果待核对',invalid:'恢复点不可读取',none:'暂无恢复点'};$('#recoveryStatus').innerHTML=`<strong>升级恢复</strong><p>${status[r.status]||'待核对'}${r.available?' · '+esc(r.from)+' → '+esc(r.to):''}</p><small>通过软件内更新时备份原程序。恢复只替换程序文件，不回滚个人数据；若新旧数据格式不兼容，应使用原安装包和事先保存的数据副本。</small>${r.available&&!['installing','restoring','installation_uncertain','restored'].includes(r.status)?'<button class="quiet" id="recoverVersion">恢复上次程序…</button>':''}`;if($('#recoverVersion'))$('#recoverVersion').onclick=()=>{panel('确认恢复 '+r.from,'<p>云屿会正常退出，再核对并恢复更新前的程序文件。个人数据和网盘文件保留，所有任务需先暂停。</p><div class="long-actions"><button id="recoverCancel" class="quiet">取消</button><button id="recoverConfirm" class="primary">退出并恢复</button></div>');$('#recoverCancel').onclick=health;$('#recoverConfirm').onclick=()=>{native.postMessage({type:'recoverUpdate'});$('#recoverConfirm').disabled=true;};};}
 });
 window.cloudDockImportPreview=importPreview;window.cloudDockHealth=health;
 const priorRefresh=refreshState;
 refreshState=async function(){await priorRefresh();for(const p of Object.keys(names)){const cache=data[p];if(!cache)continue;const keep=live?.providers[p]?.path;const keys=Object.keys(cache);for(const key of keys)if(Object.keys(cache).length>24&&key!==keep&&!(p===state.provider&&key===state.path))delete cache[key];}if(state.recent.length>100)state.recent.length=100;};
 const priorTasks=renderTasks;renderTasks=function(){priorTasks();for(const t of live?.tasks||[]){const row=$(`[data-task="${t.id}"]`);if(!row||!t.account_name)continue;let label=row.querySelector('.task-account');if(!label){label=document.createElement('small');label.className='task-account';(row.querySelector('.task-foot>span')||row).append(label);}label.textContent=t.account_name+(t.account_current===false?' · 切回原账号后继续':'');}};
})();
