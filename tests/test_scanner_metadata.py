"""Scanner/metadata lifecycle regressions: real fixture files and SQLite."""
import importlib
import inspect
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import file_scanner as scanner


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    root = tmp_path / 'documents'
    root.mkdir()
    db = tmp_path / 'index.db'
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE files(id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT UNIQUE,
                name TEXT,ext TEXT,size INTEGER,mtime REAL,ctime REAL,atime REAL,
                is_dir INTEGER DEFAULT 0,depth INTEGER,scanned_at TEXT,
                is_deleted INTEGER DEFAULT 0,file_hash TEXT,mime_type TEXT,media_meta TEXT,
                root_path TEXT);
            CREATE TABLE rag_chunks(id INTEGER PRIMARY KEY,file_id INTEGER,chunk_index INTEGER,
                meta_json TEXT,vector BLOB,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(file_id) REFERENCES files(id));
            CREATE TABLE rag_status(file_id INTEGER PRIMARY KEY,status TEXT,
                chunks_written INTEGER DEFAULT 0,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE rag_descriptions(file_id INTEGER PRIMARY KEY,description TEXT,
                source TEXT,embedded INTEGER DEFAULT 0,retry_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE rag_ocr_status(file_id INTEGER PRIMARY KEY,status TEXT,reason TEXT,
                checked_at TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')
    cfg = {'database': {'path': str(db)}, 'scanner': {
        'scan_paths': [str(root)], 'home_paths': [], 'everything_exe': '',
        'skip_dirs': ['.git'], 'skip_files': ['.env', '.env.*', '*.pem', '*.key']},
        'metadata': {'workers_per_disk': 1, 'file_timeout': 10}}
    import config_loader
    cfg=config_loader.validate_config(cfg)
    monkeypatch.setattr(config_loader, 'get_config', lambda *a, **k: cfg)
    monkeypatch.setattr(config_loader, 'get_live_config', lambda: cfg)
    monkeypatch.setattr(config_loader, 'get_db_path', lambda: str(db))
    # The fallback reaches the original code's public API before the repair.
    for name, value in [('DB_PATH', str(db)), ('SCAN_PATHS', [str(root)]),
                        ('HOME_PATHS', []), ('SKIP_DIRS', {'.git'})]:
        monkeypatch.setattr(scanner, name, value, raising=False)
    return root, db, cfg


def scan(corpus, paths=None, dry_run=False):
    root, db, cfg = corpus
    chosen = [str(root)] if paths is None else [str(p) for p in paths]
    if 'paths' in inspect.signature(scanner.sync).parameters:
        return scanner.sync(dry_run=dry_run, paths=chosen, db_path=str(db))
    scanner.SCAN_PATHS = chosen
    return scanner.sync(dry_run=dry_run)


def state(db):
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute('SELECT * FROM files ORDER BY id')]


def attach_derived(db, fid):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (?,0,'{}',?)", (fid, b'old'))
        conn.execute("INSERT INTO rag_status(file_id,status) VALUES (?,'done')", (fid,))
        conn.execute("INSERT INTO rag_descriptions(file_id,description,source) VALUES (?,'old description','vision')", (fid,))
        conn.execute("INSERT INTO rag_ocr_status(file_id,status,reason) VALUES (?,'insufficient','old')", (fid,))


def test_B10_same_size_same_mtime_edit_invalidates_all_derived_state(corpus):
    root, db, _ = corpus
    path = root / 'note.txt'
    path.write_text('alpha', encoding='utf-8')
    scan(corpus)
    row = state(db)[0]
    attach_derived(db, row['id'])
    before = path.stat()
    path.write_text('bravo', encoding='utf-8')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    scan(corpus)
    updated = state(db)[0]
    assert updated['file_hash'] and updated['file_hash'] != row['file_hash']
    with sqlite3.connect(db) as conn:
        for table in ['rag_chunks', 'rag_status', 'rag_descriptions', 'rag_ocr_status']:
            assert conn.execute(f'SELECT COUNT(*) FROM {table} WHERE file_id=?', (row['id'],)).fetchone()[0] == 0


def test_B11_unavailable_root_preserves_files_and_vectors(corpus):
    root, db, _ = corpus
    (root / 'note.txt').write_text('keep me', encoding='utf-8')
    scan(corpus)
    attach_derived(db, state(db)[0]['id'])
    root.rename(root.with_name('disconnected'))
    scan(corpus)
    assert len(state(db)) == 1
    assert state(db)[0]['is_deleted'] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0] == 1


def test_B11_authoritative_file_deletion_clears_children(corpus):
    root, db, _ = corpus
    path = root / 'note.txt'
    path.write_text('remove from index', encoding='utf-8')
    scan(corpus)
    fid = state(db)[0]['id']
    attach_derived(db, fid)
    path.unlink()
    scan(corpus)
    rows = state(db)
    assert not rows or rows[0]['is_deleted'] == 1
    with sqlite3.connect(db) as conn:
        for table in ['rag_chunks', 'rag_status', 'rag_descriptions', 'rag_ocr_status']:
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_B11_empty_configuration_does_not_delete_prior_roots(corpus):
    root, db, _ = corpus
    (root / 'note.txt').write_text('retain configured-out roots', encoding='utf-8')
    scan(corpus)
    scan(corpus, paths=[])
    assert len(state(db)) == 1 and state(db)[0]['is_deleted'] == 0


def test_B11_walk_failure_is_not_a_successful_empty_snapshot(corpus, monkeypatch):
    root, db, _ = corpus
    (root / 'note.txt').write_text('preserve on permission error', encoding='utf-8')
    scan(corpus)
    original_walk = scanner.os.walk
    def inaccessible(top, *args, onerror=None, **kwargs):
        if Path(top).resolve() == root.resolve():
            if onerror:
                onerror(PermissionError(13, 'audit fixture directory unavailable', str(top)))
            return iter([])
        return original_walk(top, *args, onerror=onerror, **kwargs)
    monkeypatch.setattr(scanner.os, 'walk', inaccessible)
    result = scan(corpus)
    assert result['errors'] and 'directory unavailable' in result['errors'][0]['error']
    assert len(state(db)) == 1 and state(db)[0]['is_deleted'] == 0


def test_B37_no_implicit_home_and_configured_exclusions(corpus, monkeypatch, tmp_path):
    root, db, cfg = corpus
    other = tmp_path / 'personal'
    other.mkdir()
    (other / 'private.txt').write_text('outside configured root', encoding='utf-8')
    cfg['scanner']['home_paths'] = [str(other)]
    monkeypatch.setattr(scanner, 'HOME_PATHS', [str(other)], raising=False)
    (root / 'inside.txt').write_text('explicitly selected', encoding='utf-8')
    (root / '.env.production').write_text('SECRET=fixture-only', encoding='utf-8')
    excluded = root / '.git'
    excluded.mkdir()
    (excluded / 'hidden.txt').write_text('excluded folder', encoding='utf-8')
    scan(corpus)
    assert [row['name'] for row in state(db)] == ['inside.txt']


def test_B37_native_windows_paths_are_not_rewritten_to_wsl():
    value = r'C:\documents\report.txt'
    assert scanner.convert_path(value) == value


def test_scan_dry_run_never_changes_index(corpus):
    root, db, _ = corpus
    (root / 'note.txt').write_text('dry run fixture', encoding='utf-8')
    scan(corpus, dry_run=True)
    assert state(db) == []


def test_B09_metadata_import_is_safe_inside_worker_thread(tmp_path):
    app_dir = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(app_dir), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    code = "import threading; errors=[];\ndef load():\n try: import metadata_extractor\n except BaseException as e: errors.append(str(e))\nt=threading.Thread(target=load);t.start();t.join();assert not errors, errors"
    run = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path, env=env,
                         capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr


def test_B39_metadata_jobs_are_reusable_and_persist_actual_image_data(corpus):
    from PIL import Image
    metadata = importlib.import_module('metadata_extractor')
    root, db, _ = corpus
    for i in range(2):
        Image.new('RGB', (12 + i, 20)).save(root / f'image-{i}.png')
        scan(corpus)
        if 'db_path' in inspect.signature(metadata.extract_metadata).parameters:
            result = metadata.extract_metadata(db_path=str(db), workers=1, timeout=10)
            assert result['errors'] == 0
        else:
            metadata.DB_PATH = str(db)
            metadata.DISK_WORKERS = 1
            metadata.extract_metadata()
        row = next(row for row in state(db) if row['name'] == f'image-{i}.png')
        assert json.loads(row['media_meta'])['width'] == 12 + i


def test_B11_checksum_read_failure_preserves_prior_snapshot(corpus, monkeypatch):
    root, db, _ = corpus
    (root / 'note.txt').write_text('do not erase a failed snapshot', encoding='utf-8')
    scan(corpus)
    attach_derived(db, state(db)[0]['id'])
    def failed_hash(path):
        raise PermissionError('audit fixture read denied')
    monkeypatch.setattr(scanner, 'file_fingerprint', failed_hash)
    result = scan(corpus)
    assert result['errors'] and result['deleted'] == 0
    assert state(db)[0]['is_deleted'] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0] == 1


def test_B11_one_unavailable_root_does_not_block_authoritative_other_root(corpus, tmp_path):
    root, db, _ = corpus
    other = tmp_path / 'other'
    other.mkdir()
    first = root / 'first.txt'
    second = other / 'second.txt'
    first.write_text('available snapshot one', encoding='utf-8')
    second.write_text('available snapshot two', encoding='utf-8')
    scan(corpus, paths=[root, other])
    root.rename(root.with_name('disconnected'))
    second.unlink()
    result = scan(corpus, paths=[root, other])
    rows = {row['name']:row for row in state(db)}
    assert rows['first.txt']['is_deleted'] == 0
    assert rows['second.txt']['is_deleted'] == 1
    assert len(result['errors']) == 1 and result['deleted'] == 1


def test_B37_root_boundary_does_not_reconcile_sibling_prefix(corpus):
    root, db, _ = corpus
    scan(corpus)
    sibling = root.with_name(root.name + '-private') / 'outside.txt'
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO files(path,name,root_path) VALUES (?, ?, NULL)', (str(sibling),sibling.name))
    scan(corpus)
    assert state(db)[0]['is_deleted'] == 0


def test_B39_hard_timeout_reaps_only_its_owned_child(corpus):
    import multiprocessing as mp
    from PIL import Image
    import metadata_extractor as metadata
    root, _, _ = corpus
    path = root / 'timeout.png'
    Image.new('RGB',(4,4)).save(path)
    before = {child.pid for child in mp.active_children()}
    result = metadata._run_with_timeout((1,str(path),'image/png'),.001)
    assert result[2] == 'timeout' and 'exceeded' in result[3]
    assert {child.pid for child in mp.active_children()} <= before


def test_B10_metadata_cannot_commit_a_changed_source_version(corpus, monkeypatch):
    import metadata_extractor as metadata
    root, db, _ = corpus
    path = root / 'changing.png'
    path.write_bytes(b'original version')
    scan(corpus)
    def changed_while_inspecting(args, timeout, stop_event=None):
        path.write_bytes(b'a different source version')
        return args[0], {'type':'image','width':999}, 'image', None
    monkeypatch.setattr(metadata,'_run_with_timeout',changed_while_inspecting)
    result = metadata.extract_metadata(db_path=str(db),workers=1)
    assert result['done'] == 0 and result['stale'] == 1
    assert state(db)[0]['media_meta'] is None
