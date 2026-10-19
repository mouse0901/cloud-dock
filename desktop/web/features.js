// Account-backed product features; no sample files or simulated progress.
let featureKey='',trashPage=0,trashData=null,trashBusy=false,soundSeen=0,soundReady=false;
const originalRender=render;
render=function(animate=false){
 if(!['backups','trash'].includes(state.view)){document.querySelector('#featureView')?.remove();originalRender(animate);return;}
 $$('.nav').forEach(e=>e.classList.toggle('active',e.dataset.view===state.view));
 $('#viewTitle').textContent=state.view==='backups'?'同步':'回收站';$('#locationLabel').textContent=state.view==='backups'?'文件夹与单文件同步':'百度与夸克 · 分别管理';
 $('.commandbar').hidden=true;$('.table-head').hidden=true;$('#fileList').hidden=true;$('#transferList').hidden=true;$('#inspector').hidden=true;$('#search').parentElement.hidden=true;$('#info').hidden=true;$('.pager').hidden=true;
 $('#breadcrumbs').textContent=state.view==='backups'?'运行云屿期间自动检查；退出软件后停止':'还原到网盘原位置，保留期由网盘决定';$('#itemCount').textContent='';
 let host=$('#featureView');if(!host){host=document.createElement('div');host.id='featureView';$('.file-region').insertBefore(host,$('footer'));}
 if(state.view==='backups')renderBackups(host);else renderTrash(host);
};
function renderBackups(host){host.textContent='正在准备同步…';}
function renderTrash(host){
 const rows=trashData?.rows||[];
 host.innerHTML=`<div class="feature-heading"><div><h2>回收站</h2><p>这里只提供还原，不会自动清空。</p></div><div class="account-actions">${Object.keys(names).map(p=>`<button class="${p===state.provider?'primary':'quiet'}" data-trash-provider="${p}">${names[p]}</button>`).join('')}<button id="refreshTrash" class="quiet">刷新</button></div></div>`+(trashBusy?'<div class="feature-empty">正在读取回收站…</div>':trashData?.error?`<div class="feature-empty">${esc(trashData.error)}</div>`:rows.length?rows.map(r=>`<article class="trash-row"><span class="file-icon">${icon('file')}</span><div><strong>${esc(r.name)}</strong><small>${esc(r.path||'还原位置由网盘确定')}${r.deleted?' · '+new Date(r.deleted*(r.deleted>1e11?1:1000)).toLocaleString('zh-CN'):''}</small></div><button class="quiet" data-restore="${esc(r.id)}">还原</button></article>`).join(''):'<div class="feature-empty">回收站是空的</div>')+`<div class="trash-pager"><button id="trashPrev" class="quiet" ${trashPage===0||trashBusy?'disabled':''}>上一页</button><span>第 ${trashPage+1} 页</span><button id="trashNext" class="quiet" ${rows.length<50||trashBusy?'disabled':''}>下一页</button></div>`;
 $$('[data-trash-provider]').forEach(b=>b.onclick=()=>{state.provider=b.dataset.trashProvider;trashPage=0;loadTrash();});$('#refreshTrash').onclick=loadTrash;$('#trashPrev').onclick=()=>{trashPage--;loadTrash();};$('#trashNext').onclick=()=>{trashPage++;loadTrash();};
 $$('[data-restore]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await api('trash_restore',{context:trashData.context,id:b.dataset.restore});toast('还原请求已完成');await loadTrash();}catch(e){report(e);b.disabled=false;}});
}
async function loadTrash(){if(trashBusy)return;trashBusy=true;render();try{trashData=await api('trash_list',{page:trashPage});}catch(e){trashData={error:e.message};}finally{trashBusy=false;if(state.view==='trash')render();}}
const changeViewFeatures=changeView;
changeView=function(view){changeViewFeatures(view);if(view==='trash')loadTrash();};
async function deleteSelected(){
 if($('#sheet').open)return;
 const p=providerState(),provider=state.provider,context=p?.context,ids=[...state.selected];let closed=false;
 try{
  if(state.view!=='files'||!p?.connected||p.busy||p.share)throw Error('请在本人网盘目录中选择项目');
  sheetKind='deleteConfirm';sheet('移入回收站','<p id="deleteStatus" role="status">正在核对所选项目与账号…</p>');
  $('#sheet').addEventListener('close',()=>{closed=true},{once:true});
  const result=await api('delete_preview',{provider,context,ids});if(closed)return;
  $('#sheetBody').innerHTML=`<p>将 ${names[provider]} 中的 ${result.names.length} 项移入回收站：</p><ul class="delete-list">${result.names.map(n=>`<li>${esc(n)}</li>`).join('')}</ul><p>可以在网盘回收站保留期内还原。</p><p id="deleteStatus" role="status"></p><button id="confirmDelete" class="danger-button">移入回收站</button>`;
  $('#confirmDelete').onclick=async()=>{const b=$('#confirmDelete');b.disabled=true;$('#deleteStatus').textContent='正在移入回收站并核对结果…';
   try{await api('delete_confirm',{provider,confirmation:result.confirmation});if(!closed)$('#sheet').close();state.selected.clear();await refreshState();toast('已在回收站确认');}
   catch(e){if(!closed){$('#deleteStatus').textContent=e.message;b.textContent='关闭并刷新';b.disabled=false;b.onclick=async()=>{$('#sheet').close();await api('refresh',{provider}).catch(report);await refreshState();};}else report(e);}
  };
 }catch(e){if(!closed&&$('#deleteStatus'))$('#deleteStatus').textContent=e.message;else report(e);}
}
$('#deleteFiles').onclick=deleteSelected;
const showContextBase=showContext;
showContext=function(file,x,y){if(state.view!=='files'||providerState()?.share){showContextBase(file,x,y);return;}menu($('#contextMenu'),[{label:file.type==='folder'?'打开文件夹':'查看文件信息',icon:'info',run:()=>openFile(file)},...(file.type==='folder'?[]:[{label:'下载',icon:'download',run:()=>queue([file])}]),{label:'移入回收站',icon:'trash',run:deleteSelected}],x,y);};
const settingsBase=settings;
settings=function(){settingsBase();const box=document.createElement('div');box.innerHTML=`<div class="setting-line"><span>传输完成提示音<small>一批任务结束后响一次，自动同步保持安静</small></span><input type="checkbox" id="completionSound" ${live?.sound!==false?'checked':''} aria-label="传输完成提示音"></div><div class="setting-line"><span>缩略图缓存<small>最多 256 MB，仅保存在本机</small></span><button id="clearThumbs" class="quiet">清理</button></div>`;$('#sheetBody').prepend(box);$('#completionSound').onchange=e=>api('sound',{value:e.target.checked}).catch(report);$('#clearThumbs').onclick=()=>api('clear_thumbnails').then(()=>toast('缓存已清理')).catch(report);};$('#settings').onclick=settings;
document.addEventListener('load',e=>{if(e.target.matches?.('.cloud-thumb'))e.target.parentElement.classList.add('has-thumb');},true);
document.addEventListener('error',e=>{if(e.target.matches?.('.cloud-thumb'))e.target.remove();},true);
setInterval(()=>{if(!live)return;const key=JSON.stringify(live.backups);if(!window.cloudDockBackground&&!document.hidden&&state.view==='backups'&&key!==featureKey){featureKey=key;render();}if(!soundReady){soundSeen=live.sound_sequence;soundReady=true;}else if(live.sound_sequence!==soundSeen){soundSeen=live.sound_sequence;/* Native feedback owns delivery. */}},800);
