#!/usr/bin/env python3
"""Generate vision descriptions from explicitly authorized endpoints, one page at a time."""
from config_loader import configured_operation
import base64
import io
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any
import requests
from office_process import run_office_process
from config_loader import get_config, get_db_path, get_env
from corpus_policy import CorpusPolicy, source_allowed
from description_contract import generation_fingerprint, VISION_PROMPT, RENDER_POLICY

log = logging.getLogger(__name__)
from format_capabilities import render_kind, OFFICE_INPUT_FILTERS, RASTER_EXTS as IMAGE_EXTS, RENDER_OFFICE_EXTS as OFFICE_EXTS
_last_request={}


def _get_db(db_path=None):
    from database import connect
    return connect(db_path or get_db_path())


def get_ocr_failed_files(limit=None, db_path=None, regenerate_legacy=False):
    cfg = get_config()
    maximum = cfg.get('vision', {}).get('max_retries', 3)
    selected = []
    limit = limit or cfg.get('pipeline', {}).get('fetch_limit', 20)
    with _get_db(db_path) as conn:
        CorpusPolicy().install(conn)
        rows = conn.execute("""SELECT f.id,f.path,COALESCE(r.reason,'generation_changed') FROM files f
            LEFT JOIN rag_ocr_status r ON r.file_id=f.id LEFT JOIN rag_descriptions d ON d.file_id=f.id
            WHERE f.is_deleted=0 AND corpus_selected(f.path)
            AND ((d.file_id IS NULL AND r.status='insufficient')
                OR (d.generation_fingerprint IS NOT NULL AND (d.generation_fingerprint!=? OR d.source_hash IS NOT f.file_hash))
                OR (d.generation_fingerprint IS NOT NULL AND d.source LIKE '%failed' AND d.retry_count<?)
                OR (? AND d.file_id IS NOT NULL AND d.generation_fingerprint IS NULL))
            ORDER BY f.id""", (generation_fingerprint(),maximum,int(regenerate_legacy)))
        for row in rows:
            if render_kind(row[1]) is not None:
                selected.append(row)
                if len(selected) >= limit:break
    return selected


def render_pdf_to_images(pdf_path):
    import fitz
    maximum = get_config()['limits'].get('max_describe_pages', 500)
    with fitz.open(pdf_path) as doc:
        if len(doc) > maximum:
            raise ValueError(f'PDF exceeds configured {maximum}-page vision limit')
        for page in doc:
            if page.rect.width*page.rect.height*(RENDER_POLICY['pdf_dpi']/72)**2>get_config()['limits']['max_render_pixels']:
                raise ValueError('Rendered PDF page exceeds pixel limit')
            yield page.get_pixmap(dpi=RENDER_POLICY['pdf_dpi'], alpha=False).tobytes('png')


def render_office_to_images(office_path):
    input_filter=OFFICE_INPUT_FILTERS.get(Path(office_path).suffix.lower())
    if not input_filter:raise ValueError('Office rendering requires a declared source filter')
    with tempfile.TemporaryDirectory(prefix='rag_office_') as temporary:
        import shutil
        executable=shutil.which('soffice.com') or shutil.which('libreoffice') or shutil.which('soffice') or 'libreoffice'
        response=run_office_process([executable, '-env:UserInstallation='+Path(temporary,'profile').as_uri(), '--headless', '--infilter='+input_filter, '--convert-to', 'pdf', '--outdir', temporary, office_path],timeout=60)
        response.check_returncode()
        pdfs = list(Path(temporary).glob('*.pdf'))
        if not pdfs: raise ValueError('Office conversion produced no PDF')
        yield from render_pdf_to_images(str(pdfs[0]))


def render_djvu_to_images(djvu_path):
    from djvu_reader import page_count,render_page
    limits=get_config()['limits']
    count=page_count(djvu_path,min(limits['max_describe_pages'],limits['max_djvu_pages']))
    with tempfile.TemporaryDirectory(prefix='rag_djvu_') as temporary:
        for index in range(1, count + 1):
            yield render_page(djvu_path,index,temporary,limits['max_render_pixels']).read_bytes()


def render_file_to_images(file_path):
    kind = render_kind(file_path)
    if kind == 'image':
        if Path(file_path).stat().st_size > get_config()['limits'].get('max_image_bytes', 100*1024*1024):
            raise ValueError('Image exceeds configured size limit')
        from PIL import Image
        with Image.open(file_path) as image:
            if image.width*image.height > get_config()['limits']['max_render_pixels']:
                raise ValueError('Rendered image exceeds pixel limit')
            output = io.BytesIO(); image.convert('RGB').save(output, format='PNG')
            return [output.getvalue()]
    if kind == 'pdf': return render_pdf_to_images(file_path)
    if kind == 'office': return render_office_to_images(file_path)
    if kind == 'djvu': return render_djvu_to_images(file_path)
    return []


def encode_image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode('ascii')


def call_openai_vision(endpoint: dict[str, Any], image_b64, file_path, page_num=0, total_pages=0):
    from security import validate_endpoint
    cfg = get_config()
    base = endpoint.get('api_base', '').rstrip('/')
    validate_endpoint(base, cfg, cloud_authorized=endpoint.get('cloud_authorized', False), live_policy=True)
    key = endpoint.get('api_key', '')
    if not key:
        env_name = endpoint.get('api_key_env', '')
        key = get_env(env_name, '') if env_name else ''
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    messages = [{'role': 'user', 'content': [
        {'type': 'text', 'text': VISION_PROMPT.format(page_num=page_num)},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + image_b64}}
    ]}]
    retries = cfg.get('vision', {}).get('max_retries', 3)+1
    with requests.Session() as session:
        session.trust_env = False
        for attempt in range(retries):
            # Revalidate every attempt, disallow redirects and environment proxies.
            validate_endpoint(base, get_config(), cloud_authorized=endpoint.get('cloud_authorized', False), live_policy=True)
            interval=endpoint.get('rate_limit',0)
            if interval:time.sleep(max(0,_last_request.get(base,0)+interval-time.monotonic()))
            _last_request[base]=time.monotonic()
            try:
                response = session.post(base + '/chat/completions', headers=headers,
                    json={'model': endpoint['model'], 'messages': messages, 'max_tokens': endpoint.get('max_tokens', 2048)},
                    timeout=endpoint.get('timeout', 60), allow_redirects=False)
                if response.status_code == 200:
                    content = response.json()['choices'][0]['message']['content']
                    if not isinstance(content, str): raise ValueError('Vision response content must be text')
                    return content
                if response.status_code != 429 or attempt + 1 == retries:
                    log.warning('Vision endpoint returned HTTP %s', response.status_code)
                    return None
            except (requests.RequestException, KeyError, IndexError, ValueError):
                if attempt + 1 == retries:
                    log.warning('Vision request failed; no document or credential content logged')
                    return None
            time.sleep(min(2 ** attempt, 5))
    return None


def check_endpoint_health(endpoint):
    from security import validate_endpoint
    base = endpoint.get('api_base', '').rstrip('/')
    validate_endpoint(base, get_config(), cloud_authorized=endpoint.get('cloud_authorized', False), live_policy=True)
    with requests.Session() as session:
        session.trust_env = False
        try: return session.get(base + '/models', timeout=2, allow_redirects=False).status_code == 200
        except requests.RequestException: return False


def call_vision_with_fallback(image_b64, file_path, page_num=0, total_pages=0):
    endpoints = get_config().get('vision', {}).get('endpoints', [])
    for endpoint in endpoints:
        if not endpoint.get('enabled', True): continue
        result = call_openai_vision(endpoint, image_b64, file_path, page_num, total_pages)
        if result: return result, endpoint.get('name') or 'local_vision'
    return None, None


def _record(file_id, description, source, db_path=None, failed=False, expected_hash=None,error=None):
    from database import source_transaction,SourceVersionChanged
    try:
        with source_transaction(db_path or get_db_path(),file_id,expected_hash) as conn:
            conn.execute("""INSERT INTO rag_descriptions(file_id,description,source,embedded,retry_count,source_hash,generation_fingerprint,embedding_fingerprint,error)
                VALUES(?,?,?,0,?,?,?,NULL,?) ON CONFLICT(file_id) DO UPDATE SET
                description=excluded.description,source=excluded.source,embedded=0,error=excluded.error,
                source_hash=excluded.source_hash,generation_fingerprint=excluded.generation_fingerprint,embedding_fingerprint=NULL,
                retry_count=CASE WHEN ? THEN CASE WHEN rag_descriptions.generation_fingerprint IS excluded.generation_fingerprint
                    THEN rag_descriptions.retry_count+1 ELSE 1 END ELSE 0 END,
                created_at=CURRENT_TIMESTAMP""", (file_id, description, source, int(failed), expected_hash, generation_fingerprint(),error, int(failed)))
        return True
    except SourceVersionChanged:
        return False


def _process_file(file_id, file_path, reason, db_path=None):
    if not source_allowed(file_path):return False,'source_out_of_scope'
    from file_scanner import file_fingerprint
    with _get_db(db_path) as conn:
        current=conn.execute('SELECT file_hash,is_deleted FROM files WHERE id=?',(file_id,)).fetchone()
    if not current or current['is_deleted']:return False,'source_stale'
    expected_hash=current['file_hash']
    def failure(source,error=None):
        saved=_record(file_id,'',source,db_path,True,expected_hash=expected_hash,error=error)
        return False,source if saved else 'source_stale'
    if not Path(file_path).is_file():return failure('file_missing_failed')
    descriptions=[]
    try:
        if file_fingerprint(file_path)!=expected_hash:raise ValueError('Source changed; scan before describing')
        images=render_file_to_images(file_path)
        for number,image in enumerate(images,1):
            if not source_allowed(file_path):return False,'source_out_of_scope'
            text,endpoint=call_vision_with_fallback(encode_image_to_base64(image),file_path,number)
            if not text:return failure('vision_failed')
            descriptions.append(f'[Page {number}]\n{text}')
        if file_fingerprint(file_path)!=expected_hash:raise ValueError('Source changed during vision inference')
    except (OSError,ValueError,ImportError,subprocess.SubprocessError) as exc:
        log.warning('Unable to render/describe file_id=%s (%s)',file_id,type(exc).__name__)
        return failure('render_failed',f'{type(exc).__name__}: {exc}'[:1000])
    if not descriptions:return failure('render_failed')
    saved=_record(file_id,'\n\n'.join(descriptions),'vision_description',db_path,expected_hash=expected_hash)
    return saved,'vision_description' if saved else 'source_stale'


@configured_operation
def process_pending_descriptions(limit=None, db_path=None,stop_event=None,regenerate_legacy=False):
    """One bounded generation pass; pipeline calls the consumer after this completes."""
    from database import ensure_initialized
    ensure_initialized(db_path or get_db_path())
    endpoints = [e for e in get_config().get('vision', {}).get('endpoints', []) if e.get('enabled', True)]
    if not endpoints:
        return {'processed': 0, 'failed': 0, 'disabled': True}
    files = get_ocr_failed_files(limit, db_path, regenerate_legacy)
    result = {'processed': 0, 'failed': 0, 'disabled': False,'cancelled':False}
    for file_id, path, reason in files:
        if stop_event is not None and stop_event.is_set():result['cancelled']=True;break
        success, _ = _process_file(file_id, path, reason, db_path)
        result['processed' if success else 'failed'] += 1
        if stop_event is not None and stop_event.is_set():result['cancelled']=True;break
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config'); parser.add_argument('--limit', type=int)
    parser.add_argument('--regenerate-legacy-descriptions',action='store_true')
    args = parser.parse_args()
    if args.config:
        from config_loader import reset_config_cache, load_config
        reset_config_cache(); load_config(args.config)
    print(json.dumps(process_pending_descriptions(args.limit,regenerate_legacy=args.regenerate_legacy_descriptions)))


if __name__ == '__main__': main()
