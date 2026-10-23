const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const hook=()=>({listeners:[],addListener(fn){this.listeners.push(fn)}});
const stored={},sockets=[],reloaded=[];
let pairingTabs=[];
let online=true,created=0,hold=false;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function worker(){
  const chrome={runtime:{onMessage:hook(),onStartup:hook(),onInstalled:hook()},
    storage:{local:{async get(){return structuredClone(stored)},async set(data){Object.assign(stored,structuredClone(data))},async setAccessLevel(){}}},
    alarms:{onAlarm:hook(),create(){}},debugger:{onEvent:hook(),onDetach:hook(),async detach(){}},
    windows:{onFocusChanged:hook(),async get(){return {width:1920,height:1080,left:0,top:0}},async create(){created++;throw Error('reconnect must not create windows')}},
    tabs:{async reload(id){reloaded.push(id)},async query(options){assert.equal(options.url,'http://127.0.0.1/*');return pairingTabs},onCreated:hook(),onRemoved:hook(),onUpdated:hook(),async create(){created++;throw Error('reconnect must not create tabs')}}};
  class Socket{
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=0;sockets.push(this);queueMicrotask(()=>{
      if(hold)return;
      if(online){this.readyState=1;this.onopen?.()}else{this.onerror?.();this.close()}
    })}
    send(){}
    close(){if(this.readyState===3)return;this.readyState=3;queueMicrotask(()=>this.onclose?.())}
  }
  const timers=new Map();let timer=0;
  const context={chrome,URL,WebSocket:Socket,navigator:{userAgent:'Edge/test'},console,
    setTimeout(fn){const id=++timer;timers.set(id,fn);return id},clearTimeout(id){timers.delete(id)},setInterval(){return 1},clearInterval(){}};
  vm.createContext(context);vm.runInContext(source,context);
  return {chrome,context,timers};
}
(async()=>{
  const first=worker();await flush();assert.equal(sockets.length,0);
  const pair={type:'connect',port:54321,token:'a'.repeat(64)};
  const result=await new Promise(resolve=>first.chrome.runtime.onMessage.listeners[0](pair,
    {url:`http://127.0.0.1:54321/greatlibrary/connect#${pair.token}`,tab:{windowId:1}},resolve));
  assert(result.ok);assert.equal(stored.greatLibraryPairing.token,pair.token);assert.equal(sockets.length,1);
  // Alarm/manual calls with a live socket must not replace it or interrupt work.
  first.chrome.alarms.onAlarm.listeners[0]({name:'greatlibrary-reconnect'});await flush();assert.equal(sockets.length,1);
  online=false;sockets[0].close();await flush();
  assert(stored.greatLibraryPairing,'offline must not erase pairing');
  online=true;first.chrome.alarms.onAlarm.listeners[0]({name:'greatlibrary-reconnect'});await flush();await flush();
  assert.equal(sockets.length,2);assert.equal(sockets[1].readyState,1);
  // A fresh service worker/browser restores storage without a connection page.
  const second=worker();await flush();await flush();
  assert.equal(sockets.length,3);assert.equal(sockets[2].url,sockets[0].url);
  assert.equal(created,0,'reconnection must stay in background without creating pages');
  pairingTabs=[
    {id:11,url:`http://127.0.0.1:54321/greatlibrary/connect#${pair.token}`},
    {id:12,url:'http://127.0.0.1:54321/unrelated#'+pair.token},
    {id:13,url:'http://127.0.0.1:54321/greatlibrary/connect#invalid'},
    {id:14,incognito:true,url:`http://127.0.0.1:54321/greatlibrary/connect#${pair.token}`}
  ];
  second.chrome.runtime.onInstalled.listeners[0]({reason:'update'});
  await flush();await flush();
  assert.deepEqual(reloaded,[11],'update must recover only a valid non-private pairing page');
  assert.equal(sockets.length,3,'updating a live pair must preserve its connection');
  // A manual pair must not report success for an older in-flight endpoint.
  hold=true;
  const oldPair={port:54322,token:'b'.repeat(64)};
  const newPair={port:54323,token:'c'.repeat(64)};
  second.context.oldPair=oldPair;second.context.newPair=newPair;
  const pending=vm.runInContext('connectPair(oldPair)',second.context);
  await flush();
  const replacement=vm.runInContext('connectPair(newPair)',second.context);
  hold=false;
  const stalled=sockets.at(-1);stalled.readyState=1;stalled.onopen();
  await Promise.all([pending,replacement]);await flush();
  assert.equal(sockets.at(-1).url,`ws://127.0.0.1:${newPair.port}/extension/${newPair.token}`);
  assert.equal(sockets.at(-1).readyState,1);
  console.log('persistent extension pairing OK');
})().catch(error=>{console.error(error);process.exitCode=1});
