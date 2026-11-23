#!/usr/bin/env python3
"""Reusable, job-scoped metadata extraction with owned hard-timeout workers.

Importing this module is safe from FastAPI worker threads: signals and global
job events are not installed. Each file is inspected in one owned spawn child;
a timeout terminates only that child, never unrelated application processes.
"""
from config_loader import configured_operation
import json
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from pathlib import Path
import time
from corpus_policy import CorpusPolicy, source_allowed

try:
    from PIL import Image
except ImportError:
    Image = None
try:
    from tinytag import TinyTag
except ImportError:
    TinyTag = None
try:
    import pymediainfo
except ImportError:
    pymediainfo = None
try:
    import fitz
except ImportError:
    fitz = None

from format_capabilities import IMAGE_EXTS, AUDIO_ONLY_EXTS, MEDIA_EXTS, metadata_kind


def get_ext(path):
    return Path(path).suffix.lower()


def extract_image_meta(path):
    """提取图片元数据"""
    if Image is None:
        return None
    try:
        with Image.open(path) as img:
            meta = {'type': 'image', 'width': img.width, 'height': img.height,
                    'format': img.format or '', 'mode': img.mode or ''}
            try:
                exif = img.getexif()
                if exif:
                    for tid, key in [(0x0112, 'orientation'), (0x010F, 'make'),
                                     (0x0110, 'model'), (0x9003, 'datetime_original'),
                                     (0x829A, 'exposure_time'), (0x829D, 'f_number'),
                                     (0x8827, 'iso'), (0x920A, 'focal_length')]:
                        v = exif.get(tid)
                        if v is not None:
                            meta[key] = str(v)
            except Exception:
                pass
            return meta
    except Exception:
        return None


def extract_audio_meta(path):
    """提取音频元数据"""
    if TinyTag is None:
        return None
    try:
        tag = TinyTag.get(path)
        meta = {'type': 'audio'}
        if tag.duration:
            meta['duration'] = round(tag.duration, 3)
        if tag.bitrate:
            meta['bitrate'] = int(tag.bitrate)
        if tag.samplerate:
            meta['samplerate'] = int(tag.samplerate)
        if tag.channels:
            meta['channels'] = int(tag.channels)
        if tag.artist:
            meta['artist'] = tag.artist
        if tag.title:
            meta['title'] = tag.title
        if tag.album:
            meta['album'] = tag.album
        if tag.year:
            meta['year'] = str(tag.year)
        if tag.genre:
            meta['genre'] = tag.genre
        return meta if len(meta) > 1 else None
    except Exception:
        return None


def extract_video_meta(path):
    """提取视频元数据"""
    if pymediainfo is None:
        return None
    try:
        mi = pymediainfo.MediaInfo.parse(path)
        meta = {'type': 'video'}
        if mi.general_tracks:
            g = mi.general_tracks[0]
            if g.duration:
                meta['duration'] = round(float(g.duration), 3)
            if g.format:
                meta['format'] = g.format
            if g.bit_rate:
                meta['bit_rate'] = int(g.bit_rate)
        for t in mi.video_tracks:
            v = {}
            if t.width:
                v['width'] = t.width
            if t.height:
                v['height'] = t.height
            if t.frame_rate:
                v['frame_rate'] = float(t.frame_rate)
            if t.codec_id:
                v['codec'] = t.codec_id
            if v:
                meta['video'] = v
            break
        for t in mi.audio_tracks:
            a = {}
            if t.sample_rate:
                a['sample_rate'] = int(t.sample_rate)
            if t.channels:
                a['channels'] = t.channels
            if t.codec_id:
                a['codec'] = t.codec_id
            if a:
                meta['audio'] = a
            break
        return meta if len(meta) > 1 else None
    except Exception:
        return None


def extract_pdf_meta(path):
    if fitz is None:
        return None
    try:
        with fitz.open(path) as doc:
            page_count = len(doc)
            text_pages = sum(bool(page.get_text('text', sort=True).strip()) for page in doc)
        kind = 'text_pdf' if page_count and text_pages == page_count else 'hybrid_pdf' if text_pages else 'image_pdf'
        return {'type':kind,'has_text':bool(text_pages),'page_count':page_count,
                'text_pages':text_pages,'text_ratio':round(text_pages/page_count,3) if page_count else 0,
                'format':'PDF'}
    except Exception:
        return None


def _inspect_file(args):
    fid, path, mime = args
    kind = metadata_kind(path, mime)
    if kind == 'pdf':
        meta = extract_pdf_meta(path)
        category = 'pdf_text' if meta and meta.get('type') == 'text_pdf' else 'pdf_image'
    elif kind == 'image':
        meta, category = extract_image_meta(path), 'image'
    elif kind == 'audio':
        meta, category = extract_audio_meta(path), 'media'
    elif kind == 'video':
        meta, category = extract_video_meta(path), 'media'
    else:
        return fid, None, 'skip', None
    if meta is None:
        return fid, None, 'error', 'No metadata extracted: file is corrupt/unsupported or an optional parser is unavailable'
    return fid, meta, category, None


def _worker_entry(args, sender):
    try:
        sender.send(_inspect_file(args))
    except BaseException as exc:
        sender.send((args[0], None, 'error', f'{type(exc).__name__}: {exc}'))
    finally:
        sender.close()


def _run_with_timeout(args, timeout, stop_event=None):
    if metadata_kind(args[1], args[2]) is None:
        return args[0], None, 'skip', None
    ctx = mp.get_context('spawn')
    receiver, sender = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=_worker_entry, args=(args, sender))
    try:
        worker.start()
        sender.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                return args[0], None, 'cancelled', 'Metadata job cancelled'
            if receiver.poll(min(.1, max(0, deadline-time.monotonic()))):
                try:
                    return receiver.recv()
                except EOFError:
                    return args[0], None, 'error', 'Metadata worker exited without a result'
            if not worker.is_alive():
                return args[0], None, 'error', f'Metadata worker exited with code {worker.exitcode}'
        return args[0], None, 'timeout', f'Metadata processing exceeded {timeout:g} seconds'
    finally:
        receiver.close()
        sender.close()
        if worker.pid is None:
            worker.close()
        else:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
            if not worker.is_alive():
                worker.close()


def process_file_with_timeout(args, timeout=None):
    """Compatibility wrapper returning the original (id, metadata, category)."""
    from config_loader import get_config
    timeout = timeout if timeout is not None else get_config()['metadata']['file_timeout']
    fid, meta, category, error = _run_with_timeout(args, float(timeout))
    return fid, meta if meta is not None else 'TIMEOUT' if category == 'timeout' else 'FAIL' if error else None, category


@configured_operation
def extract_metadata(*, db_path=None, file_ids=None, workers=None, timeout=None, stop_event=None):
    """Inspect pending active files and return this job's actual outcomes.

    No persistent stop flags, writer threads, global counters, or import-time
    signal handlers survive between calls. Failed work remains retryable.
    Version-conditional writes prevent stale metadata from a concurrent rescan.
    """
    from config_loader import get_config, get_db_path
    import database
    cfg = get_config().get('metadata', {})
    db_path = db_path or get_db_path()
    workers = int(workers if workers is not None else cfg.get('workers_per_disk', 4))
    timeout = float(timeout if timeout is not None else cfg.get('file_timeout', 20))
    if not 1 <= workers <= 32 or timeout <= 0:
        raise ValueError('Metadata workers must be 1..32 and timeout must be positive')
    database.ensure_initialized(db_path)
    policy=CorpusPolicy()
    with closing(database.connect(db_path)) as conn:
        policy.install(conn)
        sql = "SELECT id,path,mime_type,file_hash FROM files WHERE is_deleted=0 AND corpus_selected(path) AND (media_meta IS NULL OR media_meta='')"
        params = []
        if file_ids is not None:
            ids = list(file_ids)
            if not ids:
                return {'total':0,'done':0,'errors':0,'skipped':0,'timeout':0,'cancelled':0,'stale':0,'details':[]}
            sql += ' AND id IN (' + ','.join('?' for _ in ids) + ')'
            params.extend(ids)
        total=conn.execute('SELECT COUNT(*) FROM ('+sql+')',params).fetchone()[0]
    def pending_rows():
        last_id=0
        while True:
            with database.connect(db_path) as conn:
                policy.install(conn)
                rows=conn.execute(sql+' AND id>? ORDER BY id LIMIT ?',params+[last_id,cfg.get('max_pending',100)]).fetchall()
            if not rows:return
            for row in rows:
                last_id=row['id']
                yield tuple(row)
    result = {'total':total,'done':0,'errors':0,'skipped':0,'timeout':0,'cancelled':0,'stale':0,'details':[]}
    # ponytail: bounded threads own isolated per-file subprocesses; use a
    # recyclable persistent service only if spawn overhead dominates measured work.
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        batch = pending_rows()
        def submit_next():
            while stop_event is None or not stop_event.is_set():
                try:
                    row = next(batch)
                except StopIteration:
                    return
                if not CorpusPolicy().allows(row[1]):continue
                if metadata_kind(row[1],row[2]) is None:
                    result['skipped'] += 1
                    continue
                if not source_allowed(row[1]):continue
                task = executor.submit(_run_with_timeout,row[:3],timeout,stop_event)
                futures[task] = (row[3],row[1])
                return
        for _ in range(min(workers,total)):
            submit_next()
        while futures:
            finished = next(as_completed(futures))
            source_hash, source_path = futures.pop(finished)
            try:
                fid, meta, category, error = finished.result()
            except Exception as exc:
                result['errors'] += 1
                result['details'].append({'error':f'{type(exc).__name__}: {exc}'})
                submit_next()
                continue
            if not source_allowed(source_path):
                submit_next()
                continue
            if meta is not None:
                from file_scanner import file_fingerprint
                try:
                    unchanged = file_fingerprint(source_path) == source_hash
                except OSError:
                    unchanged = False
                if not unchanged:
                    result['stale'] += 1
                    result['details'].append({'file_id':fid,'status':'stale','error':'Source changed or became unavailable during metadata extraction; rescan first'})
                    submit_next()
                    continue
                with closing(database.connect(db_path)) as conn:
                    with conn:
                        cursor = conn.execute("UPDATE files SET media_meta=? WHERE id=? AND is_deleted=0 AND file_hash IS ?", (json.dumps(meta,ensure_ascii=False),fid,source_hash))
                        result['done' if cursor.rowcount else 'stale'] += 1
            elif category == 'skip':
                result['skipped'] += 1
            else:
                result['errors'] += 1
                if category in ('timeout','cancelled'):
                    result[category] += 1
                result['details'].append({'file_id':fid,'status':category,'error':error})
            submit_next()
    return result


if __name__ == '__main__':
    print(json.dumps(extract_metadata(),indent=2,ensure_ascii=False))
