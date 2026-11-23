"""T01: real runtime gate, concurrent WAL operations, migration and backup/restore."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import sys
import threading
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import initialize,connect
from tools.verify_runtime import sqlite_wal_fix_present,runtime_provenance

@pytest.mark.parametrize(('version','patched'),[
    ((3,44,5),False),((3,44,6),True),((3,49,1),False),((3,50,6),False),
    ((3,50,7),True),((3,51,2),False),((3,51,3),True),((3,52,0),True),
])
def test_documented_sqlite_fix_boundaries(version,patched):
    assert sqlite_wal_fix_present(version) is patched


def test_actual_interpreter_has_sqlite_wal_fix():
    report=runtime_provenance()
    assert report['sqlite_wal_fix_present'],f"Unpatched actual runtime: Python {report['python_version']} / SQLite {report['sqlite_version']}"
    assert report['sqlite_source_id'] and report['python_executable_sha256']


def test_actual_concurrent_writers_and_checkpoint_integrity(tmp_path):
    db=tmp_path/'wal.db';initialize(db)
    with connect(db) as c:c.execute('CREATE TABLE runtime_probe(key TEXT PRIMARY KEY,value INTEGER NOT NULL)')
    start=threading.Barrier(3);finish=threading.Event()
    def writer(prefix):
        with connect(db) as c:
            start.wait(timeout=5)
            for n in range(40):
                c.execute('INSERT INTO runtime_probe(key,value) VALUES (?,?)',(f'{prefix}-{n}',n));c.commit()
        return 40
    def checkpoint():
        count=0
        with connect(db) as c:
            start.wait(timeout=5)
            while not finish.is_set():
                c.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone();count+=1
                finish.wait(.001)
        return count
    with ThreadPoolExecutor(max_workers=3) as pool:
        a=pool.submit(writer,'a');b=pool.submit(writer,'b');check=pool.submit(checkpoint)
        try:assert a.result(timeout=15)+b.result(timeout=15)==80
        finally:finish.set()
        assert check.result(timeout=5)>=1
    with connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM runtime_probe').fetchone()[0]==80
        assert c.execute('SELECT SUM(value) FROM runtime_probe').fetchone()[0]==1560
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_actual_legacy_migration_backup_and_restore(tmp_path):
    original=tmp_path/'legacy.db';backup=tmp_path/'restored.db'
    with sqlite3.connect(original) as c:
        c.executescript("CREATE TABLE files(id INTEGER PRIMARY KEY,path TEXT UNIQUE,name TEXT);CREATE TABLE rag_chunks(file_id INTEGER,chunk_index INTEGER,meta_json TEXT,vector BLOB);INSERT INTO files VALUES(1,'fixture.txt','fixture.txt');INSERT INTO rag_chunks VALUES(1,0,'{}',X'0102');INSERT INTO rag_chunks VALUES(999,0,'{}',X'0102');")
    initialize(original)
    with connect(original) as source,sqlite3.connect(backup) as destination:source.backup(destination)
    initialize(backup)
    with connect(backup) as c:
        assert c.execute('SELECT name FROM files WHERE id=1').fetchone()[0]=='fixture.txt'
        assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM legacy_rag_chunks_v0').fetchone()[0]==2
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
