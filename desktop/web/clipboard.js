// Receives validated share metadata from the native clipboard listener.
(()=>{
 const native=window.chrome?.webview;
 const banner=document.createElement('section');banner.className='clipboard-share';banner.hidden=true;banner.setAttribute('aria-label','剪贴板分享链接');
 const label=document.createElement('div');label.className='clipboard-description';
 const title=document.createElement('strong');title.setAttribute('role','status');
 const detail=document.createElement('small');label.append(title,detail);
 const open=document.createElement('button');open.className='primary';open.textContent='查看分享';
 const ignore=document.createElement('button');ignore.className='quiet';ignore.textContent='忽略';ignore.setAttribute('aria-label','忽略本次分享链接');
 banner.append(label,open,ignore);document.body.append(banner);let current=[];
 function clear(){banner.hidden=true;current=[];}
 ignore.onclick=clear;window.addEventListener('clouddock-clear-clipboard',clear);
 open.onclick=()=>{
  if(!current.length)return;
  if($('#sheet').open){toast('请先完成或关闭当前窗口');return;}
  const links=current.slice();clear();window.openShareWorkspace(links[0].url,links[0].password,links);
 };
 native?.addEventListener('message',event=>{
  const value=event.data;if(value?.type==='clipboardClear'){clear();return;}
  if(live?.clipboard_links===false)return;
  const links=value?.type==='clipboardShares'?value.links:value?.type==='clipboardShare'?[value]:[];
  if(!Array.isArray(links))return;
  const accepted=links.slice(0,20).filter(x=>['baidu','quark'].includes(x?.provider)&&typeof x.url==='string');if(!accepted.length)return;
  current=accepted;title.textContent=accepted.length>1?'检测到 '+accepted.length+' 个网盘分享':'检测到'+names[accepted[0].provider]+'分享';
  detail.textContent=accepted.length>1?'选择链接，查看内容后下载':(accepted[0].password?'提取码 '+accepted[0].password+' · ':'')+'在云屿中查看文件';banner.hidden=false;
 });
})();
