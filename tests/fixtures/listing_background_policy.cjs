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
    onFocusChanged: hook(),
    async create(options) {
      calls.push({op:'window-create',options});
      assert.equal(options.state,'minimized'); assert.equal(options.focused,false);
      assert.equal(options.width,undefined); assert.equal(options.height,undefined);
      const tab={id:++seq,windowId:100,url:options.url};tabs.set(tab.id,tab);
      return {id:100,tabs:[tab]};
    },
    async get(id) {assert.equal(id,100);return {id};},
    async update() {throw new Error('must never restore or alter the work window');}
  },
  tabs: {
    onCreated: hook(), onRemoved: hook(), onUpdated: hook(),
    async create(options) {
      assert.equal(options.active,false);assert.equal(options.windowId,100);
      calls.push({op:'tab-create',options});
      const tab={id:++seq,windowId:100,url:options.url,openerTabId:options.openerTabId};tabs.set(tab.id,tab);return tab;
    },
    async update(id,options) {calls.push({op:"user-view-tab",id,options});return tabs.get(id);},
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
  const beforeBinding=calls.length;
  chrome.debugger.onEvent.listeners[0]({tabId:first.id},'Runtime.bindingCalled',{name:'__greatLibraryOpenBackground',payload:JSON.stringify({url:'https://fxg.jinritemai.com/ffa/g/create'})});
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(calls.slice(beforeBinding).filter(c=>c.op==='tab-create').length,1);
  assert.equal(calls.at(-1).options.openerTabId,first.id);
  assert(!calls.some(c=>c.op==='window-update'),'new tabs must not restore a minimized window');
  const pageCalls=[], listeners={};
  const pageWindow={open(...args){pageCalls.push({native:args});return {native:true};},__greatLibraryOpenBackground(payload){pageCalls.push(JSON.parse(payload));}};
  const policy=commands.find(c=>c.method==='Page.addScriptToEvaluateOnNewDocument').params.source;
  vm.runInNewContext(policy,{window:pageWindow,document:{addEventListener(name,fn){listeners[name]=fn;}},URL,location:{href:'https://fxg.jinritemai.com/ffa/g/list'}});
  pageWindow.open('/ffa/g/create','_blank').focus();
  assert.equal(pageCalls[0].url,'https://fxg.jinritemai.com/ffa/g/create');
  assert(!pageCalls.some(c=>c.native),'merchant popup must not use native window.open');
  let prevented=false;
  listeners.click({button:0,defaultPrevented:false,target:{closest(){return {href:'https://fxg.jinritemai.com/ffa/g/create?from=link',hasAttribute(){return false;}};}},preventDefault(){prevented=true;}});
  assert(prevented);assert.equal(pageCalls[1].url,'https://fxg.jinritemai.com/ffa/g/create?from=link');
  pageWindow.open('https://example.org/','_blank');assert(pageCalls.at(-1).native);
  const beforeUser=calls.length;
  chrome.windows.onFocusChanged.listeners[0](500);
  assert.equal(calls.length,beforeUser,'ordinary window focus must not change work tabs');
  chrome.windows.onFocusChanged.listeners[0](100);
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(calls.at(-1).op,'user-view-tab');assert.equal(calls.at(-1).options.active,true);
  console.log('background window, inactive tabs, owned popup routing, focus suppression: passed');
})().catch(error=>{console.error(error);process.exit(1);});
