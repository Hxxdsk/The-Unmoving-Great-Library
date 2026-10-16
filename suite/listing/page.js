({op, label, selector}) => {
  const norm = s => String(s || '').replace(/[\s*＊]/g,'').replace(/：/g,':').trim();
  const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none');
  const mark = e => {
    if (!e) return null;
    if (!e.dataset.glId) e.dataset.glId = 'gl' + crypto.randomUUID().replaceAll('-','');
    return '[data-gl-id="' + e.dataset.glId + '"]';
  };
  const photos = root => {
    const notes=[...root.querySelectorAll('input[placeholder*=备注]')];
    const note=notes.length===1?notes[0]:null;
    return [...root.querySelectorAll('img')].filter(e=>{
      const r=e.getBoundingClientRect();
      const smallSpec=root.dataset.glSpecPhoto==='true'||note && r.x>=note.getBoundingClientRect().right-4;
      return !e.closest('[role=tooltip],.ant-popover,[class*=assistantContent],[class*=AssistantContent]') && visible(e) && r.width>=(smallSpec?16:35) && !/data:image\/svg|\.svg(?:\?|$)/i.test(e.getAttribute('src')||'');
    });
  };
  const remove = (root,photo=false) => [...root.querySelectorAll('button,[role=button],[class*=delete],[class*=Delete],[class*=remove],[data-icon=delete],.anticon-close-circle'+(photo?',[class*=close],[class*=Close],[data-icon*=close]':''))]
    .find(e => visible(e) && (/删除|移除|清空/.test(e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || '') || /delete|Delete|remove|close-circle/.test(e.className?.baseVal || e.className || '') || e.getAttribute('data-icon') === 'delete' || photo && (/close/i.test(e.className?.baseVal || e.className || '') || /^[×✕]$/.test(e.innerText || ''))));
  if (op === 'section' || op === 'section_from') {
    const target = norm(label);
    const anchors=new Set(['主图','主图3:4','白底图','商品详情','水洗标/吊牌图','水洗标/吊牌图片','货号','颜色分类','尺码大小','价格与库存','面料材质','填充物种类','品牌','重要属性','材质成分','商品主图','商品标题']);
    const specField=target==='颜色分类'||target==='尺码大小';
    const imageField=/^(主图|主图3:4|白底图|商品详情|水洗标\/吊牌图|水洗标\/吊牌图片)$/.test(target);
    const labels = op === 'section_from' ? [document.querySelector(selector)].filter(Boolean) : [...document.querySelectorAll('label,span,div,p,h1,h2,h3,h4')].filter(e => visible(e) && (norm(e.innerText) === target || [...e.childNodes].some(n=>n.nodeType===3 && norm(n.textContent)===target)))
      .sort((a,b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
    for (const leaf of labels) {
      for (let e=leaf.parentElement, depth=0; e && e !== document.body && depth < 9; e=e.parentElement, depth++) {
        if(anchors.has(target) && [...e.querySelectorAll('label,span,div,p')].some(n=>visible(n) && anchors.has(norm(n.innerText)) && norm(n.innerText)!==target))break;
        if(specField&&!e.querySelector('input[placeholder*=备注],select,[role=combobox],[class*=select],[class*=Select]'))continue;
        if (e.querySelector(imageField ? 'input[type=file],[class*=upload],[class*=Upload],[data-icon=picture],.anticon-picture' : 'input,select,textarea,[role=combobox],table,[contenteditable=true],[class*=upload],[class*=Upload],[data-icon=picture],.anticon-picture') || photos(e).length ||
            [...e.querySelectorAll('button,[role=button],span,div')].some(b => /^(上传图片|上传主图|上传辅助图|图片|本地上传|清空)$/.test(b.innerText.trim()))) {
          if (e.getBoundingClientRect().height > 2000 && depth > 2) break;
          return mark(e);
        }
      }
    }
    return null;
  }
  const root = selector ? document.querySelector(selector) : document.body;
  if (!root) return null;
  if(op==='color_spec_order'){
    const eligible=e=>visible(e)&&!e.closest('aside,[role=dialog],[role=listbox]');
    const headings=[...root.querySelectorAll('label,span,div,p,h3,h4')].filter(e=>eligible(e)&&/^(颜色分类|尺码大小)$/.test(norm(e.innerText)))
      .sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
    const color=headings.find(e=>norm(e.innerText)==='颜色分类');
    if(!color)return null;
    const r=color.getBoundingClientRect();
    const before=headings.filter(e=>norm(e.innerText)==='尺码大小'&&e.getBoundingClientRect().bottom<=r.top+2).length;
    // Only the color header's right-hand action, never a size or SKU row action.
    const moves=[...root.querySelectorAll('button,a,span,div,[role=button]')].filter(e=>{
      if(!eligible(e)||norm(e.innerText)!=='上移')return false;
      const b=e.getBoundingClientRect();
      return b.left>=r.right&&Math.abs(b.top+b.height/2-r.top-r.height/2)<=24;
    }).sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
    const up=moves[0];
    const disabled=up&&!!up.closest('[disabled],[aria-disabled=true],[class*=disabled],[class*=Disabled]');
    return {first:up?disabled:before===0,before,top:r.top+window.scrollY,move:up&&!disabled?mark(up):null};
  }
  if(op==='batch_price_stock'){
    const outside=e=>!e.closest('aside,[role=dialog],thead,tbody,[role=table] [role=row],[role=grid] [role=row]');
    const prices=[...root.querySelectorAll('input')].filter(e=>visible(e)&&outside(e)&&norm(e.placeholder||e.getAttribute('aria-label'))==='价格');
    const stocks=[...root.querySelectorAll('input')].filter(e=>visible(e)&&outside(e)&&/^(现货库存|请输入库存|库存)$/.test(norm(e.placeholder||e.getAttribute('aria-label'))));
    const matches=[];
    for(const price of prices){
      for(let box=price.parentElement,depth=0;box&&box!==document.body&&depth<12;box=box.parentElement,depth++){
        const stock=stocks.filter(e=>box.contains(e));
        const buttons=[...box.querySelectorAll('button,[role=button]')].filter(e=>visible(e)&&norm(e.innerText)==='批量设置');
        if(stock.length!==1||buttons.length!==1)continue;
        // Locate the toolbar without requiring native table headers. The site
        // can render its header and body in separate div-based components.
        let region=box;
        for(let p=box;p&&p!==document.body;p=p.parentElement){
          if(p.querySelector('table,[role=table],[role=grid]')||[...p.querySelectorAll('input')].filter(e=>visible(e)&&!box.contains(e)&&!e.closest('aside')).length>=2){region=p;break;}
        }
        matches.push({selector:mark(region),toolbar:mark(box),price:mark(price),stock:mark(stock[0]),button:mark(buttons[0])});break;
      }
    }
    return matches.length===1?matches[0]:null;
  }
  if(op==='interaction_guard'){
    const r=root.getBoundingClientRect();
    const points=[[.5,.5],[.2,.5],[.8,.5]];
    let blocker=null;
    for(const [fx,fy] of points){
      const x=r.x+r.width*fx,y=r.y+r.height*fy;
      if(x<0||y<0||x>=innerWidth||y>=innerHeight)continue;
      const hit=document.elementFromPoint(x,y);
      if(hit&&(root===hit||root.contains(hit)))return {point:{x:r.width*fx,y:r.height*fy}};
      blocker ||= hit;
    }
    const panel=blocker?.closest('aside[class*=sidePanel],aside[class*=preview],[class*=sidePanelOverlay]');
    if(!panel)return {blocked:true};
    const close=[...panel.querySelectorAll('button,[role=button],[aria-label],[title],[data-icon],svg,[class*=close],[class*=Close]')].filter(e=>{
      if(!visible(e))return false;
      const labels=[e.getAttribute('aria-label'),e.title,e.innerText].filter(Boolean).map(s=>s.trim());
      const text=labels.join(' ');
      const box=e.getBoundingClientRect();
      return box.width<=80&&box.height<=80&&(labels.some(s=>/^(关闭|收起|关闭预览|关闭消费者预览|×|✕|Close)$/i.test(s))||/close/i.test([e.className?.baseVal||e.className,e.getAttribute('data-icon')].join(' ')))&&!/删除|移除/.test(text);
    }).sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
    if(close.length)return {close:mark(close[0].closest('button,[role=button]')||close[0])};
    const label=[...document.querySelectorAll('label,span,div')].find(e=>visible(e)&&/^(消费者预览效果|消费者效果预览|消费者预览)$/.test(norm(e.innerText)));
    const group=label?.closest('label')||label?.parentElement;
    const toggle=group?.querySelector('[role=switch][aria-checked=true],input[type=checkbox]:checked');
    return toggle?{close:mark(toggle)}:{blocked:true,preview:true};
  }
  if (op === 'creation_upload') {
    // Detect the actual empty upload wizard, independently of a hovered menu
    // or unrelated AI recommendation cards elsewhere on the page.
    const next=[...root.querySelectorAll('button,[role=button]')].some(e=>visible(e)&&/^下一步.*生成商品/.test(norm(e.innerText)));
    if(!next)return null;
    const candidates=[...root.querySelectorAll('button,[role=button],a,label,span,div')]
      .filter(e=>visible(e)&&norm(e.innerText)==='本地上传')
      .sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
    const leaves=candidates.filter(e=>!candidates.some(child=>child!==e&&e.contains(child)));
    const targets=[...new Set(leaves.map(e=>e.closest('button,[role=button],a,label')||e))];
    return targets.length===1?mark(targets[0]):null;
  }
  if (op === 'hover_point') {
    const boundary=document.querySelector(label);
    if(!boundary||!boundary.contains(root))return null;
    const r=root.getBoundingClientRect();
    for(const [fx,fy] of [[.5,.5],[.25,.25],[.75,.25],[.25,.75],[.75,.75]]){
      const x=r.x+r.width*fx,y=r.y+r.height*fy;
      if(x<0||y<0||x>=innerWidth||y>=innerHeight)continue;
      const hit=document.elementFromPoint(x,y);
      for(let e=root;e&&boundary.contains(e);e=e.parentElement){
        if(e===boundary && (e.getBoundingClientRect().width>450||e.getBoundingClientRect().height>450))break;
        if(hit && e.contains(hit)){
          const box=e.getBoundingClientRect();
          return {selector:mark(e),x:x-box.x,y:y-box.y};
        }
        if(e===boundary)break;
      }
    }
    return null;
  }
  if(op==='assistant_close'){
    const r=root.getBoundingClientRect();
    const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    let box=hit?.closest('[class*=assistant],[class*=Assistant]');
    for(let i=0;box&&i<3;i++,box=box.parentElement){
      const controls=[...box.querySelectorAll('button,[role=button],[class*=close],[class*=Close]')].filter(e=>visible(e)&&(/^(关闭|收起|最小化|×|✕)$/.test((e.getAttribute('aria-label')||e.title||e.innerText||'').trim())||/close/i.test(e.className||'')));
      if(controls.length===1)return mark(controls[0]);
      if(box.getBoundingClientRect().width>innerWidth*.8)break;
    }
    return null;
  }
  if(op==='upload_menu'){
    const origin=root.getBoundingClientRect(), rows=[];
    for(const e of document.querySelectorAll('button,[role=menuitem],[role=button],span,div')){
      if(!visible(e)||norm(e.innerText)!=='本地上传'||e.querySelector('button,[role=menuitem],[role=button]'))continue;
      const r=e.getBoundingClientRect(), hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      if(!hit||!(e.contains(hit)||hit.contains(e)))continue;
      const distance=Math.hypot(r.x+r.width/2-(origin.x+origin.width/2),r.y+r.height/2-(origin.y+origin.height/2));
      if(distance<400)rows.push({e,distance});
    }
    rows.sort((a,b)=>a.distance-b.distance);
    return rows.length?mark(rows[0].e):null;
  }
  if (op === 'photos') return photos(root).map(mark);
  if (op === 'busy') return [...root.querySelectorAll('[aria-busy=true],[class*=uploading],[class*=Uploading],.ant-spin-spinning')].some(visible);
  if (op === 'input') {
    const inputs = [...root.querySelectorAll('input,textarea')].filter(e => visible(e) && !['hidden','file','radio','checkbox','password'].includes(e.type));
    return inputs.length === 1 ? mark(inputs[0]) : null;
  }
  if (op === 'file_inputs') return [...root.querySelectorAll('input[type=file]')].map(e => {
    let box=e.parentElement;
    for (let i=0; box?.parentElement && i<4; i++) {
      if (box.parentElement === root || box.parentElement.querySelectorAll('input[type=file]').length > 1) break;
      box=box.parentElement;
    }
    return {selector:mark(e), multiple:e.multiple, filled:photos(box || root).length > 0};
  });
  if (op === 'remove') return mark(remove(root));
  if (op === 'upload_trigger') {
    const candidates=[...root.querySelectorAll('[class*=upload],[class*=Upload],[data-icon=picture],.anticon-picture,[title*=上传],[aria-label*=上传]')]
      .filter(e=>visible(e) && e.getBoundingClientRect().width<=250 && e.getBoundingClientRect().height<=250)
      .sort((a,b)=>a.querySelectorAll('*').length-b.querySelectorAll('*').length);
    return mark(candidates[0]);
  }
  if(op==='spec_photo_trigger'){
    const note=root.querySelector('input[placeholder*=备注]');
    if(!note)return null;
    const edge=note.getBoundingClientRect().right;
    const deletion=remove(root);
    const candidates=[...root.querySelectorAll('[data-icon=picture],[data-icon=image],[class*=upload],[class*=Upload],[aria-label*=图],[title*=图],svg,[role=img]')]
      .filter(e=>{const r=e.getBoundingClientRect();return visible(e)&&r.width>=8&&r.height>=8&&r.x>=edge-4&&r.x<edge+110&&!(deletion&&(deletion.contains(e)||e.contains(deletion)))&&!/delete|remove|trash/i.test([e.className?.baseVal||e.className,e.getAttribute('data-icon'),e.getAttribute('aria-label')].join(' '));})
      .sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x);
    let target=candidates[0];
    if(!target)return null;
    for(let e=target.parentElement;e&&e!==root;e=e.parentElement){
      const r=e.getBoundingClientRect();
      if(r.x<edge-4||r.width>80||r.height>80||e.contains(note)||e.contains(deletion))break;
      target=e;
    }
    return mark(target);
  }
  if (op === 'remove_photo') {
    const image=photos(root)[0];
    for(let box=image?.parentElement;box && root.contains(box);box=box.parentElement){
      if(box.querySelector('select,[role=combobox],input[placeholder*=备注]'))break;
      const control=remove(box,true);
      if(control)return mark(control);
      if(box===root)break;
    }
    return null;
  }
  if (op === 'specs') {
    const result=[];
    for (const note of root.querySelectorAll('input[placeholder*=备注]')) {
      if (!visible(note)) continue;
      let row=note.parentElement;
      for (let i=0; row && i<6; row=row.parentElement,i++) {
        if (row.querySelectorAll('input[placeholder*=备注]').length !== 1) break;
        const control=row.querySelector('select,[role=combobox],[class*=select],[class*=Select]');
        if (!control) continue;
        const selected=row.querySelector('select,[class*=selection-item],[class*=selectionItem],[class*=selection-text],[class*=SelectionText],[class*=selected-value],[class*=selectedValue]');
        let value=selected?.tagName === 'SELECT' ? selected.selectedOptions[0]?.textContent : selected?.innerText;
        if (!value) value=control.getAttribute('aria-valuetext') || (control.tagName === 'INPUT' ? control.value : control.innerText);
        if (!value) value=[...control.querySelectorAll('span,div')].filter(visible).map(e=>e.innerText.trim()).find(v=>v&&!/请选择|请输入/.test(v));
        if (!value) value=[...row.querySelectorAll('input')].find(e=> e !== note && !['file','hidden'].includes(e.type))?.value;
        value=String(value||'').replace(/×\s*$/,'').trim();
        if (value && !/^(请选择|请输入)/.test(value)) result.push({name:value, row:mark(row), control:mark(control), note:mark(note), remove:mark(remove(row)), photos:photos(row).length});
        break;
      }
    }
    return result;
  }
  if (op === 'recommendations') {
    const heading=[...root.querySelectorAll('span,label,div')].find(e=>visible(e) && norm(e.innerText)==='AI推荐');
    if(!heading)return [];
    for(let box=heading.parentElement,i=0;box && i<4;box=box.parentElement,i++){
      const forbidden=/AI推荐|换一批|请输入|确认信息|下一步|继续生成|发布商品|立即发布|确认发布/;
      let cards=[...box.querySelectorAll('button,[role=button],[class*=recommend],[class*=Recommend]')]
        .filter(e=>visible(e) && !e.querySelector('button,[role=button]') && e.innerText.trim().length>=6 && e.innerText.trim().length<=80 && !forbidden.test(e.innerText));
      if(!cards.length)cards=[...box.querySelectorAll('div,span,p')].filter(e=>visible(e) && !e.children.length && getComputedStyle(e).cursor==='pointer' && e.innerText.trim().length>=6 && e.innerText.trim().length<=80 && !forbidden.test(e.innerText));
      if(cards.length)return cards.map(e=>({text:e.innerText.trim(),selector:mark(e)}));
    }
    return [];
  }
  if (op === 'sku_rows') {
    const headers=[...root.querySelectorAll('thead th,[role=columnheader]')].map(e=>norm(e.innerText));
    const priceIndex=headers.findIndex(s=>/^价格/.test(s));
    const stockIndex=headers.findIndex(s=>/^现货库存/.test(s));
    if (priceIndex<0 || stockIndex<0 || !root.querySelector('tbody tr')) {
      const batch=[...root.querySelectorAll('button,[role=button]')].find(e=>visible(e)&&norm(e.innerText)==='批量设置');
      let toolbar=batch?.parentElement;
      while(toolbar&&toolbar!==root&&!toolbar.querySelector('input[placeholder="价格"]'))toolbar=toolbar.parentElement;
      const inputs=[...root.querySelectorAll('input')].filter(e=>visible(e)&&!e.disabled&&!e.readOnly&&!['hidden','checkbox','radio','file'].includes(e.type)&&!e.closest('aside')&&!toolbar?.contains(e));
      const result=[],used=new Set();
      for(const price of inputs){
        if(used.has(price)||!/^请输入|^价格$/.test(norm(price.placeholder)))continue;
        const r=price.getBoundingClientRect();
        const stock=inputs.filter(e=>e!==price&&!used.has(e)&&/^\d+(?:\.\d+)?$/.test(e.value||'')&&Math.abs(e.getBoundingClientRect().y-r.y)<10&&e.getBoundingClientRect().x>=r.right).sort((a,b)=>a.getBoundingClientRect().x-b.getBoundingClientRect().x)[0];
        if(!stock)continue;
        used.add(price);used.add(stock);
        result.push({row:mark(price.parentElement),price:price.value,stock:stock.value,stockCell:mark(stock.parentElement)});
      }
      return result;
    }
    const occupied=[], previous=[];
    return [...root.querySelectorAll('tbody tr')].filter(visible).map(row => {
      for(let i=0;i<occupied.length;i++)occupied[i]=Math.max(0,(occupied[i]||0)-1);
      const cells=[...previous];let column=0;
      for(const cell of row.cells){
        while(occupied[column]>0)column++;
        for(let i=0;i<cell.colSpan;i++){cells[column+i]=cell;previous[column+i]=cell;occupied[column+i]=cell.rowSpan;}
        column+=cell.colSpan;
      }
      const priceCell=cells[priceIndex], stockCell=cells[stockIndex];
      const price=priceCell?.querySelector('input')?.value ?? priceCell?.innerText;
      const visibleStock=stockCell?.innerText.match(/^\s*(\d+(?:\.\d+)?)/)?.[1];
      const stock=visibleStock ?? stockCell?.querySelector('input')?.value;
      return {row:mark(row), price:String(price||'').replace(/[^\d.]/g,''),stock:String(stock||'').replace(/[^\d.]/g,''),stockCell:mark(stockCell)};
    });
  }
  if (op === 'stock_mode') {
    let e=root;
    for(let i=0;e && i<5;e=e.parentElement,i++){
      if(e.querySelectorAll('input').length>1)break;
      if(/增/.test(e.innerText) && /减/.test(e.innerText))return mark(e);
    }
    return null;
  }
  return null;
}
