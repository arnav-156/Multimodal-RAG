"""T07: real parser/SQLite ownership and stale-version rejection; no model acceptance claims."""
from pathlib import Path
import sqlite3
import sys
import pytest
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
from database import connect,initialize
import extractors

@pytest.fixture
def databases(tmp_path):
    cl.reset_config_cache();root=tmp_path/'corpus';root.mkdir();a=tmp_path/'a.db';b=tmp_path/'b.db'
    cfg=tmp_path/'config.yaml';cfg.write_text(yaml.safe_dump({'database':{'path':str(a)},'scanner':{'scan_paths':[str(root)]},'chunking':{'min_text_chars':1000},'embedding':{'dimension':4,'native_dimension':4}}),encoding='utf8');cl.load_config(cfg);initialize(a);initialize(b)
    source=root/'small.fb2';source.write_text('<FictionBook><body><section><p>Short book.</p></section></body></FictionBook>',encoding='utf8')
    from file_scanner import sync
    sync(db_path=a);sync(db_path=b)
    yield a,b,source
    cl.reset_config_cache()


def test_real_parser_returns_diagnostic_without_database_mutation(databases):
    a,b,source=databases
    with pytest.raises(extractors.ExtractionError):extractors.extract(1,str(source),'.fb2','ebook')
    with connect(a) as c:
        assert c.execute('SELECT COUNT(*) FROM rag_ocr_status').fetchone()[0]==0
        assert c.execute('SELECT is_deleted FROM files WHERE id=1').fetchone()[0]==0
    outcome=extractors.extract_outcome(1,str(source),'.fb2','ebook')
    assert outcome.status=='error' and outcome.error and not outcome.chunks
    assert any(d['reason'].startswith('ebook_short_') for d in outcome.diagnostics)


def test_missing_source_never_marks_database_deleted(databases):
    a,b,source=databases;source.unlink()
    with pytest.raises(extractors.ExtractionError):extractors._extract_unbounded(1,str(source),'.fb2','ebook')
    with connect(a) as c:assert c.execute('SELECT is_deleted FROM files WHERE id=1').fetchone()[0]==0


class NoInferenceExpected:
    embedding_dim=4
    identity={'model':'NO INFERENCE EXPECTED'}
    def fingerprint(self):return 't07-persistence-only'
    def encode(self,*args,**kwargs):raise AssertionError('Failed real parser must not reach inference')


def test_explicit_pipeline_database_owns_recovery_details(databases):
    from run_pipeline import process_files
    a,b,source=databases
    result=process_files(db_path=b,embedder=NoInferenceExpected())
    assert result['failed']==1
    with connect(a) as c:
        assert c.execute('SELECT COUNT(*) FROM rag_ocr_status').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM rag_status').fetchone()[0]==0
    with connect(b) as c:
        assert c.execute('SELECT status FROM rag_status').fetchone()[0]=='error'
        assert 'ebook_short_' in c.execute('SELECT reason FROM rag_ocr_status').fetchone()[0]


def test_stale_pipeline_failure_cannot_overwrite_newer_pending_state(databases):
    from run_pipeline import process_files
    import numpy as np
    a,b,source=databases;source=source.with_suffix('.txt');source.write_text('A complete useful text document.',encoding='utf8')
    cl.save_config({'chunking':{'min_text_chars':1}})
    from file_scanner import sync
    sync(db_path=b)
    with connect(b) as c:fid=c.execute("SELECT id FROM files WHERE name='small.txt'").fetchone()[0]
    class ConcurrentRevision(NoInferenceExpected):
        def encode(self,texts,**kwargs):
            with connect(b) as c:
                c.execute("UPDATE files SET file_hash='new-source-version' WHERE id=?",(fid,))
                c.execute("UPDATE rag_status SET status='pending',source_hash='new-source-version',error='newer operation' WHERE file_id=?",(fid,))
            return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)
    result=process_files(db_path=b,file_ids=[fid],embedder=ConcurrentRevision())
    assert result['failed']==1
    with connect(b) as c:
        status=c.execute('SELECT status,error,source_hash FROM rag_status WHERE file_id=?',(fid,)).fetchone()
        assert tuple(status)==('pending','newer operation','new-source-version')
        assert c.execute('SELECT COUNT(*) FROM rag_chunks WHERE file_id=?',(fid,)).fetchone()[0]==0


def test_stale_description_failure_preserves_newer_description(databases,monkeypatch):
    import describer
    a,b,source=databases
    from file_scanner import file_fingerprint
    def fail_after_new_revision(path):
        with connect(b) as c:
            c.execute("UPDATE files SET file_hash='new-source-version' WHERE id=1")
            c.execute("INSERT INTO rag_descriptions(file_id,description,source) VALUES (1,'newer description','newer generation')")
        raise ValueError('controlled render failure after newer version')
    monkeypatch.setattr(describer,'render_file_to_images',fail_after_new_revision)
    success,reason=describer._process_file(1,str(source),'fixture',b)
    assert not success
    with connect(b) as c:assert tuple(c.execute('SELECT description,source FROM rag_descriptions').fetchone())==('newer description','newer generation')


def test_real_parser_never_opens_sqlite_and_diagnostics_do_not_leak(databases,monkeypatch):
    a,b,source=databases;calls=[]
    original=sqlite3.connect
    def observed(*args,**kwargs):
        calls.append(args);return original(*args,**kwargs)
    monkeypatch.setattr(sqlite3,'connect',observed)
    failed=extractors.extract_outcome(1,str(source),'.fb2','ebook')
    text=source.with_suffix('.txt');text.write_text('A useful standalone note.',encoding='utf8')
    complete=extractors.extract_outcome(2,str(text),'.txt','text')
    assert failed.diagnostics and complete.status=='done' and not complete.diagnostics
    assert calls==[]


def test_description_embedding_rejects_changed_database_source_version(databases):
    from description_embedder import process_batch
    from database import SourceVersionChanged
    import numpy as np
    a,b,source=databases
    description='A useful diagram description.'
    with connect(b) as c:c.execute('INSERT INTO rag_descriptions(file_id,description,source) VALUES (1,?,?)',(description,'test-substituted-generation'))
    with connect(b) as c:
        from description_contract import generation_fingerprint
        c.execute('UPDATE rag_descriptions SET source_hash=(SELECT file_hash FROM files WHERE files.id=rag_descriptions.file_id),generation_fingerprint=?',(generation_fingerprint(),))
    class ConcurrentRevision(NoInferenceExpected):
        def encode(self,texts,**kwargs):
            with connect(b) as c:c.execute("UPDATE files SET file_hash='new-source-version' WHERE id=1")
            return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)
    with pytest.raises(SourceVersionChanged):process_batch([(1,description,'test-substituted-generation')],embedder=ConcurrentRevision(),db_path=b)
    with connect(b) as c:
        assert c.execute('SELECT embedded FROM rag_descriptions WHERE file_id=1').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==0
