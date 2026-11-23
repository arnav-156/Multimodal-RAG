'use strict';
const $=id=>document.getElementById(id);
let offset=0,total=0,lastSearch=null,searchVersion=0,previewVersion=0,messageVersion=0,jobsVersion=0,jobNotice=null,statsVersion=0,jobStates=null;
let configSavedText=null,configEditVersion=0,configBusy=false,configRevision=null,sessionVersion=0;
let jobAdmission=false,jobsActive=false,hasSelectedRoots=true,jobViewKey='',healthVersion=0;
const cancellingJobs=new Set();
const terminalJobStates=['completed','partial','cancelled','failed','interrupted'];
const message=(text,owner=++messageVersion)=>{if(owner===messageVersion)$('message').textContent=text;};
const configDirty=()=>Boolean($('configJson')&&$('configJson').value!==(configSavedText??''));
function configState(){if($('configState')){$('configState').textContent=configDirty()?'You have unsaved changes.':'All changes saved.';$('undoConfig').disabled=configBusy||!configDirty();}}
function acceptConfig(config,version,revision=null){configRevision=revision;configSavedText=JSON.stringify(config,null,2);if(version===configEditVersion)$('configJson').value=configSavedText;configState();}
function confirmDiscard(action){if(!configDirty())return Promise.resolve(true);const dialog=$('discardSettings');$('discardAction').textContent=action;dialog.returnValue='cancel';return new Promise(resolve=>{dialog.addEventListener('close',()=>resolve(dialog.returnValue==='discard'),{once:true});dialog.showModal();});}
function settingsAction(fn){return event=>{if(configBusy){event?.preventDefault();return;}return handle(async(e,owner)=>{configBusy=true;for(const id of ['saveConfig','reload','undoConfig','logout'])$(id).disabled=true;try{await fn(e,owner);}finally{configBusy=false;for(const id of ['saveConfig','reload','logout'])$(id).disabled=false;configState();}})(event);};}
async function api(path,options={},allowUnavailable=false){
 const sessionOwner=sessionVersion;let r;try{r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});}catch{throw new Error('Cannot reach the local server. Check that it is running, then try again.');}
 let data;try{data=await r.json();}catch{throw new Error('The server returned an unreadable response. Try again or check the server log.');}
 if(path==='/api/session'&&r.ok&&['POST','DELETE'].includes(options.method))++sessionVersion;
 if(r.status===401&&path!=='/api/session'&&sessionOwner===sessionVersion){++sessionVersion;$('login').hidden=false;$('workspace').hidden=true;$('apiToken').focus();}
 if(!r.ok&&!(allowUnavailable&&r.status===503&&data&&typeof data.ready==='boolean'))throw new Error(typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(e=>`${(e.loc||[]).filter(v=>v!=='body').join('.')||'Settings'}: ${e.msg||'Invalid value'}`).join('\n'):'The request could not be completed. Try again.');return data;
}
async function refreshStats(){
 const owner=++statsVersion;
 try{const stats=await api('/api/stats');if(owner===statsVersion)$('stats').textContent=`${stats.files} files · ${stats.chunks} chunks`;}
 catch(error){if(owner===statsVersion)$('stats').textContent='Library counts unavailable. Reload to retry.';}
}
async function showWorkspace(owner){
 $('login').hidden=true;$('workspace').hidden=false;message('',owner);
 if($('configJson')){const version=configDirty()?-1:configEditVersion;const data=await api('/api/config');acceptConfig(data.config,version,data.revision);}
 else{const [,settings]=await Promise.all([refreshStats(),api('/api/config')]);$('topK').value=settings.config.search.default_top_k;$('topK').max=settings.config.search.max_top_k;hasSelectedRoots=settings.config.scanner?settings.config.scanner.scan_paths.length>0:true;if(!hasSelectedRoots)$('indexHelp').textContent='Choose folders in Settings before starting an indexing stage.';updateJobButtons();await refreshJobs(owner);}
}
function handle(fn){return async event=>{event?.preventDefault();const owner=++messageVersion;message('',owner);try{await fn(event,owner);}catch(error){message(error.message,owner);}};}
$('loginForm').addEventListener('submit',handle(async(_,owner)=>{const token=$('apiToken').value;await api('/api/session',{method:'POST',body:JSON.stringify({token})});$('apiToken').value='';await showWorkspace(owner);}));
$('logout').addEventListener('click',handle(async()=>{if(!await confirmDiscard('Discard and sign out'))return;await api('/api/session',{method:'DELETE'});if($('configJson'))configSavedText=$('configJson').value;location.reload();}));
async function inspectDocument(url,searchOwner){
 if(searchOwner!==searchVersion)return;
 const owner=++previewVersion;
 const noticeOwner=++messageVersion;message('',noticeOwner);
 try{
  const doc=await api(url);if(owner!==previewVersion||searchOwner!==searchVersion)return;
  $('previewPath').textContent=doc.path;$('previewText').textContent=doc.chunks.join('\n\n');$('download').href=doc.download_url;$('preview').showModal();
 }catch(error){if(owner===previewVersion&&searchOwner===searchVersion)message(error.message,noticeOwner);}
}
async function renderSearch(noticeOwner,focusResults=false){
 const owner=++searchVersion,pageOffset=offset,request={...lastSearch,offset:pageOffset,page_size:10};
 ++previewVersion;if($('preview').open)$('preview').close();
 $('previous').disabled=true;$('next').disabled=true;$('results').replaceChildren();$('results').setAttribute('aria-busy','true');$('resultCount').textContent='Searching your library…';$('page').textContent='';message('Searching…',noticeOwner);
 let data;
 try{data=await api('/api/search',{method:'POST',body:JSON.stringify(request)});}
 catch(error){if(owner===searchVersion){total=0;$('results').replaceChildren();$('results').setAttribute('aria-busy','false');$('resultCount').textContent='Search could not be completed.';$('page').textContent='';message(error.message,noticeOwner);}return;}
 if(owner!==searchVersion)return;
 total=data.total;
 $('results').replaceChildren();$('results').setAttribute('aria-busy','false');$('resultCount').textContent=`${total} nearest files${data.skipped_invalid?` · ${data.skipped_invalid} invalid chunks skipped`:''}`;
 if(!total){const empty=document.createElement('p');empty.className='empty-state';empty.textContent='No indexed files were found for these filters. Try All types, clear the directory filter, or check your selected folders and index status.';$('results').append(empty);}
 for(const result of data.results){const article=document.createElement('article');article.className='result';const title=document.createElement('h3');title.textContent=result.path.split(/[\\/]/).pop()||result.path;const source=document.createElement('p');source.className='source-path';source.textContent=result.path;const score=document.createElement('p');score.className='score';score.textContent=`Similarity: ${result.score.toFixed(3)}`;const snippet=document.createElement('p');snippet.className='snippet';snippet.textContent=result.snippet;const open=document.createElement('button');open.textContent='Inspect document';open.setAttribute('aria-label',`Inspect document: ${result.path}`);open.addEventListener('click',()=>inspectDocument(result.document_url,owner));article.append(title,source,score,snippet,open);$('results').append(article);}
 $('previous').disabled=pageOffset===0;$('next').disabled=pageOffset+10>=total;$('page').textContent=total?`${pageOffset+1}–${Math.min(pageOffset+10,total)} of ${total}`:'No results';message('',noticeOwner);if(focusResults)$('resultsHeading').focus();
}
async function refreshJobs(noticeOwner=messageVersion){
 const owner=++jobsVersion;let data;
 try{data=await api('/api/pipeline/status');}catch(error){if(owner===jobsVersion)message(error.message,noticeOwner);return;}
 if(owner!==jobsVersion)return;
 const finished=jobStates!==null&&data.jobs.some(j=>terminalJobStates.includes(j.status)&&jobStates.get(j.id)!==j.status);
 jobStates=new Map(data.jobs.map(j=>[j.id,j.status]));
 if(finished)void refreshStats();
 const view=JSON.stringify(data.jobs.slice(0,6));if(view!==jobViewKey){jobViewKey=view;$('jobs').textContent=JSON.stringify(data.jobs.slice(0,6),null,2);renderJobs(data.jobs.slice(0,6));}
 const active=data.jobs.some(j=>['queued','running'].includes(j.status));jobsActive=active;updateJobButtons();
 if(jobNotice?.owner===messageVersion){
  const job=data.jobs.find(j=>j.id===jobNotice.id);
  if(job&&terminalJobStates.includes(job.status)){message(`Job ${job.status}. Progress and errors appear below.`,jobNotice.owner);jobNotice=null;}
 }
 if(active||jobNotice?.owner===messageVersion)setTimeout(()=>refreshJobs(noticeOwner),1000);
}
function updateJobButtons(){document.querySelectorAll('[data-job]').forEach(b=>b.disabled=jobAdmission||jobsActive||!hasSelectedRoots);}
function renderJobs(jobs){
 $('jobList').replaceChildren();
 if(!jobs.length){const empty=document.createElement('p');empty.textContent='No indexing jobs yet. Run the stages above to prepare your library.';$('jobList').append(empty);return;}
 const names={scan:'Scan folders',extract:'Extract metadata',process:'Build index'},states={queued:'Queued',running:'Running',completed:'Completed',partial:'Completed with issues',cancelled:'Cancelled',failed:'Failed',interrupted:'Interrupted'};
 for(const job of jobs){
  const card=document.createElement('article');card.className='job-card';const title=document.createElement('h3');title.textContent=`${names[job.kind]||'Indexing job'} · ${states[job.status]||job.status}`;
  const progress=document.createElement('p'),result=job.result||job.progress||{},labels={scanned:'scanned',inserted:'new',updated:'updated',deleted:'removed',unchanged:'unchanged',processed:'processed',skipped:'unchanged',failed:'failed',unsupported:'unsupported',chunks_written:'chunks written',done:'done',total:'total',errors:'errors'};
  progress.textContent=Object.entries(labels).filter(([key])=>typeof result[key]==='number').map(([key,label])=>`${result[key]} ${key==='skipped'&&job.kind!=='process'?'skipped':label}`).join(' · ')||(['queued','running'].includes(job.status)?'Waiting for a progress update.':'No progress was reported.');
  card.append(title,progress);if(job.error||job.status==='partial'){const error=document.createElement('p');error.className='job-error';error.textContent=job.error||'Some sources need attention. Open technical details for the recorded errors.';card.append(error);}
  if(['queued','running'].includes(job.status)){
   const cancel=document.createElement('button');cancel.textContent=cancellingJobs.has(job.id)?'Cancellation requested':'Cancel job';cancel.disabled=cancellingJobs.has(job.id);cancel.setAttribute('aria-label',`Cancel ${names[job.kind]||'indexing job'}`);
   cancel.addEventListener('click',handle(async(_,owner)=>{if(cancellingJobs.has(job.id))return;cancellingJobs.add(job.id);cancel.disabled=true;try{const response=await api('/api/pipeline/jobs/'+encodeURIComponent(job.id)+'/cancel',{method:'POST'});if(owner===messageVersion){jobNotice={id:job.id,owner};message(response.requested?'Cancellation requested. The current file may finish before the job stops.':'This job has already finished.',owner);}jobViewKey='';await refreshJobs(owner);}catch(error){cancellingJobs.delete(job.id);cancel.disabled=false;throw error;}}));card.append(cancel);
  }else cancellingJobs.delete(job.id);
  $('jobList').append(card);
 }
}
if($('searchForm')){
 $('searchForm').addEventListener('submit',handle(async(_,owner)=>{const query=$('query').value.trim();if(!query){message('Enter a question or keywords to search.',owner);$('query').focus();return;}offset=0;lastSearch={query,top_k:Number($('topK').value),file_types:$('types').value,path:$('path').value.trim()};await renderSearch(owner);}));
 $('previous').addEventListener('click',handle(async(_,owner)=>{offset=Math.max(0,offset-10);await renderSearch(owner,true);}));$('next').addEventListener('click',handle(async(_,owner)=>{offset+=10;await renderSearch(owner,true);}));
 $('closePreview').addEventListener('click',()=>{++previewVersion;$('preview').close();});
 document.querySelectorAll('[data-job]').forEach(button=>button.addEventListener('click',event=>{if(jobAdmission){event?.preventDefault();return;}return handle(async(_,owner)=>{jobAdmission=true;updateJobButtons();try{const data=await api('/api/pipeline/'+button.dataset.job,{method:'POST',body:'{}'});jobsActive=true;jobStates??=new Map();jobStates.set(data.job_id,'queued');if(owner===messageVersion){jobNotice={id:data.job_id,owner};message('Job queued. Progress and errors appear below.',owner);}await refreshJobs(owner);}finally{jobAdmission=false;updateJobButtons();}})(event);}));
 $('refreshJobs').addEventListener('click',handle(async(_,owner)=>{await refreshJobs(owner);}));
}
if($('configForm')){
 $('configJson').addEventListener('input',()=>{++configEditVersion;$('configJson').setAttribute('aria-invalid','false');configState();});
 window.addEventListener('beforeunload',event=>{if(configDirty()){event.preventDefault();event.returnValue='';}});
 $('undoConfig').addEventListener('click',()=>{if(configBusy||configSavedText===null)return;++configEditVersion;$('configJson').value=configSavedText;$('configJson').setAttribute('aria-invalid','false');configState();message('Unsaved edits undone.');$('configJson').focus();});
 $('configForm').addEventListener('submit',settingsAction(async(_,owner)=>{let config;try{config=JSON.parse($('configJson').value);}catch{$('configJson').setAttribute('aria-invalid','true');throw new Error('Settings must be valid JSON. Check brackets, commas and quoted property names.');}const version=configEditVersion;const data=await api('/api/config',{method:'PUT',headers:configRevision?{'If-Match':'"'+configRevision+'"'}:{},body:JSON.stringify({config})});acceptConfig(data.config,version,data.revision);message(configDirty()?'Saved the earlier version. Your newer edits are still unsaved.':data.restart_required?'Saved. Restart the server to apply host/port changes.':'Validated and saved.',owner);}));
 $('reload').addEventListener('click',settingsAction(async(_,owner)=>{if(!await confirmDiscard('Discard and reload'))return;const version=configEditVersion;const data=await api('/api/config/reload',{method:'POST'});acceptConfig(data.config,version,data.revision);message(configDirty()?'Reloaded settings; your newer edits are still unsaved.':data.restart_required?'Reloaded. Restart required.':'Reloaded from disk.',owner);}));
 $('health').addEventListener('click',handle(async()=>{const owner=++healthVersion;$('health').disabled=true;$('healthResult').setAttribute('aria-busy','true');$('healthResult').textContent='Checking the saved settings…';try{const data=await api('/api/config/status',{},true);if(owner!==healthVersion)return;$('healthResult').textContent=data.ready?`Ready to search. ${data.index.chunks} indexed chunks.`:!data.embedding?.connected?`Embedding service unavailable. ${data.embedding?.error||'Check the saved model and endpoint.'}`:!data.database?.connected?'The database needs attention. Check the details below.':'The service is connected, but the index is not ready. Check selected folders, build the index and verify its model matches.';$('healthDetails').textContent=JSON.stringify(data,null,2);}catch(error){if(owner===healthVersion)$('healthResult').textContent='Could not check readiness. '+error.message;throw error;}finally{if(owner===healthVersion){$('health').disabled=false;$('healthResult').setAttribute('aria-busy','false');}}}));
}
const initialOwner=++messageVersion;
api('/api/session').then(data=>data.authenticated&&showWorkspace(initialOwner)).catch(error=>message(error.message,initialOwner));
