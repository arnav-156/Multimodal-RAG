"""Client lifecycle with real SQLite; vectors are labeled ownership substitutes."""
from pathlib import Path
import numpy as np
import pytest
import yaml
import config_loader as cl
from database import connect,initialize
from file_scanner import sync
import description_embedder as consumer
from description_contract import generation_fingerprint


@pytest.fixture
def library(tmp_path):
    cl.reset_config_cache();root=tmp_path/'sources';root.mkdir();profile=tmp_path/'config.yaml'
    profile.write_text(yaml.safe_dump({'database':{'path':str(tmp_path/'index.db')},'scanner':{'scan_paths':[str(root)]},'embedding':{'dimension':4,'native_dimension':4,'description_batch_size':1}}),encoding='utf8');cl.load_config(profile);initialize()
    for index in range(3):(root/f'{index}.txt').write_text('Actual source for ownership fixture.',encoding='utf8')
    sync()
    with connect() as connection:
        for row in connection.execute('SELECT id,file_hash FROM files').fetchall():
            connection.execute('INSERT INTO rag_descriptions(file_id,description,source,source_hash,generation_fingerprint) VALUES(?,?,?,?,?)',(row['id'],'CONTROLLED description for lifecycle verification.','OWNERSHIP SUBSTITUTE',row['file_hash'],generation_fingerprint()))
    yield root
    cl.reset_config_cache()


class Client:
    embedding_dim=4;identity={'model':'T12 OWNERSHIP SUBSTITUTE'}
    def __init__(self,fail_at=None,stop=None):self.closed=0;self.calls=0;self.fail_at=fail_at;self.stop=stop
    def fingerprint(self):return 't12-client-ownership'
    def encode(self,texts,**kwargs):
        self.calls+=1
        if self.fail_at==self.calls:raise RuntimeError('Controlled inference failure')
        if self.stop:self.stop.set()
        return np.array([[1,0,0,0] for _ in texts],dtype=np.float32)
    def close(self):self.closed+=1


@pytest.mark.parametrize('fail_at',[None,2])
def test_multibatch_drain_owns_one_closed_client(library,monkeypatch,fail_at):
    made=[]
    def factory():client=Client(fail_at=fail_at);made.append(client);return client
    monkeypatch.setattr(consumer,'get_jina_embedder',factory)
    if fail_at:
        with pytest.raises(RuntimeError,match='Controlled'):consumer.drain_descriptions()
    else:assert consumer.drain_descriptions()['processed']==3
    assert len(made)==1 and made[0].closed==1
    with connect() as connection:
        assert connection.execute('SELECT SUM(embedded) FROM rag_descriptions').fetchone()[0]==(1 if fail_at else 3)


@pytest.mark.parametrize('truthy',[True,False])
def test_borrowed_client_remains_caller_owned(library,monkeypatch,truthy):
    class Borrowed(Client):
        def __bool__(self):return truthy
    monkeypatch.setattr(consumer,'get_jina_embedder',lambda:(_ for _ in ()).throw(AssertionError('Injected client must be used')))
    client=Borrowed();assert consumer.drain_descriptions(embedder=client)['processed']==3
    assert client.closed==0;client.close();assert client.closed==1


def test_direct_batch_closes_internal_client_on_failure(library,monkeypatch):
    client=Client(fail_at=1);monkeypatch.setattr(consumer,'get_jina_embedder',lambda:client)
    with pytest.raises(RuntimeError,match='Controlled'):consumer.process_batch(consumer.get_pending_descriptions())
    assert client.closed==1


def test_cancelled_drain_closes_owner_without_next_file(library,monkeypatch):
    import threading
    stop=threading.Event();client=Client(stop=stop);monkeypatch.setattr(consumer,'get_jina_embedder',lambda:client)
    result=consumer.drain_descriptions(stop_event=stop)
    assert result['cancelled'] and result['processed']==1 and client.calls==1 and client.closed==1


def test_out_of_scope_drain_creates_no_client(library,monkeypatch):
    cl.save_config({'scanner':{'scan_paths':[]}})
    monkeypatch.setattr(consumer,'get_jina_embedder',lambda:(_ for _ in ()).throw(AssertionError('No client needed')))
    assert consumer.drain_descriptions()['processed']==0
