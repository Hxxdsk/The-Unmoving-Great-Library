// Exercise the real service worker's window/tab policy without OS focus changes.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const hook = () => ({listeners: [], addListener(fn) {this.listeners.push(fn);}});
const tabs = new Map(), calls = [], commands = [];
let seq = 0;
const chrome = {
  runtime: {onMessage: hook()},
  debugger: {
    onEvent: hook(), onDetach: hook(),
    async getTargets() {return [...tabs.values()].map(t => ({tabId:t.id,type:'page',id:'target-'+t.id}));},
    async attach() {}, async detach() {},
    async sendCommand(target, method, params) {commands.push({target,method,params});return {};}
  },
  windows: {
    async create(options) {
      calls.push({op:'window-create',options});
      assert.equal(options.state,'maximized'); assert.equal(options.focused,false);
      assert.equal(options.width,undefined); assert.equal(options.height,undefined);
      const tab={id:++seq,windowId:100,url:options.url};tabs.set(tab.id,tab);
      return {id:100,tabs:[tab]};
    },
    async get(id) {assert.equal(id,100);return {id};},
    async update(id,options) {
      assert.equal(id,100,'ordinary windows must never be resized or focused');
      assert.equal(options.state,'maximized');assert.equal(options.focused,false);assert.equal(Object.keys(options).sort().join(','),'focused,state');
      calls.push({op:'window-update',id});return {id,...options};
    }
  },
  tabs: {
    onCreated: hook(), onRemoved: hook(), onUpdated: hook(),
    async create(options) {
      assert.equal(options.active,false);assert.equal(options.windowId,100);
      calls.push({op:'tab-create',options});
      const tab={id:++seq,windowId:100,url:options.url};tabs.set(tab.id,tab);return tab;
    },
    async move(id,options) {
      assert.equal(options.windowId,100);assert.equal(options.index,-1);
      calls.push({op:'tab-move',id});const tab=tabs.get(id);tab.windowId=100;return tab;
    },
    async get(id) {return tabs.get(id);}, async remove(id) {tabs.delete(id);}
  }
};
const context={chrome,console,URL,WebSocket:{OPEN:1},navigator:{userAgent:'Chrome/147'},setTimeout,clearTimeout,setInterval,clearInterval};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
(async()=>{
  const first=await vm.runInContext('create()',context);
  await vm.runInContext('create("https://fxg.jinritemai.com/ffa/g/create")',context);
  const popup={id:++seq,windowId:200,openerTabId:first.id,url:'https://fxg.jinritemai.com/ffa/g/create'};
  tabs.set(popup.id,popup);chrome.tabs.onCreated.listeners[0](popup);
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(calls.filter(c=>c.op==='tab-move'&&c.id===popup.id).length,1);
  const before=calls.length;
  chrome.tabs.onCreated.listeners[0]({id:999,windowId:500,url:'https://fxg.jinritemai.com/'});
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(calls.length,before,'ordinary tab must remain untouched');
  await vm.runInContext(`attach(${first.id})`,context);
  const commandsBefore=commands.length;
  await vm.runInContext(`command({method:'Page.bringToFront',sessionId:'library-tab-${first.id}'})`,context);
  await vm.runInContext(`command({method:'Target.activateTarget',params:{targetId:'target-${first.id}'}})`,context);
  assert.equal(commands.length,commandsBefore,'focus commands must not be relayed');
  assert(commands.some(c=>c.method==='Emulation.setDeviceMetricsOverride'&&c.params.width===1600&&c.params.height===1000&&c.params.mobile===false));
  assert(commands.some(c=>c.method==='Emulation.setFocusEmulationEnabled'&&c.params.enabled));
  assert(commands.some(c=>c.method==='Page.addScriptToEvaluateOnNewDocument'&&c.params.source.includes('window.focus')));
  console.log('background window, inactive tabs, owned popup routing, focus suppression: passed');
})().catch(error=>{console.error(error);process.exit(1);});
