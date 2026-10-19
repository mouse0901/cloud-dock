(async()=>{
 const r={},delay=ms=>new Promise(x=>setTimeout(x,ms)),original=api,originalRefresh=refreshState,calls=[];let searchRunning=false,uncertain=false;
 try{
  $('#sheet').close();await delay(40);document.body.classList.remove('dark','full-names');state.mode='list';$('#searchScope').value='page';$('#search').value='';
  const files=Array.from({length:40},(_,i)=>({id:'erg-'+i,name:i?'工作文件 '+i+'.pdf':'设计说明.pdf',path:'/项目/文件'+i+'.pdf',type:'pdf',size:123456,date:'2026-10-19 10:30'}));
  Object.assign(live.providers.baidu,{connected:true,busy:false,share:false,session:'erg-A',context:'erg-context',path:'/项目',page:0,files});
  Object.assign(live.providers.quark,{connected:true,busy:false,share:false,session:'erg-Q',context:'erg-q',path:'/'});
  live.capacities={baidu:{total:1000,used:200,free:800,state:'ready'},quark:{total:1000,used:700,free:300,state:'ready'}};
  api=async(action,d={})=>{calls.push([action,d]);if(action==='browse_view')return {session:d.session,page:0,files,context:'erg-context'};
   if(action==='profiles_list')return {providers:Object.fromEntries(['baidu','quark'].map(p=>[p,{active:'default',rows:[{id:'default',name:'日常账号',verified:true}]}]))};
   if(action==='tools_plan'){if(d.name==='重复.pdf')throw Error('已有同名文件');await delay(20);return {confirmation:'rename-once',kind:'rename'};}
   if(action==='tools_commit'){if(uncertain)throw Error('连接中断');return {token:'rename-job'};}
   if(action==='tools_search')return {token:'erg-search-'+d.provider};
   if(action==='tools_status')return d.token==='rename-job'?{status:'completed'}:{status:searchRunning?'running':'completed',token:d.token,message:'搜索完成',rows:[{id:files[0].id,name:files[0].name,parent:'/项目'}]};
   return {};
  };refreshState=async()=>{updateLiveControls();};await go('baidu','/项目');
  $('[data-capacity="baidu"]').click();r.capacity_readonly=!$('#sheet').open&&$('[data-capacity="baidu"]').tagName==='DIV'&&$('[data-capacity="baidu"]').tabIndex===-1;
  await accounts();$('[data-account-capacity="baidu"]').click();r.capacity_in_accounts=sheetKind==='capacity'&&$('#capacityDetail').textContent.includes('剩余空间');$('#refreshCapacity').click();await delay(20);r.capacity_refresh_provider=calls.some(x=>x[0]==='capacity_refresh'&&x[1].provider==='baidu');$('#capacityBack').click();await delay(20);r.capacity_back=!!$('#profileRows');$('#sheet').close();
  // Reopen immediately: a queued close event must not destroy the new settings shell.
  settings();await delay(80);const nav=$('.settings-nav').getBoundingClientRect(),panels=$('.settings-panels').getBoundingClientRect();
  r.settings_two_columns=panels.left>=nav.right&&Math.abs(panels.top-nav.top)<2;r.settings_contained=panels.bottom<=$('#sheet').getBoundingClientRect().bottom&&getComputedStyle($('.settings-panels')).overflowY==='auto';
  $('[data-settings-tab="feedback"]').click();r.settings_category=!!$('[data-settings-panel="feedback"]')&&!$('[data-settings-panel="feedback"]').hidden&&$('[data-settings-panel="general"]').hidden;
  $('#sheet').close();await delay(30);
  state.selected=new Set([files[0].id]);syncSelection();cloudDockFileActions.name('rename',files[0]);let input=$('.inline-rename input');r.inline_basename=input.value==='设计说明.pdf'&&input.selectionStart===0&&input.selectionEnd===4&&!$('#sheet').open;
  input.value='重命名.pdf';render();r.inline_survives_render=$('.inline-rename input')===input&&input.value==='重命名.pdf';input.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));r.rename_escape=!$('.inline-rename')&&!calls.some(x=>x[0]==='tools_commit');
  cloudDockFileActions.name('rename',files[0]);input=$('.inline-rename input');input.value='重复.pdf';input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await delay(40);r.rename_error_local=$('.inline-rename small').textContent.includes('同名')&&!$('#sheet').open;
  input.value='新名字.pdf';input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await delay(80);r.rename_single_commit=calls.filter(x=>x[0]==='tools_commit').length===1&&!$('.inline-rename');
  cloudDockFileActions.name('rename',files[0]);input=$('.inline-rename input');input.value='不应提交.pdf';input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));state.path='/另一个目录';render();await delay(60);r.stale_rename_not_committed=calls.filter(x=>x[0]==='tools_commit').length===1;await go('baidu','/项目');
  uncertain=true;cloudDockFileActions.name('rename',files[0]);input=$('.inline-rename input');input.value='待确认.pdf';const commitsBefore=calls.filter(x=>x[0]==='tools_commit').length;input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await delay(60);input.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await delay(20);r.uncertain_rename_not_repeated=input.disabled&&calls.filter(x=>x[0]==='tools_commit').length===commitsBefore+1&&$('.inline-rename').textContent.includes('待核对');$('.inline-rename button').click();await cloudDockTabs.idle();uncertain=false;
  const handle=$('#sidebarResize');handle.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));await delay(30);r.sidebar_resize_persisted=calls.some(x=>x[0]==='file_view'&&x[1].sidebar_width===300)&&Math.round($('.sidebar').getBoundingClientRect().width)===300;handle.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
  const nameHandle=$('[data-column="name"] .column-resize'),beforeName=$('[data-column="name"]').getBoundingClientRect().width;nameHandle.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));r.name_column_changes=$('[data-column="name"]').getBoundingClientRect().width>beforeName+20;nameHandle.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
  const col=$('[data-column="size"] .column-resize');col.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));await delay(30);r.column_resize_persisted=calls.some(x=>x[0]==='file_view'&&x[1].size_width===180);col.dispatchEvent(new MouseEvent('dblclick',{bubbles:true}));
  $('#viewOptions').click();const toggle=$('[data-column-setting="show_date"]');toggle.checked=false;toggle.dispatchEvent(new Event('change'));r.column_hidden=getComputedStyle($('[data-column="date"]')).display==='none';$('#resetLayout').click();$('#sheet').close();await delay(30);
  state.selected=new Set([files[0].id]);syncSelection();showContext(files[0],800,400);r.menu_file_only=!$('#contextMenu').textContent.includes('新建文件夹')&&!$('#contextMenu').textContent.includes('固定当前位置')&&$('#contextMenu').querySelectorAll('[role=separator]').length>=2;closeMenus();
  $('#fileList').dispatchEvent(new MouseEvent('click',{bubbles:true}));r.blank_deselects=state.selected.size===0;
  $('#searchScope').value='page';$('#search').value='不存在';$('#search').dispatchEvent(new Event('input'));r.local_search_no_network=!calls.some(x=>x[0]==='tools_search')&&visible().length===0;
  $('#searchScope').value='drive';$('#searchScope').dispatchEvent(new Event('change'));r.remote_scope_keeps_listing=visible().length===40;
  $('#search').value='设计';$('#search').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));await delay(80);r.remote_explicit_scope=calls.filter(x=>x[0]==='tools_search').length===1&&calls.find(x=>x[0]==='tools_search')[1].provider==='baidu';$('#sheet').close();await delay(30);$('#returnSearch').click();r.search_retained=$('#aggregateResults').textContent.includes('设计说明')&&$('#aggregateTerm').value==='设计';$('#sheet').close();await delay(30);
  searchRunning=true;cloudDockSearchAll({scope:'baidu',term:'设计',submit:true});await delay(60);$('#stopSearch').click();await delay(30);r.stop_search_preserves_rows=calls.some(x=>x[0]==='tools_stop')&&$$('[data-aggregate-result]').length===1&&$('#stopSearch').hidden;searchRunning=false;$('#sheet').close();await delay(20);
  $('#searchScope').value='page';$('#search').value='';render();r.breadcrumb_only=getComputedStyle($('#viewTitle')).display==='none'&&getComputedStyle($('#breadcrumbs')).display!=='none';r.no_horizontal_overflow=document.documentElement.scrollWidth<=innerWidth;
  state.selected=new Set([files[10].id]);syncSelection();$('#fileList').scrollTop=300;const y=$('#fileList').scrollTop;$('#refresh').click();await cloudDockTabs.idle();r.refresh_preserves_selection=state.selected.has(files[10].id);r.refresh_preserves_scroll=$('#fileList').scrollTop===y;
  window.qaUsabilityFilesReady=true;while(!window.qaUsabilityContinue)await delay(50);
  settings();$('[data-settings-tab="general"]').click();await delay(40);
 }catch(e){r.error=String(e)+' '+e.stack;}finally{api=original;refreshState=originalRefresh;window.qaUsability=r;}
})();
