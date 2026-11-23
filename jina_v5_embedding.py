#!/usr/bin/env python3
"""Validated local embedding client; legacy JinaV5Embedder name is retained.

Supports a pinned Jina retrieval model through an OpenAI-compatible local service
and explicit Ollama embedding profiles. Never downloads, auto-starts, kills,
truncates text, or guesses that an HTTP 200 proves the correct model.
"""
import copy
import hashlib
import json
import numpy as np
import requests

class EmbeddingError(RuntimeError):
    pass

class JinaV5Embedder:
    def __init__(self, model_path=None, device=None, max_length=32768,
                 batch_size=None, truncate_dim=None, api_url=None, model_name=None,
                 config=None):
        from config_loader import get_config
        self.config=copy.deepcopy(config if config is not None else get_config())
        self.settings=self.config['embedding']
        vllm=self.settings.get('vllm',{})
        self.provider=self.settings.get('provider','openai')
        self.api_url=(api_url or self.settings.get('api_url') or vllm.get('api_url','http://127.0.0.1:8001')).rstrip('/')
        self.model_name=model_name or self.settings.get('model') or vllm.get('model_name','')
        if not self.model_name:raise ValueError('An embedding model must be explicitly configured')
        self.truncate_dim=truncate_dim or self.settings.get('dimension',512)
        self.native_dimension=self.settings.get('native_dimension') or self.truncate_dim
        self.batch_size=batch_size or self.settings.get('batch_size',4)
        self.max_length=self.settings.get('max_input_chars',max_length)
        self.session=requests.Session()
        self.session.trust_env=False  # Do not send local documents through environment proxies.
        self._checked=False
        self.identity={}

    def _request(self,method,route,**kwargs):
        from security import validate_endpoint
        validate_endpoint(self.api_url,cfg=self.config,
                          cloud_authorized=self.settings.get('cloud_authorized',False),live_policy=True)
        headers={}
        if self.settings.get('api_key'):headers['Authorization']='Bearer '+self.settings['api_key']
        try:
            response=self.session.request(method,self.api_url+route,headers=headers,
                timeout=self.settings.get('timeout',120),allow_redirects=False,**kwargs)
            if response.status_code!=200:
                # Server bodies may contain echoed document contents or secrets.
                raise EmbeddingError(f'Embedding service returned HTTP {response.status_code}')
            data=response.json()
            if not isinstance(data,dict):raise EmbeddingError('Embedding service returned an invalid JSON object')
            return data
        except requests.RequestException as exc:
            raise EmbeddingError('Local embedding service is unavailable; check the configured endpoint') from exc
        except (json.JSONDecodeError,ValueError) as exc:
            raise EmbeddingError('Embedding service returned invalid JSON') from exc

    def _check_service(self):
        if self._checked:return
        if self.provider=='ollama':
            tags=self._request('GET','/api/tags').get('models',[])
            if not isinstance(tags,list) or any(not isinstance(m,dict) for m in tags):
                raise EmbeddingError('Ollama returned an invalid model list')
            matches=[m for m in tags if m.get('name')==self.model_name or m.get('model')==self.model_name]
            if not matches:raise EmbeddingError('Configured embedding model is not installed in Ollama')
            identity=matches[0]
            revision=self.settings.get('revision','').removeprefix('sha256:')
            if revision and identity.get('digest')!=revision:
                raise EmbeddingError('Ollama model digest differs from the pinned revision')
            shown=self._request('POST','/api/show',json={'model':self.model_name})
            capabilities=shown.get('capabilities',[])
            if not isinstance(capabilities,list) or 'embedding' not in capabilities:raise EmbeddingError('Configured Ollama model is not an embedding model')
            info=shown.get('model_info',{})
            if not isinstance(info,dict):raise EmbeddingError('Ollama returned invalid model metadata')
            actual_dimension=next((v for k,v in info.items() if k.endswith('.embedding_length')),None)
            if actual_dimension!=self.native_dimension:raise EmbeddingError('Served model dimension differs from configuration')
            if self.truncate_dim!=self.native_dimension:
                raise EmbeddingError('Ollama profiles currently require native output dimension; implicit Matryoshka slicing is disabled')
            self.identity={'model':self.model_name,'revision':identity.get('digest'),'native_dimension':actual_dimension,
                           'pooling':next((v for k,v in info.items() if k.endswith('.pooling_type')),None)}
        elif self.provider=='tei':
            info=self._request('GET','/info')
            served=info.get('served_model_name') or info.get('model_id')
            if served!=self.model_name:raise EmbeddingError('TEI served model differs from configuration')
            revision=self.settings.get('revision','')
            if not revision or info.get('model_sha')!=revision:
                raise EmbeddingError('TEI does not verify the pinned model revision')
            kind=info.get('model_type')
            embedding=kind.get('embedding') if isinstance(kind,dict) else None
            if not isinstance(embedding,dict) or not isinstance(embedding.get('pooling'),str) or not embedding['pooling']:
                raise EmbeddingError('Configured TEI endpoint is not a verified embedding model')
            pooling=embedding['pooling'].replace('_','-')
            if 'jina-embeddings-v5' in self.model_name:
                if not self.model_name.rstrip('/').endswith('-retrieval'):
                    raise EmbeddingError('Use the merged Jina retrieval checkpoint')
                if pooling!='last-token':raise EmbeddingError('Jina v5 retrieval requires last-token pooling')
            if info.get('auto_truncate') is not False:
                raise EmbeddingError('TEI automatic truncation must be explicitly disabled')
            self.identity={'model':served,'revision':info['model_sha'],'native_dimension':self.native_dimension,
                           'pooling':pooling}
        elif self.provider=='openai':
            models=self._request('GET','/v1/models').get('data',[])
            if not isinstance(models,list) or any(not isinstance(m,dict) for m in models):
                raise EmbeddingError('Embedding service returned an invalid model list')
            matches=[m for m in models if m.get('id')==self.model_name]
            if not matches:raise EmbeddingError('Configured embedding model is not advertised by the local service')
            revision=self.settings.get('revision','')
            metadata=matches[0].get('metadata',{})
            if not isinstance(metadata,dict):raise EmbeddingError('Embedding service returned invalid model metadata')
            advertised=matches[0].get('revision') or metadata.get('revision')
            if revision and advertised!=revision:raise EmbeddingError('Service does not verify the configured model revision')
            if 'jina-embeddings-v5' in self.model_name and not self.model_name.rstrip('/').endswith('-retrieval'):
                raise EmbeddingError('Use the merged Jina retrieval checkpoint, not the unrouted multi-adapter base model')
            served_dimension=metadata.get('native_dimension',self.native_dimension)
            if served_dimension!=self.native_dimension:raise EmbeddingError('Served model dimension differs from configuration')
            self.identity={'model':self.model_name,'revision':advertised,'native_dimension':served_dimension,
                           'pooling':metadata.get('pooling')}
        else:raise ValueError('Unsupported embedding provider')
        self._checked=True

    def semantic_contract(self):
        """Versioned vector semantics; unverified deployments cannot relocate silently."""
        self._check_service()
        verified=bool(self.identity.get('revision')) and self.identity.get('pooling') is not None
        contract={'version':1,'provider':self.provider,'identity':copy.deepcopy(self.identity),
            'dimension':self.truncate_dim,'native_dimension':self.native_dimension,
            'query_prefix':self.settings.get('query_prefix',''),
            'document_prefix':self.settings.get('document_prefix',''),
            'normalization':'client-l2-v1','codec':'signed-int8-round-127-l2-decode-v1',
            'dimension_reduction':'native' if self.truncate_dim==self.native_dimension else 'leading-matryoshka-slice-v1',
            'verification':'artifact-and-pooling' if verified else 'deployment-bound-unverified'}
        if not verified:contract['unverified_deployment']=self.api_url
        return contract

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.semantic_contract(),sort_keys=True).encode('utf-8')).hexdigest()

    def encode(self,texts,task='retrieval',prompt_name='document',batch_size=None,max_retries=1):
        if task!='retrieval' or prompt_name not in ('query','document'):
            raise ValueError('Only explicit retrieval query/document encoding is supported')
        if not isinstance(texts,list) or any(not isinstance(t,str) or not t.strip() or '\x00' in t for t in texts):
            raise ValueError('Embedding inputs must be nonempty text strings without null bytes')
        if not texts:return np.empty((0,self.truncate_dim),dtype=np.float32)
        prefix=self.settings.get(prompt_name+'_prefix','')
        prefixed=[prefix+t for t in texts]
        if any(len(t)>self.max_length for t in prefixed):
            raise ValueError('Embedding input exceeds the configured character budget; rechunk without truncation')
        self._check_service()
        size=batch_size or self.batch_size
        if not isinstance(size,int) or size<1:raise ValueError('Batch size must be positive')
        batches=[]
        for start in range(0,len(prefixed),size):
            batch=prefixed[start:start+size]
            if self.provider=='ollama':
                data=self._request('POST','/api/embed',json={'model':self.model_name,'input':batch,'truncate':False})
                if data.get('model') and data['model']!=self.model_name:raise EmbeddingError('Embedding response model mismatch')
                raw=data.get('embeddings')
            else:
                data=self._request('POST','/v1/embeddings',json={'model':self.model_name,'input':batch})
                items=data.get('data',[])
                if not isinstance(items,list) or any(not isinstance(i,dict) or type(i.get('index')) is not int for i in items):
                    raise EmbeddingError('Embedding response indexes/count do not match input')
                if len(items)!=len(batch) or {i.get('index') for i in items}!=set(range(len(batch))):
                    raise EmbeddingError('Embedding response indexes/count do not match input')
                raw=[i.get('embedding') for i in sorted(items,key=lambda i:i['index'])]
            try:vectors=np.asarray(raw,dtype=np.float32)
            except (TypeError,ValueError) as exc:raise EmbeddingError('Embedding response is not a numeric matrix') from exc
            if vectors.shape!=(len(batch),self.native_dimension) or not np.isfinite(vectors).all():
                raise EmbeddingError('Embedding response has invalid dimensions or non-finite values')
            if self.truncate_dim!=self.native_dimension:
                if 'jina' not in self.model_name.lower() or self.truncate_dim not in [32,64,128,256,512,768,1024] or self.truncate_dim>self.native_dimension:
                    raise EmbeddingError('Unsupported embedding dimension reduction')
                vectors=vectors[:,:self.truncate_dim]
            norms=np.linalg.norm(vectors,axis=1,keepdims=True)
            if not np.isfinite(norms).all() or np.any(norms<1e-8):raise EmbeddingError('Embedding service returned a zero/invalid vector')
            batches.append(vectors/norms)
        return np.concatenate(batches,axis=0)

    def close(self):self.session.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    @property
    def embedding_dim(self):return self.truncate_dim
    def _health_check(self):self._checked=False;self._check_service()

def load_model(model_path=None,**kwargs):return JinaV5Embedder(model_path=model_path,**kwargs)
