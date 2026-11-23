"""Actual SQLite plan/ordering regressions; model substitute is not quality proof."""
import json
import numpy as np
import yaml
import config_loader as cl
from database import initialize,connect
from corpus_policy import CorpusPolicy
from vectors import serialize_vector
import search_numpy

class ControlEmbedding:
    embedding_dim=4
    def fingerprint(self):return 'retrieval-performance-control'
    def encode(self,texts,**kw):return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)

def test_ordered_scan_avoids_payload_sort_and_preserves_boundaries(tmp_path,monkeypatch):
    root=tmp_path/'corpus';root.mkdir()
    profile=tmp_path/'profile.yaml';profile.write_text(yaml.safe_dump({'scanner':{'scan_paths':[str(root)]},'database':{'path':str(tmp_path/'index.db')},'embedding':{'dimension':4,'native_dimension':4}}),encoding='utf8');cl.load_config(profile);initialize()
    vector=serialize_vector([1,0,0,0],4)
    with connect() as c:
        c.execute("INSERT INTO index_metadata VALUES ('embedding_fingerprint','retrieval-performance-control')")
        for fid,path,ext,status,source in [(1,root/'a.txt','.txt','done','current'),(2,root/'b.pdf','.pdf','done','current'),(3,tmp_path/'revoked.txt','.txt','done','current'),(4,root/'stale.txt','.txt','done','old'),(5,root/'failed.txt','.txt','error','current')]:
            c.execute('INSERT INTO files(id,path,name,ext,file_hash,is_deleted,media_meta) VALUES (?,?,?,?,?,0,?)',(fid,str(path),path.name,ext,'current',json.dumps({'type':'hybrid_pdf'})))
            c.execute('INSERT INTO rag_status(file_id,status,source_hash) VALUES (?,?,?)',(fid,status,source))
            for i in range(130 if fid==1 else 3):
                c.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (?,?,?,?)',(fid,i,json.dumps({'_text':f'file{fid}-chunk{i}','provenance':i}),vector))
        invalid=[(b'bad',{'_text':'length'}),(bytes(4),{'_text':'zero'}),(vector,[]),(vector,{'_text':7})]
        for index,(blob,meta) in enumerate(invalid,130):c.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (1,?,?,?)',(index,json.dumps(meta),blob))
        c.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (1,134,?,?)',('{bad',vector))
        c.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (1,-1,?,?)',(json.dumps({'_text':'stale description'}),vector))
    trace=[];original=search_numpy.connect
    def traced(*args,**kwargs):
        c=original(*args,**kwargs);c.set_trace_callback(trace.append);return c
    monkeypatch.setattr(search_numpy,'connect',traced)
    result=search_numpy.search_documents('query',embedder=ControlEmbedding(),top_k=10,page_size=1)
    assert result['total']==result['eligible_files']==2 and result['skipped_invalid']==5
    assert result['results'][0]['file_id']==2 and result['results'][0]['snippet']=='file2-chunk0'
    second=search_numpy.search_documents('query',embedder=ControlEmbedding(),top_k=10,page_size=1,offset=1)
    assert second['results'][0]['file_id']==1 and second['results'][0]['snippet']=='file1-chunk0'
    pdf=search_numpy.search_documents('query',embedder=ControlEmbedding(),file_types='pdf')
    assert [x['file_id'] for x in pdf['results']]==[2]
    sql=next(q for q in trace if 'SELECT f.id AS file_id' in q)
    with connect() as c:
        CorpusPolicy().install(c);plan=[tuple(row) for row in c.execute('EXPLAIN QUERY PLAN '+sql)]
    assert not any('TEMP B-TREE' in row[-1] for row in plan),plan
