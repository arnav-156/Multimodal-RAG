"""B01/B22/B30/B41: actual client request/response boundaries, fake transport."""
import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def configuration():
    return {'security':{'local_only':True,'allow_cloud':False},'embedding':{
      'provider':'openai','api_url':'http://127.0.0.1:8001','model':'jina-test','revision':'testrev',
      'dimension':4,'native_dimension':4,'query_prefix':'Query: ','document_prefix':'Document: ',
      'batch_size':4,'timeout':2,'vllm':{}},'chunking':{'max_chars':1000}}

class Transport:
    def __init__(self):self.payloads=[];self.bad=None
    def request(self,method,url,**kw):
        if method=='GET':body={'data':[{'id':'jina-test','revision':'testrev'}]}
        else:
            self.payloads.append(kw['json']);texts=kw['json']['input']
            body={'data':[{'index':i,'embedding':[1.,2.,3.,4.]} for i in range(len(texts))]}
            if self.bad=='count':body['data']=[]
            if self.bad=='nan':body['data'][0]['embedding'][0]=float('nan')
            if self.bad=='index':body['data'][0]['index']=9
            if self.bad=='dimension':body['data'][0]['embedding']=[1.,2.]
        return type('Response',(),{'status_code':200,'json':lambda _:body,'raise_for_status':lambda _:None})()
    def close(self):pass

def client():
    from jina_v5_embedding import JinaV5Embedder
    c=JinaV5Embedder(config=configuration());c.session=Transport();return c

def test_query_document_roles_and_no_silent_truncation():
    c=client();q=c.encode(['same text'],prompt_name='query');c.encode(['same text'],prompt_name='document')
    assert c.session.payloads[0]['input']==['Query: same text']
    assert c.session.payloads[1]['input']==['Document: same text']
    text='x'*13000+'TAIL';c.encode([text]);assert c.session.payloads[-1]['input'][0].endswith('TAIL')
    assert np.allclose(np.linalg.norm(q,axis=1),1)
    assert c.encode([]).shape==(0,4)

@pytest.mark.parametrize('kind',['count','nan','index','dimension'])
def test_bad_response_rejected(kind):
    c=client();c.session.bad=kind
    with pytest.raises((RuntimeError,ValueError)):c.encode(['fixture'])

def test_model_identity_checked():
    cfg=configuration();cfg['embedding']['model']='not-the-served-model'
    from jina_v5_embedding import JinaV5Embedder
    c=JinaV5Embedder(config=cfg);c.session=Transport()
    with pytest.raises(RuntimeError):c.encode(['fixture'])
