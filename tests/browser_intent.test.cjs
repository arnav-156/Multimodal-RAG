// Request-order/DOM-ownership tests run the actual browser script in stdlib vm.
// Minimal DOM/HTTP substitutes prove ordering only, never model or browser quality.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function element(tag='div'){
 const listeners={};
 return {tag,children:[],textContent:'',value:'',disabled:false,hidden:false,open:false,
  addEventListener(name,fn){(listeners[name]??=[]).push(fn);},
  async dispatch(name){let prevented=false;for(const fn of listeners[name]??[])await fn({preventDefault(){prevented=true;}});return prevented;},
  append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=[...nodes];},
  attributes:{},setAttribute(name,value){this.attributes[name]=String(value);},focus(){this.focused=true;},
  showModal(){this.open=true;},close(){this.open=false;for(const fn of listeners.close??[])fn();}
 };
}
async function library({config=false,confirm=true}={}){
 const ids=['login','workspace','message','loginForm','apiToken','logout','searchForm','query','topK','types','path','results','resultsHeading','resultCount','previous','next','page','preview','previewPath','previewText','download','closePreview','stats','jobs','jobList','indexHelp','refreshJobs'];
 const nodes=Object.fromEntries(ids.map(id=>[id,element()]));nodes.topK.value='50';
 if(config)for(const id of ['configForm','configJson','reload','health','healthResult','healthDetails','saveConfig','undoConfig','configState','discardSettings','discardAction'])nodes[id]=element();
 const buttons=['scan','extract','process'].map(job=>Object.assign(element('button'),{dataset:{job}}));
 const timers=[];
 const pending=[];
 const document={getElementById:id=>nodes[id]??null,createElement:element,querySelectorAll:()=>buttons};
 const fetch=(url,options={})=>url==='/api/session'?Promise.resolve({ok:true,json:async()=>({authenticated:false})}):new Promise(resolve=>pending.push({url,headers:options.headers,body:options.body&&JSON.parse(options.body),finish:(data,ok=true,status=ok?200:400)=>resolve({ok,status,json:async()=>data})}));
 const windowEvents={},confirmations=[];
 const window={addEventListener(name,fn){windowEvents[name]=fn;},confirm(text){confirmations.push(text);return confirm;}};
 const context={document,fetch,window,location:{reload(){}},setTimeout(fn){timers.push(fn);}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8'),context);
 const take=url=>{const i=pending.findIndex(r=>r.url===url);assert.notEqual(i,-1,'request exists: '+url);return pending.splice(i,1)[0];};
 const search=query=>{nodes.query.value=query;return nodes.searchForm.dispatch('submit');};
 return {nodes,pending,take,search,buttons,windowEvents,confirmations,poll:()=>context.refreshJobs(),tick:()=>timers.shift()()};
}
const result=(name,total=1,count=1)=>({total,results:Array.from({length:count},(_,i)=>({path:name+i,score:.75,snippet:'<img src=x onerror=alert(1)>',document_url:'/api/documents/'+name+i}))});
const first=ui=>ui.nodes.results.children[0]?.children[0].textContent;
const inspect=(ui,index=0)=>ui.nodes.results.children[index].children.find(n=>n.tag==='button').dispatch('click');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('newer query owns rows, count and page after late old success',async()=>{
 const ui=await library(),a=ui.search('A'),ra=ui.take('/api/search'),b=ui.search('B'),rb=ui.take('/api/search');
 rb.finish(result('B'));await b;ra.finish(result('A',31));await a;
 assert.equal(first(ui),'B0');assert.equal(ui.nodes.resultCount.textContent,'1 nearest files');assert.equal(ui.nodes.page.textContent,'1–1 of 1');assert.equal(ui.nodes.next.disabled,true);
});
test('late old failure cannot overwrite newer success notice',async()=>{
 const ui=await library(),a=ui.search('A'),ra=ui.take('/api/search'),b=ui.search('B');
 ui.take('/api/search').finish(result('B'));await b;ra.finish({detail:'old failure'},false);await a;
 assert.equal(first(ui),'B0');assert.equal(ui.nodes.message.textContent,'');
});
test('latest failure stays authoritative after old success',async()=>{
 const ui=await library(),a=ui.search('A'),ra=ui.take('/api/search'),b=ui.search('B');
 ui.take('/api/search').finish({detail:'latest failure'},false);await b;ra.finish(result('A'));await a;
 assert.equal(ui.nodes.message.textContent,'latest failure');assert.equal(ui.nodes.results.children.length,0);assert.equal(ui.nodes.next.disabled,true);
});
test('delayed pagination cannot replace a newer query or its page labels',async()=>{
 const ui=await library(),initial=ui.search('A');ui.take('/api/search').finish(result('A',21,10));await initial;
 const page=ui.nodes.next.dispatch('click'),old=ui.take('/api/search');assert.equal(old.body.offset,10);assert.equal(old.body.query,'A');
 const latest=ui.search('B');ui.take('/api/search').finish(result('B'));await latest;old.finish(result('oldpage',21,10));await page;
 assert.equal(first(ui),'B0');assert.equal(ui.nodes.page.textContent,'1–1 of 1');assert.equal(ui.nodes.previous.disabled,true);
});
test('current pagination uses the captured offset with matching labels',async()=>{
 const ui=await library(),initial=ui.search('A');ui.take('/api/search').finish(result('A',21,10));await initial;
 const page=ui.nodes.next.dispatch('click');ui.take('/api/search').finish(result('page2',21,10));await page;
 assert.equal(first(ui),'page20');assert.equal(ui.nodes.page.textContent,'11–20 of 21');assert.equal(ui.nodes.previous.disabled,false);assert.equal(ui.nodes.next.disabled,false);
});
test('a newer document inspection owns the dialog',async()=>{
 const ui=await library(),initial=ui.search('A');ui.take('/api/search').finish(result('A',2,2));await initial;
 const a=inspect(ui,0),ra=ui.take('/api/documents/A0'),b=inspect(ui,1);ui.take('/api/documents/A1').finish({path:'second',chunks:['second content'],download_url:'/second'});await b;
 ra.finish({path:'first',chunks:['first content'],download_url:'/first'});await a;
 assert.equal(ui.nodes.previewPath.textContent,'second');assert.equal(ui.nodes.download.href,'/second');assert.equal(ui.nodes.preview.open,true);
});
test('new query invalidates a pending inspection and its late error',async()=>{
 for(const fail of [false,true]){
  const ui=await library(),initial=ui.search('A');ui.take('/api/search').finish(result('A'));await initial;
  const preview=inspect(ui),old=ui.take('/api/documents/A0'),latest=ui.search('B');ui.take('/api/search').finish(result('B'));await latest;
  old.finish(fail?{detail:'old preview error'}:{path:'old',chunks:['old'],download_url:'/old'},!fail);await preview;
  assert.equal(ui.nodes.preview.open,false);assert.equal(ui.nodes.message.textContent,'');assert.equal(first(ui),'B0');
 }
});
test('latest failed inspection prevents an old success opening a dialog',async()=>{
 const ui=await library(),initial=ui.search('A');ui.take('/api/search').finish(result('A',2,2));await initial;
 const a=inspect(ui),old=ui.take('/api/documents/A0'),b=inspect(ui,1);ui.take('/api/documents/A1').finish({detail:'latest preview failed'},false);await b;
 old.finish({path:'old',chunks:['old'],download_url:'/old'});await a;
 assert.equal(ui.nodes.preview.open,false);assert.equal(ui.nodes.message.textContent,'latest preview failed');
});
test('submit prevents navigation and untrusted snippets remain inert text',async()=>{
 const ui=await library(),search=ui.search('plain');ui.take('/api/search').finish(result('<img>'));assert.equal(await search,true);
 assert.equal(first(ui),'<img>0');const snippet=ui.nodes.results.children[0].children.find(n=>n.className==='snippet');assert.equal(snippet.textContent,'<img src=x onerror=alert(1)>');assert.equal(snippet.children.length,0);
});

async function queued(ui,id='owned'){
 const action=ui.buttons[2].dispatch('click');ui.take('/api/pipeline/process').finish({job_id:id});
 await settle();
 ui.take('/api/pipeline/status').finish({jobs:[{id,status:'running'}]});await action;
 assert.match(ui.nodes.message.textContent,/Job queued/);
}
test('each terminal state replaces the notice of its own queued job',async()=>{
 for(const status of ['completed','partial','cancelled','failed','interrupted']){
  const ui=await library();await queued(ui);const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status}]});await poll;
  assert.equal(ui.nodes.message.textContent,`Job ${status}. Progress and errors appear below.`);
  assert(ui.buttons.every(button=>!button.disabled));
 }
});
test('terminal job polling cannot overwrite a newer pending search or search validation',async()=>{
 for(const finishSearch of [false,true]){
  const ui=await library();await queued(ui);const search=ui.search('new action'),response=ui.take('/api/search');
  if(finishSearch){response.finish({detail:'new search validation'},false);await search;}
  const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'completed'}]});await poll;
  assert.equal(ui.nodes.message.textContent,finishSearch?'new search validation':'Searching…');
  if(!finishSearch){response.finish(result('new'));await search;assert.equal(ui.nodes.message.textContent,'');}
 }
});
test('a late job admission response cannot reclaim a newer search notice',async()=>{
 for(const fail of [false,true]){
 const ui=await library(),job=ui.buttons[2].dispatch('click'),admission=ui.take('/api/pipeline/process'),search=ui.search('new');
 ui.take('/api/search').finish({detail:'current validation'},false);await search;admission.finish({job_id:'late'});
 await settle();ui.take('/api/pipeline/status').finish(fail?{detail:'old job polling error'}:{jobs:[{id:'late',status:'completed'}]},!fail);await job;
 assert.equal(ui.nodes.message.textContent,'current validation');
 }
});
test('older active poll cannot replace a newer terminal poll or disable buttons',async()=>{
 for(const fail of [false,true]){
 const ui=await library();await queued(ui);const old=ui.poll(),response=ui.take('/api/pipeline/status'),latest=ui.poll();
 ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'cancelled'}]});await latest;
 response.finish(fail?{detail:'old poll failure'}:{jobs:[{id:'owned',status:'running'}]},!fail);await old;
 assert(ui.buttons.every(button=>!button.disabled));assert.match(ui.nodes.jobs.textContent,/cancelled/);assert.match(ui.nodes.message.textContent,/Job cancelled/);
 }
});
test('job terminal and delayed job error preserve newer config validation/save notices',async()=>{
 for(const valid of [false,true]){
  const ui=await library({config:true});await queued(ui);const poll=ui.poll(),response=ui.take('/api/pipeline/status');
  ui.nodes.configJson.value=valid?'{}':'invalid JSON';const save=ui.nodes.configForm.dispatch('submit');
  if(valid)ui.take('/api/config').finish({config:{},restart_required:false});await save;const notice=ui.nodes.message.textContent;
  assert(notice);response.finish({jobs:[{id:'owned',status:'partial'}]});await poll;assert.equal(ui.nodes.message.textContent,notice);
  const timer=ui.tick();ui.take('/api/pipeline/status').finish({detail:'old poll failure'},false);await timer;assert.equal(ui.nodes.message.textContent,notice);
 }
});
test('another job terminal record cannot complete the owned notice',async()=>{
 const ui=await library();await queued(ui);const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'different',status:'completed'}]});await poll;
 assert.match(ui.nodes.message.textContent,/Job queued/);
 const latest=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'partial'}]});await latest;
 assert.match(ui.nodes.message.textContent,/Job partial/);
});
test('terminal jobs refresh counts once without replacing their terminal notice',async()=>{
 for(const status of ['completed','partial','cancelled','failed','interrupted']){
  const ui=await library();await queued(ui);assert(!ui.pending.some(r=>r.url==='/api/stats'));
  const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status}]});await poll;
  ui.take('/api/stats').finish({files:3,chunks:7});await settle();
  assert.equal(ui.nodes.stats.textContent,'3 files · 7 chunks');assert.match(ui.nodes.message.textContent,new RegExp('Job '+status));
  const repeated=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status}]});await repeated;
  assert(!ui.pending.some(r=>r.url==='/api/stats'),'unchanged terminal poll does not repeat stats RPC');
 }
});
test('an older count response or error cannot replace the latest terminal refresh',async()=>{
 for(const oldError of [false,true]){
  const ui=await library();await queued(ui);const first=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'completed'}]});await first;const old=ui.take('/api/stats');
  await queued(ui,'second');const next=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'second',status:'partial'}]});await next;
  ui.take('/api/stats').finish({files:9,chunks:12});await settle();old.finish(oldError?{detail:'old counts failed'}:{files:1,chunks:1},!oldError);await settle();
  assert.equal(ui.nodes.stats.textContent,'9 files · 12 chunks');assert.match(ui.nodes.message.textContent,/Job partial/);
 }
});
test('count refresh failures stop displaying stale numbers and preserve newer validation',async()=>{
 const ui=await library();ui.nodes.stats.textContent='2 files · 5 chunks';await queued(ui);
 const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'failed'}]});await poll;const counts=ui.take('/api/stats');
 const search=ui.search('current');ui.take('/api/search').finish({detail:'current query validation'},false);await search;
 counts.finish({detail:'stats unavailable'},false);await settle();
 assert.equal(ui.nodes.stats.textContent,'Library counts unavailable. Reload to retry.');assert.equal(ui.nodes.message.textContent,'current query validation');
});
test('workspace startup counts cannot overwrite a later terminal refresh',async()=>{
 const ui=await library();const login=ui.nodes.loginForm.dispatch('submit');await settle();const old=ui.take('/api/stats'),config=ui.take('/api/config');
 await queued(ui);const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'completed'}]});await poll;
 ui.take('/api/stats').finish({files:4,chunks:8});await settle();old.finish({files:0,chunks:0});config.finish({config:{search:{default_top_k:50,max_top_k:200}}});await settle();
 ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',status:'completed'}]});await login;
 assert.equal(ui.nodes.stats.textContent,'4 files · 8 chunks');
});

async function settings(ui){const login=ui.nodes.loginForm.dispatch('submit');await settle();ui.take('/api/config').finish({config:{search:{default_top_k:50}}});await login;}
test('settings save retains edits entered while the actual save request is pending',async()=>{
 const ui=await library({config:true});await settings(ui);
 ui.nodes.configJson.value='{"search":{"default_top_k":7}}';await ui.nodes.configJson.dispatch('input');
 const save=ui.nodes.configForm.dispatch('submit'),request=ui.take('/api/config');
 ui.nodes.configJson.value='{"search":{"default_top_k":9}}';await ui.nodes.configJson.dispatch('input');
 request.finish({config:{search:{default_top_k:7}}});await save;
 assert.equal(JSON.parse(ui.nodes.configJson.value).search.default_top_k,9);
 assert.match(ui.nodes.configState.textContent,/unsaved/i);assert.match(ui.nodes.message.textContent,/earlier version/i);
});
test('settings initial load cannot overwrite a newer typed draft',async()=>{
 const ui=await library({config:true});const login=ui.nodes.loginForm.dispatch('submit');await settle();const request=ui.take('/api/config');
 ui.nodes.configJson.value='{"draft":true}';await ui.nodes.configJson.dispatch('input');request.finish({config:{search:{default_top_k:50}}});await login;
 assert.equal(ui.nodes.configJson.value,'{"draft":true}');assert.match(ui.nodes.configState.textContent,/unsaved/i);
});
test('reload cancellation preserves the draft and sends no reload request',async()=>{
 const ui=await library({config:true});await settings(ui);ui.nodes.configJson.value='{"draft":true}';await ui.nodes.configJson.dispatch('input');
 const reload=ui.nodes.reload.dispatch('click');await settle();assert.equal(ui.nodes.discardSettings.open,true);ui.nodes.discardSettings.returnValue='cancel';ui.nodes.discardSettings.close();await reload;
 assert.equal(ui.nodes.configJson.value,'{"draft":true}');assert(!ui.pending.some(r=>r.url==='/api/config/reload'));
});
test('undo restores the last accepted settings and clears navigation protection',async()=>{
 const ui=await library({config:true});await settings(ui);const original=ui.nodes.configJson.value;ui.nodes.configJson.value='{"draft":true}';await ui.nodes.configJson.dispatch('input');
 let prevented=false;ui.windowEvents.beforeunload({preventDefault(){prevented=true;}});assert(prevented);
 await ui.nodes.undoConfig.dispatch('click');assert.equal(ui.nodes.configJson.value,original);
 prevented=false;ui.windowEvents.beforeunload({preventDefault(){prevented=true;}});assert(!prevented);
});
test('settings mutations are serialized and failed saves preserve draft with controls restored',async()=>{
 const ui=await library({config:true});await settings(ui);ui.nodes.configJson.value='{"search":{"default_top_k":7}}';await ui.nodes.configJson.dispatch('input');
 const save=ui.nodes.configForm.dispatch('submit');assert.equal(ui.nodes.saveConfig.disabled,true);await ui.nodes.configForm.dispatch('submit');assert.equal(ui.pending.filter(r=>r.url==='/api/config').length,1);
 ui.take('/api/config').finish({detail:'validation rejected'},false);await save;
 assert.equal(ui.nodes.saveConfig.disabled,false);assert.equal(JSON.parse(ui.nodes.configJson.value).search.default_top_k,7);assert.match(ui.nodes.configState.textContent,/unsaved/i);
});

test('a new search clears old actionable rows and owns its busy state until it settles',async()=>{
 const ui=await library();const a=ui.search('A');ui.take('/api/search').finish(result('A'));await a;
 const b=ui.search('B'),old=ui.take('/api/search');assert.equal(ui.nodes.results.children.length,0);assert.equal(ui.nodes.results.attributes['aria-busy'],'true');
 const c=ui.search('C'),latest=ui.take('/api/search');old.finish(result('B'));await b;
 assert.equal(ui.nodes.results.attributes['aria-busy'],'true');assert.equal(ui.nodes.results.children.length,0);
 latest.finish({detail:'current failure'},false);await c;assert.equal(ui.nodes.results.attributes['aria-busy'],'false');assert.equal(ui.nodes.message.textContent,'current failure');
});
test('empty search explains filter recovery and pagination moves keyboard focus to results',async()=>{
 const ui=await library();const empty=ui.search('none');ui.take('/api/search').finish(result('none',0,0));await empty;
 assert.match(ui.nodes.results.children[0].textContent,/filters|folders/i);
 const search=ui.search('many');ui.take('/api/search').finish(result('many',13,10));await search;
 const next=ui.nodes.next.dispatch('click');ui.take('/api/search').finish(result('last',13,3));await next;
 assert.equal(ui.nodes.resultsHeading.focused,true);assert.equal(ui.nodes.page.textContent,'11–13 of 13');
});
test('whitespace input stays local and structured validation errors name the field',async()=>{
 const ui=await library();await ui.search('   ');assert(!ui.pending.some(r=>r.url==='/api/search'));assert.match(ui.nodes.message.textContent,/question|keywords/i);
 const search=ui.search('valid');ui.take('/api/search').finish({detail:[{loc:['body','top_k'],msg:'Must be positive',input:'do not echo input'}]},false);await search;
 assert.equal(ui.nodes.message.textContent,'top_k: Must be positive');
});

test('job cards expose truthful progress and cancellation waits for terminal status',async()=>{
 const ui=await library();const poll=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',kind:'process',status:'running',progress:{processed:2,failed:1}}]});await poll;
 const card=ui.nodes.jobList.children[0];assert.match(card.children[0].textContent,/Build index/);assert.match(card.children[1].textContent,/2 processed/);
 const button=card.children.find(n=>n.tag==='button');const cancel=button.dispatch('click');ui.take('/api/pipeline/jobs/owned/cancel').finish({requested:true});await settle();
 ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',kind:'process',status:'running'}]});await cancel;
 assert.match(ui.nodes.message.textContent,/Cancellation requested/);assert(ui.buttons.every(b=>b.disabled));
 const terminal=ui.poll();ui.take('/api/pipeline/status').finish({jobs:[{id:'owned',kind:'process',status:'cancelled'}]});await terminal;
 assert.match(ui.nodes.message.textContent,/Job cancelled/);assert(ui.buttons.every(b=>!b.disabled));
});
test('readiness explains unavailable services and latest request owns summary and busy flag',async()=>{
 const ui=await library({config:true}),first=ui.nodes.health.dispatch('click'),old=ui.take('/api/config/status'),second=ui.nodes.health.dispatch('click'),latest=ui.take('/api/config/status');
 latest.finish({ready:false,embedding:{connected:false,error:'Not installed'},database:{connected:true},index:{chunks:0}},false,503);await second;
 assert.match(ui.nodes.healthResult.textContent,/Embedding service unavailable/);assert.equal(ui.nodes.healthResult.attributes['aria-busy'],'false');
 old.finish({ready:true,index:{chunks:9}});await first;assert.match(ui.nodes.healthResult.textContent,/unavailable/);
});
test('result headings show the filename while retaining the exact source path as inert text',async()=>{
 for(const [source,name] of [['C:\\Library\\Finance\\returns.md','returns.md'],['/manuals/evacuation.txt','evacuation.txt']]){
  const ui=await library(),search=ui.search('source'),data=result('document');data.results[0].path=source;
  ui.take('/api/search').finish(data);await search;
  assert.equal(first(ui),name);
  const path=ui.nodes.results.children[0].children.find(n=>n.className==='source-path');
  assert.equal(path.textContent,source);assert.equal(path.children.length,0);
 }
});
test('a conflicting settings save sends its loaded revision and keeps the draft',async()=>{
 const ui=await library({config:true});const login=ui.nodes.loginForm.dispatch('submit');await settle();ui.take('/api/config').finish({config:{search:{default_top_k:50}},revision:'loaded-revision'});await login;
 ui.nodes.configJson.value='{"search":{"default_top_k":7}}';await ui.nodes.configJson.dispatch('input');
 const save=ui.nodes.configForm.dispatch('submit'),request=ui.take('/api/config');
 assert.equal(request.headers['If-Match'],'"loaded-revision"');
 request.finish({detail:'Settings changed since you loaded them. Reload and review before saving.'},false,412);await save;
 assert.equal(JSON.parse(ui.nodes.configJson.value).search.default_top_k,7);assert.match(ui.nodes.configState.textContent,/unsaved/i);assert.match(ui.nodes.message.textContent,/Reload and review/);
});
test('readiness distinguishes database errors from embedding availability',async()=>{
 const ui=await library({config:true}),health=ui.nodes.health.dispatch('click');
 ui.take('/api/config/status').finish({detail:'Local database unavailable. Check database access and integrity.'},false,503);await health;
 assert.match(ui.nodes.healthResult.textContent,/Could not check readiness.*Local database unavailable/);assert.doesNotMatch(ui.nodes.healthResult.textContent,/Embedding service unavailable/);assert.equal(ui.nodes.healthResult.attributes['aria-busy'],'false');
});
test('session loss exposes sign-in and reauthentication preserves a settings draft',async()=>{
 const ui=await library({config:true});await settings(ui);ui.nodes.configJson.value='{"search":{"default_top_k":9}}';await ui.nodes.configJson.dispatch('input');
 const save=ui.nodes.configForm.dispatch('submit');ui.take('/api/config').finish({detail:'Sign in with your local API token'},false,401);await save;
 assert.equal(ui.nodes.login.hidden,false);assert.equal(ui.nodes.workspace.hidden,true);assert.equal(ui.nodes.apiToken.focused,true);
 const login=ui.nodes.loginForm.dispatch('submit');await settle();ui.take('/api/config').finish({config:{search:{default_top_k:50}},revision:'current'});await login;
 assert.equal(JSON.parse(ui.nodes.configJson.value).search.default_top_k,9);assert.match(ui.nodes.configState.textContent,/unsaved/i);assert.equal(ui.nodes.workspace.hidden,false);
});

test('an old unauthorized response cannot hide a successfully reauthenticated workspace',async()=>{
 const ui=await library({config:true});await settings(ui);const old=ui.nodes.health.dispatch('click'),request=ui.take('/api/config/status');
 const login=ui.nodes.loginForm.dispatch('submit');await settle();ui.take('/api/config').finish({config:{search:{default_top_k:50}},revision:'fresh'});await login;
 request.finish({detail:'old unauthorized request'},false,401);await old;assert.equal(ui.nodes.workspace.hidden,false);assert.equal(ui.nodes.login.hidden,true);
});
