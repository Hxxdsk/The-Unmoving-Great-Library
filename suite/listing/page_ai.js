(root) => {
  const scope = root?.querySelectorAll ? root : document.body;
  const width=innerWidth, height=innerHeight;
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>3&&r.height>3&&s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)!==0;};
  const text=e=>String(e.innerText||e.getAttribute('aria-label')||e.title||e.getAttribute('placeholder')||'').replace(/\s+/g,' ').trim().slice(0,140);
  const selectors='button,a,[role=button],[role=menuitem],[role=option],[role=tab],[role=combobox],[role=radio],label,h1,h2,h3,h4,span,div,input:not([type=password]):not([type=hidden]):not([type=file])';
  const overlays=[...document.querySelectorAll('[role=menu],[role=listbox],[role=dialog],[data-popper-placement],[class*=dropdown],[class*=popover],[class*=popper]')].filter(visible);
  const overlayNodes=overlays.flatMap(e=>[e,...e.querySelectorAll(selectors)]);
  // Portals appended to document.body must precede the busy product list.
  const nodes=[...overlayNodes,...scope.querySelectorAll(selectors)];
  const seen=new Set(), candidates=[];
  for(let e of nodes){
    if(!visible(e))continue;
    e=e.closest('button,a,[role=button],[role=menuitem],[role=option]')||e;
    const overlay=overlays.some(p=>p===e||p.contains(e));
    if(seen.has(e)||(!scope.contains(e)&&!overlay)||!visible(e))continue;
    if(!e.matches('button,a,input,label,h1,h2,h3,h4,[role]') && e.children.length && !e.querySelector('svg') && getComputedStyle(e).cursor!=='pointer')continue;
    const r=e.getBoundingClientRect();
    if(r.width*r.height>width*height*.5)continue;
    seen.add(e);
    if(!e.dataset.glAiId)e.dataset.glAiId=crypto.randomUUID();
    candidates.push({id:String(candidates.length+1),selector:'[data-gl-ai-id="'+e.dataset.glAiId+'"]',
      text:text(e),enabled:!e.matches(':disabled,[aria-disabled=true]'),role:e.getAttribute('role')||e.tagName.toLowerCase(),title:e.title||'',overlay,
      box:[r.x+scrollX,r.y+scrollY,r.width,r.height]});
  }
  const masks=[...document.querySelectorAll('input,textarea,[contenteditable=true]')].filter(visible).map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height];});
  return {width,height,candidates,masks};
}
