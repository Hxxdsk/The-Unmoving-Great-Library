// Test-only Chrome API stand-in. Runs the unchanged extension JS against real
// Chromium CDP; it does not load an extension or bypass machine extension policy.
const fs = require('node:fs'), vm = require('node:vm');
const [source, realEndpoint, port, token] = process.argv.slice(2);
const ws = new WebSocket(realEndpoint), pending = new Map();
const tabWindows = new Map(), workWindows = new Set();
const tabs = new Map(), roots = new Map(), rootTabs = new Map();
let sequence = 0, tabSequence = 0;
const hook = () => ({handlers: [], addListener(fn) {this.handlers.push(fn);}, emit(...args) {for (const fn of this.handlers) fn(...args);}});
const debuggerEvent = hook(), debuggerDetach = hook(), tabRemoved = hook(), tabUpdated = hook(), tabCreated = hook(), message = hook();
const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
  const id = ++sequence; pending.set(id, {resolve, reject});
  ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
});
ws.onmessage = incoming => {
  const value = JSON.parse(incoming.data);
  if (value.id) { const task = pending.get(value.id); if (!task) return;
    pending.delete(value.id); value.error ? task.reject(new Error(value.error.message)) : task.resolve(value.result); return; }
  if (value.sessionId && rootTabs.has(value.sessionId)) debuggerEvent.emit({tabId: rootTabs.get(value.sessionId)}, value.method, value.params);
  if (value.method === 'Target.targetDestroyed') {
    for (const [id, target] of tabs) if (target === value.params.targetId) {tabRemoved.emit(id); tabs.delete(id);}
  }
};
const chrome = {
  runtime: {onMessage: message},
  debugger: {async getTargets() { return [...tabs].map(([tabId, id]) => ({tabId, id, type: "page"})); }, onEvent: debuggerEvent, onDetach: debuggerDetach,
    async attach({tabId}) {const result = await call('Target.attachToTarget', {targetId: tabs.get(tabId), flatten: true}); roots.set(tabId, result.sessionId); rootTabs.set(result.sessionId, tabId);},
    async detach({tabId}) {const sessionId = roots.get(tabId); roots.delete(tabId); rootTabs.delete(sessionId); if (sessionId) await call('Target.detachFromTarget', {sessionId});},
    sendCommand(target, method, params) {return call(method, params, target.sessionId || roots.get(target.tabId));}},
  windows: {
    async create({url, type, focused, state}) {
      if(type !== 'normal' || focused !== false || state !== 'normal') throw new Error('work window must be ordinary and unfocused');
      const result=await call('Target.createTarget',{url,newWindow:true,background:true});
      const {windowId}=await call('Browser.getWindowForTarget',{targetId:result.targetId});
      workWindows.add(windowId);
      const id=++tabSequence; tabs.set(id,result.targetId);tabWindows.set(id,windowId);
      return {id:windowId,tabs:[{id,url,windowId}]};
    },
    async get(id) {if(![...tabWindows.values()].includes(id))throw new Error('window closed');return {id};},
    async update(id, options) {if(!workWindows.has(id)||options.state!=='normal'||options.focused)throw new Error('cannot activate or alter ordinary windows');return {id,state:'normal',focused:false,width:1600,height:1100};}
  },
  tabs: {onRemoved: tabRemoved, onUpdated: tabUpdated, onCreated: tabCreated,
    async create({url, windowId, active}) {if(windowId && active!==false)throw new Error('work tab must open inactive');const result = await call('Target.createTarget', {url, background:true, newWindow:!!windowId}); const id = ++tabSequence; tabs.set(id, result.targetId);tabWindows.set(id,windowId||1); return {id, url, windowId: windowId||1};},
    async get(id) {const result = await call('Target.getTargetInfo', {targetId: tabs.get(id)}); return {id, url: result.targetInfo.url, title: result.targetInfo.title, windowId: tabWindows.get(id)};},
    async remove(id) {await call('Target.closeTarget', {targetId: tabs.get(id)});}}
};
ws.onopen = async () => {
  await call('Target.setDiscoverTargets', {discover: true});
  // A normal tab exists before the extension connects and must stay inaccessible.
  await chrome.tabs.create({url: 'about:blank#ordinary-user-tab'});
  const context = {chrome, WebSocket, URL, navigator: {userAgent: 'Chrome/147.0.0.0'}, setTimeout, clearTimeout, setInterval, clearInterval, console};
  vm.createContext(context); vm.runInContext(fs.readFileSync(source, 'utf8'), context);
  const result = await new Promise(resolve => message.handlers[0]({type: 'connect', port: Number(port), token},
    {url: `http://127.0.0.1:${port}/greatlibrary/connect#${token}`, tab: {id: 1, windowId: 1, incognito: false}}, resolve));
  if (result.error) throw new Error(result.error);
  console.log('READY');
};
process.on('unhandledRejection', error => {console.error(error); process.exit(1);});
