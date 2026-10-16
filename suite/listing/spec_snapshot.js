(label) => {
  const norm=s=>String(s||'').replace(/[\s*＊]/g,'').replace(/[⌄∨▼▾×✕]/g,'').trim();
  const shown=e=>{const r=e.getBoundingClientRect();if(r.width<=4||r.height<=4)return false;for(let n=e;n&&n!==document.body;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0)return false;}return true;};
  const headingMatch=(e,name)=>norm(e.innerText)===name||[...e.childNodes].some(n=>n.nodeType===3&&norm(n.textContent)===name);
  const headingNodes=[...document.querySelectorAll('label,span,div,p,legend,strong,h1,h2,h3,h4')];
  const mark=e=>{if(!e)return null;if(!e.dataset.glId)e.dataset.glId='gl'+crypto.randomUUID().replaceAll('-','');return '[data-gl-id="'+e.dataset.glId+'"]';};
  const headings=headingNodes.filter(e=>shown(e)&&headingMatch(e,norm(label))).sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
  const heading=headings.find(e=>!e.closest('aside,[role=dialog],[role=listbox]'));if(!heading)return {rows:[],reason:'未找到规格标题'};
  const start=heading.getBoundingClientRect().bottom;
  const stops=new Set(label==='颜色分类'?['尺码大小','尺码表','价格与库存','价格库存']:['尺码表','价格与库存','价格库存']);
  const end=Math.min(Infinity,...headingNodes.filter(e=>shown(e)&&!e.closest('aside,[role=dialog],[role=listbox]')&&[...stops].some(name=>headingMatch(e,name))&&e.getBoundingClientRect().top>start).map(e=>e.getBoundingClientRect().top));
  let field=heading.parentElement;
  while(field&&field!==document.body&&!field.querySelector('input[placeholder*=备注],textarea[placeholder*=备注]'))field=field.parentElement;
  field ||= document.body;
  const inBand=e=>{const r=e.getBoundingClientRect();return field.contains(e)&&!e.closest('aside,[role=dialog],[role=listbox]')&&shown(e)&&r.top>=start-4&&r.bottom<=end+4;};
  // Only actual remark fields are row anchors. Empty search boxes inside
  // dropdowns are not remarks, even if their DOM rectangles have a size.
  const noteCandidates=[...document.querySelectorAll('input,textarea')].filter(e=>inBand(e)&&!['hidden','file','checkbox','radio','password'].includes(e.type)&&!e.disabled&&!e.readOnly&&(
    /备注/.test([e.placeholder,e.getAttribute('aria-label')].join(' '))||norm(e.parentElement.innerText)==='备注'));
  const notes=[];
  noteCandidates.sort((a,b)=>{const x=a.getBoundingClientRect(),y=b.getBoundingClientRect();return Math.abs(x.y-y.y)>8?x.y-y.y:x.x-y.x;});
  for(const candidate of noteCandidates){
    const r=candidate.getBoundingClientRect();
    if(!notes.some(e=>{const p=e.getBoundingClientRect();return Math.abs(p.x-r.x)<4&&Math.abs(p.y-r.y)<4;}))notes.push(candidate);
  }
  const controls=[...document.querySelectorAll('select,input,[role=combobox],[role=button],button,span,div')].filter(inBand);
  const nameOf=e=>norm(e.tagName==='SELECT'?e.selectedOptions[0]?.textContent:e.getAttribute('aria-valuetext')||e.value||e.innerText||e.title);
  const valid=s=>s&&s.length<=40&&!/备注|请选择|请输入|添加规格|本地上传|图库选择|删除|自定义排序/.test(s);
  const rows=[];
  for(const note of notes){
    const nr=note.getBoundingClientRect(),cy=nr.y+nr.height/2;
    const left=controls.filter(e=>{const r=e.getBoundingClientRect();return e!==note&&!e.contains(note)&&r.right<=nr.left+4&&r.right>=nr.left-100&&r.width>=15&&r.width<=260&&r.height<=Math.max(60,nr.height*1.6)&&Math.abs(r.y+r.height/2-cy)<Math.max(r.height,nr.height)*.55;});
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
    const deletion=right.filter(isDelete).sort((a,b)=>b.getBoundingClientRect().x-a.getBoundingClientRect().x)[0];
    let photo=label==='颜色分类'?right.find(e=>!isDelete(e)&&!deletion?.contains(e)&&!e.contains(deletion)):null;
    if(photo){for(let p=photo.parentElement;p&&p!==document.body;p=p.parentElement){const r=p.getBoundingClientRect();if(r.width>85||r.height>85||p.contains(note)||p.contains(deletion))break;photo=p;}photo.dataset.glSpecPhoto='true';}
    const count=photo?[...photo.querySelectorAll('img'),...(photo.tagName==='IMG'?[photo]:[])].filter(e=>shown(e)&&!/\.svg|data:image\/svg/i.test(e.src||'')).length:[...row.querySelectorAll('img')].filter(e=>shown(e)&&e.getBoundingClientRect().width>=16).length;
    rows.push({name:valid(nameOf(control))?nameOf(control):'',row:mark(row),control:mark(control),note:mark(note),remove:mark(deletion),photo:mark(photo),photos:count});
  }
  let region=heading.parentElement;
  while(region&&region!==document.body&&rows.some(r=>!region.contains(document.querySelector(r.note))))region=region.parentElement;
  return {selector:mark(region||heading.parentElement),rows,reason:rows.length?'':'未匹配到备注与左侧规格控件',note_count:notes.length};
}
