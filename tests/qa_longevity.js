(async()=>{
 const r={},original=api,originalRefresh=refreshState,delay=ms=>new Promise(x=>setTimeout(x,ms)),calls=[];
 const real=async(action,d={})=>{const response=await fetch(apiUrl('api/action'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...d})});const value=await response.json();if(!response.ok||value.error)throw Error(value.error||'backend');return value;};
 try{
  $('#sheet').close();await delay(30);
  const saved=await real('settings_export');delete saved.ok;r.export_private_excluded=!JSON.stringify(saved).includes('download_root')&&!JSON.stringify(saved).includes('cookie');
  const preview=await real('settings_preview',{text:JSON.stringify({product:'Cloud Dock',schema:1,settings:{density:'compact',reduce_motion:true}})});r.preview_fields=preview.count===2;
  api=async(action,d={})=>{calls.push([action,d]);return real(action,d);};refreshState=async()=>{};
  await accounts();r.two_providers=$$('.profiles-group').length===2;r.default_profiles=$$('.profile-row').length===2;
  $('[data-profile-rename="default"]').click();$('#profileName').value='工作测试';$('#profileSave').click();await delay(250);r.rename_persisted=(await real('profiles_list')).providers.baidu.rows[0].name==='工作测试';
  // Exercise actual confirmation UI with isolated mock profiles; no QR/network or user account mutation.
  const profiles={providers:{baidu:{active:'default',rows:[{id:'default',name:'工作',verified:true},{id:'b'.repeat(32),name:'个人',verified:false}]},quark:{active:'default',rows:[{id:'default',name:'日常',verified:false}]}}};
  api=async(action,d={})=>{calls.push([action,d]);if(action==='profiles_list')return profiles;if(action==='profiles_switch'){profiles.providers[d.provider].active=d.id;return profiles;}if(action==='profiles_remove'){profiles.providers[d.provider].rows=profiles.providers[d.provider].rows.filter(x=>x.id!==d.id);return profiles;}return real(action,d);};
  await accounts();r.profile_nicknames=$('#sheet').textContent.includes('个人');$('[data-profile-switch="'+('b'.repeat(32))+'"]').click();r.switch_confirmation=!!$('#profileConfirm')&&$('#sheet').textContent.includes('同步规则会暂停');$('#profileCancel').click();await delay(30);r.cancel_no_switch=!calls.some(x=>x[0]==='profiles_switch');
  $('[data-profile-switch="'+('b'.repeat(32))+'"]').click();$('#profileConfirm').click();await delay(50);r.switch_applied=profiles.providers.baidu.active==='b'.repeat(32);
  $('[data-profile-remove="default"][data-provider-name="baidu"]').click();r.remove_preserves_notice=$('#sheet').textContent.includes('历史任务保留');$('#profileCancel').click();await delay(30);r.cancel_no_remove=!calls.some(x=>x[0]==='profiles_remove');
  $('[data-profile-add="quark"]').click();r.add_explains_switch=$('#sheet').textContent.includes('原账号仍保留');$('#profileBack').click();await delay(20);
  settings();r.settings_portability=!!$('#settingsExport')&&!!$('#settingsImport');$('[data-settings-tab="about"]').click();r.health_entry=!!$('#healthOpen');
  window.cloudDockImportPreview(preview);r.import_confirmation=!!$('#settingsApply')&&$('#sheet').textContent.includes('列表密度');$('#settingsApply').click();await delay(200);r.import_applied=(await real('settings_export')).settings.density==='compact';r.import_visible=document.body.dataset.density==='compact';
  await window.cloudDockHealth();await delay(150);r.health_cards=$$('.health-grid article').length===4;r.health_privacy_scope=$('#sheet').textContent.includes('不包含界面与 WebView2');r.recovery_status=!!$('#recoveryStatus')&&$('#recoveryStatus').textContent.includes('暂无恢复点');
  const back=await real('settings_preview',{text:JSON.stringify(saved)});await real('settings_apply',{token:back.token});
 }catch(e){r.error=e.message;}
 finally{api=original;refreshState=originalRefresh;window.qaLongevity=r;}
})();
