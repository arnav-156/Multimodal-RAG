"""Integrated API/lifecycle regressions with explicitly substituted vectors.
Real model acceptance lives in tools/verify_local_model.py, never in this suite.
"""
import json
import sys
from pathlib import Path
import numpy as np
import pytest
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
from database import connect,initialize
from file_scanner import sync
from run_pipeline import process_files
from search_numpy import search_documents

class FakeEmbedding:
    embedding_dim=4
    identity={'model':'TEST SUBSTITUTE'}
    def fingerprint(self):return 'test-substitute-v1'
    def encode(self,texts,**kwargs):return np.array([[1,0.1,0.2,0.3] for _ in texts],dtype=np.float32)
    def close(self):pass

@pytest.fixture
def library(tmp_path,monkeypatch):
    cl.reset_config_cache()
    root=tmp_path/'corpus';root.mkdir()
    cfg=tmp_path/'config.yaml'
    cfg.write_text(yaml.safe_dump({'database':{'path':str(tmp_path/'db.sqlite')},'scanner':{'scan_paths':[str(root)]},'ocr':{'enabled':False},'embedding':{'dimension':4,'native_dimension':4}}),encoding='utf8')
    monkeypatch.setenv('RAG_API_TOKEN','test-token-never-a-production-secret-123456')
    cl.load_config(cfg);initialize()
    yield root
    cl.reset_config_cache()

def test_fresh_process_resume_modify_delete_and_contract(library):
    p=library/'guide.md';p.write_text('Restore the library from the backup file.',encoding='utf8')
    assert sync()['inserted']==1
    assert process_files(embedder=FakeEmbedding())['processed']==1
    assert process_files(embedder=FakeEmbedding())['skipped']==1
    result=search_documents('restore',embedder=FakeEmbedding())
    assert result['total']==1 and 'backup' in result['results'][0]['snippet']
    p.write_text('Updated instructions for restore.',encoding='utf8')
    assert sync()['updated']==1
    assert search_documents('restore',embedder=FakeEmbedding())['total']==0
    assert process_files(embedder=FakeEmbedding())['processed']==1
    p.unlink();assert sync()['deleted']==1
    assert search_documents('restore',embedder=FakeEmbedding())['total']==0
    with connect() as c:assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==0

def test_database_inside_root_is_excluded(library):
    db=library/'active.sqlite';initialize(db)
    (library/'keep.db').write_text('ordinary user database',encoding='utf8')
    assert sync(db_path=db)['scanned']==1
    with connect(db) as c:assert c.execute('SELECT name FROM files').fetchone()[0]=='keep.db'

def test_corrupt_vector_skipped_and_page_bounds(library):
    for n in range(14):(library/f'{n}.txt').write_text(f'Document {n} searchable fixture.',encoding='utf8')
    sync();process_files(embedder=FakeEmbedding())
    with connect() as c:c.execute("UPDATE rag_chunks SET vector=? WHERE file_id=(SELECT MIN(id) FROM files)",(b'bad',))
    r=search_documents('document',top_k=14,offset=10,page_size=10,embedder=FakeEmbedding())
    assert r['total']==13 and len(r['results'])==3 and r['skipped_invalid']==1
    with pytest.raises(ValueError):search_documents('x',top_k=0,embedder=FakeEmbedding())

def test_jobs_persist_duplicate_and_failures(library):
    import jobs
    jid=jobs.reserve('scan')
    assert jobs.process_alive(__import__('os').getpid())
    with pytest.raises(jobs.JobConflict):jobs.reserve('process')
    jobs.execute(jid,lambda context:jobs.JobOutcome({'errors':['unavailable root']},failed=1))
    assert jobs.list_jobs()[0]['status']=='partial'
    jid=jobs.reserve('process')
    with pytest.raises(RuntimeError):jobs.execute(jid,lambda context:(_ for _ in ()).throw(RuntimeError('failure fixture')))
    assert jobs.list_jobs()[0]['status']=='failed'
    with connect() as c:c.execute("INSERT INTO jobs(id,kind,status,owner_pid) VALUES ('dead','scan','running',2147483646)")
    jobs.recover_interrupted()
    assert next(j for j in jobs.list_jobs() if j['id']=='dead')['status']=='interrupted'

def test_http_auth_origin_config_preview_and_download(library):
    from fastapi.testclient import TestClient
    import server
    p=library/'payload.html';p.write_text('<img src=x onerror=alert(1)> searchable text',encoding='utf8')
    sync();process_files(embedder=FakeEmbedding())
    with TestClient(server.app,base_url='http://127.0.0.1:8100') as client:
        assert client.get('/api/config').status_code==401
        assert client.get('/api/live',headers={'Host':'attacker.example'}).status_code==403
        assert client.get('/api/session',headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/session',json={'token':'bad'}).status_code==401
        r=client.post('/api/session',json={'token':'test-token-never-a-production-secret-123456'})
        assert r.status_code==200 and 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
        cfg=client.get('/api/config').json()['config'];cfg['search']['default_top_k']=17
        assert client.put('/api/config',json={'config':cfg}).status_code==200
        assert cl.get_config()['search']['default_top_k']==17
        assert client.put('/api/config',json={'config':{'server':{'port':0}}}).status_code==422
        doc=client.get('/api/documents/1');assert doc.status_code==200
        assert client.get('/api/documents/1/download').content==p.read_bytes()
        p.write_text('changed',encoding='utf8')
        assert client.get('/api/documents/1/download').status_code==409
        assert client.get('/api/documents/9000').status_code==404
        assert "script-src 'self'" in client.get('/').headers['content-security-policy']
        assert client.get('/api/pipeline/status').status_code==200
        client.delete('/api/session');assert client.get('/api/stats').status_code==401

def test_b34_limit_and_b35_pending_description_only(library):
    for name in ['a.txt','b.txt']:(library/name).write_text('A small generated document.',encoding='utf8')
    sync();assert process_files(limit=1,types='',embedder=FakeEmbedding())['processed']==1
    assert process_files(limit=1,embedder=FakeEmbedding())['processed']==1
    with connect() as c:
        c.execute('DELETE FROM rag_chunks WHERE file_id=1')
        c.execute("INSERT INTO rag_descriptions(file_id,description,source,embedded) VALUES (1,'The visual diagram shows a cooling fan.','test-substituted-vision',0)")
    with connect() as c:
        from description_contract import generation_fingerprint
        c.execute('UPDATE rag_descriptions SET source_hash=(SELECT file_hash FROM files WHERE files.id=rag_descriptions.file_id),generation_fingerprint=?',(generation_fingerprint(),))
    result=process_files(file_ids=[],embedder=FakeEmbedding())
    assert result['descriptions']['processed']==1
    result=search_documents('cooling fan',embedder=FakeEmbedding())
    assert any('visual diagram' in r['snippet'] for r in result['results'])
    with connect() as c:assert c.execute('SELECT embedded FROM rag_descriptions WHERE file_id=1').fetchone()[0]==1


def test_audio_executables_require_local_configuration(library):
    from fastapi.testclient import TestClient
    import server
    headers={'Authorization':'Bearer test-token-never-a-production-secret-123456'}
    original=cl.get_config()['audio']
    with TestClient(server.app,base_url='http://127.0.0.1:8100',headers=headers) as client:
        for invalid in (None,[],False,'not an object'):
            assert client.put('/api/config',json={'config':{'audio':invalid}}).status_code==422
        for update in [{'python_executable':str(library/'untrusted.exe')},
                       {'cpp_executable':str(library/'untrusted.exe'),'cpp_model_path':str(library/'model.bin')}]:
            response=client.put('/api/config',json={'config':{'audio':update}})
            assert response.status_code==409 and 'YAML' in response.json()['detail']
        assert cl.get_config()['audio']==original
        assert client.put('/api/config',json={'config':{'audio':{'timeout':23}}}).status_code==200
        assert cl.get_config()['audio']['timeout']==23

def test_b36_hybrid_pdf_all_pages_and_missing_page_failure(library,monkeypatch):
    fitz=pytest.importorskip('fitz')
    import extractors
    path=library/'hybrid.pdf'
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((30,50),'Text page with actual searchable content.')
        doc.new_page();doc.save(path)
    monkeypatch.setattr(extractors,'_ocr_image',lambda p:'OCR SUBSTITUTE: scanned page content')
    chunks=extractors.extract(1,str(path),'.pdf','pdf_hybrid')
    assert {c.meta['page'] for c in chunks}=={1,2}
    assert any('scanned page content' in c.text for c in chunks)
    monkeypatch.setattr(extractors,'_ocr_image',lambda p:None)
    monkeypatch.setattr(extractors,'_mark_ocr_insufficient',lambda *a:None)
    with pytest.raises(extractors.ExtractionError,match='pages without'):extractors.extract(1,str(path),'.pdf','pdf_hybrid')

def test_b28_owned_audio_subprocess_contract(library,tmp_path,monkeypatch):
    import extractors
    import types
    model=tmp_path/'local-whisper';model.mkdir()
    cl.save_config({'audio':{'model_path':str(model)}})
    path=library/'generated.wav';path.write_bytes(b'RIFF-generated-test')
    monkeypatch.setattr(extractors,'_get_audio_duration',lambda p:1)
    monkeypatch.setattr(extractors,'_transcribe_with_whisper_cpp',lambda p:None)
    commands=[]
    def transport(args,**kw):
        commands.append((args,kw));return types.SimpleNamespace(stdout='{"text":"SUBSTITUTED transcript","language":"en"}')
    monkeypatch.setattr(extractors.subprocess,'run',transport)
    chunks=extractors.extract(1,str(path),'.wav','audio')
    assert 'SUBSTITUTED transcript' in chunks[0].text
    assert commands[0][0][0]==sys.executable and commands[0][1]['timeout']>0
    assert 'local_files_only=True' in commands[0][0][2]
    assert commands[0][1]['env']['HF_HUB_OFFLINE']=='1'

def test_b38_ebook_cleanup_does_not_touch_another_job(library,tmp_path,monkeypatch):
    import extractors
    path=library/'generated.epub'
    import zipfile
    with zipfile.ZipFile(path,'w') as z:z.writestr('mimetype','application/epub+zip')
    other=tmp_path/'rag_ebook_1';other.mkdir();sentinel=other/'keep';sentinel.write_text('unrelated owned content')
    created=[]
    def reader(path,directory):
        created.append(directory);return 'A short ebook with useful searchable text.',[]
    monkeypatch.setattr(extractors,'_extract_epub_content',reader)
    assert extractors.extract(1,str(path),'.epub','ebook')
    assert sentinel.exists() and not Path(created[0]).exists()

def test_index_contract_change_requires_explicit_rebuild(library):
    (library/'one.txt').write_text('A versioned document.')
    sync();process_files(embedder=FakeEmbedding())
    changed=FakeEmbedding();changed.fingerprint=lambda:'test-substitute-v2'
    with pytest.raises(ValueError,match='contract'):process_files(embedder=changed)
    assert process_files(embedder=changed,force=True)['processed']==1
    with pytest.raises(ValueError,match='mismatch'):search_documents('test',embedder=FakeEmbedding())

def test_b19_empty_index_readiness_and_b21_concurrent_http(library,monkeypatch):
    import threading,time
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    import server,jina_v5_embedding,search_numpy
    class HealthModel(FakeEmbedding):
        def __enter__(self):return self
        def __exit__(self,*args):pass
    monkeypatch.setattr(jina_v5_embedding,'JinaV5Embedder',HealthModel)
    entered=threading.Event();release=threading.Event()
    def slow_search(**kwargs):
        entered.set();release.wait(3);return {'results':[],'total':0}
    monkeypatch.setattr(search_numpy,'search_documents',slow_search)
    headers={'Authorization':'Bearer test-token-never-a-production-secret-123456'}
    with TestClient(server.app,base_url='http://127.0.0.1:8100',headers=headers) as client:
        health=client.get('/api/health');assert health.status_code==503
        assert health.json()['ready_to_ingest'] and not health.json()['ready']
        with ThreadPoolExecutor(max_workers=1) as executor:
            request=executor.submit(client.post,'/api/search',json={'query':'slow fixture'})
            try:
                assert entered.wait(2)
                start=time.monotonic();assert client.get('/api/stats').status_code==200
                assert time.monotonic()-start<1
            finally:release.set()
            assert request.result().status_code==200

def test_b08_b20_api_duplicate_and_durable_error(library,monkeypatch):
    import threading,time
    from fastapi.testclient import TestClient
    import run_pipeline,server
    entered=threading.Event();release=threading.Event()
    def processing(**kwargs):
        entered.set();release.wait(3);raise RuntimeError('Controlled pipeline failure')
    monkeypatch.setattr(run_pipeline,'process_files',processing)
    headers={'Authorization':'Bearer test-token-never-a-production-secret-123456'}
    with TestClient(server.app,base_url='http://127.0.0.1:8100',headers=headers) as client:
        first=client.post('/api/pipeline/process',json={});assert first.status_code==202
        try:
            assert entered.wait(2)
            assert client.post('/api/pipeline/process',json={}).status_code==409
        finally:release.set()
        for _ in range(100):
            job=client.get('/api/pipeline/status').json()['jobs'][0]
            if job['status']=='failed':break
            time.sleep(.01)
        assert job['status']=='failed' and 'Controlled pipeline failure' in job['error']

def test_archive_preflight_rejects_traversal_and_expansion(library):
    import zipfile,extractors
    path=library/'hostile.docx'
    with zipfile.ZipFile(path,'w') as z:z.writestr('word/media/../../../outside.png',b'fixture')
    with pytest.raises(extractors.ExtractionError,match='unsafe path'):extractors.extract(1,str(path),'.docx','office')
    cl.save_config({'limits':{'max_archive_uncompressed_bytes':1024}})
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('word/document.xml','x'*2048)
    with pytest.raises(extractors.ExtractionError,match='uncompressed'):extractors.extract(1,str(path),'.docx','office')

def test_bounded_document_bytes_and_extracted_chars(library):
    import extractors
    path=library/'large.txt';path.write_text('word '*1000)
    cl.save_config({'limits':{'max_text_file_bytes':1000}})
    with pytest.raises(extractors.ExtractionError,match='byte limit'):extractors.extract(1,str(path),'.txt','text')
    cl.save_config({'limits':{'max_text_file_bytes':10000,'max_extracted_chars':1000}})
    with pytest.raises(extractors.ExtractionError,match='character limit'):extractors.extract(1,str(path),'.txt','text')

def test_vectorized_batches_keep_one_best_result_per_file(library):
    cl.save_config({'chunking':{'max_chars':1000}})
    for name in ('a.txt','b.txt','c.txt'):(library/name).write_text('Sentence about indexed content. '*6000,encoding='utf8')
    sync();processed=process_files(embedder=FakeEmbedding())
    assert processed['chunks_written']>384
    result=search_documents('content',top_k=2,embedder=FakeEmbedding())
    assert result['total']==2 and result['eligible_files']==3
    assert len({r['file_id'] for r in result['results']})==2

def test_search_scope_uses_root_boundary_and_updates_after_removal(library):
    (library/'one.txt').write_text('Indexed source content.')
    sync();process_files(embedder=FakeEmbedding())
    assert search_documents('content',path=str(library.with_name(library.name+'-private')),embedder=FakeEmbedding())['total']==0
    cl.save_config({'scanner':{'scan_paths':[]}})
    assert search_documents('content',embedder=FakeEmbedding())['total']==0
