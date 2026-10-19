// Full-page link collections. Provider-owned files are never changed here.
(()=>{
 const linkNav=$('#linkInbox');linkNav.dataset.view='links';linkNav.onclick=()=>openView('links');
 const mineNav=document.createElement('button');mineNav.className='nav';mineNav.id='myShares';mineNav.dataset.view='myshares';mineNav.innerHTML=icon('link')+'<span>我的分享</span>';linkNav.after(mineNav);mineNav.onclick=()=>openView('myshares');
 let page=0,filter='',provider='all',minePage=0,mineProvider='all',serial=0,mineSerial=0,selected=new Set();
 const context=document.createElement('div');context.className='library-context';context.hidden=true;document.body.append(context);
 document.addEventListener('click',e=>{if(!e.target.closest('.library-context'))context.hidden=true;});
 document.addEventListener('keydown',e=>{if(e.key==='Escape')context.hidden=true;});
 function openView(view){context.hidden=true;if($('#sheet').open)$('#sheet').close();changeView(view);}
 window.cloudDockInbox=()=>openView('links');
 function hostFor(view){
  let host=$('#featureView');if(!host){host=document.createElement('div');host.id='featureView';$('.file-region').insertBefore(host,$('footer'));}
  host.classList.add('library-view');host.dataset.library=view;return host;
 }
 const oldRender=render;
 render=function(animate=false){
  oldRender(animate);
  context.hidden=true;
  if(!['links','myshares'].includes(state.view))return;
  const view=state.view,host=hostFor(view);
  $$('.nav').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  $('#viewTitle').textContent=view==='links'?'链接库':'我的分享';
  $('#locationLabel').textContent=view==='links'?'保存打开过的分享':'云屿创建过的分享';
  $('.commandbar').hidden=true;$('.table-head').hidden=true;$('#fileList').hidden=true;$('#transferList').hidden=true;
  $('#inspector').hidden=true;$('#search').parentElement.hidden=true;$('#info').hidden=true;$('.pager').hidden=true;
  $('#breadcrumbs').textContent=view==='links'?'链接保存在本机，并按 Windows 账号加密':'这里只管理在云屿创建的分享；原网盘中的其他分享暂不在此列出';
  $('#itemCount').textContent='';
  if(view==='links')buildLinks(host);else buildMine(host);
 };
 function buildLinks(host){
  host.innerHTML='<div class="library-heading"><div><h2>链接库</h2><p>打开过的百度、夸克分享自动记录在本机。保存记录不会转存资源。</p></div><button class="primary" id="libraryOpen">打开链接</button></div><div class="library-tools"><input id="librarySearch" type="search" placeholder="搜索名称、地址或备注" aria-label="搜索链接库"><select id="libraryProvider" aria-label="筛选网盘"><option value="all">所有网盘</option><option value="baidu">百度网盘</option><option value="quark">夸克网盘</option></select><button class="quiet" id="libraryRemoveSelected" hidden>移除所选</button><button class="quiet" id="libraryClear">清空全部…</button></div><p class="library-feedback" id="libraryFeedback" role="status" hidden></p><div class="library-layout"><div><div id="libraryRows" class="library-rows">正在读取…</div><div class="library-pages"><button class="quiet" id="libraryPrev">上一页</button><span id="libraryPage"></span><button class="quiet" id="libraryNext">下一页</button></div></div><aside id="libraryEditor" class="library-editor" hidden></aside></div>';
  $('#libraryOpen').onclick=()=>window.openShareWorkspace();
  $('#librarySearch').value=filter;$('#libraryProvider').value=provider;
  let timer;$('#librarySearch').oninput=e=>{filter=e.target.value;page=0;clearTimeout(timer);timer=setTimeout(loadLinks,180);};
  $('#libraryProvider').onchange=e=>{provider=e.target.value;page=0;loadLinks();};
  $('#libraryPrev').onclick=()=>{page=Math.max(0,page-1);loadLinks();};
  $('#libraryNext').onclick=()=>{page++;loadLinks();};
  $('#libraryRemoveSelected').onclick=()=>{
   sheet('移除所选链接',`<p>将从本机链接库移除 ${selected.size} 条记录。原分享和网盘文件不受影响。</p><div class="library-actions"><button class="quiet" id="libraryCancel">取消</button><button class="danger-button" id="libraryConfirm">移除记录</button></div>`);
   $('#libraryCancel').onclick=()=>$('#sheet').close();$('#libraryConfirm').onclick=async()=>{const b=$('#libraryConfirm');b.disabled=true;try{await api('inbox_remove_batch',{ids:[...selected]});selected.clear();$('#sheet').close();await loadLinks();feedback('已移除所选记录，可在 30 秒内撤销。',true);}catch(e){report(e);b.disabled=false;}};
  };
  $('#libraryClear').onclick=()=>{sheet('清空链接库','<p>将移除本机保存的全部链接记录，包括置顶内容。原分享和网盘文件不会变化。此操作无法撤销。</p><div class="library-actions"><button class="quiet" id="libraryCancel">取消</button><button class="danger-button" id="libraryConfirm">清空全部记录</button></div>');$('#libraryCancel').onclick=()=>$('#sheet').close();$('#libraryConfirm').onclick=async()=>{const b=$('#libraryConfirm');b.disabled=true;try{await api('inbox_clear');selected.clear();page=0;$('#sheet').close();await loadLinks();feedback('本机链接库已清空。');}catch(e){report(e);b.disabled=false;}};};
  loadLinks();
 }
 function feedback(message,undo=false){const box=$('#libraryFeedback');if(!box)return;box.hidden=false;box.replaceChildren(document.createTextNode(message));if(undo){const b=document.createElement('button');b.className='quiet';b.textContent='撤销';b.onclick=async()=>{try{await api('inbox_undo');box.hidden=true;loadLinks();}catch(e){report(e);}};box.append(b);}}
 async function loadLinks(){
  const view=state.view,s=++serial,box=$('#libraryRows');if(view!=='links'||!box)return;
  box.textContent='正在读取…';
  try{const data=await api('inbox_list',{page,query:filter,provider});if(s!==serial||state.view!=='links')return;
   if(!data.rows.length&&page>0&&data.total){page--;return loadLinks();}
   box.innerHTML=data.rows.length?data.rows.map(r=>`<article class="library-row" data-link-id="${esc(r.id)}"><label><input type="checkbox" data-link-select="${esc(r.id)}" ${selected.has(r.id)?'checked':''} aria-label="选择 ${esc(r.title)}"></label><div class="library-row-main"><strong>${r.pinned?'★ ':''}${esc(r.title)}</strong><small>${esc(names[r.provider]||'未知网盘')} · ${esc(r.status||'未检查')} · ${r.opened||0} 次打开</small><small title="${esc(r.url||'')}">${esc(r.url||'无法解密')}</small><small>${r.at?syncTime(r.at):''}${r.note?' · '+esc(r.note):''}</small></div><div class="library-row-actions">${r.unavailable?'':`<button class="quiet" data-link-open="${esc(r.id)}">打开</button><button class="quiet" data-link-edit="${esc(r.id)}">编辑</button><button class="quiet" data-link-copy="${esc(r.id)}">复制</button>`}<button class="quiet" data-link-remove="${esc(r.id)}">移除</button></div></article>`).join(''):'<div class="library-empty">还没有链接。打开一个分享后，它会自动出现在这里。</div>';
   $('#libraryPage').textContent=`第 ${page+1} 页 · 共 ${data.total} 条`;
   $('#libraryPrev').disabled=page===0;$('#libraryNext').disabled=(page+1)*50>=data.total;
   $('#libraryRemoveSelected').hidden=!selected.size;$('#libraryRemoveSelected').textContent=`移除所选 ${selected.size} 条`;
   const rowMap=new Map(data.rows.map(r=>[r.id,r]));
   box.querySelectorAll('[data-link-select]').forEach(b=>b.onchange=()=>{b.checked?selected.add(b.dataset.linkSelect):selected.delete(b.dataset.linkSelect);$('#libraryRemoveSelected').hidden=!selected.size;$('#libraryRemoveSelected').textContent=`移除所选 ${selected.size} 条`;});
   box.querySelectorAll('[data-link-open]').forEach(b=>b.onclick=()=>{const r=rowMap.get(b.dataset.linkOpen);window.openShareWorkspace(r.url,r.password,[],{inboxId:r.id,path:r.path,page:r.page});});
   box.querySelectorAll('[data-link-edit]').forEach(b=>b.onclick=()=>editLink(rowMap.get(b.dataset.linkEdit)));
   box.querySelectorAll('[data-link-copy]').forEach(b=>b.onclick=async()=>{const r=rowMap.get(b.dataset.linkCopy);try{await navigator.clipboard.writeText(r.url+(r.password?'?pwd='+r.password:''));toast('分享链接已复制');}catch(e){report(e);}});
   box.querySelectorAll('[data-link-remove]').forEach(b=>b.onclick=async()=>{try{await api('inbox_remove',{id:b.dataset.linkRemove});selected.delete(b.dataset.linkRemove);await loadLinks();feedback('已从本机链接库移除，可在 30 秒内撤销。',true);}catch(e){report(e);}});
   box.querySelectorAll('.library-row-main').forEach(b=>b.ondblclick=()=>{const r=rowMap.get(b.closest('[data-link-id]').dataset.linkId);if(!r.unavailable)window.openShareWorkspace(r.url,r.password,[],{inboxId:r.id,path:r.path,page:r.page});});
   box.querySelectorAll('[data-link-id]').forEach(row=>row.oncontextmenu=e=>{e.preventDefault();const r=rowMap.get(row.dataset.linkId);context.replaceChildren();for(const [label,selector] of [['打开分享','[data-link-open]'],['编辑','[data-link-edit]'],['复制链接','[data-link-copy]'],['移除记录','[data-link-remove]']]){const action=row.querySelector(selector);if(!action)continue;const b=document.createElement('button');b.textContent=label;b.onclick=()=>{context.hidden=true;action.click();};context.append(b);}if(!r.unavailable){const pin=document.createElement('button');pin.textContent=r.pinned?'取消置顶':'置顶';pin.onclick=async()=>{context.hidden=true;try{await api('inbox_pin',{id:r.id,pinned:!r.pinned});loadLinks();}catch(error){report(error);}};context.insertBefore(pin,context.lastChild);}context.hidden=false;context.style.left=Math.min(e.clientX,innerWidth-context.offsetWidth-12)+'px';context.style.top=Math.min(e.clientY,innerHeight-context.offsetHeight-12)+'px';});
  }catch(e){if(s===serial&&box)box.textContent=e.message;}
 }
 function editLink(r){const editor=$('#libraryEditor');if(!editor)return;editor.hidden=false;
  editor.innerHTML=`<div class="library-editor-head"><h3>编辑链接</h3><button class="quiet" id="libraryEditorClose">关闭</button></div><label>名称<input id="editLinkTitle" maxlength="200" value="${esc(r.title)}"></label><label>分享链接<input id="editLinkUrl" maxlength="2048" value="${esc(r.url)}"></label><label>提取码<input id="editLinkPassword" maxlength="8" value="${esc(r.password||'')}"></label><label>备注<textarea id="editLinkNote" maxlength="2000" rows="4">${esc(r.note||'')}</textarea></label><label>标签（用逗号分开）<input id="editLinkTags" maxlength="250" value="${esc((r.tags||[]).join(', '))}"></label><label class="library-pin"><input id="editLinkPin" type="checkbox" ${r.pinned?'checked':''}>置顶此链接</label><small>首次保存：${r.created_at?syncTime(r.created_at):'旧版记录'}<br>最近打开：${r.opened&&r.at?syncTime(r.at):'尚未打开'}<br>上次位置：${esc(r.path||'/')}</small><p id="editLinkError" class="sync-error" role="alert" hidden></p><div class="library-actions"><button class="primary" id="saveLinkEdit">保存修改</button></div>`;
  $('#libraryEditorClose').onclick=()=>editor.hidden=true;
  $('#saveLinkEdit').onclick=async()=>{const b=$('#saveLinkEdit');b.disabled=true;const values={id:r.id,text:$('#editLinkUrl').value,password:$('#editLinkPassword').value.trim(),title:$('#editLinkTitle').value,note:$('#editLinkNote').value,tags:$('#editLinkTags').value.split(',').map(x=>x.trim()).filter(Boolean),pinned:$('#editLinkPin').checked};try{await api('inbox_edit',values);editor.hidden=true;await loadLinks();feedback('链接记录已更新。');}catch(e){const box=$('#editLinkError');box.hidden=false;box.textContent=e.message;if(e.message.includes('已在链接库中')){const merge=document.createElement('button');merge.className='quiet';merge.textContent='合并到已有记录';merge.onclick=()=>{sheet('合并重复链接','<p>新地址已有记录。合并后只保留一条，打开次数相加，置顶和已有提取码保留。</p><div class="library-actions"><button class="quiet" id="mergeCancel">取消</button><button class="primary" id="mergeConfirm">确认合并</button></div>');$('#mergeCancel').onclick=()=>$('#sheet').close();$('#mergeConfirm').onclick=async()=>{const c=$('#mergeConfirm');c.disabled=true;try{await api('inbox_edit',{...values,merge:true});$('#sheet').close();editor.hidden=true;await loadLinks();feedback('两条链接记录已合并。');}catch(error){report(error);c.disabled=false;}};};box.append(merge);}b.disabled=false;}};
  $('#editLinkTitle').focus();
 }
 function buildMine(host){
  host.innerHTML='<div class="library-heading"><div><h2>我的分享</h2><p>这里保留你在云屿创建的分享，断开账号后也可查看和复制。</p></div><button class="quiet" id="refreshMyShares">刷新记录</button></div><div class="library-tools"><select id="mySharesProvider" aria-label="筛选网盘"><option value="all">所有网盘</option><option value="baidu">百度网盘</option><option value="quark">夸克网盘</option></select></div><p class="library-feedback">本页管理本机记录。取消分享及平台已创建的其他分享，请在对应网盘官方应用中操作。</p><div id="mySharesRows" class="library-rows">正在读取…</div><div class="library-pages"><button class="quiet" id="mySharesPrev">上一页</button><span id="mySharesPage"></span><button class="quiet" id="mySharesNext">下一页</button></div>';
  $('#mySharesProvider').value=mineProvider;$('#mySharesProvider').onchange=e=>{mineProvider=e.target.value;minePage=0;loadMine();};
  $('#refreshMyShares').onclick=loadMine;$('#mySharesPrev').onclick=()=>{minePage--;loadMine();};$('#mySharesNext').onclick=()=>{minePage++;loadMine();};loadMine();
 }
 async function loadMine(){const box=$('#mySharesRows'),request=++mineSerial;if(state.view!=='myshares'||!box)return;box.textContent='正在读取…';
  try{const data=await api('sharing_list',{page:minePage,provider:mineProvider});if(state.view!=='myshares'||request!==mineSerial)return;
   if(!data.rows.length&&minePage>0&&data.total){minePage--;return loadMine();}
   box.innerHTML=data.rows.length?data.rows.map(r=>`<article class="library-row"><div class="library-row-main"><strong>${esc(r.title)}</strong><small>${esc(names[r.provider]||'未知网盘')} · ${r.account_current?'当前连接账号 · ':''}${r.names?.length||0} 项 · ${r.expires_at&&r.expires_at<Date.now()/1000?'已过预计期限':r.days?r.days+' 天有效':'待核对'}</small><small>${esc((r.names||[]).join('、'))}</small><small>创建于 ${r.at?syncTime(r.at):'未知'} · ${esc(r.note||'无备注')}</small></div><div class="library-row-actions">${r.unavailable?'':`<button class="quiet" data-share-copy="${esc(r.id)}">复制链接</button><button class="quiet" data-share-edit="${esc(r.id)}">编辑记录</button>`}<button class="quiet" data-share-remove="${esc(r.id)}">移除记录</button></div></article>`).join(''):'<div class="library-empty">还没有通过云屿创建的分享。</div>';
   $('#mySharesPage').textContent=`第 ${minePage+1} 页 · 共 ${data.total} 条`;$('#mySharesPrev').disabled=minePage===0;$('#mySharesNext').disabled=(minePage+1)*50>=data.total;
   const map=new Map(data.rows.map(r=>[r.id,r]));
   box.querySelectorAll('[data-share-copy]').forEach(b=>b.onclick=async()=>{const r=map.get(b.dataset.shareCopy);try{await navigator.clipboard.writeText(r.url+(r.password?'?pwd='+r.password:''));toast('分享链接已复制');}catch(e){report(e);}});
   box.querySelectorAll('[data-share-edit]').forEach(b=>b.onclick=()=>{const r=map.get(b.dataset.shareEdit);sheet('编辑分享记录',`<div class="library-form"><label>名称<input id="myShareTitle" maxlength="200" value="${esc(r.title)}"></label><label>备注<textarea id="myShareNote" maxlength="2000" rows="4">${esc(r.note||'')}</textarea></label><p>这里只修改本机显示的名称和备注。</p><p id="myShareError" class="sync-error" hidden></p><div class="library-actions"><button class="quiet" id="myShareBack">取消</button><button class="primary" id="myShareSave">保存</button></div></div>`);$('#myShareBack').onclick=()=>$('#sheet').close();$('#myShareSave').onclick=async()=>{try{await api('sharing_edit_record',{id:r.id,title:$('#myShareTitle').value,note:$('#myShareNote').value});$('#sheet').close();loadMine();}catch(e){$('#myShareError').hidden=false;$('#myShareError').textContent=e.message;}};});
   box.querySelectorAll('[data-share-remove]').forEach(b=>b.onclick=()=>{const r=map.get(b.dataset.shareRemove);sheet('移除分享记录',`<p>只移除本机对“${esc(r.title)}”的记录。分享链接可能仍在网盘中有效；如需取消访问，请到官方应用操作。</p><div class="library-actions"><button class="quiet" id="myShareBack">取消</button><button class="danger-button" id="myShareDelete">移除本地记录</button></div>`);$('#myShareBack').onclick=()=>$('#sheet').close();$('#myShareDelete').onclick=async()=>{try{await api('sharing_remove_record',{id:r.id});$('#sheet').close();loadMine();}catch(e){report(e);}};});
  }catch(e){if(request===mineSerial&&state.view==='myshares')box.textContent=e.message;}
 }
 const priorSettings=settings;settings=function(){priorSettings();const run=$('[data-settings-panel="general"]');if(!run)return;const row=document.createElement('div');row.className='setting-line';row.innerHTML=`<span>自动记录打开的分享链接<small>只记录你主动打开的链接；关闭后保留已有记录</small></span><button id="linkAutoSave" class="switch ${live?.link_auto_save!==false?'on':''}" role="switch" aria-checked="${live?.link_auto_save!==false}"></button>`;run.append(row);$('#linkAutoSave').onclick=async()=>{const b=$('#linkAutoSave');b.disabled=true;try{const r=await api('inbox_preferences',{auto_save:b.getAttribute('aria-checked')!=='true'});live.link_auto_save=r.auto_save;b.setAttribute('aria-checked',String(r.auto_save));b.classList.toggle('on',r.auto_save);}catch(e){report(e);}finally{b.disabled=false;}};};$('#settings').onclick=settings;
})();
