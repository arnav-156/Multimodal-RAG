"""T02: real thread interleavings and database/vector isolation; no inference doubles."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import pytest
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
import jobs
from database import initialize,connect

@pytest.fixture
def configured(tmp_path):
    cl.reset_config_cache()
    p=tmp_path/'config.yaml';p.write_text(yaml.safe_dump({'database':{'path':str(tmp_path/'active.db')},'embedding':{'dimension':4,'native_dimension':4}}),encoding='utf8')
    cl.load_config(p);initialize()
    yield tmp_path
    cl.reset_config_cache()


def test_returned_settings_cannot_mutate_active_revision(configured):
    cfg=cl.get_config();cfg['embedding']['dimension']=99;cfg['scanner']['scan_paths'].append('unapproved')
    assert cl.get_embedding_dim()==4
    assert cl.get_config()['scanner']['scan_paths']==[]
    loaded=cl.load_config();loaded['embedding']['dimension']=77
    assert cl.get_embedding_dim()==4


def test_save_and_admission_are_atomic(configured,monkeypatch):
    import server
    import file_scanner
    paused=threading.Event();release=threading.Event();finished=threading.Event();observed=[]
    save=server.save_config
    def paused_save(update):
        paused.set();assert release.wait(5);return save(update)
    def scan(**kwargs):
        observed.append(cl.get_embedding_dim());finished.set();return {'scanned':0}
    monkeypatch.setattr(server,'save_config',paused_save)
    monkeypatch.setattr(file_scanner,'sync',scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving=pool.submit(server.update_config,server.ConfigUpdate(config={'embedding':{'dimension':8}}))
        assert paused.wait(5)
        admitted=pool.submit(server.pipeline,'scan',server.ProcessOptions())
        try:
            # This must wait for the entire configuration mutation, not slip
            # between the active-job check and the atomic YAML replacement.
            with pytest.raises(TimeoutError):admitted.result(timeout=.15)
        finally:release.set()
        assert saving.result(timeout=5)['config']['embedding']['dimension']==8
        job=admitted.result(timeout=5)
    assert finished.wait(5)
    # Join by observing persisted completion, not just the callable event.
    import time
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        record=next(j for j in jobs.list_jobs() if j['id']==job['job_id'])
        if record['status']=='completed':break
        time.sleep(.01)
    assert record['status']=='completed' and observed==[8]


def test_parallel_owned_snapshots_keep_real_databases_and_dimensions(configured):
    from vectors import serialize_vector
    barrier=threading.Barrier(2)
    first=cl.get_config();second=cl.get_config()
    first['database']['path']=str(configured/'first.db');first['embedding']['dimension']=3
    second['database']['path']=str(configured/'second.db');second['embedding']['dimension']=5
    def operation(settings):
        with cl.operation_settings(settings):
            initialize();barrier.wait(timeout=5)
            cfg=cl.get_config();cfg['embedding']['dimension']=999
            dimension=cl.get_embedding_dim();blob=serialize_vector([1]*dimension)
            with connect() as c:
                c.execute('CREATE TABLE isolation_probe(payload BLOB)');c.execute('INSERT INTO isolation_probe VALUES (?)',(blob,))
            return cl.get_db_path(),dimension
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(operation,first);b=pool.submit(operation,second)
        assert a.result(timeout=10)==(first['database']['path'],3)
        assert b.result(timeout=10)==(second['database']['path'],5)
    for settings,size in [(first,3),(second,5)]:
        with connect(settings['database']['path']) as c:assert c.execute('SELECT length(payload) FROM isolation_probe').fetchone()[0]==size
    assert cl.get_embedding_dim()==4


def test_job_worker_keeps_admitted_snapshot_and_api_rejects_changes(configured):
    import server
    started=threading.Event();release=threading.Event();observed=[]
    def work(context):
        observed.append(cl.get_embedding_dim());started.set();assert release.wait(5)
        observed.append(cl.get_embedding_dim());return jobs.JobOutcome({'processed':0})
    job=jobs.submit('test',work)
    assert started.wait(5)
    try:
        with pytest.raises(server.HTTPException) as error:server.update_config(server.ConfigUpdate(config={'embedding':{'dimension':8}}))
        assert error.value.status_code==409
        # Embedded callers may publish a new revision; an admitted worker must
        # still own one complete revision. HTTP changes above are rejected.
        cl.save_config({'embedding':{'dimension':6}})
    finally:release.set()
    import time
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        record=next(j for j in jobs.list_jobs() if j['id']==job['job_id'])
        if record['status']=='completed':break
        time.sleep(.01)
    assert record['status']=='completed' and observed==[4,4]
    assert cl.get_embedding_dim()==6


def test_live_egress_policy_revokes_old_snapshot_before_transport(configured):
    from security import validate_endpoint
    allowed={'security':{'local_only':False,'allow_cloud':True},'embedding':{'api_url':'https://example.com','cloud_authorized':True}}
    cl.save_config(allowed)
    with cl.operation_settings():
        snapshot=cl.get_config()
        assert validate_endpoint('https://example.com',snapshot,cloud_authorized=True,live_policy=True)
        cl.save_config({'embedding':{'cloud_authorized':False}})
        assert cl.get_config()['embedding']['cloud_authorized'] is True
        with pytest.raises(ValueError,match='revoked'):
            validate_endpoint('https://example.com',snapshot,cloud_authorized=True,live_policy=True)
    cl.save_config(allowed)
    with cl.operation_settings():
        snapshot=cl.get_config()
        cl.save_config({'security':{'local_only':True,'allow_cloud':False}})
        with pytest.raises(ValueError,match='revoked'):
            validate_endpoint('https://example.com',snapshot,cloud_authorized=True,live_policy=True)


def test_standalone_entrypoint_keeps_snapshot_across_publication(configured,monkeypatch):
    import file_scanner
    root=configured/'corpus';root.mkdir();(root/'document.txt').write_text('fixture',encoding='utf8')
    cl.save_config({'scanner':{'scan_paths':[str(root)]}})
    original=file_scanner._snapshot;seen=[]
    def during_scan(*args,**kwargs):
        seen.append(cl.get_embedding_dim())
        cl.save_config({'embedding':{'dimension':8}})
        seen.append(cl.get_embedding_dim())
        return original(*args,**kwargs)
    monkeypatch.setattr(file_scanner,'_snapshot',during_scan)
    assert file_scanner.sync()['inserted']==1
    assert seen==[4,4] and cl.get_embedding_dim()==8


def test_snapshot_credentials_and_reset_are_operation_owned(configured,monkeypatch):
    monkeypatch.setenv('RAG_TEST_EPHEMERAL_KEY','old')
    with cl.operation_settings():
        monkeypatch.setenv('RAG_TEST_EPHEMERAL_KEY','new')
        assert cl.get_env('RAG_TEST_EPHEMERAL_KEY')=='old'
        with pytest.raises(RuntimeError):
            with cl.operation_settings(cl.get_config()):raise RuntimeError('fixture')
        assert cl.get_embedding_dim()==4
    assert cl.get_env('RAG_TEST_EPHEMERAL_KEY')=='new'


def test_parallel_real_extractions_cannot_overwrite_byte_limits(configured,monkeypatch):
    import extractors
    source=configured/'note.txt';source.write_text('A real short document with useful text.',encoding='utf8')
    generous=cl.get_config();generous['limits']['max_text_file_bytes']=1024
    strict=cl.get_config();strict['limits']['max_text_file_bytes']=1
    first_entered=threading.Event();second_entered=threading.Event()
    classify=extractors.classify_file
    def paused_classify(*args,**kwargs):
        if cl.get_limits_config()['max_text_file_bytes']==1024:
            first_entered.set();assert second_entered.wait(5)
        else:second_entered.set()
        return classify(*args,**kwargs)
    monkeypatch.setattr(extractors,'classify_file',paused_classify)
    def extract(settings):
        with cl.operation_settings(settings):
            return extractors.extract(1,str(source),'.txt','text')
    with ThreadPoolExecutor(max_workers=2) as pool:
        good=pool.submit(extract,generous);assert first_entered.wait(5)
        bad=pool.submit(extract,strict)
        with pytest.raises(extractors.ExtractionError,match='byte limit'):bad.result(timeout=5)
        chunks=good.result(timeout=5)
    assert any('useful text' in chunk.text for chunk in chunks)
