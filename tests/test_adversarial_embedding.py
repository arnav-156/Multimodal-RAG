"""Actual local HTTP protocol faults; never model-quality substitutes."""
import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from jina_v5_embedding import JinaV5Embedder,EmbeddingError

@pytest.mark.parametrize('provider,route,bad',[
 ('ollama','/api/tags',{'models':None}),('ollama','/api/tags',{'models':[None]}),
 ('ollama','/api/show',{'capabilities':['embedding'],'model_info':[]}),
 ('ollama','/api/show',{'capabilities':None,'model_info':{}}),
 ('openai','/v1/models',{'data':None}),('openai','/v1/models',{'data':[None]}),
 ('openai','/v1/models',{'data':[{'id':'testmodel','metadata':None}]}),
 ('openai','/v1/embeddings',{'data':[{'index':{},'embedding':[1,0,0,0]}]}),
])
def test_malformed_nested_provider_payload_is_a_controlled_embedding_error(provider,route,bad):
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args):pass
  def do_GET(self):self.respond()
  def do_POST(self):self.rfile.read(int(self.headers.get('Content-Length',0)));self.respond()
  def respond(self):
   normal={'/api/tags':{'models':[{'name':'testmodel','digest':'rev'}]},'/api/show':{'capabilities':['embedding'],'model_info':{'test.embedding_length':4,'test.pooling_type':1}},'/v1/models':{'data':[{'id':'testmodel','revision':'rev'}]},'/v1/embeddings':{'data':[{'index':0,'embedding':[1,0,0,0]}]}}
   raw=json.dumps(bad if self.path==route else normal[self.path]).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 cfg={'security':{'local_only':True,'allow_cloud':False},'embedding':{'provider':provider,'api_url':f'http://127.0.0.1:{server.server_port}','model':'testmodel','revision':'rev','dimension':4,'native_dimension':4,'batch_size':1,'timeout':2,'vllm':{}},'chunking':{'max_chars':1000}}
 try:
  with JinaV5Embedder(config=cfg) as model:
   with pytest.raises(EmbeddingError):model.encode(['isolated protocol fixture'])
 finally:server.shutdown();server.server_close();thread.join(2)
