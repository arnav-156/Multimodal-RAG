"""Real HTTP acceptance against an owned subprocess and generated model-run corpus."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time
import requests
import yaml

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);args=parser.parse_args()
    root=Path(args.run).resolve();config=root/'config.yaml';cfg=yaml.safe_load(config.read_text(encoding='utf8'))
    base=f"http://127.0.0.1:{cfg['server']['port']}";token='disposable-local-http-acceptance-token-2026'
    env=dict(os.environ,RAG_CONFIG=str(config),RAG_API_TOKEN=token,PYTHONUTF8='1')
    source=Path(__file__).resolve().parents[1]
    report={'model_substitution':False,'corpus':'generated acceptance fixtures','cases':[]}
    def case(name,condition,details=None):
        report['cases'].append({'name':name,'passed':bool(condition),'details':details})
        if not condition:raise AssertionError(name)
    with (root/'http-server.log').open('w',encoding='utf8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(source/'run_pipeline.py'),'serve'],cwd=source,env=env,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            with requests.Session() as client:
                client.trust_env=False
                for _ in range(100):
                    if child.poll() is not None:raise RuntimeError('Owned server failed to start; see log')
                    try:
                        if client.get(base+'/api/live',timeout=1).status_code==200:break
                    except requests.RequestException:pass
                    time.sleep(.1)
                case('unauthenticated search rejected',client.post(base+'/api/search',json={'query':'restore'}).status_code==401)
                case('host boundary',client.get(base+'/api/live',headers={'Host':'evil.example'}).status_code==403)
                case('cross-origin boundary',client.get(base+'/api/session',headers={'Origin':'https://evil.example'}).status_code==403)
                case('authorized preflight',client.options(base+'/api/search',headers={'Origin':base,'Access-Control-Request-Method':'POST'}).status_code==200)
                response=client.post(base+'/api/session',json={'token':token});case('session login',response.status_code==200 and 'HttpOnly' in response.headers['set-cookie'])
                health=client.get(base+'/api/health');case('real model and matching index readiness',health.status_code==200,health.json())
                first=client.post(base+'/api/search',json={'query':'restore SQLite backup','top_k':50,'page_size':10}).json()
                second=client.post(base+'/api/search',json={'query':'restore SQLite backup','top_k':50,'page_size':10,'offset':10}).json()
                case('real rank and pagination',Path(first['results'][0]['path']).name=='restore.md' and len(first['results'])+len(second['results'])==first['total'],{'total':first['total']})
                result=first['results'][0];preview=client.get(base+result['document_url']).json()
                download=client.get(base+preview['download_url'])
                case('safe indexed attachment',download.content==(root/'corpus/restore.md').read_bytes() and download.headers['content-type'].startswith('application/octet-stream'))
                case('query validation',client.post(base+'/api/search',json={'query':'  '}).status_code==422)
                case('top-k validation',client.post(base+'/api/search',json={'query':'restore','top_k':0}).status_code==422)
                filtered=client.post(base+'/api/search',json={'query':'operating pressure','file_types':'pdf'}).json()
                case('type filter',filtered['total']==1 and Path(filtered['results'][0]['path']).suffix=='.pdf')
                settings=client.get(base+'/api/config').json()['config'];settings['search']['default_top_k']=7
                case('config save',client.put(base+'/api/config',json={'config':settings}).status_code==200)
                r=client.post(base+'/api/search',json={'query':'restore'}).json();case('effective search default',r['total']==7)
                timings=[]
                def request_one(i):
                    start=time.perf_counter()
                    r=requests.post(base+'/api/search',headers={'Authorization':'Bearer '+token},json={'query':'restore the SQLite database','top_k':5},timeout=120)
                    return r.status_code,(time.perf_counter()-start)*1000
                with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(request_one,range(16)))
                timings=[r[1] for r in responses]
                case('concurrent actual HTTP queries',all(r[0]==200 for r in responses),{'requests':16,'concurrency':4})
                report['latency_ms']={'mean':statistics.mean(timings),'p95':sorted(timings)[math.ceil(.95*len(timings))-1],'samples':timings,'volume_files':first['total']}
                for kind in ('scan','extract','process'):
                    response=client.post(base+'/api/pipeline/'+kind,json={});case(kind+' accepted',response.status_code==202)
                    jobid=response.json()['job_id']
                    for _ in range(1200):
                        job=next(j for j in client.get(base+'/api/pipeline/status').json()['jobs'] if j['id']==jobid)
                        if job['status'] not in ('queued','running'):break
                        time.sleep(.05)
                    case(kind+' completed',job['status']=='completed',job)
                case('logout',client.delete(base+'/api/session').status_code==200 and client.get(base+'/api/stats').status_code==401)
            backup=root/'verified-backup.db'
            result=subprocess.run([sys.executable,'-B',str(source/'run_pipeline.py'),'--config',str(config),'backup',str(backup)],capture_output=True,text=True,timeout=30)
            with sqlite3.connect(backup) as connection:integrity=connection.execute('PRAGMA integrity_check').fetchone()[0]
            case('online backup and integrity',result.returncode==0 and integrity=='ok')
        finally:
            # Only the explicitly owned child, never an inference service.
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
            report['owned_server_stopped']=child.poll() is not None
            (root/'http-results.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'passed':sum(c['passed'] for c in report['cases']),'cases':len(report['cases']),'latency_ms':report['latency_ms'],'owned_server_stopped':report['owned_server_stopped']},indent=2))
if __name__=='__main__':main()
