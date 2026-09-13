const fs = require('fs');
const vm = require('vm');
const path = require('path');
const html = fs.readFileSync(process.argv[2] || path.join(__dirname, '../src/culprit/web/static/index.html'), 'utf8');
const source = html.slice(html.indexOf('  function subscribe()'), html.indexOf('  // ---------------------------------------------------------------- views'));
let resolveOld;
const streams = [];
const ctx = vm.createContext({
  current: 'run-A', record: {run_id:'run-A', status:'running'}, es:null, lastSeq:0,
  pollTimer:null, openGen:1,
  clearTimeout(){}, setTimeout(){},
  EventSource: class { constructor(url){this.url=url;streams.push(this);this.listeners={};} close(){} addEventListener(k,fn){this.listeners[k]=fn;} },
  api: () => new Promise(r => {resolveOld=r;}),
  handle(){},paint(){},loadRuns(){},say(){},pollWhilePaused(){},$:()=>({})
});
vm.runInContext(source,ctx);
(async()=>{
  ctx.subscribe();
  const pending = streams[0].listeners.done();
  ctx.current='run-B';ctx.record={run_id:'run-B',status:'running'};ctx.openGen++;
  ctx.subscribe();
  resolveOld({run_id:'run-A',status:'completed'});
  await pending;
  if(ctx.record.run_id!=='run-B') throw Error('Stale SSE completion overwrote run-B with '+ctx.record.run_id);
  console.log('PASS: stale completion cannot overwrite the newly selected run');
})().catch(e=>{console.error(e.message);process.exit(1)});
