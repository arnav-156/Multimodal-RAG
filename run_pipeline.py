#!/usr/bin/env python3
"""Portable CLI and reusable bounded ingestion. Inference services are externally owned."""
from config_loader import configured_operation
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from corpus_policy import CorpusPolicy, source_allowed

MAX_ERROR_DETAILS=100


def init_db(db_path=None):
    from database import initialize
    initialize(db_path)


def get_db(db_path=None):
    from database import connect
    return connect(db_path)



def _source_rows(db_path,page_size):
    """Finite high-water selection; close every reader before yielding a page."""
    with get_db(db_path) as connection:
        CorpusPolicy().install(connection)
        high_water=connection.execute('SELECT COALESCE(MAX(id),0) FROM files').fetchone()[0]
    last_id=0
    while last_id<high_water:
        with get_db(db_path) as connection:
            CorpusPolicy().install(connection)
            rows=connection.execute('''SELECT f.*,r.status,r.source_hash,r.index_fingerprint FROM files f
                LEFT JOIN rag_status r ON r.file_id=f.id WHERE f.is_deleted=0 AND corpus_selected(f.path) AND f.id>? AND f.id<=?
                ORDER BY f.id LIMIT ?''',(last_id,high_water,page_size)).fetchall()
        if not rows:return
        last_id=rows[-1]['id']
        yield from rows

def pipeline_fingerprint(embedder):
    from config_loader import get_config
    cfg=get_config()
    fields={'embedding':embedder.fingerprint(),'chunking':cfg['chunking'],'extractor_version':5,
        'embedding_input_budget':cfg['embedding']['max_input_chars'],
        'ocr':{k:v for k,v in cfg['ocr'].items() if k not in ('api_key',)},'audio':cfg['audio'],'limits':cfg['limits']}
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode('utf-8')).hexdigest()


def ensure_index_contract(embedder,db_path=None,force=False):
    fingerprint=embedder.fingerprint()
    with get_db(db_path) as c:
        old=c.execute("SELECT value FROM index_metadata WHERE key='embedding_fingerprint'").fetchone()
        count=c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]
        if count and (not old or old[0]!=fingerprint):
            if not force:raise ValueError('Index/model contract changed or is legacy; explicitly rebuild with process --force')
            c.execute('DELETE FROM rag_chunks');c.execute('DELETE FROM rag_status')
            c.execute('UPDATE rag_descriptions SET embedded=0')
        for key,value in [('embedding_fingerprint',fingerprint),('dimension',str(embedder.embedding_dim)),
                          ('model_identity',json.dumps(getattr(embedder,'identity',{}),sort_keys=True))]:
            c.execute('INSERT OR REPLACE INTO index_metadata(key,value) VALUES (?,?)',(key,value))
        if hasattr(embedder,'semantic_contract'):
            c.execute("INSERT OR REPLACE INTO index_metadata(key,value) VALUES ('embedding_contract',?)",
                      (json.dumps(embedder.semantic_contract(),sort_keys=True),))
    return fingerprint


@configured_operation
def process_files(limit=0,types=None,force=False,embedder=None,db_path=None,progress=None,stop_event=None,file_ids=None,regenerate_legacy_descriptions=False):
    from config_loader import get_config,get_db_path
    from database import connect,source_transaction,SourceVersionChanged,ensure_initialized
    from file_scanner import file_fingerprint
    from extractors import extract_outcome,classify_file,UnsupportedFormatError,ExtractionError
    from vectors import serialize_vector
    from describer import process_pending_descriptions
    from description_embedder import drain_descriptions
    from jina_v5_embedding import JinaV5Embedder
    cfg=get_config();db_path=db_path or get_db_path();ensure_initialized(db_path)
    if not isinstance(limit,int) or limit<0:raise ValueError('limit must be nonnegative')
    kinds={value.strip() for value in (types.split(',') if isinstance(types,str) else types or []) if value.strip()}
    result={'processed':0,'skipped':0,'unsupported':0,'failed':0,'chunks_written':0,'recovered':0,'recovered_chunks':0,'errors':[], 'cancelled':False}
    failed_sources={}
    with connect(db_path) as c:
        CorpusPolicy().install(c)
        if not c.execute('SELECT 1 FROM files WHERE is_deleted=0 AND corpus_selected(path) LIMIT 1').fetchone():return result
    owned=embedder is None;embedder=embedder or JinaV5Embedder()
    try:
        ensure_index_contract(embedder,db_path,force)
        fingerprint=pipeline_fingerprint(embedder)
        with connect(db_path) as c:
            CorpusPolicy().install(c)
            c.execute("UPDATE rag_status SET status='pending',error='Previous processing was interrupted' WHERE status='processing' AND file_id IN (SELECT id FROM files WHERE is_deleted=0 AND corpus_selected(path))")
        selected=0
        for row in _source_rows(db_path,cfg['pipeline']['fetch_limit']):
            if stop_event is not None and stop_event.is_set():result['cancelled']=True;break
            if not source_allowed(row['path']):continue
            if file_ids is not None and row['id'] not in file_ids:continue
            kind=classify_file(row['ext'] or '',row['media_meta'],row['path'])
            if kinds and kind not in kinds and not ('pdf' in kinds and kind.startswith('pdf')):continue
            if not force and row['status'] in ('done','unsupported') and row['source_hash']==row['file_hash'] and row['index_fingerprint']==fingerprint:
                result['skipped']+=1;continue
            if limit and selected>=limit:break
            selected+=1;fid=row['id'];source_hash=row['file_hash'];outcome=None
            try:
                with source_transaction(db_path,fid,source_hash) as writer:
                    writer.execute('''INSERT INTO rag_status(file_id,status,source_hash,index_fingerprint,updated_at)
                        VALUES (?,'processing',?,?,CURRENT_TIMESTAMP) ON CONFLICT(file_id) DO UPDATE SET
                        status='processing',error=NULL,source_hash=excluded.source_hash,index_fingerprint=excluded.index_fingerprint,updated_at=CURRENT_TIMESTAMP''',(fid,source_hash,fingerprint))
                    if force:writer.execute('DELETE FROM rag_ocr_status WHERE file_id=?',(fid,))
                if not source_hash or file_fingerprint(row['path'])!=source_hash:raise ExtractionError('Source changed; scan the configured roots again')
                outcome=extract_outcome(fid,row['path'],row['ext'] or '',kind,row['media_meta'],row['size'] or 0)
                if outcome.error is not None:raise outcome.error
                chunks=outcome.chunks
                if not source_allowed(row['path']):raise ExtractionError('Source permission revoked during extraction')
                vectors=embedder.encode([chunk.text for chunk in chunks],prompt_name='document')
                if len(vectors)!=len(chunks):raise ValueError('Embedding/chunk count mismatch')
                blobs=[serialize_vector(v,embedder.embedding_dim) for v in vectors]
                if not source_allowed(row['path']):raise ExtractionError('Source permission revoked during inference')
                if file_fingerprint(row['path'])!=source_hash:raise ExtractionError('Source changed during extraction; scan again')
                with source_transaction(db_path,fid,source_hash) as writer:
                    writer.execute('DELETE FROM rag_chunks WHERE file_id=? AND chunk_index>=0',(fid,))
                    for chunk,blob in zip(chunks,blobs):
                        meta=dict(chunk.meta or {});meta['_text']=chunk.text;meta['source_hash']=source_hash
                        writer.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (?,?,?,?)',
                          (fid,chunk.chunk_index,json.dumps(meta,ensure_ascii=False),blob))
                    writer.execute("UPDATE rag_status SET status='done',error=NULL,chunks_written=?,updated_at=CURRENT_TIMESTAMP WHERE file_id=?",(len(chunks),fid))
                    writer.execute('DELETE FROM rag_ocr_status WHERE file_id=?',(fid,))
                result['processed']+=1;result['chunks_written']+=len(chunks)
            except Exception as exc:
                unsupported=isinstance(exc,UnsupportedFormatError)
                reason=f'{type(exc).__name__}: {exc}'
                try:
                    with source_transaction(db_path,fid,source_hash) as writer:
                        writer.execute("UPDATE rag_status SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE file_id=?",('unsupported' if unsupported else 'error',reason,fid))
                        if outcome is not None and outcome.recovery_eligible:
                            details='; '.join(d['reason'] for d in outcome.diagnostics) or reason
                            writer.execute("INSERT OR REPLACE INTO rag_ocr_status(file_id,status,reason) VALUES (?,'insufficient',?)",(fid,details))
                except SourceVersionChanged as stale:
                    reason+=f'; {stale}';unsupported=False
                if unsupported:result['unsupported']+=1
                else:
                    result['failed']+=1;failed_sources[fid]=source_hash
                    if len(result['errors'])<MAX_ERROR_DETAILS:result['errors'].append({'file_id':fid,'error':reason,'source_hash':source_hash})
            if progress:progress(dict(result))
            if stop_event is not None and stop_event.is_set():result['cancelled']=True;break
        # Always generate then drain a finite description snapshot, including jobs
        # with no new file chunks. No background producer can outlive this drain.
        if not result['cancelled']:
            result['vision']=process_pending_descriptions(db_path=db_path,stop_event=stop_event,regenerate_legacy=regenerate_legacy_descriptions)
            if stop_event is not None and stop_event.is_set():result['cancelled']=True
            if not result['cancelled']:result['descriptions']=drain_descriptions(embedder=embedder,db_path=db_path,stop_event=stop_event)
            if stop_event is not None and stop_event.is_set():result['cancelled']=True
            recovered=[]
            for fid,source_hash in failed_sources.items():
                if result['cancelled']:break
                try:
                    with source_transaction(db_path,fid,source_hash) as writer:
                        n=writer.execute('SELECT COUNT(*) FROM rag_chunks WHERE file_id=? AND chunk_index<0',(fid,)).fetchone()[0]
                        if n:
                            writer.execute("UPDATE rag_status SET status='done',error=NULL,chunks_written=?,updated_at=CURRENT_TIMESTAMP WHERE file_id=?",(n,fid));recovered.append(fid);result['recovered_chunks']+=n
                except SourceVersionChanged:continue
            if recovered:
                result['failed']-=len(recovered);result['processed']+=len(recovered);result['recovered']=len(recovered)
                result['errors']=[e for e in result['errors'] if e['file_id'] not in recovered]
        return result
    finally:
        if owned:embedder.close()


def main_entry(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',help='Explicit YAML path; defaults to project config.yaml')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ['scan','extract-meta','process','search','serve','init-db','token','backup']:
        cmd=commands.add_parser(name);cmd.add_argument('--config',default=argparse.SUPPRESS)
        if name=='process':
            cmd.add_argument('--limit',type=int,default=0);cmd.add_argument('--types',default='');cmd.add_argument('--force',action='store_true')
            cmd.add_argument('--regenerate-legacy-descriptions',action='store_true',help='Explicitly regenerate selected descriptions with unknown provenance using configured vision endpoints')
            cmd.add_argument('--resume',action='store_true',help='Normal processing already resumes unchanged completed files')
        if name=='search':
            cmd.add_argument('query');cmd.add_argument('--top-k',type=int,default=None);cmd.add_argument('--types',default='');cmd.add_argument('--offset',type=int,default=0)
        if name=='backup':cmd.add_argument('destination')
    args=parser.parse_args(argv)
    from config_loader import load_config
    load_config(args.config)
    try:
        if args.command=='serve':
            from server import main
            return main()
        if args.command=='token':
            from security import get_api_token
            print(get_api_token());return 0
        if args.command=='init-db':init_db();print('Database initialized');return 0
        if args.command=='backup':
            target=Path(args.destination).expanduser().resolve()
            if target.exists():raise ValueError('Backup destination already exists; choose a new file')
            target.parent.mkdir(parents=True,exist_ok=True)
            with get_db() as source,sqlite3.connect(target) as destination:source.backup(destination)
            print('Database backup complete');return 0
        exit_code=0
        if args.command=='search':
            from search_numpy import search_documents
            result=search_documents(args.query,top_k=args.top_k,file_types=args.types,offset=args.offset)
        else:
            from jobs import run_sync,stage_task,stage_outcome
            kind={'scan':'scan','extract-meta':'extract','process':'process'}[args.command]
            options={'limit':args.limit,'types':args.types,'force':args.force,'regenerate_legacy_descriptions':args.regenerate_legacy_descriptions} if kind=='process' else None
            result=run_sync(kind,stage_task(kind,options=options))
            exit_code=0 if stage_outcome(kind,result).status=='completed' else 1
        print(json.dumps(result,ensure_ascii=True,indent=2))
        return exit_code
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr);return 1


def run_rag_process(**kwargs):return process_files(**kwargs)
def run_scan():
    from file_scanner import sync
    return sync()
def run_extract_meta():
    from metadata_extractor import extract_metadata
    return extract_metadata()
def run_search(query,top_k=None):
    from search_numpy import search_documents
    return search_documents(query,top_k=top_k)
def main():return main_entry()
if __name__=='__main__':raise SystemExit(main_entry())
