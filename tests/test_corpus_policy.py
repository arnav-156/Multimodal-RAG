"""Selected-corpus consistency; substitutes count inference, never model quality."""
import json
from pathlib import Path
import numpy as np
import pytest
import yaml
import config_loader as cl
from database import initialize,connect
from file_scanner import sync


class Embedding:
    embedding_dim=4
    identity={'model':'T06 POLICY SUBSTITUTE'}
    def fingerprint(self):return 'corpus-policy-test'
    def encode(self,texts,**kw):return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)
    def close(self):pass
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


@pytest.fixture
def library(tmp_path):
    cl.reset_config_cache();a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    profile=tmp_path/'config.yaml';db=tmp_path/'index.db'
    profile.write_text(yaml.safe_dump({'database':{'path':str(db)},'scanner':{'scan_paths':[str(a)]},'embedding':{'dimension':4,'native_dimension':4},'ocr':{'enabled':False}}),encoding='utf8')
    cl.load_config(profile);initialize()
    yield a,b,db
    cl.reset_config_cache()


def choose(root):
    cfg=cl.get_config();cfg['scanner']['scan_paths']=[str(root)]
    cl.save_config(cfg)


def test_empty_selected_root_has_zero_stats_readiness_and_search(library,monkeypatch):
    from run_pipeline import process_files
    from search_numpy import search_documents
    import server,jina_v5_embedding
    a,b,db=library;(a/'guide.txt').write_text('Restore the library from backup.')
    sync();assert process_files(embedder=Embedding())['processed']==1
    monkeypatch.setattr(jina_v5_embedding,'JinaV5Embedder',Embedding)
    assert server.stats()['files']==1
    choose(b)
    assert server.stats()=={'files':0,'chunks':0,'statuses':{}}
    health=json.loads(server.status().body)
    assert health['ready'] is False and health['index']['chunks']==0
    class NoCalls(Embedding):
        def fingerprint(self):raise AssertionError('Empty corpus needs no model call')
        def encode(self,*args,**kwargs):raise AssertionError('Empty corpus needs no model call')
    assert search_documents('restore',embedder=NoCalls())['total']==0
    with connect(db) as c:assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==1


def test_pending_selection_filters_scope_before_limits(library):
    import describer,description_embedder
    a,b,db=library
    with connect(db) as c:
        for root in (a,b):
            p=root/'image.png';p.write_bytes(b'policy fixture')
            fid=c.execute('INSERT INTO files(path,name,ext) VALUES(?,?,?)',(str(p),p.name,'.png')).lastrowid
            c.execute("INSERT INTO rag_ocr_status(file_id,status) VALUES(?,'insufficient')",(fid,))
            c.execute("INSERT INTO rag_descriptions(file_id,description,source) VALUES(?,'generated text','vision_failed')",(fid,))
    with connect(db) as c:
        from description_contract import generation_fingerprint
        c.execute('UPDATE rag_descriptions SET generation_fingerprint=?',(generation_fingerprint(),))
    choose(b)
    assert describer.get_ocr_failed_files(limit=1,db_path=db)[0][0]==2
    with connect(db) as c:c.execute("UPDATE rag_descriptions SET source='local'")
    assert description_embedder.get_pending_descriptions(limit=1,db_path=db)[0][0]==2


def test_revoked_sources_cause_no_reads_or_model_calls(library,monkeypatch):
    import describer,description_embedder,metadata_extractor,file_scanner
    from run_pipeline import process_files
    a,b,db=library;p=a/'image.png';p.write_bytes(b'selected then revoked')
    sync()
    with connect(db) as c:
        fid=c.execute('SELECT id FROM files').fetchone()[0]
        c.execute("INSERT INTO rag_ocr_status(file_id,status) VALUES(?,'insufficient')",(fid,))
        c.execute("INSERT INTO rag_descriptions(file_id,description,source) VALUES(?,'pending description','local')",(fid,))
    snapshot=cl.get_config();choose(b)
    def forbidden(*args,**kwargs):raise AssertionError('Revoked source must not reach I/O or inference')
    monkeypatch.setattr(file_scanner,'file_fingerprint',forbidden)
    monkeypatch.setattr(describer,'render_file_to_images',forbidden)
    monkeypatch.setattr(description_embedder,'get_jina_embedder',forbidden)
    monkeypatch.setattr(metadata_extractor,'_run_with_timeout',forbidden)
    # An already-admitted snapshot cannot override a later root revocation.
    with cl.operation_settings(snapshot):
        assert describer._process_file(fid,str(p),'insufficient',db)[1]=='source_out_of_scope'
        assert description_embedder.process_batch([(fid,'pending description','local')],db_path=db)==0
        assert metadata_extractor.extract_metadata(db_path=db)['done']==0
        assert process_files(db_path=db)['processed']==0


def test_freshness_and_deleted_status_do_not_count_as_searchable(library):
    import server
    from run_pipeline import process_files
    a,b,db=library;p=a/'guide.txt';p.write_text('A current source.')
    sync();process_files(embedder=Embedding())
    with connect(db) as c:c.execute("UPDATE rag_status SET source_hash='obsolete'")
    assert server.stats()['chunks']==0
    with connect(db) as c:c.execute('UPDATE files SET is_deleted=1')
    assert server.stats()=={'files':0,'chunks':0,'statuses':{}}
