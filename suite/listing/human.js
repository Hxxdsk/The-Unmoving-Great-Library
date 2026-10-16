({token,mode,binding,stop=false}) => {
  window.__glHumanStop?.();
  if(stop)return;
  window.__glHumanLease=Date.now()+3000;
  const expired=()=>{if(Date.now()>window.__glHumanLease){window.__glHumanStop?.();return true;}return false;};
  const describe=e=>{
    e=e.closest('button,a,[role=button],[role=menuitem],[role=tab]')||e;
    if(!e.dataset.glHumanId)e.dataset.glHumanId=crypto.randomUUID();
    const attrs={};for(const name of ['id','data-testid','data-test-id','aria-label','title','role','name','type']){
      const v=e.getAttribute(name);if(v && v.length<140 && !/^(gl|rc_select_|:r)|[a-f0-9]{24,}/i.test(v))attrs[name]=v;
    }
    const parts=[];for(let n=e,i=0;n && n!==document.body && i<5;n=n.parentElement,i++){
      const siblings=[...n.parentElement.children].filter(x=>x.tagName===n.tagName);
      parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
    }
    return {tag:e.tagName.toLowerCase(),live:e.dataset.glHumanId,text:e.matches('input,textarea')?'':(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,140),attrs,css:parts.join(' > ')};
  };
  let lastHover='', picked=false;
  window.__glHumanQueue=[];
  const send=(kind,target)=>{const data={token,event_id:crypto.randomUUID(),kind,target,url:location.origin+location.pathname};window.__glHumanQueue.push(data);window[binding](data).catch(()=>{});};
  const hover=e=>{
    if(!e.isTrusted||expired())return;
    const target=e.target.closest('button,a,[role=button],[role=menuitem],[aria-haspopup]')||e.target;
    if(!target || (!target.matches('button,a,[role=button],[role=menuitem],[aria-haspopup]') && getComputedStyle(target).cursor!=='pointer'))return;
    const d=describe(target),key=JSON.stringify(d);
    if(key!==lastHover){lastHover=key;send('hover',d);}
  };
  const click=e=>{
    if(!e.isTrusted||expired())return;
    const d=describe(e.target),text=(d.text+' '+(d.attrs['aria-label']||'')).replace(/\s+/g,'');
    const forbidden=/发布|上架|下架|删除商品|批量删除/.test(text) || mode==='record' && /保存草稿/.test(text);
    if(mode==='pick'||forbidden){e.preventDefault();e.stopImmediatePropagation();}
    if(forbidden){send('blocked',d);return;}
    if(mode==='pick'&&picked)return;
    if(mode==='pick')picked=true;
    send('click',d);
  };
  document.addEventListener('pointerover',hover,true);
  document.addEventListener(mode==='pick'?'pointerdown':'click',click,true);
  const swallow=e=>{if(mode==='pick'&&e.isTrusted&&!expired()){e.preventDefault();e.stopImmediatePropagation();}};
  if(mode==='pick')document.addEventListener('click',swallow,true);
  const leaseTimer=setInterval(expired,500);
  window.__glHumanStop=()=>{clearInterval(leaseTimer);delete window.__glHumanLease;document.removeEventListener('pointerover',hover,true);document.removeEventListener(mode==='pick'?'pointerdown':'click',click,true);document.removeEventListener('click',swallow,true);delete window.__glHumanStop;};
}
