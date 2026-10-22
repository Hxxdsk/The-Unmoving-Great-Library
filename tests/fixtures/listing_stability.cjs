const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const hook=()=>({listeners:[],addListener(fn){this.listeners.push(fn)}});
const tabs=new Map(), commands=[], attaches=new Map();let serial=0,windows=0,attaching=0,peakAttach=0;
const chrome={
 runtime:{onMessage:hook(),onStartup:hook(),onInstalled:hook()},
 storage:{local:{async get(){return {}},async set(){},async setAccessLevel(){}}},
 alarms:{onAlarm:hook(),create(){}},
 debugger:{onEvent:hook(),onDetach:hook(),
  async getTargets(){return [...tabs.values()].map(t=>({tabId:t.id,id:'target-'+t.id,type:'page'}))},
  async attach({tabId}){attaching++;peakAttach=Math.max(peakAttach,attaching);attaches.set(tabId,(attaches.get(tabId)||0)+1);await new Promise(r=>setTimeout(r,5));attaching--},
  async detach(){},async sendCommand(target,method,params){commands.push({target,method,params});return {}}},
 windows:{onFocusChanged:hook(),async get(id){return {id,focused:false,state:'normal'}},async create({url}){windows++;await new Promise(r=>setTimeout(r,5));const tab={id:++serial,url,windowId:100};tabs.set(tab.id,tab);return {id:100,tabs:[tab]}}},
 tabs:{onCreated:hook(),onRemoved:hook(),onUpdated:hook(),onActivated:hook(),
  async create({url,windowId,openerTabId}){const tab={id:++serial,url,windowId,openerTabId};tabs.set(tab.id,tab);return tab},
  async get(id){return tabs.get(id)},async update(id,options){Object.assign(tabs.get(id),options);return tabs.get(id)},async remove(id){tabs.delete(id)}}
};
const context={chrome,URL,console,WebSocket:{OPEN:1},navigator:{userAgent:'Chrome/147'},setTimeout,clearTimeout,setInterval,clearInterval};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
(async()=>{
 const pages=await vm.runInContext('Promise.all([create(),create(),create()])',context);
 assert.equal(windows,1,'concurrent startup must create one window');
 await Promise.all(Array.from({length:8},()=>vm.runInContext(`attach(${pages[0].id})`,context)));
 assert.equal(attaches.get(pages[0].id),1,'duplicate debugger attachment must coalesce');
 assert.equal(peakAttach,1);
 const id=pages[0].id, sid='library-tab-'+id;
 await vm.runInContext(`command({method:'GreatLibrary.parkSavedPage',sessionId:'${sid}'})`,context);
 assert.equal(vm.runInContext(`parked.has(${id})`,context),true);
 assert(commands.some(c=>c.method==='Page.setWebLifecycleState'&&c.params.state==='frozen'));
 assert.equal(tabs.get(id).autoDiscardable,true);
 const targets=await vm.runInContext(`command({method:'Target.getTargets'})`,context);
 assert(!targets.targetInfos.some(t=>t.targetId==='target-'+id),'parked pages must not reattach on later sessions');
 await vm.runInContext(`command({method:'Target.detachFromTarget',sessionId:'${sid}'})`,context);
 assert.equal(vm.runInContext(`parked.has(${id})`,context),true,'driver cleanup must not wake a saved page');
 chrome.tabs.onActivated.listeners[0]({tabId:id});await new Promise(r=>setTimeout(r,5));
 assert.equal(vm.runInContext(`parked.has(${id})`,context),false);
 assert.equal(tabs.get(id).autoDiscardable,false);
 assert(commands.some(c=>c.method==='Page.setWebLifecycleState'&&c.params.state==='active'));
 await vm.runInContext(`command({method:'GreatLibrary.parkSavedPage',sessionId:'${sid}'})`,context);
 await vm.runInContext(`command({method:'Runtime.evaluate',params:{expression:'1+1'},sessionId:'${sid}'})`,context);
 assert.equal(vm.runInContext(`parked.has(${id})`,context),false,'explicit automation must wake its page');
 const browserClient=await vm.runInContext(`command({method:'Target.attachToBrowserTarget'})`,context);
 const extra=await vm.runInContext(`command({method:'Target.attachToTarget',params:{targetId:'target-${id}'},sessionId:'${browserClient.sessionId}'})`,context);
 assert.notEqual(extra.sessionId,sid,'auxiliary CDP must never reuse the main page session');
 await vm.runInContext(`command({method:'GreatLibrary.parkSavedPage',sessionId:'${extra.sessionId}'})`,context);
 await vm.runInContext(`command({method:'Runtime.runIfWaitingForDebugger',sessionId:'${extra.sessionId}'})`,context);
 await vm.runInContext(`command({method:'Target.detachFromTarget',params:{sessionId:'${extra.sessionId}'},sessionId:'${browserClient.sessionId}'})`,context);
 assert.equal(vm.runInContext(`parked.has(${id})`,context),true);
 assert.equal(vm.runInContext(`attached.has(${id})`,context),true,'detaching an auxiliary session must preserve the main driver');
 await vm.runInContext('disconnect()',context);
 await vm.runInContext("command({method:'Target.setAutoAttach',params:{autoAttach:true}})",context);
 assert.equal(vm.runInContext(`attached.has(${id})`,context),false,'reconnection must leave saved pages idle');
 chrome.tabs.onActivated.listeners[0]({tabId:id});await new Promise(r=>setTimeout(r,15));
 assert.equal(vm.runInContext(`parked.has(${id})`,context),false,'a cold saved page must also wake on view');
 assert.equal(vm.runInContext(`attached.has(${id})`,context),true);
 console.log('creation queue, debugger deduplication, saved-page lifecycle and wake: passed');
})().catch(e=>{console.error(e);process.exit(1)});
