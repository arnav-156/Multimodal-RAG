"""Description identity/persistence tests with declared generation substitutes."""
from pathlib import Path
import json
import numpy as np
import pytest
import yaml
import config_loader as cl
from database import initialize,connect
from file_scanner import sync


@pytest.fixture
def library(tmp_path):
    cl.reset_config_cache();root=tmp_path/'corpus';root.mkdir();db=tmp_path/'index.db';profile=tmp_path/'config.yaml'
    profile.write_text(yaml.safe_dump({'database':{'path':str(db)},'scanner':{'scan_paths':[str(root)]},'embedding':{'dimension':4,'native_dimension':4},'vision':{'endpoints':[{'model':'generation-v1','api_base':'http://127.0.0.1:1234/v1'}]}}),encoding='utf8')
    cl.load_config(profile);initialize();p=root/'diagram.png';p.write_bytes(b'generated ownership fixture');sync()
    with connect() as c:c.execute("INSERT INTO rag_ocr_status(file_id,status) VALUES(1,'insufficient')")
    yield p,db
    cl.reset_config_cache()


class Embedding:
    embedding_dim=4
    identity={'model':'DESCRIPTION PERSISTENCE SUBSTITUTE'}
    def __init__(self,version='embedding-v1'):self.version=version;self.calls=0
    def fingerprint(self):return self.version
    def encode(self,texts,**kwargs):self.calls+=1;return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)


def generated(monkeypatch):
    import describer
    calls=[]
    monkeypatch.setattr(describer,'render_file_to_images',lambda _: [b'GENERATED IMAGE SUBSTITUTE'])
    def vision(*args):calls.append(True);return 'Cooling fan shown in the diagram.','controlled-generation'
    monkeypatch.setattr(describer,'call_vision_with_fallback',vision)
    return calls


def test_model_change_regenerates_but_embedding_change_reuses_text(library,monkeypatch):
    import describer,description_embedder
    from run_pipeline import ensure_index_contract
    p,db=library;calls=generated(monkeypatch);first=Embedding();ensure_index_contract(first)
    assert describer.process_pending_descriptions()['processed']==1
    assert description_embedder.drain_descriptions(embedder=first)['processed']==1
    with connect() as c:
        c.execute("INSERT INTO rag_status(file_id,status,source_hash) SELECT id,'done',file_hash FROM files")
    import server
    assert len(server.document(1)['chunks'])==1
    assert describer.process_pending_descriptions()['processed']==0
    assert description_embedder.drain_descriptions(embedder=first)['processed']==0
    assert len(calls)==1 and first.calls==1
    second=Embedding('embedding-v2');ensure_index_contract(second,force=True)
    assert describer.process_pending_descriptions()['processed']==0
    assert description_embedder.drain_descriptions(embedder=second)['processed']==1
    assert len(calls)==1 and second.calls==1
    cl.save_config({'vision':{'endpoints':[{'model':'generation-v2','api_base':'http://127.0.0.1:1234/v1'}]}})
    # force=True removed the status above; restore the completed owner state to
    # isolate generation freshness at the document-inspection boundary.
    with connect() as c:
        c.execute("INSERT INTO rag_status(file_id,status,source_hash) SELECT id,'done',file_hash FROM files")
    assert server.document(1)['chunks']==[]
    stale=description_embedder.drain_descriptions(embedder=second)
    assert stale['processed']==0 and stale['stale_at_start']==1 and second.calls==1
    assert describer.process_pending_descriptions()['processed']==1
    assert description_embedder.drain_descriptions(embedder=second)['processed']==1
    assert len(calls)==2
    with connect() as c:
        row=c.execute('SELECT * FROM rag_descriptions').fetchone()
        assert row['source_hash'] and row['generation_fingerprint'] and row['embedding_fingerprint']=='embedding-v2'
        meta=json.loads(c.execute('SELECT meta_json FROM rag_chunks').fetchone()[0])
        assert meta['kind']=='description' and meta['generation_fingerprint']==row['generation_fingerprint']


def test_legacy_requires_explicit_regeneration_and_scope(library,monkeypatch):
    import describer,description_embedder
    p,db=library;calls=generated(monkeypatch)
    with connect() as c:c.execute("INSERT INTO rag_descriptions(file_id,description,source) VALUES(1,'Unknown provenance text','legacy')")
    assert describer.process_pending_descriptions()['processed']==0 and calls==[]
    with pytest.raises(ValueError,match='unverified|Legacy'):
        description_embedder.drain_descriptions(embedder=Embedding())
    assert describer.process_pending_descriptions(regenerate_legacy=True)['processed']==1 and len(calls)==1
    cl.save_config({'scanner':{'scan_paths':[]}})
    assert describer.process_pending_descriptions(regenerate_legacy=True)['processed']==0
    assert description_embedder.drain_descriptions(embedder=Embedding())['processed']==0
    assert len(calls)==1


def test_contract_excludes_transport_secrets_and_includes_prompt_policy(library):
    from description_contract import generation_fingerprint
    cfg=cl.get_config();before=generation_fingerprint(cfg)
    cfg['vision']['endpoints'][0].update(api_key='secret',timeout=120,rate_limit=2)
    assert generation_fingerprint(cfg)==before
    cfg['vision']['endpoints'][0]['max_tokens']=1024
    assert generation_fingerprint(cfg)!=before


def test_stale_description_work_is_a_partial_job_not_false_completion():
    from jobs import stage_outcome
    assert stage_outcome('process',{'failed':0,'descriptions':{'stale_at_start':3}}).status=='partial'


def test_new_generation_has_its_own_retry_budget(library,monkeypatch):
    import describer
    generated(monkeypatch)
    monkeypatch.setattr(describer,'call_vision_with_fallback',lambda *args:(None,None))
    for expected in (1,2):
        assert describer.process_pending_descriptions()['failed']==1
        with connect() as c:assert c.execute('SELECT retry_count FROM rag_descriptions').fetchone()[0]==expected
    cl.save_config({'vision':{'endpoints':[{'model':'generation-v2','api_base':'http://127.0.0.1:1234/v1'}]}})
    assert describer.process_pending_descriptions()['failed']==1
    with connect() as c:assert c.execute('SELECT retry_count FROM rag_descriptions').fetchone()[0]==1


def test_version_one_migration_preserves_unknown_provenance(library):
    p,db=library
    with connect() as c:
        c.execute('DROP TABLE rag_descriptions')
        c.execute('CREATE TABLE rag_descriptions(file_id INTEGER PRIMARY KEY,description TEXT,source TEXT,embedded INTEGER DEFAULT 0,retry_count INTEGER DEFAULT 0,error TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        c.execute("INSERT INTO rag_descriptions(file_id,description,source) VALUES(1,'preserve me','legacy')")
        c.execute('PRAGMA user_version=1')
    initialize(db)
    with connect(db) as c:
        row=c.execute('SELECT * FROM rag_descriptions').fetchone()
        assert row['description']=='preserve me' and row['generation_fingerprint'] is None and row['source_hash'] is None
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
