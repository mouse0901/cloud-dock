// Workspace ergonomics. Layout preferences contain no account or file data.
(()=>{
 const defaults={sidebar_width:208,name_width:320,size_width:95,date_width:150,show_size:true,show_date:true};
 let layout={...defaults},loaded=false,saveChain=Promise.resolve(),editing=null;
 const bounds={sidebar_width:[180,300],name_width:[180,700],size_width:[70,180],date_width:[110,240]};
 function applyLayout(){
  for(const k of Object.keys(bounds))document.documentElement.style.setProperty('--'+k.replaceAll('_','-'),layout[k]+'px');
  document.documentElement.style.setProperty('--file-columns',['32px',`minmax(180px,${layout.name_width}fr)`,...(layout.show_size?[`minmax(70px,${layout.size_width}fr)`]:[]),...(layout.show_date?[`minmax(110px,${layout.date_width}fr)`]:[]),'32px'].join(' '));
  document.body.classList.toggle('hide-size',!layout.show_size);document.body.classList.toggle('hide-date',!layout.show_date);
 }
 function persist(values){saveChain=saveChain.catch(()=>{}).then(()=>api('file_view',values)).catch(report);return saveChain;}
 function resize(handle,key,invert=false){
  const [low,high]=bounds[key];handle.tabIndex=0;handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');
  handle.setAttribute('aria-valuemin',low);handle.setAttribute('aria-valuemax',high);
  const update=n=>{layout[key]=Math.max(low,Math.min(high,Math.round(n)));applyLayout();handle.setAttribute('aria-valuenow',layout[key]);};update(layout[key]);
  handle.onpointerdown=e=>{if(e.button!==0)return;e.preventDefault();e.stopPropagation();const start=e.clientX,initial=layout[key];handle.setPointerCapture(e.pointerId);document.body.classList.add('resizing-layout');
   handle.onpointermove=m=>update(initial+(m.clientX-start)*(invert?-1:1));
   const finish=()=>{handle.onpointermove=null;document.body.classList.remove('resizing-layout');persist({[key]:layout[key]});};
   handle.onpointerup=finish;handle.onpointercancel=finish;
  };
  handle.onclick=e=>e.stopPropagation();handle.ondblclick=e=>{e.preventDefault();update(defaults[key]);persist({[key]:layout[key]});};
  handle.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();e.stopPropagation();update(e.key==='Home'?low:e.key==='End'?high:layout[key]+(e.key==='ArrowLeft'?-10:10)*(invert?-1:1));persist({[key]:layout[key]});};
 }
 const rail=document.createElement('div');rail.id='sidebarResize';rail.title='拖动调整侧栏宽度，双击恢复';rail.setAttribute('aria-label','调整侧栏宽度');$('.sidebar').append(rail);resize(rail,'sidebar_width');
 function columns(){
  const head=$('.table-head');['name','size','date'].forEach((name,i)=>{const cell=head.children[i+1];cell.dataset.column=name;
   if(cell.querySelector('.column-resize'))return;const h=document.createElement('span');h.className='column-resize';h.title='拖动调整列宽，双击恢复';h.setAttribute('aria-label','调整'+({name:'名称',size:'大小',date:'修改日期'})[name]+'列宽');cell.append(h);resize(h,name+'_width');
  });
 }
 const originalDisplay=$('#viewOptions').onclick;
 $('#viewOptions').onclick=()=>{originalDisplay();const box=document.createElement('div');box.className='column-options';box.innerHTML='<h3 class="setting-group-title">列表列</h3><p>名称始终显示。拖动表头分隔线调整列宽。</p>'+[['show_size','大小'],['show_date','修改日期']].map(([k,label])=>`<label><input type="checkbox" data-column-setting="${k}" ${layout[k]?'checked':''}>${label}</label>`).join('')+'<button class="quiet" id="resetLayout">恢复默认布局</button>';$('#sheetBody').append(box);
  $$('[data-column-setting]').forEach(b=>b.onchange=()=>{layout[b.dataset.columnSetting]=b.checked;applyLayout();persist({[b.dataset.columnSetting]:b.checked});});
  $('#resetLayout').onclick=()=>{layout={...defaults};applyLayout();persist(layout);$$('[data-column-setting]').forEach(b=>b.checked=layout[b.dataset.columnSetting]);};
 };
 const addTab=document.createElement('button');addTab.id='compactNewTab';addTab.className='icon-button';addTab.title='新建标签页（Ctrl + T）';addTab.setAttribute('aria-label','新建标签页');addTab.innerHTML=icon('plus');$('.history').append(addTab);addTab.onclick=()=>cloudDockTabs.newTab(state.provider,'/');
 function compact(){
  const files=state.view==='files';document.body.classList.toggle('file-workspace',files);const single=cloudDockTabs.entries().length<2;
  $('#browserTabs').classList.toggle('single-tab',single);run.hidden=!files;run.disabled=scope.value==='all'?!Object.values(live?.providers||{}).some(p=>p.connected):!providerState()?.connected;addTab.hidden=!files||!single;columns();
 }
 // Scope is explicit; remote searches only start on Enter or the search button.
 const scope=document.createElement('select');scope.id='searchScope';scope.setAttribute('aria-label','搜索范围');scope.innerHTML='<option value="page">当前页</option><option value="drive">当前网盘</option><option value="all">全部网盘</option>';$('.search').prepend(scope);
 const run=$('#searchAll');run.textContent='搜索';run.title='搜索网盘文件（Ctrl + Shift + F）';
 function submitSearch(){if(scope.value==='page'){render();return;}cloudDockSearchAll({scope:scope.value==='drive'?state.provider:'all',term:$('#search').value,submit:!!$('#search').value.trim()});}
 run.onclick=()=>{if(scope.value==='page'){scope.value='drive';scopeChanged();}submitSearch();};
 function scopeChanged(){const local=scope.value==='page';$('#search').placeholder=local?'筛选当前页文件':'输入文件名，按 Enter 搜索';$('#search').setAttribute('aria-label',local?'筛选当前页文件':'搜索网盘文件');render();}
 scope.onchange=scopeChanged;$('#search').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();submitSearch();}if(e.key==='Escape'){$('#search').value='';render();}});
 const previousVisible=visible;visible=function(){if(scope.value==='page')return previousVisible();const value=$('#search').value;try{$('#search').value='';return previousVisible();}finally{$('#search').value=value;}};
 const results=document.createElement('button');results.id='returnSearch';results.className='quiet';results.textContent='返回搜索结果';results.hidden=true;$('#normalActions').append(results);results.onclick=()=>cloudDockSearchAll({resume:true});
 window.cloudDockSearchRetained=has=>results.hidden=!has;
 let search=null,searchEpoch=0,searchTimer=null;
 const searchValid=s=>s&&Date.now()-s.at<900000&&s.providers.every(p=>live?.providers[p]?.connected&&live.providers[p].session===s.sessions[p]);
 function discardSearch(){clearTimeout(searchTimer);searchEpoch++;if(search)for(const token of search.tokens)api('tools_cancel',{token}).catch(()=>{});search=null;results.hidden=true;}
 function drawSearch(s){
  if(!$('#aggregateResults')||sheetKind!=='search'||search!==s)return;
  $('#aggregateSearchStatus').textContent=s.message;$('#stopSearch').hidden=!s.running;
  $('#aggregateResults').innerHTML=s.rows.length?s.rows.map((x,i)=>`<button class="tool-result" data-aggregate-result="${i}">${icon(x.directory?'folder':'file')}<span>${esc(x.name)}<small>${names[x.provider]} · ${esc(x.parent)}</small></span><small>打开所在位置</small></button>`).join(''):'<p>暂无匹配结果。</p>';
  $$('[data-aggregate-result]').forEach(b=>b.onclick=async()=>{if(!searchValid(s)){discardSearch();toast('搜索结果已过期，请重新搜索');return;}const x=s.rows[+b.dataset.aggregateResult];$('#sheet').close();$('#search').value='';try{await cloudDockLocate(x.provider,x,x.token);}catch(e){report(e);}});
 }
 async function stopSearch(s){if(!s?.running)return;s.running=false;clearTimeout(searchTimer);const id=++searchEpoch;
  const data=await Promise.all(s.tokens.map(async token=>{try{return await api('tools_stop',{token});}catch(e){return {message:e.message};}}));
  if(search!==s||id!==searchEpoch)return;s.message='搜索已停止，已找到 '+s.rows.length+' 项。';drawSearch(s);
 }
 $('#sheet').addEventListener('close',()=>{if(!$('#sheet').open&&sheetKind==='search')stopSearch(search);});
 window.cloudDockSearchAll=function(options={}){
  if(search&&!searchValid(search))discardSearch();
  sheetKind='search';sheet('搜索网盘文件','<form id="aggregateSearch" class="aggregate-toolbar"><input id="aggregateTerm" placeholder="文件名关键词" maxlength="100" aria-label="搜索关键词"><select id="aggregateScope" aria-label="搜索范围"><option value="all">所有已连接网盘</option>'+Object.keys(names).map(p=>`<option value="${p}">${names[p]}</option>`).join('')+'</select><button class="primary">搜索</button></form><div class="search-status-line"><p id="aggregateSearchStatus" role="status">按文件名搜索，可随时停止；关闭窗口后仍可返回已找到的结果。</p><button id="stopSearch" class="quiet" hidden>停止搜索</button></div><div id="aggregateResults" class="tool-results"></div><p id="aggregateError" class="sync-error" role="alert"></p>');
  $('#aggregateTerm').value=options.term??(options.resume?search?.term:'')??'';$('#aggregateScope').value=options.scope||(options.resume?search?.scope:'all')||'all';$('#aggregateTerm').focus();
  if(options.resume&&search)drawSearch(search);$('#stopSearch').onclick=()=>stopSearch(search);
  $('#aggregateSearch').onsubmit=async event=>{event.preventDefault();const term=$('#aggregateTerm').value.trim(),scope=$('#aggregateScope').value;
   if(!term){$('#aggregateError').textContent='请输入文件名关键词';return;}const providers=Object.keys(names).filter(p=>(scope==='all'||scope===p)&&live?.providers[p]?.connected);
   if(!providers.length){$('#aggregateError').textContent='请先连接要搜索的网盘';return;}discardSearch();const id=searchEpoch;
   const s={term,scope,providers,sessions:Object.fromEntries(providers.map(p=>[p,live.providers[p].session])),tokens:[],rows:[],running:true,message:'正在开始搜索…',at:Date.now()};search=s;results.hidden=false;$('#aggregateError').textContent='';drawSearch(s);
   const jobs=await Promise.all(providers.map(async provider=>{try{return {provider,...await api('tools_search',{provider,term})};}catch(e){return {provider,error:e.message};}}));
   if(search!==s||id!==searchEpoch){for(const j of jobs)if(j.token)api('tools_cancel',{token:j.token}).catch(()=>{});return;}s.tokens=jobs.filter(j=>j.token).map(j=>j.token);
   async function poll(){
    const statuses=await Promise.all(jobs.map(async j=>{if(j.error)return j;try{return {provider:j.provider,token:j.token,...await api('tools_status',{token:j.token})};}catch(e){return {provider:j.provider,error:e.message};}}));
    if(search!==s||id!==searchEpoch)return;if(!searchValid(s)){discardSearch();return;}
    s.rows=statuses.flatMap(j=>(j.rows||[]).map(x=>({...x,provider:j.provider,token:j.token})));s.message=statuses.map(j=>names[j.provider]+'：'+(j.error||j.message||'搜索完成')).join(' · ');s.running=statuses.some(j=>j.status==='running');drawSearch(s);
    if(s.running)searchTimer=setTimeout(poll,1000);
   }await poll();
  };
  if(options.submit)$('#aggregateSearch').requestSubmit();
 };
 // Inline rename keeps the input node through periodic renders; file identity and
 // account session are checked again before committing the one-use plan.
 function same(e){return e&&state.view==='files'&&state.provider===e.provider&&state.path===e.path&&live?.providers[e.provider]?.session===e.session;}
 function finish(e){if(editing!==e)return;editing=null;e.node.remove();render();const row=$$('.file-row').find(r=>r.dataset.id===e.item.id);row?.focus({preventScroll:true});}
 function attach(e){if(!same(e)){editing=null;e.node.remove();return;}const row=$$('.file-row').find(r=>r.dataset.id===e.item.id);if(!row){editing=null;return;}row.classList.add('renaming');const name=row.querySelector('.file-name>span:last-child');if(name){name.hidden=true;name.after(e.node);}}
 window.cloudDockInlineRename=function(item){
  if(editing?.saving){toast('正在保存名称，请稍候');return;}if(editing)finish(editing);const p=providerState();if(!p?.connected||p.busy||p.share||p.path!==state.path)return;
  const e={item,provider:state.provider,path:state.path,context:p.context,session:p.session,saving:false,node:document.createElement('span')};editing=e;e.node.className='inline-rename';e.node.innerHTML='<input aria-label="新的文件名" maxlength="200" autocomplete="off" spellcheck="false"><small role="status" aria-live="polite"></small>';
  const input=e.node.querySelector('input'),message=e.node.querySelector('small');input.value=item.name;
  for(const type of ['click','dblclick','contextmenu'])e.node.addEventListener(type,x=>x.stopPropagation());
  input.onkeydown=async event=>{event.stopPropagation();if(event.key==='Escape'){event.preventDefault();if(!e.saving)finish(e);return;}if(event.key!=='Enter'||e.saving)return;event.preventDefault();
   const name=input.value;if(name===item.name){finish(e);return;}if(!name.trim()||/[\\/\x00-\x1f]/.test(name)||['.','..'].includes(name)){message.textContent='请输入有效名称，不能包含斜杠。';input.setAttribute('aria-invalid','true');return;}
   e.saving=true;input.disabled=true;message.textContent='正在保存…';input.removeAttribute('aria-invalid');
   try{const plan=await api('tools_plan',{provider:e.provider,context:e.context,ids:[item.id],kind:'rename',name});if(!same(e)||editing!==e)throw Error('目录或账号已变化，请重新操作。');
    e.committed=true;const job=await api('tools_commit',{provider:e.provider,confirmation:plan.confirmation});
    for(let count=0;count<120;count++){const status=await api('tools_status',{token:job.token});if(status.status==='completed'){finish(e);await refreshState();toast('名称已更新');return;}if(status.status!=='running')throw Error(status.message||'重命名未完成');await new Promise(r=>setTimeout(r,500));}
    finish(e);toast('操作仍在处理中，请稍后刷新目录核对。');
   }catch(err){if(editing===e&&same(e)){input.setAttribute('aria-invalid','true');if(e.committed){message.textContent='保存结果待核对，请刷新目录确认。';const check=document.createElement('button');check.className='quiet';check.textContent='核对目录';check.onclick=()=>{finish(e);$('#refresh').click();};message.append(check);}else{e.saving=false;input.disabled=false;message.textContent=err.message;input.focus();}}else toast(err.message);}
  };
  attach(e);input.focus();const dot=item.type==='folder'?-1:item.name.lastIndexOf('.');input.setSelectionRange(0,dot>0?dot:item.name.length);
 };
 $('#fileList').addEventListener('click',e=>{if(e.target.closest('.file-row'))return;if(editing&&!editing.saving)finish(editing);state.selected.clear();state.anchor=null;syncSelection();});
 const oldRender=render;render=function(animate=false){
  const e=editing,input=e?.node.querySelector('input'),focused=input===document.activeElement,start=input?.selectionStart,end=input?.selectionEnd;
  if(e)e.node.remove();oldRender(animate);compact();if(e&&editing===e){attach(e);if(focused&&editing===e){input.focus({preventScroll:true});input.setSelectionRange(start,end);}}
 };
 const oldControls=updateLiveControls;updateLiveControls=function(){oldControls();compact();};
 const oldRefresh=refreshState;refreshState=async function(){await oldRefresh();if(search&&!searchValid(search))discardSearch();if(!loaded&&live?.file_view){loaded=true;Object.assign(layout,Object.fromEntries(Object.keys(defaults).filter(k=>k in live.file_view).map(k=>[k,live.file_view[k]])));applyLayout();columns();}};
 const oldSettings=settings;settings=function(){oldSettings();$('#sheet').classList.add('settings-sheet');$('#sheet').classList.remove('wide-sheet');$('.settings-nav').setAttribute('aria-label','设置分类');$('.settings-panels').scrollTop=0;$$('[data-settings-tab]').forEach(b=>b.addEventListener('click',()=>$('.settings-panels').scrollTop=0));};$('#settings').onclick=settings;
 window.cloudDockApplyLayout=values=>{Object.assign(layout,Object.fromEntries(Object.keys(defaults).filter(k=>k in values).map(k=>[k,values[k]])));applyLayout();columns();};
 applyLayout();compact();
})();
