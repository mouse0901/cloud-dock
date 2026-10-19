// Context menus use the current selection, with a frozen directory guard.
(()=>{
 const ready=()=>state.view==='files'&&providerState()?.connected&&!providerState().busy&&!providerState().share&&providerState().path===state.path;
 function context(items,x,y){
  const provider=state.provider,path=state.path,stamp=providerState()?.context,selection=[...state.selected].sort().join('\u0000');
  menu($('#contextMenu'),items.map(a=>({...a,run:()=>{
   if(!ready()||provider!==state.provider||path!==state.path||stamp!==providerState()?.context||selection!==[...state.selected].sort().join('\u0000')){toast('目录或选择已变化，请重新打开菜单');return;}a.run();
  }})),x,y);
  const m=$('#contextMenu');m.setAttribute('role','menu');m.setAttribute('aria-label','文件操作');
  m.style.left=Math.max(8,Math.min(x,innerWidth-m.offsetWidth-8))+'px';m.style.top=Math.max(8,Math.min(y,innerHeight-m.offsetHeight-8))+'px';
  let group='';m.querySelectorAll('button').forEach((b,i)=>{b.setAttribute('role','menuitem');const next=items[i].group||group;if(i&&next!==group){const line=document.createElement('div');line.className='menu-divider';line.setAttribute('role','separator');b.before(line);}group=next;b.classList.toggle('danger-text',next==='danger');});m.style.top=Math.max(8,Math.min(y,innerHeight-m.offsetHeight-8))+'px';m.querySelector('button')?.focus({preventScroll:true});
 }
 function pin(){api('favorite_toggle',{provider:state.provider,context:providerState().context}).then(refreshState).catch(report);}
 function locationActions(){return [{label:'新建文件夹　Ctrl + Shift + N',icon:'plus',run:()=>cloudDockFileActions.name('mkdir')},
  {label:live?.favorites?.some(f=>f.provider===state.provider&&f.path===state.path)?'取消固定当前位置':'固定当前位置',icon:'folder',run:pin},
  {label:'上传文件或文件夹…',icon:'upload',run:()=>$('#upload').click()},
  {label:'刷新',icon:'refresh',run:()=>$('#refresh').click()}];}
 showContext=function(file,x,y){
  if(!ready())return;const picked=currentFiles().filter(f=>state.selected.has(f.id));const one=picked.length===1;
  const actions=[];
  if(one){actions.push({label:file.type==='folder'?'打开文件夹':'打开 / 预览',icon:'folder',run:()=>openFile(file)});
   if(file.type==='folder')actions.push({label:'在新标签页打开',icon:'plus',run:()=>cloudDockTabs.newTab(state.provider,file.path)});
  }
  actions.push({group:'transfer',label:one?'下载…':'下载所选 '+picked.length+' 项…',icon:'download',run:()=>queue(picked)});
  if(one)actions.push(...cloudDockFileActions.actions(file).filter(a=>a.label!=='预览').map(a=>({...a,group:'edit'})));
  else actions.push({label:'移动所选项目…',icon:'folder',run:()=>$('#batchMove').click()},{label:'批量重命名…',icon:'file',run:()=>$('#batchRename').click()});
  actions.push({group:'share',label:'复制到另一个网盘…',icon:'cloud',run:()=>cloudDockCrossCopy(picked)},
   {label:'创建分享链接…',icon:'link',run:()=>cloudDockCreateShare(picked)},
   {label:'移入回收站…',icon:'trash',run:deleteSelected,group:'danger'});
  context(actions,x,y);
 };
 $('#fileList').addEventListener('contextmenu',e=>{
  if(e.target.closest('.file-row')||!ready())return;e.preventDefault();state.selected.clear();syncSelection();context(locationActions(),e.clientX,e.clientY);
 });
 document.addEventListener('keydown',e=>{
  const m=$('#contextMenu');if(!m.hidden&&['ArrowDown','ArrowUp','Home','End'].includes(e.key)){
   e.preventDefault();e.stopImmediatePropagation();const rows=[...m.querySelectorAll('button')],i=rows.indexOf(document.activeElement);
   rows[e.key==='Home'?0:e.key==='End'?rows.length-1:(i+(e.key==='ArrowDown'?1:rows.length-1))%rows.length]?.focus();
  }else if(!$('#sheet').open&&(e.key==='ContextMenu'||e.shiftKey&&e.key==='F10')&&ready()){
   e.preventDefault();const file=currentFiles().find(f=>state.selected.has(f.id)),box=(file?document.querySelector('.file-row[data-id="'+CSS.escape(file.id)+'"]'):$('#fileList'))?.getBoundingClientRect();
   if(box)file?showContext(file,box.x+40,box.y+30):context(locationActions(),box.x+40,box.y+30);
  }
 },true);
 const oldSettings=settings;
 settings=function(){oldSettings();const button=$('#releaseNotes');if(button){button.textContent='完整更新日志';button.onclick=changelog;}};$('#settings').onclick=settings;
 async function changelog(){
  sheetKind='changelog';sheet('完整更新日志','<p id="changelogLoading">正在读取本地更新记录…</p>');
  try{const data=await api('changelog');if(sheetKind!=='changelog'||!$('#sheet').open)return;
   $('#sheetBody').innerHTML='<div class="journal-toolbar"><input id="releaseFilter" aria-label="筛选版本或更新内容" placeholder="搜索版本或更新内容"><button id="releaseBack" class="quiet">返回设置</button></div><p class="sync-subtle">日志随软件保存，可离线查看。标记“本地测试”的版本尚未发布。</p><div id="releaseList"></div>';
   function draw(){const term=$('#releaseFilter').value.trim().toLowerCase();const exact=/^v?\d+\.\d+\.\d+$/.test(term);const rows=data.releases.filter(r=>exact?r.version===term.replace(/^v/,''):JSON.stringify(r).toLowerCase().includes(term));$('#releaseList').innerHTML=rows.map(r=>`<article class="release-entry"><h3>${esc(r.version)} <small>${esc(r.channel)}</small></h3><p>${esc(r.title)}</p><ul>${r.changes.map(c=>`<li>${esc(c)}</li>`).join('')}</ul>${r.notes?`<details><summary>查看该版本的完整原始记录</summary><pre>${esc(r.notes)}</pre></details>`:''}</article>`).join('')||'<p>没有匹配记录。</p>';}
   $('#releaseFilter').oninput=draw;$('#releaseBack').onclick=settings;draw();
  }catch(e){if($('#changelogLoading'))$('#changelogLoading').textContent=e.message;}
 }
 window.cloudDockChangelog=changelog;
})();
