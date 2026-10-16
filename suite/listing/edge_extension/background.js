// A narrow CDP relay: expose only tabs created by this extension to Playwright.
let socket = null, heartbeat = null, autoAttach = false, discover = false;
const noWindowFocus = 'window.focus = () => {};';
let workWindow = null, epoch = null, creating = 0, detaching = Promise.resolve();
const owned = new Set(), attached = new Set(), children = new Map(), targets = new Map();
const tabForTarget = targetId => [...targets].find(([tabId, id]) => id === targetId)?.[0];
async function remember(tab) {
  let target;
  // New windows can be reported before their debugger target exists.
  for (let attempt = 0; attempt < 40; attempt++) {
    target = (await chrome.debugger.getTargets()).find(t => t.tabId === tab.id && t.type === "page");
    if (target) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!target) throw new Error("工作页尚未就绪，请重新连接");
  targets.set(tab.id, target.id);
  return tab;
}
const sid = tabId => `library-tab-${tabId}`;
const allowedUrl = url => url === 'about:blank' ||
  (url?.startsWith('https://fxg.jinritemai.com/') && new URL(url).hostname === 'fxg.jinritemai.com');
const send = value => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value)); };
const event = (method, params, sessionId) => send({method, params, relayEpoch: epoch, ...(sessionId ? {sessionId} : {})});
const info = tab => ({targetId: targets.get(tab.id), browserContextId: 'edge-default', type: 'page', title: tab.title || '',
  url: tab.url || tab.pendingUrl || 'about:blank', attached: attached.has(tab.id), canAccessOpener: false,
  ...(owned.has(tab.openerTabId) ? {openerId: targets.get(tab.openerTabId)} : {})});

async function keepBackgroundWindow() {
  if (workWindow == null) return;
  const window = await chrome.windows.get(workWindow);
  if (window.state !== 'maximized' || window.focused) {
    await chrome.windows.update(workWindow, {state: 'maximized', focused: false});
  }
}
async function attach(tabId) {
  if (!owned.has(tabId)) throw new Error('禁止控制用户原有标签页');
  if (attached.has(tabId)) return;
  await chrome.debugger.attach({tabId}, '1.3');
  attached.add(tabId);
  // Keep a full desktop layout even if OS visibility or display scaling changes.
  await chrome.debugger.sendCommand({tabId}, 'Emulation.setDeviceMetricsOverride', {width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false, screenWidth: 1600, screenHeight: 1000}).catch(() => {});
  await chrome.debugger.sendCommand({tabId}, 'Page.addScriptToEvaluateOnNewDocument', {source: noWindowFocus}).catch(() => {});
  await chrome.debugger.sendCommand({tabId}, 'Runtime.evaluate', {expression: noWindowFocus}).catch(() => {});
  await chrome.debugger.sendCommand({tabId}, 'Emulation.setFocusEmulationEnabled', {enabled: true}).catch(() => {});
  await chrome.debugger.sendCommand({tabId}, 'Page.setWebLifecycleState', {state: 'active'}).catch(() => {});
  event('Target.attachedToTarget', {sessionId: sid(tabId), targetInfo: info(await chrome.tabs.get(tabId)), waitingForDebugger: false});
}
async function create(url = 'about:blank') {
  if (!allowedUrl(url)) throw new Error('只允许新建抖店工作页');
  let tab;
  if (workWindow != null) {
    try { await chrome.windows.get(workWindow); }
    catch (_) { workWindow = null; }
  }
  creating++;
  try {
  if (workWindow == null) {
    // Render at full size behind the current window; do not minimize or focus.
    const window = await chrome.windows.create({url, type: 'normal', focused: false, state: 'maximized'});
    workWindow = window.id;
    tab = await remember(window.tabs[0]);
  } else {
    await keepBackgroundWindow();
    tab = await remember(await chrome.tabs.create({url, active: false, windowId: workWindow}));
  }
  } finally { creating--; }
  owned.add(tab.id);
  if (discover) event('Target.targetCreated', {targetInfo: info(tab)});
  if (autoAttach) await attach(tab.id);
  return tab;
}
async function disconnect() {
  autoAttach = discover = false;
  const tabs = [...attached];
  attached.clear(); children.clear();
  await Promise.all(tabs.map(tabId => chrome.debugger.detach({tabId}).catch(() => {})));
  // Keep operation tabs and all ordinary tabs open; never close the browser.
}
async function command(message) {
  const {method, params = {}, sessionId} = message;
  if (sessionId) {
    const tabId = sessionId.startsWith('library-tab-') ? Number(sessionId.slice(12)) : children.get(sessionId);
    if (!owned.has(tabId) || !attached.has(tabId)) throw new Error('操作页已断开，请重新连接 Edge');
    const tab = await chrome.tabs.get(tabId);
    let url = tab.url || tab.pendingUrl;
    if (!url) url = (await chrome.debugger.getTargets()).find(t => t.tabId === tabId)?.url;
    if (!allowedUrl(url)) throw new Error('请在操作页完成抖店登录后重试');
    if (method === 'Page.navigate' && !allowedUrl(params.url)) throw new Error('禁止跳转到抖店以外的网站');
    if (method === 'Page.bringToFront') return {}; // Never steal focus from ordinary browsing.
    const target = sessionId.startsWith('library-tab-') ? {tabId} : {tabId, sessionId};
    if (method === 'Page.enable' && !target.sessionId) {
      await chrome.debugger.sendCommand(target, 'Emulation.setFocusEmulationEnabled', {enabled: true}).catch(() => {});
    }
    return await chrome.debugger.sendCommand(target, method, params) || {};
  }
  switch (method) {
    case 'Browser.getVersion': return {protocolVersion: '1.3', product: 'Chrome/' + (navigator.userAgent.match(/(?:Chrome|Edg)\/([\d.]+)/)?.[1] || '130.0.0.0'), revision: '', userAgent: navigator.userAgent, jsVersion: ''};
    case 'Browser.setDownloadBehavior': return {};
    case 'Target.getBrowserContexts': return {browserContextIds: []};
    case 'Target.setDiscoverTargets':
      discover = params.discover;
      if (discover) for (const id of owned) event('Target.targetCreated', {targetInfo: info(await chrome.tabs.get(id))});
      return {};
    case 'Target.setAutoAttach':
      autoAttach = params.autoAttach;
      if (autoAttach) {
        if (!owned.size) await create('about:blank');
        for (const id of owned) await attach(id);
      }
      return {};
    case 'Target.getTargets': return {targetInfos: await Promise.all([...owned].map(async id => info(await chrome.tabs.get(id))))};
    case 'Target.getTargetInfo': {
      if (!params.targetId) return {targetInfo: {targetId: 'library-browser', type: 'browser', title: '', url: '', attached: true}};
      const id = tabForTarget(params.targetId);
      if (!owned.has(id)) throw new Error('只能读取工具工作页');
      return {targetInfo: info(await chrome.tabs.get(id))};
    }
    case 'Target.activateTarget': {
      if (!owned.has(tabForTarget(params.targetId))) throw new Error('只能操作工具工作页');
      return {}; // Logical page selection must never activate the OS window.
    }
    case 'Target.createTarget': return {targetId: targets.get((await create(params.url)).id)};
    case 'Target.attachToTarget': {
      const id = tabForTarget(params.targetId); await attach(id); return {sessionId: sid(id)};
    }
    case 'Target.detachFromTarget': return {};
    case 'Target.closeTarget': {
      const id = tabForTarget(params.targetId);
      if (!owned.has(id)) throw new Error('禁止关闭用户原有标签页');
      await chrome.tabs.remove(id); return {success: true};
    }
    default: throw new Error('连接扩展不支持此浏览器级操作：' + method);
  }
}
chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!owned.has(source.tabId) || !attached.has(source.tabId)) return;
  if (method === 'Target.attachedToTarget') children.set(params.sessionId, source.tabId);
  if (method === 'Target.detachedFromTarget') children.delete(params.sessionId);
  event(method, params, source.sessionId || sid(source.tabId));
});
chrome.debugger.onDetach.addListener((source, reason) => {
  if (attached.delete(source.tabId)) event('Target.detachedFromTarget', {sessionId: sid(source.tabId), targetId: targets.get(source.tabId)});
});
chrome.tabs.onRemoved.addListener(tabId => {
  if (!owned.delete(tabId)) return;
  if (attached.delete(tabId)) event('Target.detachedFromTarget', {sessionId: sid(tabId), targetId: targets.get(tabId)});
  event('Target.targetDestroyed', {targetId: targets.get(tabId)});
  targets.delete(tabId);
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (owned.has(tabId) && targets.has(tabId) && (change.url || change.title)) event('Target.targetInfoChanged', {targetInfo: info(tab)});
});
// Track popups opened by a tool tab, without attaching to unrelated user tabs.
chrome.tabs.onCreated.addListener(tab => {
  if (creating || (!owned.has(tab.openerTabId) && !(tab.windowId === workWindow && allowedUrl(tab.url || tab.pendingUrl)))) return;
  owned.add(tab.id);
  (async () => {
    // Move only the owned child tab; never resize or focus the user's window.
    try {
      if (workWindow != null && tab.windowId !== workWindow) {
        await chrome.tabs.move(tab.id, {windowId: workWindow, index: -1});
      }
      await keepBackgroundWindow();
    } catch (error) {
      console.warn('工作子页暂未恢复后台最大化：', error.message);
    }
    await remember(tab);
    if (discover) event('Target.targetCreated', {targetInfo: info(await chrome.tabs.get(tab.id))});
    if (autoAttach) await attach(tab.id);
  })().catch(error => console.warn('工作子页尚未就绪：', error.message));
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.type !== 'connect') return;
  (async () => {
    const url = new URL(sender.url);
    if (sender.tab?.incognito || url.hostname !== '127.0.0.1' || url.pathname !== '/greatlibrary/connect' ||
        url.protocol !== 'http:' || Number(url.port) !== message.port || !/^[a-f0-9]{64}$/.test(message.token) ||
        url.hash.slice(1) !== message.token) throw new Error('连接来源无效，请从工具箱点击连接 Edge');
    if (socket) { clearInterval(heartbeat); socket.onclose = null; socket.close(); await disconnect(); }
    socket = new WebSocket(`ws://127.0.0.1:${message.port}/extension/${message.token}`);
    await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = () => reject(new Error('工具箱未运行，请重新点击连接 Edge')); });
    socket.onmessage = async incoming => {
      const value = JSON.parse(incoming.data);
      if (value.type === 'detach') { epoch = null; detaching = disconnect(); await detaching; return; }
      await detaching;
      epoch = value.relayEpoch;
      try { send({id: value.id, result: await command(value), relayEpoch: value.relayEpoch, ...(value.sessionId ? {sessionId: value.sessionId} : {})}); }
      catch (error) { send({id: value.id, error: {code: -32000, message: error.message}, relayEpoch: value.relayEpoch, ...(value.sessionId ? {sessionId: value.sessionId} : {})}); }
    };
    socket.onclose = () => { clearInterval(heartbeat); disconnect(); socket = null; };
    heartbeat = setInterval(() => send({type: 'heartbeat'}), 20000);
    respond({ok: true});
  })().catch(error => respond({error: error.message}));
  return true;
});
