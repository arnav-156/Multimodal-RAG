"""T04 real-model endpoint relocation through an owned loopback forwarding server."""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config_loader as cl
from database import connect
from jina_v5_embedding import JinaV5Embedder
from run_pipeline import process_files
from search_numpy import search_documents


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    cfg=cl.load_config(args.config);upstream=cfg['embedding']['api_url'].rstrip('/')
    if upstream!='http://127.0.0.1:11434':raise ValueError('This acceptance probe only forwards to the existing loopback Ollama service')
    counts={}
    class Forwarder(BaseHTTPRequestHandler):
        def relay(self):
            if self.path not in ('/api/tags','/api/show','/api/embed'):
                self.send_error(404);return
            data=self.rfile.read(int(self.headers.get('Content-Length','0')))
            with requests.Session() as session:
                session.trust_env=False
                response=session.request(self.command,upstream+self.path,data=data,headers={'Content-Type':'application/json'},timeout=120,allow_redirects=False)
            counts[self.path]=counts.get(self.path,0)+1
            self.send_response(response.status_code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response.content)));self.end_headers();self.wfile.write(response.content)
        do_GET=relay;do_POST=relay
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Forwarder);worker=threading.Thread(target=server.serve_forever);worker.start()
    def rows_digest():
        with connect(cfg['database']['path']) as c:
            rows=[(r['id'],r['file_id'],r['chunk_index'],r['meta_json'],bytes(r['vector']).hex()) for r in c.execute('SELECT * FROM rag_chunks ORDER BY id')]
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    try:
        relocated=copy.deepcopy(cfg);relocated['embedding']['api_url']=f'http://127.0.0.1:{server.server_port}'
        relocated['embedding']['timeout']=30;relocated['embedding']['batch_size']=2
        with JinaV5Embedder(config=cfg) as original,JinaV5Embedder(config=relocated) as moved:
            before=rows_digest();assert original.fingerprint()==moved.fingerprint()
            with cl.operation_settings(relocated):
                result=search_documents('How do I restore the SQLite library backup?',embedder=moved)
                assert Path(result['results'][0]['path']).name=='restore.md'
                resumed=process_files(embedder=moved)
                assert resumed['processed']==0 and resumed['failed']==0 and resumed['skipped']==18
            assert rows_digest()==before
            report={'model_substitution':False,'original_url':upstream,'relocated_url':relocated['embedding']['api_url'],'semantic_contract':moved.semantic_contract(),'fingerprint_equal':True,'search_first':'restore.md','resume':resumed,'chunk_rows_unchanged':True,'forwarded_real_requests':counts,'owned_forwarder_stopped':False}
    finally:
        server.shutdown();server.server_close();worker.join(timeout=5)
        assert not worker.is_alive()
    report['owned_forwarder_stopped']=True
    Path(args.output).write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
