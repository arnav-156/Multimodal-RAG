"""Capacity benchmark ONLY. Copies real vectors into labeled artificial DB rows.
These rows are never used to claim retrieval quality or source-ingestion acceptance.
"""
import argparse,json,math,os
from pathlib import Path
import statistics,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
import requests,yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import initialize,connect

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source-run',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    original=Path(args.source_run).resolve();root=Path(args.output).resolve()
    if root.exists():raise SystemExit('Choose a new output directory')
    root.mkdir(parents=True);corpus=root/'artificial-paths';corpus.mkdir()
    cfg=yaml.safe_load((original/'config.yaml').read_text(encoding='utf8'));cfg['database']['path']=str(root/'load.db');cfg['scanner']['scan_paths']=[str(corpus)];cfg['server']['port']=18112;cfg['server']['cors_origins']=['http://127.0.0.1:18112']
    config=root/'config.yaml';config.write_text(yaml.safe_dump(cfg),encoding='utf8')
    db=root/'load.db';initialize(db)
    with connect(original/'index.db') as source:
        chunks=source.execute('SELECT vector,meta_json FROM rag_chunks ORDER BY id').fetchall()
        metadata=source.execute('SELECT key,value FROM index_metadata').fetchall()
    with connect(db) as c:
        c.executemany('INSERT INTO index_metadata(key,value) VALUES (?,?)',[tuple(r) for r in metadata])
        for i in range(10000):
            fid=i+1;name=f'capacity-{i:05d}.txt';file_path=str(corpus/name)
            c.execute('INSERT INTO files(id,path,name,ext,is_deleted,file_hash,root_path) VALUES (?,?,?,?,0,?,?)',(fid,file_path,name,'.txt','capacity-only',str(corpus)))
            c.execute("INSERT INTO rag_status(file_id,status,source_hash) VALUES (?,'done','capacity-only')",(fid,))
            row=chunks[i%len(chunks)];c.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (?,0,?,?)',(fid,row['meta_json'],row['vector']))
    base='http://127.0.0.1:18112';token='capacity-only-disposable-auth-token-2026';headers={'Authorization':'Bearer '+token}
    code=Path(__file__).resolve().parents[1]/'run_pipeline.py'
    report={'purpose':'capacity only; artificial DB rows with copied real vectors','quality_metrics':None,'files':10000,'chunks':10000,'dimension':768}
    with (root/'server.log').open('w',encoding='utf8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(code),'--config',str(config),'serve'],env=dict(os.environ,RAG_API_TOKEN=token,PYTHONUTF8='1'),stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            for _ in range(100):
                if child.poll() is not None:raise RuntimeError('Owned benchmark server failed to start')
                try:
                    if requests.get(base+'/api/live',timeout=1).status_code==200:break
                except requests.RequestException:pass
                time.sleep(.1)
            def query(i):
                start=time.perf_counter();r=requests.post(base+'/api/search',headers=headers,json={'query':'recover the database','top_k':50},timeout=120)
                data=r.json();return {'status':r.status_code,'ms':(time.perf_counter()-start)*1000,'total':data.get('total'),'eligible_files':data.get('eligible_files'),'invalid':data.get('skipped_invalid')}
            query(0) # Explicit warmup excluded from measured samples.
            with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(query,range(6)))
            report['requests']=results;times=[r['ms'] for r in results];report['latency_ms']={'mean':statistics.mean(times),'p95':sorted(times)[math.ceil(.95*len(times))-1],'concurrency':2,'samples':len(times)}
            report['passed']=all(r['status']==200 and r['total']==50 and r['eligible_files']==10000 and r['invalid']==0 for r in results)
        finally:
            child.terminate()
            try:child.wait(timeout=10)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
            report['owned_server_stopped']=child.poll() is not None
            (root/'results.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,indent=2));return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
