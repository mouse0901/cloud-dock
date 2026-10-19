(async()=>{
 const r={},original=api,originalRefresh=refreshState,calls=[],delay=ms=>new Promise(x=>setTimeout(x,ms));
 try{
  $('#sheet').close();await delay(30);const changelog=await (await fetch(apiUrl('api/action'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'changelog'})})).json();r.changelog_all_versions=changelog.releases.length===19&&changelog.releases[0].version==='1.0.0';
  const files=[{id:'folder',name:'项目资料',path:'/项目资料',type:'folder',size:0},{id:'image',name:'分镜参考.png',path:'/分镜参考.png',type:'image',size:1024},{id:'file',name:'项目说明.pdf',path:'/项目说明.pdf',type:'pdf',size:2048}];
  for(const p of Object.values(live.providers))Object.assign(p,{connected:true,session:'qa-organize',busy:false,share:false,page:0,path:'/',context:'qa-context',files});
  const changes={rows:[{rel:'分镜/镜头清单.txt',action:'conflict',local:{dir:false,fp:{size:4096,mtime_ns:1700000000000000000}},remote:{dir:false,size:8192,revision:'v2'},remote_modified:'2026-10-18 14:21'},{rel:'素材/音乐.wav',action:'download',local:null,remote:{dir:false,size:4194304}},{rel:'交付/视频.mp4',action:'upload',local:{dir:false,fp:{size:12000000}},remote:null}],counts:{conflict:1,download:1,upload:1},at:Date.now()/1000,enabled:false};
  const rule={id:'a'.repeat(32),name:'动画项目',provider:'baidu',kind:'folder',mode:'two_way',enabled:false,local_root:'H:\\项目',remote_root:'/项目',status:'已暂停'};
  let crossState='ready';const cross=()=>({id:'copy',source:'baidu',target:'quark',path:'/目标',state:crossState,message:'确认后开始复制，源文件保留。',at:Date.now()/1000,count:1,bytes:2048,counts:{pending:1},progress:{},rows:[{name:'/项目说明.pdf',target:'/目标/项目说明.pdf',state:'pending',error:''}]});
  refreshState=async()=>{updateLiveControls();};api=async(action,d={})=>{calls.push([action,d]);
   if(action==='changelog')return changelog;
   if(action==='browse_view'||action==='browse_locate'){Object.assign(live.providers[d.provider],{path:d.path,page:d.page||0,context:'qa-context',files});return {path:d.path,page:d.page||0,files,context:'qa-context',session:d.session,selected:d.id};}
   if(action==='tools_browse')return {rows:[]};
   if(action==='tools_search')return {token:'search-'+d.provider};
   if(action==='tools_status')return {token:d.token,status:'completed',message:'搜索完成',rows:[{id:'file',name:'项目说明.pdf',parent:'/',path:'/项目说明.pdf',directory:false}]};
   if(action==='sync_changes')return changes;
   if(action==='sync_versions')return {rows:[{token:'restore-test',rel:'镜头清单.txt',size:4096,at:Date.now()/1000}],bytes:4096};
   if(action==='sync_restore_version')return {path:'H:\\项目\\镜头清单（恢复）.txt',name:'镜头清单（恢复）.txt'};
   if(action==='inbox_list')return {rows:[{id:'inbox1',url:'https://pan.quark.cn/s/11bbafa97c9b',provider:'quark',title:'参考资料',password:'abcd',at:Date.now()/1000,path:'/分镜',page:0}]};
   if(action==='cross_prepare'||action==='cross_status')return cross();
   if(action==='cross_start'){crossState='running';return cross();}
   if(action==='cross_pause'){crossState='paused';return cross();}
   if(action==='sharing_prepare')return {token:'share-confirm',provider:'baidu',kind:d.kind,names:['项目说明.pdf'],password:'a1b2',days:7};
   if(action==='sharing_commit')return {url:'https://pan.baidu.com/s/1abcdefghijk',password:'a1b2',days:7};
   return {};
  };
  await go('baidu','/');r.toolbar_removed=!$('#backupHere')&&!$('#newFolder')&&!$('#pinFolder');
  $('#fileList').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:600,clientY:450}));
  r.blank_context_actions=$('#contextMenu').textContent.includes('新建文件夹')&&$('#contextMenu').textContent.includes('固定当前位置')&&!$('#contextMenu').textContent.includes('备份');
  const before=document.activeElement;document.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));r.menu_keyboard=document.activeElement!==before&&document.activeElement.getAttribute('role')==='menuitem';
  [...$('#contextMenu').querySelectorAll('button')].find(b=>b.textContent.includes('新建文件夹')).click();r.mkdir_from_context=!!$('#toolName');$('#sheet').close();await delay(20);
  state.selected=new Set(['image','file']);syncSelection();showContext(files[1],800,650);r.multi_context_download=$('#contextMenu').textContent.includes('下载所选 2 项')&&$('#contextMenu').textContent.includes('批量重命名');r.menu_fits=$('#contextMenu').getBoundingClientRect().bottom<=innerHeight;closeMenus();
  cloudDockCrossCopy([files[2]]);await delay(40);r.cross_destination=!!$('#crossPolicy')&&!!$('#aggregateDestination');$('#aggregateDestination').click();await delay(60);r.cross_preview_before_write=!!$('#crossItems')&&!calls.some(x=>x[0]==='cross_start');$('#crossAction').click();await delay(40);r.cross_explicit_commit=calls.some(x=>x[0]==='cross_start');$('#crossAction').click();await delay(40);r.cross_pause=calls.some(x=>x[0]==='cross_pause');$('#sheet').close();await delay(20);
  cloudDockSearchAll();$('#aggregateTerm').value='项目';$('#aggregateSearch').dispatchEvent(new Event('submit',{cancelable:true}));await delay(80);r.search_both_drives=calls.filter(x=>x[0]==='tools_search').length===2&&$('#aggregateResults').textContent.includes('百度网盘')&&$('#aggregateResults').textContent.includes('夸克网盘');$('#sheet').close();await delay(30);r.search_retains_both=!$('#returnSearch').hidden;$('#returnSearch').click();r.search_reopens_both=$$('[data-aggregate-result]').length===2;$('#sheet').close();await delay(20);
  await cloudDockInbox();await delay(40);r.inbox_resume_and_edit=$('#inboxRows').textContent.includes('分镜')&&!!$('[data-inbox-edit]');$('[data-inbox-edit]').click();r.inbox_edit_password=$('#inboxPassword').value==='abcd';$('#sheet').close();await delay(20);
  cloudDockCreateShare([files[2]]);$('#createSharePreview').click();await delay(40);r.share_confirm_before_publish=!!$('#shareCommit')&&!calls.some(x=>x[0]==='sharing_commit');$('#shareCommit').click();await delay(40);r.share_result=!!$('#shareResultText')&&$('#shareResultText').value.includes('pwd=a1b2');$('#sheet').close();await delay(20);
  cloudDockSyncVersions(rule);await delay(50);$('[data-restore-version]').click();r.restore_explicit_confirmation=!!$('#restoreVersion')&&!calls.some(x=>x[0]==='sync_restore_version');$('#restoreVersion').click();await delay(40);r.restore_shows_copy_path=$('#restoreConfirmation').textContent.includes('恢复');$('#sheet').close();await delay(20);
  newSync();r.exclusion_templates=$$('.sync-exclusions button').length===3;$$('.sync-exclusions button')[0].click();r.template_editable=$('#syncExclude').value.includes('Intermediate');$('#sheet').close();await delay(20);
  settings();$('[data-settings-tab="about"]').click();r.settings_full_log=$('#releaseNotes').textContent==='完整更新日志';$('#releaseNotes').click();await delay(50);r.log_offline=$$('.release-entry').length===19;$('#releaseFilter').value='0.6.11';$('#releaseFilter').dispatchEvent(new Event('input'));r.log_search=$$('.release-entry').length===1;$('#sheet').close();await delay(20);
  await cloudDockSyncChanges(rule);$('#reviewKind').value='conflict';$('#reviewKind').dispatchEvent(new Event('change'));r.changes_filter=$$('#reviewRows tbody tr').length===1;$('#reviewKind').value='all';$('#reviewKind').dispatchEvent(new Event('change'));r.changes_table=$$('#reviewRows tbody tr').length===3;r.review_fits=$('#sheet').getBoundingClientRect().width<innerWidth;
 }catch(e){r.error=String(e);}finally{api=original;refreshState=originalRefresh;await delay(450);window.qaOrganize=r;}
})();
