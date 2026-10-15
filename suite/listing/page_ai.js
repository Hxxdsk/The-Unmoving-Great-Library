(root) => {
  const scope = root?.querySelectorAll ? root : document.body;
  const width=innerWidth, height=innerHeight;
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>3&&r.height>3&&r.bottom>0&&r.right>0&&r.x<width&&r.y<height&&s.display!=='none'&&s.visibility!=='hidden';};
  const text=e=>String(e.innerText||e.getAttribute('aria-label')||e.title||e.getAttribute('placeholder')||'').replace(/\s+/g,' ').trim().slice(0,140);
  const nodes=[...scope.querySelectorAll('button,a,[role=button],[role=menuitem],[role=tab],[role=combobox],[role=radio],label,h1,h2,h3,h4,span,div,input:not([type=password]):not([type=hidden]):not([type=file])')]
    .filter(e=>visible(e) && (e.matches('button,a,input,label,h1,h2,h3,h4,[role=button],[role=menuitem],[role=tab],[role=combobox],[role=radio]') ||
      ((!e.children.length || e.querySelector('svg')) && (text(e)||getComputedStyle(e).cursor==='pointer'))));
  const seen=new Set(), candidates=[];
  for(let e of nodes){
    e=e.closest('button,a,[role=button],[role=menuitem]')||e;
    if(seen.has(e)||!scope.contains(e)||!visible(e))continue;
    const r=e.getBoundingClientRect();
    if(r.width*r.height>width*height*.5)continue;
    seen.add(e);
    if(!e.dataset.glAiId)e.dataset.glAiId=crypto.randomUUID();
    candidates.push({id:String(candidates.length+1),selector:'[data-gl-ai-id="'+e.dataset.glAiId+'"]',
      text:text(e),role:e.getAttribute('role')||e.tagName.toLowerCase(),title:e.title||'',
      box:[r.x,r.y,r.width,r.height]});
    if(candidates.length>=160)break;
  }
  const masks=[...document.querySelectorAll('input,textarea,[contenteditable=true]')].filter(visible).map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height];});
  return {width,height,candidates,masks};
}
