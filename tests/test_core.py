"""Core regressions: B01/B05/B11/B30 and versioned persistent vectors."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def test_cli_help_portable():
    p=subprocess.run([sys.executable,'-B',str(ROOT/'run_pipeline.py'),'--help'],capture_output=True,text=True,env=dict(os.environ,PYTHONUTF8='1'))
    assert p.returncode==0,p.stderr
    assert 'scan' in p.stdout and 'process' in p.stdout

def test_fresh_schema_and_cascade(tmp_path):
    from database import initialize,connect
    db=tmp_path/'fresh.db';initialize(db)
    with connect(db) as c:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
        assert c.execute('PRAGMA user_version').fetchone()[0]>=1
        for name in ['files','rag_chunks','rag_status','rag_descriptions','rag_ocr_status','jobs','index_metadata']:
            assert c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(name,)).fetchone()
        c.execute("INSERT INTO files(path,name) VALUES ('fixture.txt','fixture.txt')")
        fid=c.execute('SELECT id FROM files').fetchone()[0]
        c.execute("INSERT INTO rag_chunks(file_id,chunk_index,vector) VALUES (?,0,?)",(fid,b'123'))
        c.execute('DELETE FROM files WHERE id=?',(fid,))
        assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==0

@pytest.mark.parametrize('bad',[np.zeros(4),[1,float('nan'),2,3],[1,float('inf'),2,3],[1,2,3]])
def test_invalid_vector_rejected(bad):
    from vectors import serialize_vector
    with pytest.raises(ValueError):serialize_vector(bad,dimension=4)

def test_vector_roundtrip_and_corruption():
    from vectors import serialize_vector,deserialize_vector
    v=np.array([1.,2.,3.,4.],dtype=np.float32);v/=np.linalg.norm(v)
    blob=serialize_vector(v,dimension=4)
    assert len(blob)==4
    assert np.dot(v,deserialize_vector(blob,dimension=4))>.999
    with pytest.raises(ValueError):deserialize_vector(b'wrong',dimension=4)

def test_legacy_migration_retains_files_and_rejects_unknown_vectors(tmp_path):
    from database import initialize,connect
    p=tmp_path/'legacy.db'
    with sqlite3.connect(p) as c:
        c.executescript("CREATE TABLE files(id INTEGER PRIMARY KEY,path TEXT UNIQUE,name TEXT);CREATE TABLE rag_chunks(id INTEGER PRIMARY KEY,file_id INTEGER,chunk_index INTEGER,meta_json TEXT,vector BLOB);INSERT INTO files VALUES(1,'old.txt','old.txt');INSERT INTO rag_chunks VALUES(1,1,0,'{}',X'0102');INSERT INTO rag_chunks VALUES(2,999,0,'{}',X'0102');")
    initialize(p);initialize(p)
    with connect(p) as c:
        assert c.execute('SELECT path FROM files').fetchone()[0]=='old.txt'
        assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==1
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        assert 'root_path' in {r[1] for r in c.execute('PRAGMA table_info(files)')}
