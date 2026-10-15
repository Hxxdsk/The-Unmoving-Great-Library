({op, label, selector}) => {
  const norm = s => String(s || '').replace(/[\s*＊]/g,'').replace(/：/g,':').trim();
  const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none');
  const mark = e => {
    if (!e) return null;
    if (!e.dataset.glId) e.dataset.glId = 'gl' + crypto.randomUUID().replaceAll('-','');
    return '[data-gl-id="' + e.dataset.glId + '"]';
  };
  const photos = root => [...root.querySelectorAll('img')].filter(e => visible(e) && e.getBoundingClientRect().width >= 35 && !/data:image\/svg|\.svg(?:\?|$)/i.test(e.getAttribute('src') || ''));
  const remove = root => [...root.querySelectorAll('button,[role=button],[class*=delete],[class*=Delete],[class*=remove],[data-icon=delete],.anticon-close-circle')]
    .find(e => visible(e) && (/删除|移除|清空/.test(e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || '') || /delete|Delete|remove|close-circle/.test(e.className?.baseVal || e.className || '') || e.getAttribute('data-icon') === 'delete'));
  if (op === 'section' || op === 'section_from') {
    const target = norm(label);
    const anchors=new Set(['主图','主图3:4','白底图','商品详情','水洗标/吊牌图','水洗标/吊牌图片','货号','颜色分类','尺码大小','价格与库存']);
    const labels = op === 'section_from' ? [document.querySelector(selector)].filter(Boolean) : [...document.querySelectorAll('label,span,div,p,h1,h2,h3,h4')].filter(e => visible(e) && (norm(e.innerText) === target || [...e.childNodes].some(n=>n.nodeType===3 && norm(n.textContent)===target)))
      .sort((a,b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
    for (const leaf of labels) {
      for (let e=leaf.parentElement, depth=0; e && e !== document.body && depth < 9; e=e.parentElement, depth++) {
        if(anchors.has(target) && [...e.querySelectorAll('label,span,div,p')].some(n=>visible(n) && anchors.has(norm(n.innerText)) && norm(n.innerText)!==target))break;
        if (e.querySelector('input,select,textarea,[role=combobox],table,[contenteditable=true],[class*=upload],[class*=Upload],[data-icon=picture],.anticon-picture') || photos(e).length ||
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
  if (op === 'remove_photo') {
    const image=photos(root)[0];
    for(let box=image?.parentElement;box && box!==root;box=box.parentElement){
      if(box.querySelector('select,[role=combobox],input[placeholder*=备注]'))break;
      const control=remove(box);
      if(control)return mark(control);
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
        const control=row.querySelector('select,[role=combobox],.ant-select');
        if (!control) continue;
        const selected=row.querySelector('.ant-select-selection-item,[class*=selection-item],select');
        let value=selected?.tagName === 'SELECT' ? selected.selectedOptions[0]?.textContent : selected?.innerText;
        if (!value && control.tagName === 'INPUT') value=control.value;
        if (!value) value=[...row.querySelectorAll('input')].find(e=> e !== note && !['file','hidden'].includes(e.type))?.value;
        value=String(value||'').replace(/×\s*$/,'').trim();
        if (value) result.push({name:value, row:mark(row), control:mark(control), note:mark(note), remove:mark(remove(row)), photos:photos(row).length});
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
    if (priceIndex<0 || stockIndex<0) return [];
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
