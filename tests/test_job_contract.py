"""T08: exact recovery accounting and explicit callable/outcome contracts."""
import json
from pathlib import Path
import sys
import pytest
import yaml
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
from database import connect,initialize
import jobs

@pytest.fixture
def configured(tmp_path):
    cl.reset_config_cache();root=tmp_path/'corpus';root.mkdir()
    p=tmp_path/'config.yaml';p.write_text(yaml.safe_dump({'database':{'path':str(tmp_path/'jobs.db')},'scanner':{'scan_paths':[str(root)]},'chunking':{'min_text_chars':1000},'embedding':{'dimension':4,'native_dimension':4}}),encoding='utf8');cl.load_config(p);initialize()
    yield root
    cl.reset_config_cache()


class PersistenceVectors:
    """Explicit numerical substitute; this test certifies SQL/accounting, not inference."""
    embedding_dim=4;identity={'model':'T08 PERSISTENCE SUBSTITUTE'}
    def fingerprint(self):return 't08-persistence-substitute'
    def encode(self,texts,**kwargs):return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)


@pytest.mark.parametrize('diagnostic_limit',[0,1,100])
def test_150_recoveries_do_not_depend_on_diagnostic_sample(configured,monkeypatch,diagnostic_limit):
    import run_pipeline
    from file_scanner import sync
    for i in range(150):(configured/f'{i:03}.fb2').write_text('<FictionBook><body><section><p>Short text.</p></section></body></FictionBook>',encoding='utf8')
    assert sync()['inserted']==150
    with connect() as c:
        for row in c.execute('SELECT id FROM files').fetchall():
            c.execute('INSERT INTO rag_descriptions(file_id,description,source) VALUES (?,?,?)',(row['id'],'A controlled recovery description for an accounting regression.','test-substituted-generation'))
    with connect() as c:
        from description_contract import generation_fingerprint
        c.execute('UPDATE rag_descriptions SET source_hash=(SELECT file_hash FROM files WHERE files.id=rag_descriptions.file_id),generation_fingerprint=?',(generation_fingerprint(),))
    monkeypatch.setattr(run_pipeline,'MAX_ERROR_DETAILS',diagnostic_limit,raising=False)
    result=run_pipeline.process_files(embedder=PersistenceVectors())
    assert result['processed']==150 and result['failed']==0 and result['errors']==[]
    assert result['recovered']==150 and result['recovered_chunks']==150
    with connect() as c:
        assert dict(c.execute('SELECT status,COUNT(*) FROM rag_status GROUP BY status').fetchall())=={'done':150}
        assert c.execute('SELECT COUNT(*) FROM rag_chunks WHERE chunk_index<0').fetchone()[0]==150
        assert c.execute('SELECT SUM(chunks_written) FROM rag_status').fetchone()[0]==150
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_job_never_introspects_explicit_task(configured):
    class Task:
        @property
        def __signature__(self):raise AssertionError('Job callables must not be inspected')
        def __call__(self,context):
            context.progress({'done':1})
            return jobs.JobOutcome({'done':1})
    result=jobs.run_sync('explicit',Task())
    assert result=={'done':1}
    record=jobs.list_jobs()[0]
    assert record['status']=='completed' and record['progress']=={'done':1} and record['result']==result


def test_invalid_task_result_is_durable_failure(configured):
    with pytest.raises(TypeError,match='JobOutcome'):jobs.run_sync('invalid',lambda context:{'failed':0})
    record=jobs.list_jobs()[0]
    assert record['status']=='failed' and 'JobOutcome' in record['error']


def test_scan_and_metadata_adapters_keep_existing_result_json(configured):
    (configured/'document.txt').write_text('A real adapter fixture.',encoding='utf8')
    result=jobs.run_sync('scan',jobs.stage_task('scan'))
    assert result['inserted']==1 and result['errors']==[]
    result=jobs.run_sync('extract',jobs.stage_task('extract'))
    assert result['total']==1 and result['skipped']==1 and result['errors']==0
    assert all(row['status']=='completed' for row in jobs.list_jobs())


def test_cli_and_durable_job_agree_on_stale_metadata(configured,monkeypatch,capsys):
    import metadata_extractor,run_pipeline
    # Controlled stage outcome; the real job DB and CLI adapter are exercised.
    monkeypatch.setattr(metadata_extractor,'extract_metadata',lambda **kwargs:{'total':1,'done':0,'errors':0,'skipped':0,'timeout':0,'cancelled':0,'stale':1,'details':[]})
    assert run_pipeline.main_entry(['extract-meta'])==1
    assert json.loads(capsys.readouterr().out)['stale']==1
    assert jobs.list_jobs()[0]['status']=='partial'
