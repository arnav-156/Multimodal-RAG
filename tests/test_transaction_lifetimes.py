"""T10 real WAL/lock interleavings, finite paged selection and spool cleanup."""
from concurrent.futures import ThreadPoolExecutor,TimeoutError
from pathlib import Path
import json
import sqlite3
import sys
import threading
import time
import pytest
import yaml
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
from database import initialize,connect
import jobs

@pytest.fixture
def configured(tmp_path):
    cl.reset_config_cache();root=tmp_path/'corpus';root.mkdir();p=tmp_path/'config.yaml';db=tmp_path/'index.db'
    p.write_text(yaml.safe_dump({'database':{'path':str(db)},'scanner':{'scan_paths':[str(root)]},'embedding':{'dimension':4,'native_dimension':4},'pipeline':{'fetch_limit':1}}),encoding='utf8')
    effective=cl.load_config(p)
    assert effective['database']['path']==str(db)
    initialize();assert db.exists();jobs.startup()
    yield root,db
    assert jobs.shutdown(timeout=5)['complete'];jobs.startup();cl.reset_config_cache()


def test_main_database_writer_is_free_while_hashing(configured,monkeypatch):
    import file_scanner
    root,db=configured;(root/'one.txt').write_text('Actual hash fixture.',encoding='utf8')
    entered=threading.Event();release=threading.Event();original=file_scanner.file_fingerprint
    def paused(path):entered.set();assert release.wait(5);return original(path)
    monkeypatch.setattr(file_scanner,'file_fingerprint',paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        scan=pool.submit(file_scanner.sync)
        try:
            assert entered.wait(3)
            with sqlite3.connect(db,timeout=.1) as c:
                c.execute('BEGIN IMMEDIATE');c.rollback()
        finally:release.set()
        assert scan.result(timeout=5)['inserted']==1


def test_duplicate_rejects_before_paused_hash_releases(configured,monkeypatch):
    import file_scanner
    root,db=configured;(root/'one.txt').write_text('Actual scan fixture.',encoding='utf8')
    entered=threading.Event();release=threading.Event();original=file_scanner.file_fingerprint
    def paused(path):entered.set();assert release.wait(5);return original(path)
    monkeypatch.setattr(file_scanner,'file_fingerprint',paused)
    job=jobs.submit('scan',jobs.stage_task('scan'));assert entered.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        duplicate=pool.submit(jobs.reserve,'scan',db)
        try:
            with pytest.raises(jobs.JobConflict):duplicate.result(timeout=.3)
            assert not release.is_set()
        finally:release.set()
        try:duplicate.result(timeout=5)
        except jobs.JobConflict:pass
    assert jobs.shutdown(timeout=5)['complete']


class ProbeEmbedding:
    embedding_dim=4;identity={'model':'T10 SQL/WAL SUBSTITUTE'}
    def fingerprint(self):return 't10-transaction-fixture'
    def encode(self,texts,**kwargs):return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)


def test_inference_has_no_open_source_reader_and_checkpoint_completes(configured):
    from file_scanner import sync
    from run_pipeline import process_files
    root,db=configured
    for n in range(3):(root/f'{n}.txt').write_text(f'Actual source document {n}.',encoding='utf8')
    sync()
    with connect() as c:c.execute('CREATE TABLE wal_probe(value INTEGER)')
    observations=[]
    class DuringInference(ProbeEmbedding):
        def encode(self,texts,**kwargs):
            with connect(db) as c:c.executemany('INSERT INTO wal_probe VALUES (?)',[(i,) for i in range(20)])
            with sqlite3.connect(db,timeout=0) as c:
                c.execute('PRAGMA busy_timeout=0');observations.append(c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
            return super().encode(texts,**kwargs)
    result=process_files(embedder=DuringInference())
    assert result['processed']==3
    assert observations and all(row==(0,0,0) for row in observations),observations


def test_finite_source_high_water_excludes_new_ids(configured):
    from file_scanner import sync
    from run_pipeline import process_files
    root,db=configured;(root/'first.txt').write_text('First source.',encoding='utf8');sync();added=[]
    class DuringInference(ProbeEmbedding):
        def encode(self,texts,**kwargs):
            if not added:
                new=root/'later.txt';new.write_text('New source after job selection.',encoding='utf8');sync();added.append(True)
            return super().encode(texts,**kwargs)
    first=process_files(embedder=DuringInference());assert first['processed']==1
    with connect() as c:assert c.execute('SELECT COUNT(*) FROM rag_status').fetchone()[0]==1
    second=process_files(embedder=ProbeEmbedding());assert second['processed']==1 and second['skipped']==1


def test_duplicate_read_rejects_with_an_unrelated_writer_held(configured):
    root,db=configured;jid=jobs.reserve('active',db)
    with sqlite3.connect(db,timeout=.1) as writer,ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute('BEGIN IMMEDIATE')
        future=pool.submit(jobs.reserve,'duplicate',db)
        try:
            with pytest.raises(jobs.JobConflict):future.result(timeout=.3)
        finally:writer.rollback()
        with pytest.raises(jobs.JobConflict):future.result(timeout=3)
    with connect(db) as c:c.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(jid,))


def test_owned_spool_is_excluded_and_removed_even_inside_selected_root(configured,monkeypatch):
    import file_scanner
    root,db=configured;(root/'source.txt').write_text('Retain only this user fixture.',encoding='utf8')
    original=file_scanner.tempfile.TemporaryDirectory;created=[]
    def inside_root(*args,**kwargs):
        kwargs['dir']=root;temporary=original(*args,**kwargs);created.append(Path(temporary.name));return temporary
    monkeypatch.setattr(file_scanner.tempfile,'TemporaryDirectory',inside_root)
    result=file_scanner.sync();assert result['inserted']==1 and result['scanned']==1
    assert created and all(not path.exists() for path in created)
    with connect(db) as c:assert [row[0] for row in c.execute('SELECT name FROM files')]==['source.txt']


def test_failed_staging_preserves_prior_data_and_cleans_spool(configured,monkeypatch):
    import file_scanner
    root,db=configured;(root/'source.txt').write_text('Prior source.',encoding='utf8');file_scanner.sync()
    with connect(db) as c:before=[tuple(row) for row in c.execute('SELECT * FROM files')]
    original=file_scanner.tempfile.TemporaryDirectory;created=[]
    def owned(*args,**kwargs):
        temporary=original(*args,**kwargs);created.append(Path(temporary.name));return temporary
    monkeypatch.setattr(file_scanner.tempfile,'TemporaryDirectory',owned)
    def unreadable(path):raise PermissionError('controlled source read failure')
    monkeypatch.setattr(file_scanner,'file_fingerprint',unreadable)
    result=file_scanner.sync();assert result['errors'] and result['inserted']==result['deleted']==0
    assert created and all(not path.exists() for path in created)
    with connect(db) as c:assert [tuple(row) for row in c.execute('SELECT * FROM files')]==before
