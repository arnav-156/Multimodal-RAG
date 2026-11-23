"""T14 TEI protocol contract tests; fake transport is not real model acceptance."""
from pathlib import Path
import sys,copy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
from jina_v5_embedding import JinaV5Embedder,EmbeddingError

MODEL='jinaai/jina-embeddings-v5-text-small-retrieval'
class Transport:
    def __init__(self):
        self.info={'model_id':'/models/jina','served_model_name':MODEL,'model_sha':'fixture-revision','model_dtype':'float32','model_type':{'embedding':{'pooling':'last_token'}},'auto_truncate':False,'max_input_length':512}
        self.inputs=[]
    def request(self,method,url,**kwargs):
        if method=='GET':
            assert url.endswith('/info');body=copy.deepcopy(self.info)
        else:
            self.inputs.extend(kwargs['json']['input']);body={'data':[{'index':i,'embedding':[1,2,3,4]} for i,_ in enumerate(kwargs['json']['input'])]}
        return type('Response',(),{'status_code':200,'json':lambda _:body})()
    def close(self):pass


def client():
    config=cl.validate_config({'embedding':{'provider':'tei','api_url':'http://127.0.0.1:8001','model':MODEL,'revision':'fixture-revision','dimension':4,'native_dimension':4}})
    c=JinaV5Embedder(config=config);c.session.close();c.session=Transport();return c


def test_tei_uses_actual_info_identity_and_explicit_roles():
    with client() as c:
        assert c.fingerprint()
        assert c.identity['revision']=='fixture-revision' and c.identity['pooling']=='last-token'
        assert c.encode(['same text'],prompt_name='query').shape==(1,4)
        c.encode(['same text'],prompt_name='document')
        assert c.session.inputs==['Query: same text','Document: same text']
        assert c.semantic_contract()['verification']=='artifact-and-pooling'


@pytest.mark.parametrize('problem',['revision','pooling','model','auto_truncate','not_embedding','missing_revision'])
def test_tei_rejects_unverified_or_wrong_runtime_semantics(problem):
    with client() as c:
        if problem=='revision':c.session.info['model_sha']='other'
        elif problem=='missing_revision':c.session.info['model_sha']=None
        elif problem=='pooling':c.session.info['model_type']['embedding']['pooling']='mean'
        elif problem=='model':c.session.info['served_model_name']='other-model'
        elif problem=='auto_truncate':c.session.info['auto_truncate']=True
        else:c.session.info['model_type']={'classifier':{}}
        with pytest.raises(EmbeddingError):c.fingerprint()
        assert c.session.inputs==[]
