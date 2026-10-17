/* One live theme update; the new appearance spreads from the pressed control. */
(() => {
  let desired=null, busy=false, origin=null;
  const reduced=()=>document.body.classList.contains('reduced')||matchMedia('(prefers-reduced-motion: reduce)').matches;
  function apply(dark){document.body.classList.toggle('dark',dark);try{localStorage.setItem('cloud-dock-design-dark',String(dark));}catch{}window.dispatchEvent(new CustomEvent('clouddock-theme',{detail:{dark}}));}
  async function toggle(event){
    const el=event?.currentTarget||document.querySelector('#theme');
    const rect=el?.getBoundingClientRect();
    origin=rect?{x:rect.left+rect.width/2,y:rect.top+rect.height/2}:{x:innerWidth-24,y:32};
    desired=!(desired===null?document.body.classList.contains('dark'):desired);
    if(busy)return;
    busy=true;
    try{
      while(document.body.classList.contains('dark')!==desired){
        const target=desired,point=origin;
        if(reduced()||!document.startViewTransition){apply(target);continue;}
        const root=document.documentElement;
        root.style.setProperty('--theme-x',`${point.x}px`);root.style.setProperty('--theme-y',`${point.y}px`);
        const radius=Math.hypot(Math.max(point.x,innerWidth-point.x),Math.max(point.y,innerHeight-point.y))+80;
        root.style.setProperty('--theme-radius',`${radius}px`);
        const transition=document.startViewTransition(()=>apply(target));
        try{
          await transition.ready;
          await root.animate({'--theme-radius':['0px',`${radius}px`]},
            {duration:620,easing:'cubic-bezier(.25,.7,.25,1)',fill:'both',pseudoElement:'::view-transition-new(root)'}).finished;
        }catch{transition.skipTransition();apply(target);}
        await transition.finished.catch(()=>{});
      }
    }finally{busy=false;desired=null;}
  }
  window.CloudDockTheme={toggle};
  window.addEventListener('clouddock-theme',()=>{
    const dark=document.body.classList.contains('dark');
    const button=document.querySelector('#theme');if(button){button.setAttribute('aria-label',dark?'切换为浅色外观':'切换为深色外观');button.title=dark?'切换为浅色外观':'切换为深色外观';}
    const sw=document.querySelector('#darkSwitch');if(sw){sw.classList.toggle('on',dark);sw.setAttribute('aria-checked',String(dark));}
  });
})();
