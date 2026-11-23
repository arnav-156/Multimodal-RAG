"""T09: owned lifecycle, bounded shutdown reporting and real worker cleanup."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import multiprocessing as mp
import subprocess
import sys
import threading
import time
import pytest
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
import jobs
from database import initialize,connect

@pytest.fixture
def configured(tmp_path):
    cl.reset_config_cache();root=tmp_path/'corpus';root.mkdir();p=tmp_path/'config.yaml'
    p.write_text(yaml.safe_dump({'database':{'path':str(tmp_path/'jobs.db')},'scanner':{'scan_paths':[str(root)]}}),encoding='utf8');cl.load_config(p);initialize()
    if hasattr(jobs,'startup'):jobs.startup()
    yield root
    if hasattr(jobs,'shutdown'):assert jobs.shutdown(timeout=3)['remaining']==[]
    if hasattr(jobs,'startup'):jobs.startup()
    cl.reset_config_cache()


def test_cancel_owns_context_and_joins_worker(configured):
    started=threading.Event();contexts=[]
    def work(context):
        contexts.append(context);started.set();context.stop_event.wait(5)
        return jobs.JobOutcome({'cancelled':context.stop_event.is_set()},cancelled=context.stop_event.is_set())
    job=jobs.submit('fixture',work);assert started.wait(3)
    try:
        assert jobs.cancel(job['job_id'])['requested']
        assert jobs.shutdown(timeout=3)['remaining']==[]
        assert next(j for j in jobs.list_jobs() if j['id']==job['job_id'])['status']=='cancelled'
    finally:
        contexts[0].stop_event.set()


def test_shutdown_deadline_reports_live_owner_without_false_interruption(configured):
    started=threading.Event();release=threading.Event()
    def blocked(context):started.set();assert release.wait(5);return jobs.JobOutcome({'finished':True})
    job=jobs.submit('blocked',blocked);assert started.wait(3)
    try:
        start=time.monotonic();report=jobs.shutdown(timeout=.05)
        assert time.monotonic()-start<1
        assert report['remaining']==[job['job_id']]
        assert jobs.list_jobs()[0]['status']=='running'
        with pytest.raises(jobs.JobConflict):jobs.submit('new',blocked)
    finally:release.set()
    assert jobs.shutdown(timeout=3)['remaining']==[]
    assert jobs.list_jobs()[0]['status']=='cancelled'


def test_queued_cancellation_never_invokes_task(configured):
    stop=threading.Event();stop.set();jid=jobs.reserve('queued')
    def forbidden(context):raise AssertionError('Already cancelled queued work must not start')
    result=jobs.execute(jid,forbidden,stop_event=stop)
    assert result['cancelled'] and jobs.list_jobs()[0]['status']=='cancelled'


def test_cancelled_root_snapshot_preserves_prior_data(configured,monkeypatch):
    import file_scanner
    source=configured/'first.txt';source.write_text('Original file',encoding='utf8');file_scanner.sync()
    source.write_text('Changed file',encoding='utf8');(configured/'second.txt').write_text('Another file',encoding='utf8')
    before=[]
    with connect() as c:before=[tuple(r) for r in c.execute('SELECT id,file_hash,is_deleted FROM files ORDER BY id')]
    stop=threading.Event();original=file_scanner.file_fingerprint;hashed=[]
    def cancel_after_hash(path):
        hashed.append(Path(path).name);value=original(path);stop.set();return value
    monkeypatch.setattr(file_scanner,'file_fingerprint',cancel_after_hash)
    result=file_scanner.sync(stop_event=stop)
    assert result['cancelled'] and len(hashed)==1
    with connect() as c:assert [tuple(r) for r in c.execute('SELECT id,file_hash,is_deleted FROM files ORDER BY id')]==before


def test_metadata_cancel_reaps_its_child_and_preserves_other_process(configured,monkeypatch):
    import metadata_extractor
    from PIL import Image
    source=configured/'image.png';Image.new('RGB',(4,4)).save(source)
    unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
    stop=threading.Event();started=threading.Event();original=mp.context.SpawnProcess.start;owned=[]
    def observed_start(process):
        original(process);owned.append(process.pid);started.set();assert stop.wait(3)
    monkeypatch.setattr(mp.context.SpawnProcess,'start',observed_start)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(metadata_extractor._run_with_timeout,(1,str(source),'image/png'),10,stop)
            assert started.wait(3);stop.set();result=future.result(timeout=5)
        assert result[2]=='cancelled' and owned
        assert not ({p.pid for p in mp.active_children()} & set(owned))
        assert unrelated.poll() is None
    finally:unrelated.terminate();unrelated.wait(timeout=5)


@pytest.mark.parametrize('file_count',[1,2])
def test_pipeline_cancel_starts_no_next_source_or_description_stage(configured,monkeypatch,file_count):
    import numpy as np
    from file_scanner import sync
    from run_pipeline import process_files
    import describer
    for name in ['first.txt','second.txt'][:file_count]:(configured/name).write_text('An actual local document.',encoding='utf8')
    sync();stop=threading.Event();calls=[]
    class ControlledEmbedding:
        embedding_dim=4;identity={'model':'PERSISTENCE SUBSTITUTE'}
        def fingerprint(self):return 't09-controlled'
        def encode(self,texts,**kwargs):calls.append(texts);stop.set();return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)
    cl.save_config({'embedding':{'dimension':4,'native_dimension':4}})
    def forbidden(**kwargs):raise AssertionError('Cancellation must not start a description stage')
    monkeypatch.setattr(describer,'process_pending_descriptions',forbidden)
    result=process_files(embedder=ControlledEmbedding(),stop_event=stop)
    assert result['cancelled'] and result['processed']==1 and len(calls)==1
    with connect() as c:assert c.execute('SELECT COUNT(*) FROM rag_status').fetchone()[0]==1


def test_shutdown_coordinates_inflight_admission_with_same_deadline(configured,monkeypatch):
    entered=threading.Event();release=threading.Event();original=jobs.reserve;called=[]
    def paused_reserve(*args,**kwargs):entered.set();assert release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(jobs,'reserve',paused_reserve)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(jobs.submit,'queued',lambda context:called.append(True))
        assert entered.wait(3)
        try:
            start=time.monotonic();report=jobs.shutdown(timeout=.05)
            assert time.monotonic()-start<1
            assert not report['complete'] and report['admission_pending']
        finally:release.set()
        job=pending.result(timeout=3)
    assert jobs.shutdown(timeout=3)['complete']
    assert not called and jobs.list_jobs()[0]['status']=='cancelled'


def test_main_thread_sigint_handler_is_owned_and_restored(configured):
    import signal
    original=signal.getsignal(signal.SIGINT)
    def work(context):
        installed=signal.getsignal(signal.SIGINT)
        assert installed is not original
        installed(signal.SIGINT,None)
        assert context.stop_event.is_set()
        return jobs.JobOutcome({'cancelled':True},cancelled=True)
    assert jobs.run_sync('signal-fixture',work)['cancelled']
    assert signal.getsignal(signal.SIGINT) is original


def test_description_generation_stops_before_next_file(configured,monkeypatch):
    import describer
    cl.save_config({'vision':{'endpoints':[{'api_base':'http://127.0.0.1:1234/v1','enabled':True,'model':'controlled-loop-fixture'}]}})
    stop=threading.Event();calls=[]
    monkeypatch.setattr(describer,'get_ocr_failed_files',lambda *args:[(1,'first.png','fixture'),(2,'second.png','fixture')])
    # Controlled callable tests the owner loop; actual vision is a separate task.
    def current_file(*args):calls.append(args[0]);stop.set();return True,'controlled-fixture'
    monkeypatch.setattr(describer,'_process_file',current_file)
    result=describer.process_pending_descriptions(stop_event=stop)
    assert result['cancelled'] and result['processed']==1 and calls==[1]


def test_description_consumer_stops_before_next_file(configured):
    import numpy as np
    from file_scanner import sync
    from description_embedder import drain_descriptions
    for name in ['first.txt','second.txt']:(configured/name).write_text('A description source.',encoding='utf8')
    sync();cl.save_config({'embedding':{'dimension':4,'native_dimension':4}})
    with connect() as c:
        for row in c.execute('SELECT id FROM files').fetchall():
            c.execute('INSERT INTO rag_descriptions(file_id,description,source) VALUES (?,?,?)',(row['id'],'Controlled description.','test-substituted-generation'))
    with connect() as c:
        from description_contract import generation_fingerprint
        c.execute('UPDATE rag_descriptions SET source_hash=(SELECT file_hash FROM files WHERE files.id=rag_descriptions.file_id),generation_fingerprint=?',(generation_fingerprint(),))
    stop=threading.Event();calls=[]
    class ControlledEmbedding:
        embedding_dim=4
        def fingerprint(self):return 't09-description-substitute'
        def encode(self,texts,**kwargs):calls.append(texts);stop.set();return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)
    result=drain_descriptions(embedder=ControlledEmbedding(),stop_event=stop)
    assert result['cancelled'] and result['processed']==1 and len(calls)==1
    with connect() as c:assert dict(c.execute('SELECT embedded,COUNT(*) FROM rag_descriptions GROUP BY embedded').fetchall())=={0:1,1:1}


def test_http_cancel_and_lifespan_shutdown_join_owned_job(configured,monkeypatch):
    from fastapi.testclient import TestClient
    import server,file_scanner
    monkeypatch.setenv('RAG_API_TOKEN','owned-lifecycle-api-fixture-token-2026')
    source=configured/'document.txt';source.write_text('Actual scan source.',encoding='utf8')
    entered=threading.Event();release=threading.Event();original=file_scanner.file_fingerprint
    def paused_hash(path):entered.set();assert release.wait(5);return original(path)
    monkeypatch.setattr(file_scanner,'file_fingerprint',paused_hash)
    with TestClient(server.app,base_url='http://127.0.0.1:8100',headers={'Authorization':'Bearer owned-lifecycle-api-fixture-token-2026'}) as client:
        job=client.post('/api/pipeline/scan',json={}).json();assert entered.wait(3)
        try:
            response=client.post(f"/api/pipeline/jobs/{job['job_id']}/cancel")
            assert response.status_code==200 and response.json()['requested']
            assert client.post('/api/pipeline/jobs/missing/cancel').status_code==404
        finally:release.set()
    # Lifespan exit waited for the worker and persisted real cancellation.
    assert jobs.list_jobs()[0]['status']=='cancelled'
    assert jobs.shutdown(timeout=0)['complete']
    with connect() as c:assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0]==0


def test_shutdown_waits_for_sync_operation_not_its_callers_thread(configured):
    started=threading.Event();release_host=threading.Event();returned=threading.Event()
    def work(context):
        started.set();assert context.stop_event.wait(3)
        return jobs.JobOutcome({'cancelled':True},cancelled=True)
    def host():
        result=jobs.run_sync('synchronous-fixture',work)
        returned.set();assert release_host.wait(3)
        return result
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(host);assert started.wait(3)
        try:
            report=jobs.shutdown(timeout=.2)
            assert returned.wait(1)
            assert report['complete'] and report['remaining']==[]
            assert not future.done()  # Caller continues unrelated work.
        finally:release_host.set()
        assert future.result(timeout=2)['cancelled']
