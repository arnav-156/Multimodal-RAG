"""Shared bounded, vectorized retrieval for CLI and HTTP, grouped by file."""
from config_loader import configured_operation
import heapq
import json
import os
import numpy as np
from database import connect
from corpus_policy import CorpusPolicy, lexical_path, under, CURRENT_CHUNKS
from vectors import deserialize_vector,validate_vector

@configured_operation
def search_documents(query,top_k=None,file_types='',path='',offset=0,page_size=20,embedder=None,db_path=None):
    from config_loader import get_config
    from jina_v5_embedding import JinaV5Embedder
    from format_capabilities import classify_file
    cfg=get_config()
    filter_root=lexical_path(path) if path else None
    if not isinstance(query,str) or not query.strip() or len(query)>8000:raise ValueError('Query must contain 1-8000 characters')
    top_k=cfg['search']['default_top_k'] if top_k is None else top_k
    if not 1<=top_k<=cfg['search']['max_top_k']:raise ValueError('top_k exceeds configured bounds')
    if offset<0 or not 1<=page_size<=100:raise ValueError('Invalid pagination')
    kinds={v.strip() for v in (file_types.split(',') if isinstance(file_types,str) else file_types or []) if v.strip()}
    with connect(db_path) as c:
        CorpusPolicy().install(c)
        present=c.execute('SELECT 1 FROM rag_chunks ch JOIN files f ON f.id=ch.file_id JOIN rag_status r ON r.file_id=f.id WHERE '+CURRENT_CHUNKS+' LIMIT 1').fetchone()
    if not present:
        return {'query':query,'results':[],'total':0,'eligible_files':0,'offset':offset,'page_size':page_size,'skipped_invalid':0}
    owned=embedder is None;embedder=embedder or JinaV5Embedder()
    try:
        fingerprint=embedder.fingerprint()
        q=validate_vector(embedder.encode([query],prompt_name='query')[0],embedder.embedding_dim)
        heap=[];invalid=0;batch=[];active=None;eligible=0
        def offer(result):
            nonlocal eligible
            if result is None:return
            eligible+=1
            item=(result['score'],result['path'],result)
            if len(heap)<top_k:heapq.heappush(heap,item)
            elif item[:2]>heap[0][:2]:heapq.heapreplace(heap,item)
        def flush():
            nonlocal active
            if not batch:return
            scores=np.stack([v for v,r in batch])@q
            for score,(_,result) in zip(scores,batch):
                result['score']=float(score)
                if active is not None and active['file_id']!=result['file_id']:offer(active);active=None
                if active is None or result['score']>active['score']:active=result
            batch.clear()
        with connect(db_path) as c:
            CorpusPolicy().install(c)
            contract=c.execute("SELECT value FROM index_metadata WHERE key='embedding_fingerprint'").fetchone()
            if not contract or contract[0]!=fingerprint:raise ValueError('Index/model contract mismatch; run process with the configured model')
            rows=c.execute('''SELECT f.id AS file_id,f.path,f.ext,f.media_meta,f.file_hash,r.source_hash,
               ch.chunk_index,ch.meta_json,ch.vector FROM rag_chunks ch JOIN files f ON f.id=ch.file_id
               JOIN rag_status r ON r.file_id=f.id WHERE '''+CURRENT_CHUNKS+' ORDER BY r.file_id,ch.chunk_index')
            for row in rows:
                if row['file_hash']!=row['source_hash']:continue
                stored=os.path.normcase(os.path.abspath(row['path']))
                if filter_root and not under(stored,filter_root):continue
                kind=classify_file(row['ext'] or '',row['media_meta'])
                if kinds and kind not in kinds and not ('pdf' in kinds and kind.startswith('pdf')):continue
                try:
                    vector=deserialize_vector(row['vector'],embedder.embedding_dim)
                    meta=json.loads(row['meta_json'] or '{}')
                    if not isinstance(meta,dict):raise ValueError('Invalid chunk metadata')
                    text=meta.get('_text','')
                    if not isinstance(text,str):raise ValueError('Invalid chunk text')
                except (ValueError,TypeError,json.JSONDecodeError):invalid+=1;continue
                result={'file_id':row['file_id'],'path':row['path'],'snippet':text[:2000],
                    'meta':{k:v for k,v in meta.items() if k!='_text'},'document_url':f"/api/documents/{row['file_id']}"}
                batch.append((vector,result))
                if len(batch)>=128:flush()
        flush();offer(active)
        # Ties are deterministic by path, descending. Heap retains only top-k files.
        ranked=[item[2] for item in sorted(heap,key=lambda item:item[:2],reverse=True)]
        return {'query':query,'results':ranked[offset:offset+page_size],'total':len(ranked),'eligible_files':eligible,
            'offset':offset,'page_size':page_size,'skipped_invalid':invalid}
    finally:
        if owned:embedder.close()
