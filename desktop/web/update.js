// Signed GitHub releases are checked after the window is interactive.
(()=>{
 const native=window.chrome?.webview;let announced='',installing=false,channelSaving=false,requestedChannel=null,savingChannel=null;
 function statusText(u){
  if(!u?.enabled)return '更新源尚未连接；建立 GitHub 发布仓库后启用。';
  if(u.status==='checking')return '正在检查更新…';
  if(u.status==='latest')return '已是当前通道的最新版本。';
  if(u.status==='available')return `发现 ${u.available}，安装包 ${bytes(u.total||0)}`;
  if(u.status==='downloading')return `正在下载 ${bytes(u.downloaded||0)} / ${bytes(u.total||0)}`;
  if(u.status==='downloaded')return !u.install_supported?`${u.available} 已验证；便携版请改用安装包升级。`:u.busy?`${u.available} 已验证，传输或同步结束后可以安装。`:`${u.available} 已下载并通过签名与文件校验。`;
  if(u.status==='error')return u.error||'检查更新未完成。';
  return '尚未检查更新。';
 }
 async function invoke(action,extra={}){try{await api(action,extra);await refreshState();renderUpdate();}catch(error){report(error);}}
 async function installNow(){
  if(installing)return;installing=true;
  try{const result=await api('update_install');if(!native)throw Error('请在 Windows 桌面程序中安装更新');native.postMessage({type:'installUpdate',...result.update});}
  catch(error){installing=false;report(error);await refreshState();renderUpdate();}
 }
 async function chooseChannel(channel){
  requestedChannel=channel;renderUpdate();if(channelSaving)return;channelSaving=true;
  try{
   while(requestedChannel){
    const target=requestedChannel;requestedChannel=null;savingChannel=target;
    try{await api('update_preferences',{channel:target});await refreshState();
     if(!requestedChannel)await api('update_check');
    }catch(e){report(e);await refreshState();}
   }
  }finally{savingChannel=null;channelSaving=false;renderUpdate();}
 }
 function renderUpdate(){
  const host=$('#updateCard');if(!host)return;const u=live?.update||{};
  if(!host.dataset.ready){
   host.dataset.ready='true';
   host.innerHTML='<div class="update-card-head"><div><strong id="updateInstalled"></strong><small id="updateStatus"></small><small id="updateRoute" class="update-route"></small></div></div><div class="update-channel-label">接收更新的通道</div><div class="update-channel-group" role="group" aria-label="更新通道"><button type="button" data-update-channel="stable">正式版</button><button type="button" data-update-channel="beta">测试版</button></div><small class="update-channel-help">切换只影响以后接收的更新，不会更换当前安装版本。</small><div class="update-progress" id="updateProgress" aria-label="更新下载进度"><span></span></div><div class="update-notes" id="updateNotes"></div><div class="update-actions" id="updateActions"></div>';
   host.querySelectorAll('[data-update-channel]').forEach(b=>b.onclick=()=>chooseChannel(b.dataset.updateChannel));
   $('#updateActions').onclick=e=>{const id=e.target.closest('button')?.id;if(!id)return;
    if(id==='installUpdate')installNow();else if(id==='installLater')invoke('update_install_when_idle').then(()=>toast('任务安全结束后将自动安装并重启'));
    else invoke(({checkUpdate:'update_check',downloadUpdate:'update_download',cancelUpdate:'update_cancel',skipUpdate:'update_skip'})[id]);};
  }
  $('#updateInstalled').textContent='当前安装：云屿 '+(u.current||live?.version||'');
  $('#updateStatus').textContent=channelSaving?'正在保存更新通道…':statusText(u);
  $('#updateStatus').classList.toggle('update-error',u.status==='error');
  $('#updateRoute').textContent=u.route?'连接：'+u.route:'';
  const chosen=requestedChannel||savingChannel||u.channel;
  host.querySelectorAll('[data-update-channel]').forEach(b=>{const yes=b.dataset.updateChannel===chosen;b.classList.toggle('active',yes);b.setAttribute('aria-pressed',String(yes));b.disabled=u.status==='downloading';});
  $('#updateProgress').hidden=u.status!=='downloading';$('#updateProgress span').style.width=Math.max(0,Math.min(100,(u.progress||0)*100))+'%';
  $('#updateNotes').textContent=u.notes||'';$('#updateNotes').hidden=!u.notes;
  let actions=`<button class="quiet" id="checkUpdate" ${!u.enabled||u.status==='checking'||u.status==='downloading'?'disabled':''}>检查更新</button>`;
  if(u.status==='available'||u.status==='error'&&u.available)actions+='<button class="primary" id="downloadUpdate">下载更新</button>';
  if(u.status==='downloading')actions+='<button class="quiet" id="cancelUpdate">暂停下载</button>';
  if(u.status==='downloaded'&&u.install_supported)actions+=u.can_install?'<button class="primary" id="installUpdate">安装并重启</button>':'<button class="primary" id="installLater">任务完成后安装</button>';
  if(u.available&&u.status!=='downloaded'&&u.status!=='downloading')actions+='<button class="quiet" id="skipUpdate">跳过此版本</button>';
  if($('#updateActions').dataset.html!==actions){$('#updateActions').innerHTML=actions;$('#updateActions').dataset.html=actions;}
 }
 const previous=settings;
 settings=function(){
  previous();const body=$('#sheetBody'),exit=[...body.children].find(x=>x.textContent==='退出云屿');
  const title=document.createElement('h3');title.className='setting-group-title';title.textContent='软件更新';
  const toggles=document.createElement('div');toggles.innerHTML=`<div class="setting-line"><span>自动检查更新<small>界面启动后后台检查，每天最多一次</small></span><button id="autoCheckUpdate" class="switch ${live?.update?.auto_check!==false?'on':''}" role="switch" aria-checked="${live?.update?.auto_check!==false}"></button></div><div class="setting-line"><span>自动下载更新<small>下载后仍由你决定何时安装</small></span><button id="autoDownloadUpdate" class="switch ${live?.update?.auto_download?'on':''}" role="switch" aria-checked="${!!live?.update?.auto_download}"></button></div>`;
  const proxy=document.createElement('div'),proxyMode=live?.update?.proxy_mode||'auto',proxyUrl=live?.update?.proxy_url||'';proxy.className='update-network';proxy.innerHTML=`<div class="setting-line"><span>更新网络<small>自动检测本机代理，并在 GitHub Raw 不通时使用签名校验的 API 通道</small></span><select id="updateProxyMode" aria-label="更新网络"><option value="auto" ${proxyMode==='auto'?'selected':''}>自动检测</option><option value="system" ${proxyMode==='system'?'selected':''}>跟随系统</option><option value="direct" ${proxyMode==='direct'?'selected':''}>直接连接</option><option value="manual" ${proxyMode==='manual'?'selected':''}>手动代理</option></select></div><div class="setting-line update-proxy-manual" ${proxyMode==='manual'?'':'hidden'}><span>代理地址<small>例如 http://127.0.0.1:19828，不保存账号或密码</small></span><div><input id="updateProxyUrl" type="text" spellcheck="false" value="${esc(proxyUrl)}" placeholder="http://127.0.0.1:端口" aria-label="更新代理地址"><button id="saveUpdateProxy" class="quiet">保存</button></div></div>`;
  const card=document.createElement('div');card.id='updateCard';card.className='update-card';
  for(const node of [title,toggles,proxy,card])body.insertBefore(node,exit||null);
  $('#autoCheckUpdate').onclick=()=>invoke('update_preferences',{auto_check:!live.update.auto_check});
  $('#autoDownloadUpdate').onclick=()=>invoke('update_preferences',{auto_download:!live.update.auto_download});
  $('#updateProxyMode').onchange=e=>{const manual=e.target.value==='manual';$('.update-proxy-manual').hidden=!manual;if(!manual)invoke('update_preferences',{proxy_mode:e.target.value});};
  $('#saveUpdateProxy').onclick=()=>invoke('update_preferences',{proxy_mode:'manual',proxy_url:$('#updateProxyUrl').value.trim()});
  $('#updateProxyUrl').onkeydown=e=>{if(e.key==='Enter')$('#saveUpdateProxy').click();};
  renderUpdate();
 };$('#settings').onclick=settings;
 setInterval(()=>{
  const u=live?.update;if(!u)return;
  if(u.available&&u.available!==announced&&u.status==='available'){announced=u.available;toast(`发现云屿 ${u.available} 更新`);}
  if(u.install_when_idle&&u.can_install&&!installing)installNow();
  if($('#updateCard'))renderUpdate();
 },800);
})();
