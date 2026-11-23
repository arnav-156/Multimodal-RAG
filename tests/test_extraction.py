"""Regression tests use generated files and explicit model/transport substitutes."""
import json
import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest

import chunker_v2
import extractors


@pytest.mark.parametrize('extension', ['.txt', '.md', '.py'])
def test_b22_long_line_never_exceeds_budget_or_loses_tail(extension):
    text = 'x' * 30000 + 'TAIL_MARKER'
    chunks = chunker_v2.chunk_greedy_semantic(text, extension, max_chars=1000)
    assert max(map(len, chunks)) <= 1000
    assert chunks[-1].endswith('TAIL_MARKER')
    assert len(chunks) > 30


def test_b22_overlap_is_inside_hard_limit():
    chunks = chunker_v2.chunk_greedy_semantic(('alpha beta gamma.\n' * 1000 + '\n\n') * 3)
    assert max(map(len, chunks)) <= 12000


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'utf-16-le', 'utf-16-be', 'gb18030'])
def test_b23_strict_encoding_roundtrip(tmp_path, encoding):
    content = '中文与 English 123，检索本地文档。\n' * 30
    path = tmp_path / 'document.txt'
    path.write_bytes(content.encode(encoding))
    assert extractors._read_file(str(path)) == content


def test_b24_short_note_is_content_not_ocr_failure(tmp_path, monkeypatch):
    path = tmp_path / 'short.md'
    path.write_text('A useful short note.', encoding='utf-8')
    marked = []
    monkeypatch.setattr(extractors, '_mark_ocr_insufficient', lambda *a: marked.append(a))
    chunks = extractors.extract(1, str(path), '.md', 'text')
    assert chunks and 'A useful short note.' in chunks[0].text
    assert not marked


def test_b25_typescript_default_is_code():
    assert extractors.classify_file('.ts') == 'code'


@pytest.fixture
def description_db(tmp_path, monkeypatch):
    path = tmp_path / 'descriptions.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
        CREATE TABLE files(id INTEGER PRIMARY KEY,path TEXT,ext TEXT,mime_type TEXT,is_deleted INTEGER DEFAULT 0,file_hash TEXT);
        CREATE TABLE rag_descriptions(file_id INTEGER PRIMARY KEY,description TEXT,source TEXT,embedded INTEGER DEFAULT 0,retry_count INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE rag_ocr_status(file_id INTEGER PRIMARY KEY,status TEXT,reason TEXT);
        CREATE TABLE rag_chunks(file_id INTEGER,chunk_index INTEGER,meta_json TEXT,vector BLOB,created_at TEXT DEFAULT CURRENT_TIMESTAMP,UNIQUE(file_id,chunk_index));
        INSERT INTO files VALUES(1,'diagram.png','.png','image/png',0,NULL);
        INSERT INTO rag_descriptions(file_id,description,source) VALUES(1,'A useful description of a technical diagram.','local');
        ''')
    import description_embedder
    import config_loader
    import yaml
    profile=tmp_path/'fixture-config.yaml'
    profile.write_text(yaml.safe_dump({'database':{'path':str(path)},'scanner':{'scan_paths':[str(tmp_path)]}}),encoding='utf8')
    monkeypatch.setenv('RAG_CONFIG',str(profile))
    config_loader.reset_config_cache()
    cfg=config_loader.load_config(profile)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE files SET path=? WHERE id=1',(str(tmp_path/'diagram.png'),))
    from database import initialize
    from description_contract import generation_fingerprint
    initialize(path)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE rag_descriptions SET generation_fingerprint=?',(generation_fingerprint(cfg),))
    monkeypatch.setattr(description_embedder, 'DB_PATH', str(path), raising=False)
    monkeypatch.setattr(description_embedder, 'get_db_path', lambda: str(path), raising=False)
    try:
        yield path
    finally:
        config_loader.reset_config_cache()


def test_b06_description_consumer_uses_file_id(description_db):
    import description_embedder
    rows = description_embedder.get_pending_descriptions(db_path=str(description_db))
    assert rows == [(1, 'A useful description of a technical diagram.', 'local')]


def test_b07_render_failure_records_without_undefined_backend(description_db, tmp_path, monkeypatch):
    import describer
    path = tmp_path / 'diagram.svg'
    path.write_text('<svg/>', encoding='utf-8')
    monkeypatch.setattr(describer, 'DB_PATH', str(description_db), raising=False)
    monkeypatch.setattr(describer, 'get_db_path', lambda: str(description_db), raising=False)
    monkeypatch.setattr(describer, 'render_file_to_images', lambda p: [])
    success, source = describer._process_file(1, str(path), 'ocr_empty')
    assert not success and source == 'render_failed'
    with sqlite3.connect(description_db) as conn:
        row = conn.execute('SELECT source,retry_count FROM rag_descriptions WHERE file_id=1').fetchone()
    assert row[0] == 'render_failed' and row[1] >= 1


def test_b42_cloud_request_blocked_before_transport(monkeypatch):
    import describer
    calls = []
    monkeypatch.setattr(describer.requests, 'post', lambda *a, **kw: calls.append(a))
    endpoint = {'api_base': 'https://example.invalid/v1', 'model': 'test', 'api_key': '', 'enabled': True}
    with pytest.raises(ValueError, match='local|loopback|cloud'):
        describer.call_openai_vision(endpoint, 'AA==', 'generated.png')
    assert not calls


def test_b26_b27_modern_ocr_constructor_and_result(monkeypatch, tmp_path):
    import ocr_server
    observed = {}
    class FakeOCR:
        def __init__(self, **kwargs): observed.update(kwargs)
        def predict(self, input): return [{'rec_texts': ['hello', 'world']}]
    monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=FakeOCR))
    (tmp_path / 'det').mkdir(); (tmp_path / 'rec').mkdir()
    monkeypatch.setattr(ocr_server, 'get_config', lambda: {'ocr': {'lang': 'en', 'use_gpu': False, 'model_dir': str(tmp_path)}}, raising=False)
    import yaml
    for kind,name in [('det','PP-OCRv5_mobile_det'),('rec','PP-OCRv5_mobile_rec')]:
        (tmp_path/kind/'inference.yml').write_text(yaml.safe_dump({'Global':{'model_name':name}}),encoding='utf8')
    info=ocr_server.init_worker()
    p = tmp_path / 'generated.png'; p.write_bytes(b'not-used-by-model-substitute')
    assert ocr_server.ocr_single_image(str(p)) == 'hello\nworld'
    assert observed['device'] == 'cpu' and info['configured_language'] == 'en'
    assert 'lang' not in observed and observed['text_recognition_model_name']=='PP-OCRv5_mobile_rec'
    assert 'use_gpu' not in observed and 'cls' not in observed
