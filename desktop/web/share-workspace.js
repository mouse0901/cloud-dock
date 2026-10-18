// Account-bound share previews and explicit download confirmation.
(()=>{
 const dialog=$('#sheet');let epoch=0;
 dialog.addEventListener('close',()=>{epoch++;dialog.classList.remove('links-sheet','download-sheet');});
 function panel(title,html,kind='links'){
  epoch++;sheetKind=kind;dialog.classList.remove('tools-sheet','media-sheet','links-sheet','download-sheet');
  dialog.classList.add(kind+'-sheet');sheet(title,html);return epoch;
 }
 function alive(id){return epoch===id&&dialog.open;}
 function error(e){const box=$('#linkError');if(box){box.hidden=false;box.textContent=e.message||'操作未完成，请重试。';}}
 const errorHtml='<p id="linkError" class="sync-error" role="alert" hidden></p>';
 window.openShareWorkspace=function(text='',password='',offered=[]){
  const id=panel('打开网盘链接','<p>粘贴百度或夸克的分享文字，自动识别链接和提取码。</p><label class="link-label" for="shareInput">分享链接</label><textarea id="shareInput" rows="3" maxlength="16384" placeholder="分享链接或完整的分享文字"></textarea><div id="linkCandidates"></div><label class="link-label" for="sharePassword">提取码（如有）</label><input id="sharePassword" type="text" maxlength="8" autocomplete="off" placeholder="自动识别，也可以手动输入">'+errorHtml+'<div class="link-actions"><button id="shareOpen" class="primary">查看文件</button></div>');
  $('#shareInput').value=text;$('#sharePassword').value=password;let timer,parseId=0,candidates=offered,busy=false;
  function options(){
   const container=$('#linkCandidates');container.replaceChildren();
   if(candidates.length<2)return;
   const label=document.createElement('small');label.textContent='识别到 '+candidates.length+' 个链接，选择一个打开';container.append(label);
   candidates.forEach((link,i)=>{const b=document.createElement('button');b.className='link-candidate quiet';b.textContent=names[link.provider]+' · '+link.url.split('?')[0];b.onclick=()=>{if(busy)return;$('#shareInput').value=link.url;$('#sharePassword').value=link.password;container.querySelectorAll('button').forEach(x=>x.classList.toggle('chosen',x===b));};container.append(b);});
  }
  options();
  $('#shareInput').oninput=()=>{clearTimeout(timer);const serial=++parseId;timer=setTimeout(async()=>{try{const r=await api('links_parse',{text:$('#shareInput').value});if(!alive(id)||serial!==parseId)return;candidates=r.links;options();$('#sharePassword').value=candidates[0]?.password||'';}catch(e){if(alive(id))error(e);}},180);};
  $('#shareOpen').onclick=async()=>{
   if(busy)return;busy=true;$('#shareOpen').disabled=true;$('#linkError').hidden=true;
   const input=$('#shareInput').value,code=$('#sharePassword').value.trim();
   try{
    const r=await api('links_parse',{text:input});if(!alive(id))return;
    if(!r.links.length)throw Error('没有识别到百度或夸克分享链接，请检查地址。');
    if(r.links.length>1){candidates=r.links;options();throw Error('请选择上面的一个链接后再打开。');}
    const link=r.links[0];
    if(!live?.providers[link.provider]?.connected){
     error(Error('请先连接'+names[link.provider]+'，连接后可继续打开此分享。'));
     let login=$('#shareLogin');if(!login){login=document.createElement('button');login.id='shareLogin';login.className='quiet';login.textContent='连接'+names[link.provider];$('#linkError').after(login);}
     login.onclick=()=>{epoch++;dialog.classList.remove('links-sheet');dialog.addEventListener('close',()=>setTimeout(()=>window.openShareWorkspace(input,code||link.password,candidates),0),{once:true});loginSheet(link.provider,true);};return;
    }
    const result=await api('links_open',{link:link.url,password:code||link.password});if(alive(id))showFiles(result,input,code||link.password);
   }catch(e){if(alive(id))error(e);}finally{if(alive(id)){busy=false;$('#shareOpen').disabled=false;}}
  };
 };
 function showFiles(snapshot,original,password){
  const id=panel(names[snapshot.provider]+' · 分享文件','<div class="share-breadcrumb"><button id="shareBack" class="quiet">'+icon('left')+'返回链接</button><button id="shareParent" class="quiet">上一级</button><span id="sharePath"></span></div><label class="share-select"><input id="shareSelectAll" type="checkbox">选择本页</label><div id="shareFiles" class="share-files"></div><div class="share-pagination"><button id="sharePrev" class="quiet">上一页</button><span id="sharePage"></span><button id="shareNext" class="quiet">下一页</button></div>'+errorHtml+'<div class="link-actions"><span id="shareCount"></span><button id="shareDownload" class="primary">'+icon('download')+'下载所选</button></div>');
  let selected=new Set(),busy=false;
  function selection(){const count=selected.size;$('#shareCount').textContent=count?'已选择 '+count+' 项':'选择文件或文件夹';$('#shareDownload').disabled=!count||busy;$('#shareSelectAll').checked=count>0&&count===snapshot.files.length;$('#shareSelectAll').indeterminate=count>0&&count<snapshot.files.length;}
  async function browse(path,page){if(busy)return;busy=true;render();try{const r=await api('links_list',{session:snapshot.session,path,page});if(!alive(id))return;snapshot=r;selected.clear();}catch(e){if(alive(id))error(e);}finally{if(alive(id)){busy=false;render();}}}
  function render(){
   $('#sharePath').textContent=snapshot.path;$('#sharePath').title=snapshot.path;
   $('#shareParent').disabled=busy||snapshot.path==='/';$('#sharePrev').disabled=busy||!snapshot.previous;$('#shareNext').disabled=busy||!snapshot.next;$('#shareSelectAll').disabled=busy||!snapshot.files.length;
   $('#sharePage').textContent='第 '+(snapshot.page+1)+' 页 · '+snapshot.files.length+' 项';const list=$('#shareFiles');list.replaceChildren();
   if(!snapshot.files.length){const p=document.createElement('p');p.textContent='此目录没有文件';list.append(p);}
   snapshot.files.forEach(file=>{
    const row=document.createElement('div');row.className='share-file';
    const check=document.createElement('input');check.type='checkbox';check.checked=selected.has(file.id);check.disabled=busy;check.setAttribute('aria-label','选择 '+file.name);check.onchange=()=>{check.checked?selected.add(file.id):selected.delete(file.id);selection();};
    const b=document.createElement('button');b.className='share-file-name';b.disabled=busy;b.innerHTML=fileIcon(file)+'<span>'+esc(file.name)+'</span>';b.title=file.name;b.onclick=()=>{if(file.type==='folder')browse(file.path,0);else{check.checked=!check.checked;check.onchange();}};
    const size=document.createElement('small');size.textContent=file.type==='folder'?'文件夹':bytes(file.size);row.append(check,b,size);list.append(row);
   });selection();
  }
  $('#shareBack').onclick=()=>window.openShareWorkspace(original,password);
  $('#shareParent').onclick=()=>browse(snapshot.path.substring(0,snapshot.path.lastIndexOf('/'))||'/',0);
  $('#sharePrev').onclick=()=>browse(snapshot.path,snapshot.page-1);$('#shareNext').onclick=()=>browse(snapshot.path,snapshot.page+1);
  $('#shareSelectAll').onchange=e=>{selected=e.target.checked?new Set(snapshot.files.map(f=>f.id)):new Set();render();};
  $('#shareDownload').onclick=()=>downloadDialog(snapshot.files.filter(f=>selected.has(f.id)),{provider:snapshot.provider,session:snapshot.session,context:snapshot.context});render();
 }
 async function downloadDialog(files,source){
  if(!files.length)return;
  const id=panel('下载文件','<div class="download-summary">'+icon(files.length===1&&files[0].type==='folder'?'folder':'download')+'<div><strong id="downloadName"></strong><small id="downloadSummary" role="status">正在确认文件清单…</small></div></div><div class="download-location"><span>下载到</span><span id="downloadPath"></span><button id="downloadChoose" class="icon-button" aria-label="选择保存文件夹" title="选择保存文件夹" disabled>'+icon('folder')+'</button></div><label class="download-remember"><input id="downloadRemember" type="checkbox">设为默认下载位置</label>'+errorHtml+'<div class="link-actions"><button id="downloadCancel" class="quiet">取消</button><button id="downloadCommit" class="primary" disabled>开始下载</button></div>','download');
  $('#downloadName').textContent=files.length===1?files[0].name:files[0].name+' 等 '+files.length+' 项';let token=null,timer,submitted=false,pickId=null,committing=false;
  const native=window.chrome?.webview;
  function cleanup(){clearTimeout(timer);native?.removeEventListener('message',picked);if(token&&!submitted)api('download_cancel',{token}).catch(()=>{});}
  dialog.addEventListener('close',cleanup,{once:true});$('#downloadCancel').onclick=()=>dialog.close();
  function draw(row){$('#downloadSummary').textContent=row.state==='scanning'?'正在扫描 · 已发现 '+row.count+' 个文件':row.count+' 个文件 · '+bytes(row.bytes)+(row.folders?' · '+row.folders+' 个文件夹':'');$('#downloadPath').textContent=row.destination;$('#downloadPath').title=row.destination;$('#downloadChoose').disabled=row.state!=='ready';$('#downloadCommit').disabled=row.state!=='ready';if(row.error)error(Error(row.error));}
  async function poll(){try{const row=await api('download_status',{token});if(!alive(id))return;draw(row);if(row.state==='scanning')timer=setTimeout(poll,400);}catch(e){if(alive(id))error(e);}}
  async function picked(event){const d=event.data;if(!pickId||d?.id!==pickId)return;pickId=null;if(!alive(id))return;try{if(d.error)throw Error(d.error);if(!d.cancelled){const row=await api('download_destination',{token,selection:d.selection});if(alive(id))draw(row);}}catch(e){if(alive(id))error(e);}finally{if(alive(id)){ $('#downloadChoose').disabled=false;$('#downloadCommit').disabled=false;}}}
  native?.addEventListener('message',picked);
  $('#downloadChoose').onclick=()=>{if(!native){error(Error('请在桌面窗口中选择保存文件夹'));return;}pickId='download-'+(++requestSerial);$('#downloadChoose').disabled=true;$('#downloadCommit').disabled=true;native.postMessage({type:'pickDirectory',id:pickId});};
  $('#downloadCommit').onclick=async()=>{
   if(committing)return;committing=true;$('#downloadCommit').disabled=true;$('#downloadChoose').disabled=true;$('#downloadCancel').disabled=true;
   try{const r=await api('download_commit',{token,remember:$('#downloadRemember').checked});submitted=true;if(alive(id)){dialog.close();toast(r.count?'已加入 '+r.count+' 个下载任务':'目录已创建，所选文件已在任务列表中');await refreshState();}}
   catch(e){if(alive(id)){error(e);$('#downloadCancel').disabled=false;}}
  };
  try{const row=await api('download_prepare',{...source,ids:files.map(f=>f.id)});token=row.token;if(!alive(id)){cleanup();return;}draw(row);if(row.state==='scanning')timer=setTimeout(poll,100);}catch(e){if(alive(id))error(e);}
 }
 queue=async function(files){
  if(dialog.open||!files.length)return;
  try{if(state.view==='recent'){const first=files[0];await go(first.provider,first.parent);files=files.filter(x=>x.provider===first.provider&&x.parent===first.parent);}
   const p=providerState();if(!p?.connected||p.busy||p.path!==state.path)throw Error('请等待当前目录加载完成。');
   if(p.share)throw Error('请从“打开链接”重新选择分享文件。');
   await downloadDialog(files,{provider:state.provider,context:p.context});
  }catch(e){report(e);}
 };
 $('#share').onclick=()=>window.openShareWorkspace();
 const previous=updateLiveControls;updateLiveControls=function(){previous();$('#share').hidden=true;};
 const entry=document.createElement('button');entry.id='openLinks';entry.className='nav';entry.title='打开网盘链接（Ctrl + Shift + L）';entry.innerHTML=icon('link')+'<span>打开链接</span>';entry.onclick=()=>{if(!dialog.open)window.openShareWorkspace();};$('.side-fill').before(entry);
 document.addEventListener('keydown',e=>{if(e.ctrlKey&&e.shiftKey&&e.key.toLowerCase()==='l'&&!dialog.open){e.preventDefault();window.openShareWorkspace();}});updateLiveControls();
})();
