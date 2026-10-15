(label) => {
  const norm=s=>String(s||'').replace(/[\s*＊]/g,'').replace(/[⌄∨▼▾×✕]/g,'').trim();
  const shown=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>4&&r.height>4&&s.display!=='none'&&s.visibility!=='hidden';};
  const mark=e=>{if(!e)return null;if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id="'+e.dataset.glId+'"]';};
  const headings=[...document.querySelectorAll('label,span,div,p,h1,h2,h3,h4')].filter(e=>shown(e)&&norm(e.innerText)===norm(label)).sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
  const heading=headings[0];if(!heading)return {rows:[],reason:'未找到规格标题'};
  const start=heading.getBoundingClientRect().bottom;
  const stops=new Set(label==='颜色分类'?['尺码大小','尺码表','价格与库存','价格库存']:['尺码表','价格与库存','价格库存']);
  const end=Math.min(Infinity,...[...document.querySelectorAll('label,span,div,p,h1,h2,h3,h4')].filter(e=>shown(e)&&stops.has(norm(e.innerText))&&e.getBoundingClientRect().top>start).map(e=>e.getBoundingClientRect().top));
  const inBand=e=>{const r=e.getBoundingClientRect();return shown(e)&&r.top>=start-4&&r.bottom<=end+4;};
  const notes=[...document.querySelectorAll('input,textarea')].filter(e=>inBand(e)&&!['hidden','file','checkbox','radio','password'].includes(e.type)&&!e.disabled&&!e.readOnly&&(
    /备注/.test([e.placeholder,e.getAttribute('aria-label')].join(' '))||norm(e.parentElement.innerText)==='备注'||
    (!e.value&&!e.placeholder&&!e.closest('[role=combobox],[class*=select],[class*=Select]'))));
  const controls=[...document.querySelectorAll('select,input,[role=combobox],[role=button],button,span,div')].filter(inBand);
  const nameOf=e=>norm(e.tagName==='SELECT'?e.selectedOptions[0]?.textContent:e.getAttribute('aria-valuetext')||e.value||e.innerText||e.title);
  const valid=s=>s&&s.length<=40&&!/备注|请选择|请输入|添加规格|本地上传|图库选择|删除|自定义排序/.test(s);
  const rows=[];
  for(const note of notes){
    const nr=note.getBoundingClientRect(),cy=nr.y+nr.height/2;
    const left=controls.filter(e=>{const r=e.getBoundingClientRect();return e!==note&&!e.contains(note)&&r.right<=nr.left+4&&r.right>=nr.left-100&&r.width>=15&&r.width<=260&&Math.abs(r.y+r.height/2-cy)<Math.max(r.height,nr.height)*.55;});
    left.sort((a,b)=>{
      const score=e=>(valid(nameOf(e))?100:0)+(e.matches('select,input,[role=combobox]')?20:0)-Math.abs(nr.left-e.getBoundingClientRect().right)/10;
      return score(b)-score(a);
    });
    const control=left[0];if(!control)continue;
    let row=note.parentElement;
    while(row&&row!==document.body&&!row.contains(control)&&!notes.some(n=>n!==note&&row.contains(n)))row=row.parentElement;
    if(!row||notes.some(n=>n!==note&&row.contains(n)))row=note.parentElement;
    const right=[...document.querySelectorAll('svg,[role=img],img,button,[role=button],[data-icon]')].filter(e=>{
      if(!inBand(e))return false;const r=e.getBoundingClientRect();
      return r.x>=nr.right-4&&r.x<nr.right+125&&Math.abs(r.y+r.height/2-cy)<Math.max(r.height,nr.height)*.55&&r.width>=8&&r.width<=80;
    }).sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x);
    const isDelete=e=>/删除|移除|delete|trash|remove/i.test([e.getAttribute('aria-label'),e.title,e.getAttribute('data-icon'),e.className?.baseVal||e.className,e.innerText].join(' '));
    const deletion=right.find(isDelete);
    let photo=label==='颜色分类'?right.find(e=>!isDelete(e)&&!deletion?.contains(e)&&!e.contains(deletion)):null;
    if(photo){for(let p=photo.parentElement;p&&p!==document.body;p=p.parentElement){const r=p.getBoundingClientRect();if(r.width>85||r.height>85||p.contains(note)||p.contains(deletion))break;photo=p;}photo.dataset.glSpecPhoto='true';}
    const count=photo?[...photo.querySelectorAll('img'),...(photo.tagName==='IMG'?[photo]:[])].filter(e=>shown(e)&&!/\.svg|data:image\/svg/i.test(e.src||'')).length:[...row.querySelectorAll('img')].filter(e=>shown(e)&&e.getBoundingClientRect().width>=16).length;
    rows.push({name:valid(nameOf(control))?nameOf(control):'',row:mark(row),control:mark(control),note:mark(note),remove:mark(deletion),photo:mark(photo),photos:count});
  }
  let region=heading.parentElement;
  while(region&&region!==document.body&&rows.some(r=>!region.contains(document.querySelector(r.note))))region=region.parentElement;
  return {selector:mark(region||heading.parentElement),rows,reason:rows.length?'':'未匹配到备注与左侧规格控件',note_count:notes.length};
}
