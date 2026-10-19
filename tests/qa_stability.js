(async()=>{
 const results={},delay=ms=>new Promise(r=>setTimeout(r,ms));
 const settle=async()=>{for(let i=0;i<100&&CloudDockTheme.busy;i++)await delay(40);if(CloudDockTheme.busy)throw Error('theme_busy_stuck');};
 try{
  $('#sheet').close();document.body.classList.remove('reduced');
  const initial=document.body.classList.contains('dark');let clicks=0;
  for(let burst=0;burst<5;burst++){for(let i=0;i<21;i++){$('#theme').click();clicks++;await delay(8);}await settle();}
  results.rapid_clicks=clicks;results.final_theme_matches_clicks=document.body.classList.contains('dark')!==initial;
  results.no_leftover_animations=document.getAnimations().filter(a=>a.effect?.pseudoElement?.includes('view-transition')).length===0;
  let animated=0;for(let i=0;i<8;i++){const started=performance.now();$('#theme').click();await settle();if(performance.now()-started>400)animated++;}
  results.sequential_switches=8;results.all_switches_still_animated=animated===8;results.theme_button_usable=!CloudDockTheme.busy;
  const savedApi=api;let resolvePreview;const calls=[];
  api=async(action,args)=>{calls.push([action,args]);if(action==='delete_preview')return await new Promise(r=>resolvePreview=r);return {};};
  const fixture={id:'qa-image',name:'测试文件.png',path:'/工作文件/测试文件.png',type:'image',size:100};Object.assign(live.providers.baidu,{connected:true,busy:false,share:false,path:'/工作文件',page:0,files:[fixture],context:'stability'});data.baidu['/工作文件']=[fixture];
  localGo('baidu','/工作文件');state.selected=new Set(['qa-image']);syncSelection();
  $('#deleteFiles').click();results.delete_immediate_feedback=!!$('#deleteStatus')&&$('#sheet').open;
  $('#sheet').close();resolvePreview({confirmation:'qa-delete',names:['测试文件']});await delay(50);
  results.closed_preview_never_deletes=!calls.some(x=>x[0]==='delete_confirm')&&!$('#sheet').open;
  api=savedApi;changeView('transfers');
 }catch(e){results.error=String(e);}
 window.qaStability=results;
})();
