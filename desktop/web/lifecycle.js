// The native host owns window visibility. Hiding never shuts down the backend.
(()=>{
 const native=window.chrome?.webview;
 let lastPreferences='',lastRead=0;
 const milestones=new Set();
 window.cloudDockBackground=false;
 function send(value){native?.postMessage(value);}
 function preferences(){
  const value={type:'desktopPreferences',close_to_tray:live?.close_to_tray!==false,
   clipboard_links:live?.clipboard_links!==false,
   dark:document.body.classList.contains('dark'),reduced:document.body.classList.contains('reduced')};
  const key=JSON.stringify(value);if(key!==lastPreferences){lastPreferences=key;send(value);}
 }
 function milestone(phase){if(!milestones.has(phase)){milestones.add(phase);send({type:'startupMilestone',phase});}}
 const originalRefresh=refreshState;
 refreshState=async function(){
  lastRead=Date.now();await originalRefresh();
  if(!live)return;preferences();
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
  if(message?.type==='openSettings')settings();
  if(message?.type==='qaBridgeProbe'){
   (async()=>{
    const result={type:'qaBridgeResult',state:false,action:false,pickerBlocked:false};
    try{
     await api('desktop_preferences',{close_to_tray:live?.close_to_tray!==false});result.action=true;
     const stateResponse=await fetch(apiUrl('api/state'));const snapshot=await stateResponse.json();
     result.state=stateResponse.ok&&snapshot.version==='0.6.4'&&!snapshot.initializing;
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
  row.innerHTML='<span>关闭窗口后继续在后台运行<small>保留在系统托盘，传输、同步与备份继续运行</small></span><button id="traySwitch" class="switch" role="switch" aria-label="关闭窗口后继续在后台运行"></button>';
  $('#sheetBody').prepend(row);
  const button=$('#traySwitch');let enabled=live?.close_to_tray!==false;
  function update(){button.classList.toggle('on',enabled);button.setAttribute('aria-checked',String(enabled));}
  update();button.onclick=async()=>{
   button.disabled=true;
   try{await api('desktop_preferences',{close_to_tray:!enabled});enabled=!enabled;if(live)live.close_to_tray=enabled;update();preferences();}
   catch(error){report(error);}finally{button.disabled=false;}
  };
  const clipboardRow=document.createElement('div');clipboardRow.className='setting-line';
  clipboardRow.innerHTML='<span>自动识别剪贴板分享链接<small>回到云屿时提示百度或夸克链接；点击后才打开</small></span><button id="clipboardSwitch" class="switch" role="switch" aria-label="自动识别剪贴板分享链接"></button>';
  row.after(clipboardRow);const clipboardButton=$('#clipboardSwitch');let clipboardEnabled=live?.clipboard_links!==false;
  function updateClipboard(){clipboardButton.classList.toggle('on',clipboardEnabled);clipboardButton.setAttribute('aria-checked',String(clipboardEnabled));}
  updateClipboard();clipboardButton.onclick=async()=>{
   clipboardButton.disabled=true;
   try{await api('desktop_preferences',{clipboard_links:!clipboardEnabled});clipboardEnabled=!clipboardEnabled;if(live)live.clipboard_links=clipboardEnabled;updateClipboard();preferences();if(!clipboardEnabled)window.dispatchEvent(new Event('clouddock-clear-clipboard'));}
   catch(error){report(error);}finally{clipboardButton.disabled=false;}
  };
  const exit=document.createElement('button');exit.className='quiet';exit.textContent='退出云屿';
  exit.disabled=!native;exit.onclick=()=>send({type:'exitApplication'});$('#sheetBody').append(exit);
 };$('#settings').onclick=settings;
 // Handlers and feature scripts are attached before this marker. Do not wait for a cloud request.
 function ready(){requestAnimationFrame(()=>requestAnimationFrame(()=>send({type:'uiReady'})));}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',ready,{once:true});else ready();
})();
