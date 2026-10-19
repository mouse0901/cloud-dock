// The native host owns window visibility. Hiding never shuts down the backend.
(()=>{
 const native=window.chrome?.webview;
 let lastPreferences='',lastRead=0,lastLocation='',restorePending=null;
 const milestones=new Set();
 window.cloudDockBackground=false;
 function send(value){native?.postMessage(value);}
 function preferences(){
  const value={type:'desktopPreferences',close_to_tray:live?.close_to_tray!==false,
   clipboard_links:live?.clipboard_links!==false,
   clipboard_wake:live?.clipboard_wake!==false,
   dark:document.body.classList.contains('dark'),reduced:document.body.classList.contains('reduced')};
  const key=JSON.stringify(value);if(key!==lastPreferences){lastPreferences=key;send(value);}
 }
 function milestone(phase){if(!milestones.has(phase)){milestones.add(phase);send({type:'startupMilestone',phase});}}
 const originalRefresh=refreshState;
 refreshState=async function(){
  lastRead=Date.now();await originalRefresh();
  if(!live)return;preferences();
  if(restorePending&&!live.initializing){
   const r=restorePending;restorePending=null;state.provider=r.provider;
   if(r.view==='files')localGo(r.provider,live.providers[r.provider]?.path||'/');else changeView(r.view);
  }
  const location=JSON.stringify([state.view,state.provider]);
  if(location!==lastLocation){lastLocation=location;send({type:'uiLocation',view:state.view,provider:state.provider});}
  if(live.initialization_error&&state.view==='files'){
   const host=$('#fileList');host.replaceChildren();
   const box=document.createElement('div');box.className='empty';
   const text=document.createElement('p');text.textContent=live.initialization_error;
   const retry=document.createElement('button');retry.className='primary';retry.textContent='重新准备工作空间';
   retry.onclick=async()=>{retry.disabled=true;try{await api('retry_workspace');await refreshState();}catch(error){report(error);retry.disabled=false;}};
   box.append(text,retry);host.append(box);
  }
  if(!live.initializing&&!live.initialization_error)milestone('workspace_ready');
  if(live.accounts_settled)milestone('accounts_settled');
  const p=providerState();if(p?.connected&&!p.busy)milestone('files_ready');
 };
 setInterval(()=>{
  const delay=document.hidden||window.cloudDockBackground?5000:700;
  if(Date.now()-lastRead>=delay)refreshState();
 },700);
 function visibility(hidden){
  window.cloudDockBackground=hidden;document.body.classList.toggle('background-window',hidden);
  if(!hidden)refreshState();
 }
 native?.addEventListener('message',event=>{
  const message=event.data;
  if(message?.type==='windowVisibility')visibility(!message.visible);
  if(message?.type==='restoreLocation'&&['baidu','quark'].includes(message.provider)&&['files','transfers','recent','backups','trash'].includes(message.view)){restorePending=message;refreshState();}
  if(message?.type==='openSettings')settings();
  if(message?.type==='qaBridgeProbe'){
   (async()=>{
    const result={type:'qaBridgeResult',state:false,action:false,pickerBlocked:false};
    try{
     await api('desktop_preferences',{close_to_tray:live?.close_to_tray!==false});result.action=true;
     const stateResponse=await fetch(apiUrl('api/state'));const snapshot=await stateResponse.json();
     result.state=stateResponse.ok&&snapshot.version==='1.0.0'&&!snapshot.initializing;
     const forbidden=await fetch(apiUrl('api/selection'));result.pickerBlocked=!forbidden.ok;
    }catch{}finally{send(result);}
   })();
  }
 });
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!window.cloudDockBackground)refreshState();});
 window.addEventListener('clouddock-theme',preferences);
 const previousSettings=settings;
 settings=function(){
  previousSettings();
  const row=document.createElement('div');row.className='setting-line';
  row.innerHTML='<span>关闭窗口后继续在后台运行<small>保留在系统托盘，传输、同步继续运行</small></span><button id="traySwitch" class="switch" role="switch" aria-label="关闭窗口后继续在后台运行"></button>';
  $('#sheetBody').prepend(row);
  const button=$('#traySwitch');let enabled=live?.close_to_tray!==false;
  function update(){button.classList.toggle('on',enabled);button.setAttribute('aria-checked',String(enabled));}
  update();button.onclick=async()=>{
   button.disabled=true;
   try{await api('desktop_preferences',{close_to_tray:!enabled});enabled=!enabled;if(live)live.close_to_tray=enabled;update();preferences();}
   catch(error){report(error);}finally{button.disabled=false;}
  };
  const clipboardRow=document.createElement('div');clipboardRow.className='setting-line';
  clipboardRow.innerHTML='<span>自动识别剪贴板分享链接<small>识别新复制的百度或夸克链接，自动提取访问码</small></span><button id="clipboardSwitch" class="switch" role="switch" aria-label="自动识别剪贴板分享链接"></button>';
  row.after(clipboardRow);const clipboardButton=$('#clipboardSwitch');let clipboardEnabled=live?.clipboard_links!==false;
  function updateClipboard(){clipboardButton.classList.toggle('on',clipboardEnabled);clipboardButton.setAttribute('aria-checked',String(clipboardEnabled));}
  updateClipboard();clipboardButton.onclick=async()=>{
   clipboardButton.disabled=true;
   try{await api('desktop_preferences',{clipboard_links:!clipboardEnabled});clipboardEnabled=!clipboardEnabled;if(live)live.clipboard_links=clipboardEnabled;updateClipboard();preferences();if(!clipboardEnabled)window.dispatchEvent(new Event('clouddock-clear-clipboard'));}
   catch(error){report(error);}finally{clipboardButton.disabled=false;}
  };
  const exit=document.createElement('button');exit.className='quiet';exit.textContent='退出云屿';
  const wakeRow=document.createElement('div');wakeRow.className='setting-line';wakeRow.innerHTML='<span>识别链接后唤起云屿<small>从最小化或托盘恢复窗口；同一链接不重复打扰</small></span><button id="clipboardWakeSwitch" class="switch" role="switch" aria-label="识别链接后唤起云屿"></button>';clipboardRow.after(wakeRow);
  const wake=$('#clipboardWakeSwitch');let wakeEnabled=live?.clipboard_wake!==false;
  const drawWake=()=>{wake.classList.toggle('on',wakeEnabled);wake.setAttribute('aria-checked',String(wakeEnabled));};drawWake();
  wake.onclick=async()=>{wake.disabled=true;try{await api('desktop_preferences',{clipboard_wake:!wakeEnabled});wakeEnabled=!wakeEnabled;if(live)live.clipboard_wake=wakeEnabled;drawWake();preferences();}catch(e){report(e);}finally{wake.disabled=false;}};
  exit.disabled=!native;exit.onclick=()=>send({type:'exitApplication'});$('#sheetBody').append(exit);
 };$('#settings').onclick=settings;
 // Handlers and feature scripts are attached before this marker. Do not wait for a cloud request.
 function ready(){requestAnimationFrame(()=>requestAnimationFrame(()=>send({type:'uiReady'})));}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready,{once:true});else ready();
})();
