/* One snapshot at a time; animate standard mask properties, not an inherited custom property. */
(() => {
  let desired=null, busy=false, origin=null, animationsEnabled=true;
  const reduced=()=>document.body.classList.contains('reduced')||matchMedia('(prefers-reduced-motion: reduce)').matches;
  function apply(dark){document.body.classList.toggle('dark',dark);try{localStorage.setItem('cloud-dock-design-dark',String(dark));}catch{}window.dispatchEvent(new CustomEvent('clouddock-theme',{detail:{dark}}));}
  async function reveal(target,point){
    if(reduced()||!animationsEnabled||!document.startViewTransition){apply(target);return;}
    const root=document.documentElement;
    const radius=Math.hypot(Math.max(point.x,innerWidth-point.x),Math.max(point.y,innerHeight-point.y))+100;
    let transition,animation,timer,expired=false;
    try{
      transition=document.startViewTransition(()=>apply(target));
      const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{expired=true;reject(new Error('theme_timeout'));},1800)});
      await Promise.race([(async()=>{
        await transition.ready;if(expired)return;
        animation=root.animate({maskSize:['0px 0px',`${radius*2}px ${radius*2}px`],
          maskPosition:[`${point.x}px ${point.y}px`,`${point.x-radius}px ${point.y-radius}px`]},
          {duration:520,easing:'cubic-bezier(.25,.7,.25,1)',pseudoElement:'::view-transition-new(root)'});
        await animation.finished;await transition.finished;
      })(),deadline]);
    }catch{animationsEnabled=false;transition?.skipTransition();apply(target);}
    finally{clearTimeout(timer);animation?.cancel();transition?.skipTransition();}
  }
  async function toggle(event){
    const rect=(event?.currentTarget||document.querySelector('#theme'))?.getBoundingClientRect();
    origin=rect?{x:rect.left+rect.width/2,y:rect.top+rect.height/2}:{x:innerWidth-24,y:32};
    desired=!(desired===null?document.body.classList.contains('dark'):desired);
    if(busy)return;busy=true;
    try{while(document.body.classList.contains('dark')!==desired)await reveal(desired,origin);}
    finally{busy=false;desired=null;}
  }
  window.CloudDockTheme={toggle,get busy(){return busy;}};
  window.addEventListener('clouddock-theme',()=>{
    const dark=document.body.classList.contains('dark');
    const button=document.querySelector('#theme');if(button){button.setAttribute('aria-label',dark?'切换为浅色外观':'切换为深色外观');button.title=dark?'切换为浅色外观':'切换为深色外观';}
    const sw=document.querySelector('#darkSwitch');if(sw){sw.classList.toggle('on',dark);sw.setAttribute('aria-checked',String(dark));}
  });
})();
