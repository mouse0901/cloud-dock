// Each tab owns its history, page, selection and scroll; account sessions are never cached.
(()=>{
 const tabs=[],sessions=new Map(),directoryCache=new Map();let nextId=0,active=null,epoch=0,chain=Promise.resolve(),pending=false,refreshing=false,loadingPrefs=false;
 const CACHE_LIMIT=240;
 const bar=document.createElement('nav');bar.id='browserTabs';bar.setAttribute('aria-label','网盘标签页');bar.setAttribute('role','tablist');$('.toolbar').after(bar);
 const errorBox=document.createElement('div');errorBox.id='tabError';errorBox.hidden=true;errorBox.setAttribute('role','alert');errorBox.innerHTML='<span></span><button class="quiet">重新读取</button>';$('.content-region').before(errorBox);errorBox.querySelector('button').onclick=()=>load(here());
 const display=document.createElement('button');display.id='viewOptions';display.className='icon-button';display.title='显示选项';display.setAttribute('aria-label','显示选项');display.innerHTML=icon('list');$('#sort').before(display);
 function point(provider,path='/',page=0){return {provider,path,page,selected:[],anchor:null,scroll:0,term:'',filter:'all'};}
 function make(provider,path='/',page=0){const t={id:++nextId,entries:[point(provider,path,page)],index:0,positions:new Map()};tabs.push(t);return t;}
 function here(t=active){return t?.entries[t.index];}
 function key(h){return JSON.stringify([h.provider,h.path,h.page]);}
 function cacheKey(h,session=live?.providers[h.provider]?.session){return JSON.stringify([h.provider,String(session??''),h.path,h.page]);}
 function cached(h,session=live?.providers[h.provider]?.session){return h&&directoryCache.get(cacheKey(h,session));}
 function rememberDirectory(h,value,session=live?.providers[h.provider]?.session){
  if(!h||!Array.isArray(value?.files)||session===undefined)return;
  const k=cacheKey(h,session);directoryCache.delete(k);directoryCache.set(k,{files:[...value.files],context:value.context||'',at:Date.now()});
  while(directoryCache.size>CACHE_LIMIT)directoryCache.delete(directoryCache.keys().next().value);
 }
 function forgetProvider(provider){for(const k of [...directoryCache.keys()])if(JSON.parse(k)[0]===provider)directoryCache.delete(k);}
 function useDirectory(h,value){if(!value)return;data[h.provider][h.path]=value.files;}
 function readyDirectory(h,session){const p=live?.providers[h.provider];return !!(p?.connected&&!p.busy&&p.session===session&&p.path===h.path&&(p.page||0)===h.page&&Array.isArray(p.files));}
 function save(){const h=here();if(!h||state.view!=='files'||pending||h.provider!==state.provider||h.path!==state.path)return;Object.assign(h,{selected:[...state.selected],anchor:state.anchor,scroll:$('#fileList').scrollTop,term:$('#search').value,filter:state.filter});active.positions.set(key(h),{...h,selected:[...h.selected]});if(active.positions.size>100)active.positions.delete(active.positions.keys().next().value);}
 function drawTabs(){bar.hidden=state.view!=='files';const sig=JSON.stringify([active?.id,tabs.map(t=>[t.id,here(t)])]);if(bar.dataset.key===sig)return;bar.dataset.key=sig;
  bar.innerHTML=tabs.map(t=>{const h=here(t),title=h.path==='/'?names[h.provider]:h.path.split('/').pop();return `<div class="browser-tab ${t===active?'selected':''}"><button role="tab" aria-selected="${t===active}" tabindex="${t===active?0:-1}" data-tab="${t.id}" title="${esc(names[h.provider]+' · '+h.path)}"><small>${h.provider==='baidu'?'百度':'夸克'}</small><span>${esc(title)}</span></button><button class="tab-close" data-close-tab="${t.id}" aria-label="关闭 ${esc(title)}">×</button></div>`;}).join('')+'<button id="newBrowserTab" class="icon-button" title="新建标签页（Ctrl + T）" aria-label="新建标签页">＋</button>';
  $$('[data-tab]').forEach(b=>{b.onclick=()=>activate(tabs.find(t=>t.id===Number(b.dataset.tab)));b.onauxclick=e=>{if(e.button===1){e.preventDefault();close(Number(b.dataset.tab));}};b.onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();activate(tabs[(tabs.indexOf(active)+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length]).then(()=>$('#browserTabs [aria-selected="true"]')?.focus());}};});$$('[data-close-tab]').forEach(b=>b.onclick=()=>close(Number(b.dataset.closeTab)));$('#newBrowserTab').onclick=()=>newTab(state.provider,'/');
 }
 function navigationControls(){if(!active)return;const quiet=pending&&!!cached(here());$('#back').disabled=active.index===0;$('#forward').disabled=active.index===active.entries.length-1;$('#fileList').classList.toggle('tab-loading',pending&&!quiet);$('#fileList').classList.toggle('tab-syncing',quiet);$('#fileList').setAttribute('aria-busy',String(pending));errorBox.hidden=state.view!=='files'||!here()?.error;errorBox.querySelector('span').textContent=here()?.error||'';bar.classList.toggle('quiet-sync',quiet);bar.title=quiet?'正在后台更新目录，当前内容仍可浏览':'';drawTabs();}
 const originalProvider=providerState;providerState=function(){const p=originalProvider();return p&&state.view==='files'&&(pending||here()?.error||p.path!==state.path||here()?.path===state.path&&p.page!==undefined&&p.page!==here()?.page)?{...p,busy:true}:p;};
 const originalFiles=currentFiles;currentFiles=function(){const h=here(),fallback=!!cached(h);return ((pending&&!refreshing&&!fallback)||(h?.error&&!fallback))&&state.view==='files'?[]:originalFiles();};
 function show(h){state.view='files';state.provider=h.provider;state.path=h.path;state.paths[h.provider]=h.path;state.selected=new Set(pending&&refreshing?h.selected:[]);state.anchor=pending&&refreshing?h.anchor:null;state.filter=h.filter||'all';$('#search').value=h.term||'';$('#filterLabel').textContent=({all:'全部类型',folder:'文件夹',image:'图片',video:'视频',pdf:'PDF',text:'文档',audio:'音频',archive:'压缩包'})[state.filter]||'全部类型';state.history=active.entries;state.cursor=active.index;render();updateLiveControls();}
 async function load(h,locate,keep=false){const current=++epoch,session=live?.providers[h.provider]?.session,memory=cached(h,session);h.error='';
  if(!locate&&!keep&&readyDirectory(h,session)){
   const p=live.providers[h.provider];rememberDirectory(h,p,session);useDirectory(h,p);pending=false;refreshing=false;show(h);
   const allowed=new Set(currentFiles().map(f=>f.id));state.selected=new Set(h.selected.filter(id=>allowed.has(id)));state.anchor=allowed.has(h.anchor)?h.anchor:null;syncSelection();$('#fileList').scrollTop=h.scroll||0;save();navigationControls();return;
  }
  refreshing=keep||!!memory;if(memory)useDirectory(h,memory);pending=true;show(h);
  chain=chain.catch(()=>{}).then(async()=>{
   if(current!==epoch)return;
   const p=live?.providers[h.provider];if(!p?.connected){pending=false;updateLiveControls();return;}
   try{const r=await api(locate?'browse_locate':'browse_view',{provider:h.provider,path:h.path,page:h.page,session,...locate});if(current!==epoch)return;
    if(r.session!==session)throw Error('账号已变化，请重新打开目录');h.page=r.page;if(r.selected){h.selected=[r.selected];h.term='';h.filter='all';}
    data[h.provider][h.path]=r.files;rememberDirectory(h,r,session);const actual=live.providers[h.provider];Object.assign(actual,{path:h.path,page:h.page,files:r.files,context:r.context,busy:false});pending=false;refreshing=false;
    show(h);const allowed=new Set(currentFiles().map(f=>f.id));state.selected=new Set(h.selected.filter(id=>allowed.has(id)));state.anchor=allowed.has(h.anchor)?h.anchor:null;syncSelection();
    $('#fileList').scrollTop=h.scroll||0;if(r.selected){const row=$$('.file-row').find(x=>x.dataset.id===r.selected);row?.scrollIntoView({block:'center'});row?.focus({preventScroll:true});toast('已定位并选中该项目');}
    save();await refreshState();if(current===epoch&&!r.selected)$('#fileList').scrollTop=h.scroll||0;
   }catch(error){if(current===epoch){pending=false;refreshing=false;h.error=error.message;if(!cached(h,session))state.selected.clear();render();syncSelection();report(error);updateLiveControls();}}finally{if(current===epoch)navigationControls();}
  });await chain;
 }
 async function activate(tab){if(!tab)return;save();active=tab;return load(here());}
 function newTab(provider,path='/',page=0){if(tabs.length>=10){toast('最多打开 10 个标签页，请先关闭一个');return Promise.resolve();}save();active=make(provider,path,page);return load(here());}
 function close(id){const i=tabs.findIndex(t=>t.id===id);if(i<0)return;if(tabs.length===1){toast('至少保留一个网盘标签页');return;}const was=tabs[i]===active;tabs.splice(i,1);if(was){active=tabs[Math.min(i,tabs.length-1)];load(here());}else drawTabs();}
 go=async function(provider,path='/',record=true){save();if(!active)active=make(provider,path);if(record){let h=point(provider,path);h={...h,...active.positions.get(key(h))};active.entries.splice(active.index+1);active.entries.push(h);active.index=active.entries.length-1;if(active.entries.length>100){active.entries.shift();active.index--;}}else{active.entries[active.index]=point(provider,path);}return load(here());};
 const previousLocal=localGo;localGo=function(provider,path='/',record=true){save();if(active&&!pending){const p=live?.providers[provider],h=point(provider,path,p?.path===path?p.page||0:0);if(record){active.entries.splice(active.index+1);active.entries.push(h);active.index=active.entries.length-1;}else active.entries[active.index]=h;}previousLocal(provider,path,false);if(active){state.history=active.entries;state.cursor=active.index;}navigationControls();};
 async function step(delta){if(!active||active.index+delta<0||active.index+delta>=active.entries.length)return;save();active.index+=delta;await load(here());}
 $('#back').onclick=()=>step(-1);$('#forward').onclick=()=>step(1);
 async function page(delta){if(pending||!providerState()?.connected)return;save();const h=here();h.page=Math.max(0,h.page+delta);Object.assign(h,{selected:[],scroll:0,...active.positions.get(key(h))});await load(h);}
 $('#pagePrev').onclick=()=>page(-1);$('#pageNext').onclick=()=>page(1);$('#refresh').onclick=()=>{save();if(active)load(here(),null,true);};
 const previousChange=changeView;changeView=function(view){save();if(view!=='files'){++epoch;pending=false;}previousChange(view);navigationControls();};
 const previousRender=render;render=function(animate=false){const scroll=$('#fileList').scrollTop,preserve=!animate&&(!pending||refreshing)&&here()?.path===state.path&&here()?.provider===state.provider;previousRender(animate);if(preserve)$('#fileList').scrollTop=scroll;for(const row of $$('.file-row')){row.title=row.getAttribute('aria-label');row.onauxclick=e=>{const file=currentFiles().find(f=>f.id===row.dataset.id);if(e.button===1&&file?.type==='folder'){e.preventDefault();newTab(file.provider||state.provider,file.path||state.path.replace(/\/$/,'')+'/'+file.name);}};}navigationControls();};
 const previousContext=showContext;showContext=function(file,x,y){previousContext(file,x,y);if(file.type!=='folder'||state.view!=='files')return;const b=document.createElement('button');b.textContent='在新标签页打开';b.onclick=()=>{closeMenus();newTab(state.provider,file.path||state.path.replace(/\/$/,'')+'/'+file.name);};$('#contextMenu').prepend(b);};
 const previousControls=updateLiveControls;updateLiveControls=function(){previousControls();navigationControls();};
 const previousRefresh=refreshState;refreshState=async function(){await previousRefresh();if(!live)return;
  if(!loadingPrefs&&live.file_view){loadingPrefs=true;applyView(live.file_view);}
  let reset=false;for(const [provider,p]of Object.entries(live.providers)){if(p.session===undefined)continue;const old=sessions.get(provider);sessions.set(provider,p.session);if(old!==undefined&&old!==p.session){data[provider]={};forgetProvider(provider);for(const t of tabs){t.positions.clear();if(t.entries.some(h=>h.provider===provider)){const h=here(t);t.entries=[point(h.provider,h.provider===provider?'/':h.path)];t.index=0;if(t===active)reset=true;}}}if(p.connected&&!p.busy)rememberDirectory(point(provider,p.path||'/',p.page||0),p,p.session);}
  if(reset){++epoch;pending=false;if(state.view==='files'){const h=here();show(h);if(live.providers[h.provider]?.connected)load(h);}}navigationControls();
 };
 function applyView(v){document.body.dataset.density=v.density||'comfortable';document.body.dataset.gridSize=v.grid_size||'medium';document.body.classList.toggle('full-names',!!v.full_names);}
 display.onclick=()=>{sheetKind='display';sheet('显示选项',`<div class="setting-line"><span>列表密度</span><select id="listDensity" aria-label="列表密度"><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></div><div class="setting-line"><span>网格大小</span><select id="gridSize" aria-label="网格大小"><option value="small">小</option><option value="medium">中</option><option value="large">大</option></select></div><label class="setting-line"><span>展开长文件名<small>列表自动换行，网格显示更多文字；悬停和信息栏始终可看完整名称</small></span><input type="checkbox" id="fullNames"></label>`);$('#listDensity').value=document.body.dataset.density||'comfortable';$('#gridSize').value=document.body.dataset.gridSize||'medium';$('#fullNames').checked=document.body.classList.contains('full-names');for(const input of $$('#sheet select,#fullNames'))input.onchange=()=>{const v={density:$('#listDensity').value,grid_size:$('#gridSize').value,full_names:$('#fullNames').checked};applyView(v);api('file_view',v).catch(report);};};
 window.cloudDockLocate=async(provider,item,token)=>{save();active.entries.splice(active.index+1);active.entries.push(point(provider,item.parent));active.index=active.entries.length-1;await load(here(),{id:item.id,token});};
 window.cloudDockTabs={newTab,activate:id=>activate(tabs.find(t=>t.id===id)),close,save,entries:()=>tabs.map(t=>({id:t.id,...here(t)})),current:()=>active?.id,idle:()=>chain};
 document.addEventListener('keydown',e=>{if($('#sheet').open||!e.ctrlKey||e.altKey)return;const k=e.key.toLowerCase();if(k==='t'){e.preventDefault();newTab(state.provider,'/');}else if(k==='w'&&state.view==='files'){e.preventDefault();close(active.id);}else if(k==='tab'){e.preventDefault();activate(tabs[(tabs.indexOf(active)+(e.shiftKey?tabs.length-1:1))%tabs.length]);}});
 active=make(state.provider,state.path,live?.providers[state.provider]?.page||0);drawTabs();
})();
