#!/usr/bin/env python3
"""Bounded description consumption. Can drain pending work without new source files."""
from config_loader import configured_operation
import json
from contextlib import ExitStack
from config_loader import get_db_path, get_embedding_dim, get_config
from chunker_v2 import chunk_greedy_semantic
from corpus_policy import CorpusPolicy, source_allowed
from description_contract import CURRENT_DESCRIPTION, generation_fingerprint


def get_db(db_path=None):
    from database import connect
    return connect(db_path or get_db_path())


def get_pending_descriptions(limit=None, db_path=None):
    limit = limit or get_config()['embedding'].get('description_batch_size', 8)
    with get_db(db_path) as conn:
        CorpusPolicy().install(conn)
        rows = conn.execute("""SELECT d.file_id, d.description, d.source FROM rag_descriptions d JOIN files f ON f.id=d.file_id
            WHERE f.is_deleted=0 AND corpus_selected(f.path) AND embedded=0 AND source NOT LIKE '%failed'
            AND """+CURRENT_DESCRIPTION+' ORDER BY created_at,file_id LIMIT ?', (limit,)).fetchall()
        return [tuple(row) for row in rows]


def get_jina_embedder():
    from jina_v5_embedding import JinaV5Embedder
    return JinaV5Embedder()


def quantize_embedding(embedding):
    from vectors import serialize_vector
    return serialize_vector(embedding, get_embedding_dim())


@configured_operation
def process_batch(descriptions, embedder=None, db_path=None,stop_event=None):
    if not descriptions:
        return 0
    owned=embedder is None
    try:
        processed = 0
        for file_id, description, source in descriptions:
            if stop_event is not None and stop_event.is_set():break
            from file_scanner import file_fingerprint
            with get_db(db_path) as conn:
                version=conn.execute('SELECT path,file_hash FROM files WHERE id=? AND is_deleted=0',(file_id,)).fetchone()
            if version and not source_allowed(version['path']):continue
            if not version or file_fingerprint(version['path'])!=version['file_hash']:
                raise ValueError('Description source changed; scan again before embedding')
            texts = chunk_greedy_semantic(description)
            with get_db(db_path) as conn:
                provenance=conn.execute('SELECT description,source_hash,generation_fingerprint FROM rag_descriptions WHERE file_id=?',(file_id,)).fetchone()
            if not provenance or provenance['generation_fingerprint']!=generation_fingerprint() or provenance['source_hash']!=version['file_hash']:
                raise ValueError('Legacy or stale description is unverified; explicitly regenerate it before embedding')
            if provenance['description']!=description:continue
            if not texts:
                raise ValueError(f'Empty description for file_id={file_id}')
            if not source_allowed(version['path']):continue
            if embedder is None:embedder=get_jina_embedder()
            from run_pipeline import ensure_index_contract
            embedding_fingerprint=ensure_index_contract(embedder,db_path)
            embeddings = embedder.encode(texts, task='retrieval', prompt_name='document')
            if len(embeddings) != len(texts):
                raise ValueError('Description embedding count does not match chunks')
            from vectors import serialize_vector
            serialized = [serialize_vector(vector,embedder.embedding_dim) for vector in embeddings]
            if not source_allowed(version['path']):continue
            if file_fingerprint(version['path'])!=version['file_hash']:raise ValueError('Description source changed during embedding')
            from database import source_transaction
            with source_transaction(db_path or get_db_path(),file_id,version['file_hash']) as conn:
                # A source update can invalidate a description while embedding is in flight.
                current = conn.execute('SELECT description,generation_fingerprint,source_hash FROM rag_descriptions WHERE file_id=? AND embedded=0', (file_id,)).fetchone()
                if current is None or tuple(current)!=(description,provenance['generation_fingerprint'],version['file_hash']):
                    continue
                conn.execute('DELETE FROM rag_chunks WHERE file_id=? AND chunk_index<0', (file_id,))
                for index, (text, vector) in enumerate(zip(texts, serialized), 1):
                    meta = json.dumps({'source': 'vision_' + source, 'description': text,
                                       'kind':'description','source_hash':version['file_hash'],
                                       'generation_fingerprint':provenance['generation_fingerprint'],
                                       'embedding_fingerprint':embedding_fingerprint,
                                       '_text': text}, ensure_ascii=False)
                    conn.execute('INSERT INTO rag_chunks(file_id,chunk_index,meta_json,vector) VALUES (?,?,?,?)',
                                 (file_id, -index, meta, vector))
                conn.execute('UPDATE rag_descriptions SET embedded=1,embedding_fingerprint=? WHERE file_id=?', (embedding_fingerprint,file_id))
            processed += 1
        return processed
    finally:
        if owned and embedder is not None:embedder.close()



@configured_operation
def drain_descriptions(embedder=None, db_path=None, limit=None,stop_event=None):
    """Drain a finite snapshot; failures raise rather than retrying forever."""
    from database import ensure_initialized
    ensure_initialized(db_path or get_db_path())
    with get_db(db_path) as conn:
        CorpusPolicy().install(conn)
        unverified=conn.execute("SELECT COUNT(*) FROM rag_descriptions d JOIN files f ON f.id=d.file_id WHERE f.is_deleted=0 AND corpus_selected(f.path) AND d.generation_fingerprint IS NULL").fetchone()[0]
        if unverified:raise ValueError(f'{unverified} Legacy descriptions are unverified; use process --regenerate-legacy-descriptions with an authorized vision service')
        stale=conn.execute("SELECT COUNT(*) FROM rag_descriptions d JOIN files f ON f.id=d.file_id WHERE f.is_deleted=0 AND corpus_selected(f.path) AND source NOT LIKE '%failed' AND NOT ("+CURRENT_DESCRIPTION+')').fetchone()[0]
        initial=conn.execute("SELECT COUNT(*) FROM rag_descriptions d JOIN files f ON f.id=d.file_id WHERE f.is_deleted=0 AND corpus_selected(f.path) AND embedded=0 AND source NOT LIKE '%failed' AND "+CURRENT_DESCRIPTION).fetchone()[0]
    remaining=min(initial,limit) if limit is not None else initial
    processed=0
    with ExitStack() as owner:
        while remaining>0:
            if stop_event is not None and stop_event.is_set():break
            pending=get_pending_descriptions(limit=min(remaining,get_config()['embedding']['description_batch_size']),db_path=db_path)
            if not pending:break
            if embedder is None:
                embedder=get_jina_embedder()
                owner.callback(embedder.close)
            done=process_batch(pending,embedder,db_path,stop_event=stop_event)
            processed+=done;remaining-=len(pending)
            if not done:break
        return {'processed':processed,'pending_at_start':initial,'stale_at_start':stale,'cancelled':bool(stop_event is not None and stop_event.is_set())}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    args = parser.parse_args()
    if args.config:
        from config_loader import load_config, reset_config_cache
        reset_config_cache(); load_config(args.config)
    print(json.dumps(drain_descriptions()))


if __name__ == '__main__':
    main()
