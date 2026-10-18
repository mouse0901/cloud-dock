(async()=>{
 const r={},pause=ms=>new Promise(ok=>setTimeout(ok,ms)),oldApi=api;
 try{
  $('#sheet').close();await pause(40);
  const calls=[];live.feedback={preferences:{download_sound:true,upload_sound:true,error_sound:false,sound_volume:30,download_notice:true,upload_notice:true,error_notice:true,sync_notice:true,desktop_notifications:true,notification_filenames:false,quiet_enabled:false,quiet_start:'22:00',quiet_end:'08:00'},items:[],unread:0};
  api=async(action,arg={})=>{calls.push([action,arg]);if(action==='feedback_preferences'){Object.assign(live.feedback.preferences,arg.preferences);return {preferences:live.feedback.preferences};}if(action==='feedback_read'){live.feedback.items.forEach(x=>x.read=true);live.feedback.unread=0;}if(action==='feedback_clear'){live.feedback.items=[];live.feedback.unread=0;}return {};};
  settings();$('[data-settings-tab="feedback"]').click();
  r.settings_category_visible=!$('[data-settings-panel="feedback"]').hidden;
  r.separate_controls=$$('[data-feedback-setting]').length===10&&!!$('#feedbackVolume');
  const toggle=$('[data-feedback-setting="download_sound"]');toggle.click();await pause(30);r.toggle_saves=toggle.getAttribute('aria-checked')==='false'&&calls.some(x=>x[0]==='feedback_preferences'&&x[1].preferences.download_sound===false);
  $('#feedbackVolume').value='22';$('#feedbackVolume').dispatchEvent(new Event('input'));$('#feedbackVolume').dispatchEvent(new Event('change'));await pause(30);r.volume_label=$('#volumeValue').textContent==='22%';
  $('[data-sound-preview="upload"]').click();
  r.no_old_system_sound_control=!$('#completionSound');
  r.settings_no_horizontal_overflow=$('#sheet').scrollWidth<=$('#sheet').clientWidth+1;
  $('[data-settings-tab="about"]').click();r.author_name=$('.author-card').textContent.includes('马蹄疾');r.author_link=$('.author-card').textContent.includes('space.bilibili.com/3546747290782061')&&!!$('#authorBilibili');
  window.qaFeedbackAboutReady=true;await pause(500);
  $('#sheet').close();await pause(30);live.providers.baidu={...live.providers.baidu,connected:true,busy:false,path:'/',context:'feedback-fixture',page:0,previous:false,next:false};
  data.baidu['/']=Array.from({length:40},(_,i)=>({id:'feedback-'+i,name:'项目资料 '+i,type:'folder',size:0,date:'2026-10-18'}));state.mode='grid';localGo('baidu','/');updateLiveControls();
  r.brand_block_removed=!$('.identity');r.shortcut_hint_removed=!$('.search kbd');r.one_page_controls_hidden=$('.pager').hidden;r.footer_hint_removed=!$('#footerHint').textContent.includes('双击');r.folder_type_not_repeated=$$('.file-size').every(el=>el.textContent==='');
  const f=currentFiles()[0];state.selected=new Set([f.id]);syncSelection();r.selection_footer=$('#footerHint').textContent.includes('已选择 1 项');state.selected.clear();syncSelection();
  live.providers.baidu.next=true;updateLiveControls();r.next_page_controls_visible=!$('.pager').hidden;live.providers.baidu.next=false;updateLiveControls();
  for(let i=0;i<16;i++)$(`[data-mode="${i%2?'grid':'list'}"]`).click();await pause(250);r.mode_switch_responsive=state.mode==='grid'&&!$('.file-list').hidden;
  $('#fileList').scrollTop=400;const scroll=$('#fileList').scrollTop;data.baidu['/子目录']=[];localGo('baidu','/子目录');localGo('baidu','/');r.scroll_restored=$('#fileList').scrollTop===scroll&&scroll>0;
  const event={id:'a'.repeat(24),at:Date.now()/1000,read:false,kind:'download',title:'3 项下载完成',provider:'baidu',view:'transfers',task:''};live.feedback.items=[event];live.feedback.unread=1;cloudDockFeedback.update();r.unread_badge=$('#notificationCount').textContent==='1';
  cloudDockFeedback.notify(event);r.actionable_toast=!$('.feedback-toast').hidden&&!!$('[data-feedback-open]');$('[data-feedback-close]').click();r.toast_dismissed=$('.feedback-toast').hidden;
  await cloudDockFeedback.showNotifications();r.history_visible=$$('.notification-row').length===1;r.history_marked_read=live.feedback.unread===0;
  $('#clearNotifications').click();await pause(30);r.history_clear=live.feedback.items.length===0;
  $('#sheet').close();await pause(40);live.feedback.preferences.download_sound=true;live.feedback.preferences.sound_volume=30;settings();$('[data-settings-tab="feedback"]').click();await pause(300);
  window.qaFeedback=r;
 }catch(e){r.error=String(e);window.qaFeedback=r;}finally{api=oldApi;}
})();
