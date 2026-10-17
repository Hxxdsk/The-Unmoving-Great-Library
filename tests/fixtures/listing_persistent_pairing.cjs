const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const hook=()=>({listeners:[],addListener(fn){this.listeners.push(fn)}});
const stored={},sockets=[];
let online=true,created=0;
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function worker(){
  const chrome={runtime:{onMessage:hook(),onStartup:hook(),onInstalled:hook()},
    storage:{local:{async get(){return structuredClone(stored)},async set(data){Object.assign(stored,structuredClone(data))},async setAccessLevel(){}}},
    alarms:{onAlarm:hook(),create(){}},debugger:{onEvent:hook(),onDetach:hook(),async detach(){}},
    windows:{onFocusChanged:hook(),async get(){return {width:1920,height:1080,left:0,top:0}},async create(){created++;throw Error('reconnect must not create windows')}},
    tabs:{onCreated:hook(),onRemoved:hook(),onUpdated:hook(),async create(){created++;throw Error('reconnect must not create tabs')}}};
  class Socket{
    static OPEN=1;
    constructor(url){this.url=url;this.readyState=0;sockets.push(this);queueMicrotask(()=>{
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
  console.log('persistent extension pairing OK');
})().catch(error=>{console.error(error);process.exitCode=1});
