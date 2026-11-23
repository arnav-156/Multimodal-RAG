"""Real Jina semantic verification using fixed generated text and the actual TEI endpoint."""
import argparse
import copy
import json
from pathlib import Path
import sys
import numpy as np
import requests
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config_loader import validate_config
from jina_v5_embedding import JinaV5Embedder,EmbeddingError
from security import validate_endpoint


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);args=parser.parse_args()
    if args.output.exists():raise ValueError('Choose a new evidence output; existing evidence is never overwritten')
    config=validate_config(yaml.safe_load(args.profile.read_text(encoding='utf-8-sig')),args.profile.resolve().parent)
    settings=config['embedding'];base=settings['api_url'].rstrip('/')
    if settings['provider']!='tei' or settings['model']!='jinaai/jina-embeddings-v5-text-small-retrieval' or settings['native_dimension']!=1024 or settings['dimension']!=512:
        raise ValueError('This proof requires the explicit Jina TEI 1024-native/512-output profile')
    validate_endpoint(base,config,cloud_authorized=settings['cloud_authorized'])
    text='restore the local library';report={'model_substitution':False,'profile':'actual pinned Jina retrieval via TEI'}
    with JinaV5Embedder(config=config) as reduced:
        report['contract']=reduced.semantic_contract()
        query=reduced.encode([text],prompt_name='query')[0];document=reduced.encode([text],prompt_name='document')[0]
        report['output_dimension']=len(query);report['roles_differ']=not np.allclose(query,document)
        assert len(query)==512 and report['roles_differ']
        native_config=copy.deepcopy(config);native_config['embedding']['dimension']=1024
        with JinaV5Embedder(config=native_config) as native:full=native.encode([text],prompt_name='query')[0]
        expected=full[:512]/np.linalg.norm(full[:512]);report['native_dimension']=len(full)
        report['matryoshka_max_abs_error']=float(np.max(np.abs(query-expected)));assert np.allclose(query,expected,atol=2e-6)
    with requests.Session() as session:
        session.trust_env=False
        headers={'Authorization':'Bearer '+settings['api_key']} if settings['api_key'] else {}
        prefixed=settings['query_prefix']+text
        raw=session.post(base+'/v1/embeddings',headers=headers,json={'model':settings['model'],'input':[prefixed]},timeout=120,allow_redirects=False);raw.raise_for_status()
        vector=np.array(raw.json()['data'][0]['embedding'],dtype=np.float32);vector/=np.linalg.norm(vector)
        report['explicit_prefix_matches_client']=bool(np.allclose(vector,full,atol=2e-6));assert report['explicit_prefix_matches_client']
        tokens=session.post(base+'/embed_all',headers=headers,json={'inputs':[prefixed],'truncate':False},timeout=120,allow_redirects=False);tokens.raise_for_status()
        states=np.array(tokens.json(),dtype=np.float32)[0];last=states[-1]/np.linalg.norm(states[-1]);mean=states.mean(axis=0);mean/=np.linalg.norm(mean)
        report['token_states_shape']=list(states.shape);report['last_token_max_abs_error']=float(np.max(np.abs(last-full)))
        report['mean_pooling_differs']=not np.allclose(mean,full,atol=1e-4)
        assert np.allclose(last,full,atol=2e-5) and report['mean_pooling_differs']
        oversized=session.post(base+'/v1/embeddings',headers=headers,json={'model':settings['model'],'input':['Query: '+'word '*700]},timeout=120,allow_redirects=False)
        report['oversize_status']=oversized.status_code;assert oversized.status_code in (400,413,422)
    wrong=copy.deepcopy(config);wrong['embedding']['revision']='wrong-revision'
    try:
        with JinaV5Embedder(config=wrong) as model:model.fingerprint()
        raise AssertionError('Wrong revision accepted')
    except EmbeddingError:report['wrong_revision_rejected']=True
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
