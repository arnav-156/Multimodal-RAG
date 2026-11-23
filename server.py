"""Authenticated loopback UI/API. Blocking work runs in worker threads."""
import hmac
import asyncio
import json
import os
import sqlite3
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ConfigDict
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from config_loader import get_config,get_db_path,public_config,save_config,reload_config,configuration_lock,configured_operation
from security import get_api_token,validate_host_origin
from corpus_policy import CorpusPolicy, source_allowed, CURRENT_CHUNKS
from database import connect,initialize,schema_status
from jina_v5_embedding import EmbeddingError
import jobs

@asynccontextmanager
async def lifespan(app):
    initialize();jobs.recover_interrupted();jobs.startup()
    try:yield
    finally:
        result=await asyncio.to_thread(jobs.shutdown,30)
        if not result['complete']:raise RuntimeError('Owned job shutdown missed its 30-second deadline; remaining owners: '+', '.join(result['remaining'])+'; admission pending: '+str(result['admission_pending']))

app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
BASE=Path(__file__).resolve().parent

def token_matches(candidate,expected):
    # compare_digest rejects non-ASCII str inputs; compare encoded bytes instead.
    return hmac.compare_digest(candidate.encode('utf-8',errors='surrogatepass'),expected.encode('utf-8',errors='surrogatepass'))


def authenticated(request):
    secret=get_api_token()
    if token_matches(request.headers.get('authorization',''),'Bearer '+secret):return True
    try:return URLSafeTimedSerializer(secret,salt='rag-session').loads(request.cookies.get('rag_session',''),max_age=43200)=='authorized'
    except (BadSignature,SignatureExpired):return False

@app.middleware('http')
async def boundary(request,call_next):
    try:validate_host_origin(request.headers.get('host',''),request.headers.get('origin'),request.url.scheme)
    except ValueError as exc:return JSONResponse({'detail':str(exc)},403)
    if request.method!='OPTIONS' and request.url.path.startswith('/api/') and request.url.path not in ('/api/session','/api/live') and not authenticated(request):
        return JSONResponse({'detail':'Sign in with your local API token'},401)
    if request.method=='OPTIONS':response=JSONResponse({})
    else:response=await call_next(request)
    origin=request.headers.get('origin')
    if origin and origin in get_config()['server']['cors_origins']:
        response.headers['Access-Control-Allow-Origin']=origin
        response.headers['Access-Control-Allow-Credentials']='true'
        response.headers['Access-Control-Allow-Headers']='Authorization,Content-Type,If-Match'
        response.headers['Access-Control-Allow-Methods']='GET,POST,PUT,DELETE,OPTIONS'
        response.headers['Vary']='Origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Cache-Control']='no-store'
    return response

class Login(BaseModel):token:str=Field(min_length=1,max_length=256)
@app.get('/api/session')
def session(request:Request):return {'authenticated':authenticated(request)}
@app.post('/api/session')
def login(body:Login):
    if not token_matches(body.token,get_api_token()):raise HTTPException(401,'Invalid token')
    response=JSONResponse({'authenticated':True})
    response.set_cookie('rag_session',URLSafeTimedSerializer(get_api_token(),salt='rag-session').dumps('authorized'),httponly=True,samesite='strict',max_age=43200)
    return response
@app.delete('/api/session')
def logout():
    response=JSONResponse({'authenticated':False});response.delete_cookie('rag_session');return response
@app.get('/api/live')
def live():return {'alive':True}

@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'detail':str(exc)},422)
@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    # Raw inputs can be recursive, non-JSON values or credentials. Never echo them.
    return JSONResponse({'detail':[{key:error[key] for key in ('type','loc','msg')} for error in exc.errors()]},422)
@app.exception_handler(EmbeddingError)
async def model_unavailable(request,exc):return JSONResponse({'detail':str(exc)},503)
@app.exception_handler(sqlite3.Error)
async def database_unavailable(request,exc):
    logging.getLogger(__name__).error('Local database operation failed: %s',exc)
    return JSONResponse({'detail':'Local database unavailable. Check database access and integrity.'},503)

@app.get('/api/config')
def config():
    with configuration_lock():
        cfg=get_config()
        return {'config':public_config(cfg),'revision':config_revision(cfg)}

def config_revision(cfg):
    # Opaque keyed revision: do not expose a plain hash of write-only secrets.
    return hmac.new(get_api_token().encode('utf-8'),json.dumps(cfg,sort_keys=True).encode('utf-8'),'sha256').hexdigest()
class ConfigUpdate(BaseModel):config:dict
@app.put('/api/config')
def update_config(body:ConfigUpdate,request:Request=None):
    with configuration_lock():
        before=get_config()
        expected=request.headers.get('if-match') if request is not None else None
        if expected is not None and not token_matches(expected,'"'+config_revision(before)+'"'):
            raise HTTPException(412,'Settings changed since you loaded them. Reload and review before saving.')
        audio_update=body.config.get('audio',{})
        if not isinstance(audio_update,dict):raise ValueError('audio must be a configuration object')
        for name in ('python_executable','cpp_executable'):
            if audio_update.get(name,before['audio'][name])!=before['audio'][name]:
                raise HTTPException(409,'Change audio executable paths in the local YAML file, then reload or restart')
        # Database/token ownership remains fixed for the lifetime of this server.
        database_update=body.config.get('database',{})
        if not isinstance(database_update,dict):raise ValueError('database must be a configuration object')
        if database_update.get('path',before['database']['path'])!=before['database']['path']:
            raise HTTPException(409,'Change database.path in the YAML file and restart the server')
        if any(j['status'] in ('queued','running') for j in jobs.list_jobs()):raise HTTPException(409,'Wait for the active job before changing configuration')
        after=save_config(body.config)
        return {'config':public_config(after),'revision':config_revision(after),'restart_required':before['server']!=after['server']}
@app.post('/api/config/reload')
def reload():
    with configuration_lock():
        before=get_config()
        if any(j['status'] in ('queued','running') for j in jobs.list_jobs()):raise HTTPException(409,'Wait for active job')
        after=reload_config(expected_db_path=before['database']['path'])
        return {'config':public_config(after),'revision':config_revision(after),'restart_required':before['server']!=after['server']}

@app.get('/api/config/status')
@app.get('/api/health')
@configured_operation
def status():
    from jina_v5_embedding import JinaV5Embedder
    db=schema_status();embedding={'connected':False}
    try:
        with JinaV5Embedder() as model:
            fp=model.fingerprint();model.encode(['readiness probe'],prompt_name='query')
            embedding={'connected':True,'identity':model.identity,'fingerprint':fp}
    except Exception as exc:embedding['error']=str(exc)
    cfg=get_config()
    with connect() as c:
        CorpusPolicy().install(c)
        indexed=c.execute('SELECT COUNT(*) FROM rag_chunks ch JOIN files f ON f.id=ch.file_id JOIN rag_status r ON r.file_id=f.id WHERE '+CURRENT_CHUNKS).fetchone()[0]
        stored=c.execute("SELECT value FROM index_metadata WHERE key='embedding_fingerprint'").fetchone()
    index={'chunks':indexed,'model_matches':bool(stored and stored[0]==embedding.get('fingerprint'))}
    result={'database':db,'embedding':embedding,'ocr':{'enabled':cfg['ocr']['enabled'],'verified':False},
        'vision':{'enabled':any(e['enabled'] for e in cfg['vision']['endpoints']),'verified':False},
        'index':index,'ready_to_ingest':db['connected'] and embedding['connected'],
        'ready':db['connected'] and embedding['connected'] and index['model_matches'] and indexed>0 and bool(cfg['scanner']['scan_paths'])}
    return JSONResponse(result,200 if result['ready'] else 503)
@app.get('/api/stats')
@configured_operation
def stats():
    with connect() as c:
        CorpusPolicy().install(c)
        return {'files':c.execute('SELECT COUNT(*) FROM files f WHERE f.is_deleted=0 AND corpus_selected(f.path)').fetchone()[0],
            'chunks':c.execute('SELECT COUNT(*) FROM rag_chunks ch JOIN files f ON f.id=ch.file_id JOIN rag_status r ON r.file_id=f.id WHERE '+CURRENT_CHUNKS).fetchone()[0],
            'statuses':dict(c.execute('SELECT r.status,COUNT(*) FROM rag_status r JOIN files f ON f.id=r.file_id WHERE f.is_deleted=0 AND corpus_selected(f.path) GROUP BY r.status').fetchall())}

class Search(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(min_length=1,max_length=8000)
    top_k:int|None=None
    file_types:str=''
    path:str=''
    offset:int=Field(0,ge=0)
    page_size:int=Field(20,ge=1,le=100)
@app.post('/api/search')
def search(body:Search):
    from search_numpy import search_documents
    return search_documents(**body.model_dump())
@app.get('/api/search')
def search_get(q:str,top_k:int|None=None,file_types:str='',path:str='',offset:int=0,page_size:int=20):
    return search(Search(query=q,top_k=top_k,file_types=file_types,path=path,offset=offset,page_size=page_size))
class ProcessOptions(BaseModel):
    model_config=ConfigDict(extra='forbid')
    limit:int=Field(0,ge=0)
    types:str=''
    force:bool=False
    regenerate_legacy_descriptions:bool=False
@app.post('/api/pipeline/{kind}',status_code=202)
def pipeline(kind:str,body:ProcessOptions):
    with configuration_lock():
        db=get_db_path()
        if kind not in ('scan','extract','process'):raise HTTPException(404,'Unknown pipeline stage')
        fn=jobs.stage_task(kind,db,body.model_dump() if kind=='process' else None)
        try:return jobs.submit(kind,fn,db)
        except jobs.JobConflict as exc:raise HTTPException(409,str(exc))
@app.post('/api/pipeline/jobs/{jid}/cancel')
def cancel_job(jid:str):
    try:return jobs.cancel(jid)
    except KeyError:raise HTTPException(404,'Job not found')
    except jobs.JobConflict as exc:raise HTTPException(409,str(exc))

@app.get('/api/pipeline/status')
def pipeline_status():return {'jobs':jobs.list_jobs()}

def indexed_file(fid):
    with connect() as c:row=c.execute('SELECT * FROM files WHERE id=? AND is_deleted=0',(fid,)).fetchone()
    if not row:raise HTTPException(404,'Indexed file not found')
    path=Path(row['path'])
    if not source_allowed(path):raise HTTPException(403,'File outside configured roots or linked')
    if any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (path,*path.parents)):
        raise HTTPException(403,'Linked files are not served')
    return row,path
@app.get('/api/documents/{fid}')
@configured_operation
def document(fid:int):
    row,path=indexed_file(fid)
    with connect() as c:
        CorpusPolicy().install(c)
        chunks=[json.loads(r[0] or '{}').get('_text','') for r in c.execute('SELECT ch.meta_json FROM rag_chunks ch JOIN files f ON f.id=ch.file_id JOIN rag_status r ON r.file_id=f.id WHERE f.id=? AND '+CURRENT_CHUNKS+' ORDER BY ch.chunk_index LIMIT 100',(fid,))]
    return {'file_id':fid,'path':str(path),'chunks':chunks,'download_url':f'/api/documents/{fid}/download'}
@app.get('/api/documents/{fid}/download')
@configured_operation
def download(fid:int):
    import hashlib
    import stat
    row,path=indexed_file(fid)
    try:
        before=os.lstat(path);stream=path.open('rb');opened=os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or before.st_ino!=opened.st_ino or opened.st_size>get_config()['limits']['max_text_file_bytes']:
            stream.close();raise HTTPException(409,'File changed or exceeds download limit')
        digest=hashlib.file_digest(stream,'sha256').hexdigest();stream.seek(0)
        if digest!=row['file_hash']:stream.close();raise HTTPException(409,'File changed; scan again')
    except OSError:raise HTTPException(404,'File unavailable')
    def blocks():
        try:yield from iter(lambda:stream.read(65536),b'')
        finally:stream.close()
    return StreamingResponse(blocks(),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="document.bin"'})
@app.get('/')
def index():return FileResponse(BASE/'static/index.html')
@app.get('/config')
def config_page():return FileResponse(BASE/'static/config.html')
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')

def main():
    import uvicorn
    cfg=get_config()['server']
    print('Local token file:',Path(get_db_path()).parent/'.api_token')
    uvicorn.run(app,host=cfg['host'],port=cfg['port'])
if __name__=='__main__':main()
