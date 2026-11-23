"""T04 semantic compatibility gates; transport substitutes only for pure contracts."""
import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jina_v5_embedding import JinaV5Embedder
from database import initialize,connect
from run_pipeline import ensure_index_contract,pipeline_fingerprint
import config_loader as cl


def profile(url='http://127.0.0.1:8101',**changes):
    cfg=cl.validate_config({'embedding':{'provider':'ollama','api_url':url,'model':'fixture','revision':'sha256:fixed-artifact','dimension':4,'native_dimension':4}})
    cfg['embedding'].update(changes)
    c=JinaV5Embedder(config=cfg);c._checked=True
    c.identity={'model':'fixture','revision':'fixed-artifact','native_dimension':4,'pooling':1}
    return c


def test_verified_operational_changes_preserve_semantic_fingerprint():
    with profile() as a,profile('http://127.0.0.1:8102',max_input_chars=4000,timeout=10,batch_size=1,api_key='never-hash-secrets') as b:
        assert a.fingerprint()==b.fingerprint()
        contract=a.semantic_contract()
        assert contract['version']==1 and contract['verification']=='artifact-and-pooling'
        serialized=json.dumps(contract)
        assert 'api_url' not in serialized and 'max_input_chars' not in serialized and 'never-hash-secrets' not in serialized


@pytest.mark.parametrize('change',['revision','query_prefix','document_prefix','dimension','native_dimension','pooling','provider','normalization','codec'])
def test_semantic_changes_remain_incompatible(change):
    with profile() as a,profile() as b:
        if change=='revision':b.identity['revision']='other-artifact'
        elif change=='pooling':b.identity['pooling']=2
        elif change=='dimension':b.truncate_dim=3
        elif change=='native_dimension':b.native_dimension=5;b.identity['native_dimension']=5
        elif change=='provider':b.provider='openai'
        elif change in ('normalization','codec'):
            # Contract version owns algorithm changes; test the fingerprint's
            # sensitivity without fabricating a numerical implementation.
            contract=b.semantic_contract; b.semantic_contract=lambda:dict(contract(),**{change:'changed-algorithm'})
        else:b.settings[change]='changed-role: '
        assert a.fingerprint()!=b.fingerprint()


def test_unknown_pooling_stays_bound_to_deployment():
    with profile() as a,profile('http://127.0.0.1:8102') as b:
        a.identity['pooling']=b.identity['pooling']=None
        assert a.fingerprint()!=b.fingerprint()
        assert a.semantic_contract()['verification']=='deployment-bound-unverified'


def test_legacy_mismatch_requires_explicit_rebuild_and_persists_contract(tmp_path):
    db=tmp_path/'legacy.db';initialize(db)
    with connect(db) as c:
        c.execute("INSERT INTO files(id,path) VALUES (1,'fixture')")
        c.execute("INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (1,0,'{}',?)",(b'legacy',))
        c.execute("INSERT INTO index_metadata(key,value) VALUES ('embedding_fingerprint','ambiguous-legacy-v0')")
    with profile() as model:
        with pytest.raises(ValueError,match='explicitly rebuild'):ensure_index_contract(model,db)
        with connect(db) as c:assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==1
        ensure_index_contract(model,db,force=True)
        with connect(db) as c:
            assert c.execute('SELECT COUNT(*) FROM rag_chunks').fetchone()[0]==0
            contract=json.loads(c.execute("SELECT value FROM index_metadata WHERE key='embedding_contract'").fetchone()[0])
        assert contract==model.semantic_contract()


def test_input_budget_changes_processing_validation_not_vector_identity(tmp_path):
    cl.reset_config_cache();p=tmp_path/'config.yaml';p.write_text('{}',encoding='utf8');cl.load_config(p)
    try:
        with profile() as model:
            semantic=model.fingerprint();old=pipeline_fingerprint(model)
            cl.save_config({'embedding':{'max_input_chars':4000}})
            assert model.fingerprint()==semantic
            assert pipeline_fingerprint(model)!=old
    finally:cl.reset_config_cache()
