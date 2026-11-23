"""Reproducible local embedding acceptance (default Nomic or an explicit profile).
Never substitutes vectors, changes the default config, downloads a model or scans user folders.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import yaml
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

DOCUMENTS={
 'restore.md':'Database recovery guide. Restore the SQLite library using an online backup. Stop writers, copy the backup into a new database path and run the integrity check before reopening the service.',
 'bread.txt':'Sourdough bread recipe. Mix flour and water, feed the starter and let the dough ferment overnight. Bake in a covered cast iron pot.',
 'refund.txt':'Refund policy. Customers can request a refund within thirty days with the original receipt. Refunds return to the original payment method.',
 'refund-near.txt':'Refund policy copy. Return an item within thirty days and present the original receipt. The purchase is refunded to the original payment method.',
 'refund-negative.txt':'Database transaction rollback is not a customer refund. Rollback undoes uncommitted SQL changes after an application error.',
 'garden.txt':'Tomato garden. Plant seedlings after the last frost in full sunlight. Water roots deeply and support vines with stakes.',
 'encryption.txt':'Encryption at rest protects stored data using AES-256. Keep the encryption key separate from encrypted backups and rotate keys according to policy.',
 'tls-negative.txt':'Transport encryption protects network traffic using TLS certificates. It does not encrypt the files stored on disk.',
 'cache.py':'def evict_expired(cache, now):\n    """Remove cached entries whose expiry timestamp is before now."""\n    for key in list(cache):\n        if cache[key]["expires"] < now:\n            del cache[key]\n',
 'short.md':'Emergency assembly point: the north parking lot.',
 'untrusted.txt':'<img src=x onerror="window.RAG_XSS=1">\nSafety fixture: untrusted document markup must be displayed as plain text.',
 'duplicate.txt':'Emergency assembly point: the north parking lot.',
 'solar.txt':'Solar panels convert sunlight into electrical energy. A photovoltaic inverter converts direct current into alternating current for the building.',
 'jazz.txt':'Jazz rehearsal schedule. The quartet rehearses piano, bass, drums and saxophone on Thursday evenings.',
 'travel.txt':'Travel packing checklist. Pack passport, tickets, medicines and a power adapter. Check baggage allowance before departure.',
}
QUERIES=[
 {'query':'How do I recover the SQLite document library from a backup?','relevant':['restore.md'],'category':'operations'},
 {'query':'When can a customer get their money back for a returned purchase?','relevant':['refund.txt','refund-near.txt'],'category':'near-duplicate/hard-negative'},
 {'query':'How do we protect backups saved on disk with an encryption key?','relevant':['encryption.txt'],'category':'hard-negative'},
 {'query':'Find Python code which removes expired cache entries.','relevant':['cache.py'],'category':'code'},
 {'query':'Where should everyone meet during an emergency evacuation?','relevant':['short.md','duplicate.txt'],'category':'short/duplicate'},
 {'query':'What does the photovoltaic inverter do?','relevant':['solar.txt'],'category':'semantic'},
 {'query':'Which folder should receive expense receipts?','relevant':['expenses.docx'],'category':'office'},
 {'query':'What pressure should the cooling circuit operate at?','relevant':['cooling.pdf'],'category':'pdf'},
 {'query':'What is the planned server maintenance window?','relevant':['maintenance.pptx'],'category':'slides'},
 {'query':'What is the stock reorder quantity for blue widgets?','relevant':['inventory.xlsx'],'category':'spreadsheet'},
 {'query':'How is rye sourdough bread fermented?','relevant':['bread.txt'],'category':'food'},
]

def generate(root):
    corpus=root/'corpus';corpus.mkdir(parents=True)
    for name,text in DOCUMENTS.items():(corpus/name).write_text(text,encoding='utf8')
    from docx import Document
    doc=Document();doc.add_heading('Expense filing instructions',0);doc.add_paragraph('Store expense receipts in the Finance/Receipts directory. Submit the monthly report to the finance team.');doc.save(corpus/'expenses.docx')
    import fitz
    with fitz.open() as doc:
        page=doc.new_page();page.insert_text((50,70),'Cooling circuit service manual. Normal operating pressure is 240 kPa.');doc.save(corpus/'cooling.pdf')
    from pptx import Presentation
    deck=Presentation();slide=deck.slides.add_slide(deck.slide_layouts[1]);slide.shapes.title.text='Server maintenance';slide.placeholders[1].text='The planned maintenance window is Sunday from 02:00 to 04:00 UTC.';deck.save(corpus/'maintenance.pptx')
    from openpyxl import Workbook
    wb=Workbook();ws=wb.active;ws.append(['Product','Reorder quantity']);ws.append(['Blue widgets',120]);wb.save(corpus/'inventory.xlsx')
    (root/'queries.json').write_text(json.dumps(QUERIES,indent=2),encoding='utf8')
    config={'database':{'path':str(root/'index.db')},'scanner':{'scan_paths':[str(corpus)]},
      'server':{'host':'127.0.0.1','port':18110,'cors_origins':['http://127.0.0.1:18110']},
      'embedding':{'provider':'ollama','api_url':'http://127.0.0.1:11434','model':'nomic-embed-text:latest',
      'revision':'0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f','dimension':768,'native_dimension':768,
      'query_prefix':'search_query: ','document_prefix':'search_document: ','max_input_chars':4000,'batch_size':4},
      'chunking':{'max_chars':1000},'ocr':{'enabled':False},'vision':{'endpoints':[]}}
    config_path=root/'config.yaml';config_path.write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf8')
    return corpus,config_path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--embedding-profile',type=Path);args=parser.parse_args()
    root=Path(args.output).resolve()
    if root.exists():raise SystemExit('Choose a new output directory; existing evidence is never overwritten')
    root.mkdir(parents=True);corpus,config_path=generate(root)
    from config_loader import load_config,validate_config
    if args.embedding_profile:
        profile_path=args.embedding_profile.resolve()
        raw_profile=yaml.safe_load(profile_path.read_text(encoding='utf-8-sig'))
        profile=validate_config(raw_profile,profile_path.parent)
        generated=yaml.safe_load(config_path.read_text(encoding='utf8'))
        generated['embedding']=profile['embedding']
        if 'chunking' in raw_profile:generated['chunking']=profile['chunking']
        config_path.write_text(yaml.safe_dump(generated,sort_keys=False),encoding='utf8')
    effective=load_config(config_path)
    dimension=effective['embedding']['dimension']
    from file_scanner import sync
    from metadata_extractor import extract_metadata
    from run_pipeline import process_files
    from jina_v5_embedding import JinaV5Embedder
    from search_numpy import search_documents
    from database import connect
    from vectors import deserialize_vector
    label=f"{effective['embedding']['provider']}: {effective['embedding']['model']} native {effective['embedding']['native_dimension']} output {dimension}" if args.embedding_profile else 'Ollama Nomic full native 768; Jina not tested'
    report={'model_substitution':False,'profile':label,'corpus_training':False,'cases':[]}
    report['scan']=sync();report['metadata']=extract_metadata();start=time.perf_counter()
    report['process']=process_files();report['process_seconds']=time.perf_counter()-start
    with JinaV5Embedder() as model:
        report['identity']=model.identity if model.fingerprint() else None
        query_vector=model.encode(['restore the library'],prompt_name='query')[0];doc_vector=model.encode(['restore the library'],prompt_name='document')[0]
        report['roles_differ']=not np.allclose(query_vector,doc_vector)
        for item in QUERIES:
            start=time.perf_counter();r=search_documents(item['query'],top_k=5,page_size=5,embedder=model)
            names=[Path(v['path']).name for v in r['results']];relevant=set(item['relevant']);ranks=[i+1 for i,n in enumerate(names) if n in relevant]
            dcg=sum(1/math.log2(i+1) for i in ranks);ideal=sum(1/math.log2(i+2) for i in range(min(5,len(relevant))))
            report['cases'].append({**item,'ranking':names,'relevant_ranks':ranks,'recall_at_5':len(ranks)/len(relevant),'precision_at_5':len(ranks)/5,
              'reciprocal_rank':1/min(ranks) if ranks else 0,'ndcg_at_5':dcg/ideal,'latency_ms':(time.perf_counter()-start)*1000})
        report['unanswerable']=search_documents('What is the secret launch code for the lunar submarine?',top_k=5,embedder=model)
        report['empty_filter']=search_documents('restore',file_types='audio',embedder=model)
    report['metrics']={key:statistics.mean(r[key] for r in report['cases']) for key in ['recall_at_5','precision_at_5','reciprocal_rank','ndcg_at_5','latency_ms']}
    report['metrics']['query_failure_rate']=sum(not r['relevant_ranks'] for r in report['cases'])/len(QUERIES)
    with connect() as c:
        blobs=[r[0] for r in c.execute('SELECT vector FROM rag_chunks')]
        report['vectors']={'count':len(blobs),'valid':sum(np.isfinite(deserialize_vector(b,dimension)).all().item() for b in blobs),'foreign_key_errors':len(c.execute('PRAGMA foreign_key_check').fetchall())}
    command=[sys.executable,'-B',str(Path(__file__).resolve().parents[1]/'run_pipeline.py'),'--config',str(config_path)]
    restarted=subprocess.run(command+['search','recover the SQLite library'],capture_output=True,text=True,encoding='utf8',timeout=120)
    report['restart_search']={'exit_code':restarted.returncode,'result':json.loads(restarted.stdout) if restarted.returncode==0 else restarted.stderr}
    report['resume']=process_files()
    changed=corpus/'short.md';changed.write_text('Emergency assembly point has moved to the east courtyard.',encoding='utf8')
    report['modified_scan']=sync();report['modified_process']=process_files()
    doomed=corpus/'duplicate.txt';doomed.unlink();report['deleted_scan']=sync()
    with connect() as c:report['deleted_chunks']=c.execute("SELECT COUNT(*) FROM rag_chunks ch JOIN files f ON f.id=ch.file_id WHERE f.name='duplicate.txt'").fetchone()[0]
    report['corpus_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in corpus.iterdir()}
    passed=report['process']['failed']==0 and report['roles_differ'] and report['vectors']['valid']==len(blobs) and report['vectors']['foreign_key_errors']==0 and restarted.returncode==0 and report['modified_process']['processed']==1 and report['deleted_chunks']==0
    report['engineering_acceptance']=passed
    (root/'results.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({'output':str(root),'engineering_acceptance':passed,'metrics':report['metrics'],'process':report['process']},indent=2))
    return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
