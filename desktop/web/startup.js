// Current-user Windows startup registration is handled only by the native host.
(()=>{
 const native=window.chrome?.webview;let startupState=null;
 function updateStartup(){const button=$('#startupSwitch'),note=$('#startupNote');if(!button)return;button.disabled=!native||!startupState;button.classList.toggle('on',!!startupState?.registered);button.setAttribute('aria-checked',String(!!startupState?.registered));if(startupState?.error)note.textContent=startupState.error;else if(startupState?.disabled&&startupState?.registered)note.textContent='已登记，但 Windows 已禁用。请在系统启动应用中开启。';else note.textContent=startupState?.registered?'登录 Windows 后在托盘启动；打开窗口才加载界面。':'开启后，登录 Windows 时在托盘启动云屿。';}
 native?.addEventListener('message',e=>{if(e.data?.type!=='startupState')return;if(e.data.error){startupState={...(startupState||{}),error:e.data.error};toast(e.data.error);}else startupState=e.data;updateStartup();});
 native?.addEventListener('message',e=>{if(e.data?.type==='diagnosticsSaved')toast('诊断报告已保存');});
 const previous=settings;
 settings=function(){
  previous();const body=$('#sheetBody'),oldRows=[...body.querySelectorAll('.setting-line')],exit=[...body.children].find(x=>x.textContent==='退出云屿');
  body.replaceChildren();
  function group(title,ids){const heading=document.createElement('h3');heading.className='setting-group-title';heading.textContent=title;body.append(heading);for(const id of ids){const row=oldRows.find(r=>r.querySelector('#'+id));if(row)body.append(row);}}
  group('运行与同步',['traySwitch']);
  const row=document.createElement('div');row.className='setting-line';row.innerHTML='<span>开机自启动<small id="startupNote">正在读取 Windows 设置…</small></span><button id="startupSwitch" class="switch" role="switch" aria-label="开机自启动" aria-checked="false" disabled></button>';body.append(row);
  const system=document.createElement('button');system.className='quiet';system.textContent='管理 Windows 启动应用';system.disabled=!native;system.onclick=()=>native.postMessage({type:'openWindowsStartup'});body.append(system);
  group('外观',['darkSwitch','motionSwitch']);group('传输与存储',['downloadLocation','completionSound','clearThumbs']);group('便捷操作',['clipboardSwitch']);
  const sound=$('#completionSound'),soundButton=document.createElement('button');let soundEnabled=sound.checked;
  soundButton.id='completionSound';soundButton.className='switch';soundButton.setAttribute('role','switch');soundButton.setAttribute('aria-label','传输完成提示音');
  function updateSound(){soundButton.classList.toggle('on',soundEnabled);soundButton.setAttribute('aria-checked',String(soundEnabled));}updateSound();sound.replaceWith(soundButton);
  soundButton.onclick=async()=>{soundButton.disabled=true;try{await api('sound',{value:!soundEnabled});soundEnabled=!soundEnabled;updateSound();}catch(e){report(e);}finally{soundButton.disabled=false;}};
  const release=document.createElement('div');release.className='setting-line';release.innerHTML='<span>关于云屿<small>0.6.4 · 更新测试版</small></span><button class="quiet" id="releaseNotes">更新说明</button>';body.append(release);
  $('#releaseNotes').onclick=()=>sheet('云屿 0.6.4', '<p>新增全盘文件名搜索、新建文件夹、重命名、移动和百度云端复制。支持拖入普通文件上传、F2 改名、Ctrl+Shift+N 新建文件夹。</p><p>新增图片与常见视频预览。下载在正常退出时保留断点，同步显示最近完成时间。夸克云端复制暂未接入；预览图片上限 32 MB、视频 128 MB，需完整载入。</p><p>本版为本地验证版本，尚未发布到更新通道。</p>');
  const diagnostics=document.createElement('div');diagnostics.className='setting-line';diagnostics.innerHTML='<span>诊断报告<small>只导出版本与状态统计，不含凭证、文件名和路径。</small></span><button class="quiet" id="exportDiagnostics">导出诊断…</button>';body.append(diagnostics);
  $('#exportDiagnostics').disabled=!native;$('#exportDiagnostics').onclick=()=>native.postMessage({type:'exportDiagnostics'});
  if(live?.migration_notice){const note=document.createElement('p');note.className='sync-callout';note.textContent='已从便携版导入数据。自动任务已暂停，请在同步与备份中核对位置后继续；原便携版仍保留，请使用安装版入口。';body.append(note);}
  const about=document.createElement('p');about.className='setting-about';about.textContent='云屿 0.6.4 · 文件夹 / 单文件同步、版本备份、真实传输与签名更新。关闭到托盘后继续运行；退出后停止。登录会话仅加密保存在本机。';body.append(about);if(exit)body.append(exit);
  $('#startupSwitch').onclick=()=>{const b=$('#startupSwitch');b.disabled=true;native?.postMessage({type:'setStartup',enabled:!startupState?.registered});};
  updateStartup();native?.postMessage({type:'startupStatus'});
 };$('#settings').onclick=settings;
})();
