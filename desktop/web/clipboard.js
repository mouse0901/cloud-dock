// Receives only validated share metadata from the native clipboard listener.
(()=>{
 const native=window.chrome?.webview;
 const banner=document.createElement('section');banner.className='clipboard-share';banner.hidden=true;
 banner.setAttribute('aria-label','剪贴板分享链接');
 const label=document.createElement('div');label.className='clipboard-description';
 const title=document.createElement('strong');title.setAttribute('role','status');
 const detail=document.createElement('small');label.append(title,detail);
 const open=document.createElement('button');open.className='primary';open.textContent='打开分享';
 const ignore=document.createElement('button');ignore.className='quiet';ignore.textContent='忽略';ignore.setAttribute('aria-label','忽略本次分享链接');
 banner.append(label,open,ignore);document.body.append(banner);
 let current=null;
 function clear(){banner.hidden=true;current=null;}
 ignore.onclick=clear;window.addEventListener('clouddock-clear-clipboard',clear);
 open.onclick=()=>{
  const link=current;if(!link)return;clear();
  if(link.provider==='baidu'){
   native?.postMessage({type:'openShareExternal',url:link.url});return;
  }
  // Reuse the existing share form: code remains editable and errors stay visible.
  $('#share').onclick();$('#shareInput').value=link.url;$('#sharePassword').value=link.password;
  if(!live?.providers.quark.connected){
   const note=document.createElement('p');note.textContent='请先连接夸克账号，再打开此分享。链接已保留在这里。';
   const login=document.createElement('button');login.className='quiet';login.textContent='连接夸克账号';
   login.onclick=()=>{current=link;banner.hidden=false;$('#sheet').close();loginSheet('quark',true);};
   $('#sheetBody').prepend(note,login);
  }
 };
 native?.addEventListener('message',event=>{
  const value=event.data;
  if(value?.type==='clipboardClear'){clear();return;}
  if(value?.type!=='clipboardShare'||live?.clipboard_links===false)return;
  if(!['baidu','quark'].includes(value.provider)||typeof value.url!=='string')return;
  current=value;title.textContent='检测到'+names[value.provider]+'分享';
  detail.textContent=(value.password?'提取码 '+value.password+' · ':'')+(value.provider==='baidu'?'在官方分享页面打开':'在云屿中查看分享文件');
  banner.hidden=false;
 });
})();
