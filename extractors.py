#!/usr/bin/env python3
"""
File type extractors for RAG pipeline - Jina v5 pure text version.

- Image files -> PaddleOCR CPU -> text -> embedding
- Image-only PDFs -> PaddleOCR CPU -> text -> embedding
- OCR quality check: garbled/too-short results are skipped
- Video files: SKIPPED (no longer processed)
- All chunks are text (no image/video embedding)

Configuration loaded from config.yaml via config_loader.
"""
from config_loader import configured_operation
import os
import sys
import re
import json
import tempfile
import logging
import shutil
import subprocess
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
from contextvars import ContextVar

from pathlib import Path
import codecs
from config_loader import get_db_path, get_ocr_api_url, get_chunking_config, get_limits_config, get_config

class ExtractionError(RuntimeError):
    """Supported content could not be extracted; do not mark successfully indexed."""


class UnsupportedFormatError(ExtractionError):
    """The current runtime deliberately does not support this format."""


from chunker_v2 import chunk_greedy_semantic


log = logging.getLogger(__name__)

# --- Authenticated local OCR service ---
# The service owns its model; bounded inline image payloads avoid shared-file
# lifetime races when the client times out or cancels.

os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

def _run_ocr_on_images(image_paths):
    if not image_paths: return []
    from security import validate_endpoint, get_api_token
    import requests
    cfg = get_config(); url = get_ocr_api_url()
    if not cfg['ocr'].get('enabled',True):raise ExtractionError('OCR is disabled in configuration')
    validate_endpoint(url, cfg, live_policy=True)
    import base64
    maximum = cfg['ocr'].get('max_batch_images', 20)
    if len(image_paths) > maximum: raise ValueError('OCR batch exceeds configured limit')
    request_limit=cfg['ocr']['max_request_bytes'];images=[];encoded_size=len(json.dumps({'image_data':[]}))
    for image in image_paths:
        path=Path(image)
        byte_limit=min(cfg['limits']['max_image_bytes'],request_limit)
        if path.stat().st_size>byte_limit:raise ExtractionError('OCR input exceeds configured byte/request limit')
        with path.open('rb') as stream:data=stream.read(byte_limit+1)
        if len(data)>byte_limit:raise ExtractionError('OCR input exceeds configured byte/request limit')
        encoded=base64.b64encode(data).decode('ascii');encoded_size+=len(encoded)+4
        if encoded_size>request_limit:raise ExtractionError('OCR batch exceeds configured request byte limit')
        images.append(encoded)
    with requests.Session() as session:
        session.trust_env = False
        try:
            response = session.post(url.rstrip('/') + '/ocr', json={'image_data': images},
                headers={'Authorization': 'Bearer ' + get_api_token()},
                timeout=cfg['ocr'].get('timeout', 120), allow_redirects=False)
            response.raise_for_status()
            results = response.json()['results']
            if not isinstance(results, list) or len(results) != len(images):
                raise ExtractionError('OCR result count/schema mismatch')
            if any(value is not None and not isinstance(value, str) for value in results):
                raise ExtractionError('OCR response contains a non-text result')
            return results
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise ExtractionError('Configured OCR service failed or is unavailable') from exc


def _ocr_image(image_path: str) -> Optional[str]:
    """Run OCR on a single image through the local service.
    
    Returns extracted text, or None if OCR fails / quality too low.
    """
    if not os.path.isfile(image_path):
        return None
    
    results = _run_ocr_on_images([image_path])
    
    if not results:
        return None
    return results[0]


def _ocr_images_batch(image_paths: List[str]) -> List[Optional[str]]:
    """Run OCR on multiple images through bounded local requests.
    
    Each HTTP request respects image-count and encoded-byte budgets.
    Returns list of extracted texts (None for failed/too-low-quality).
    """
    if not image_paths:
        return []

    all_results = []
    batch_size = get_config()['ocr'].get('max_batch_images', 20)
    total_batches = (len(image_paths) + batch_size - 1) // batch_size
    
    for batch_idx in range(total_batches):
        batch_start = batch_idx * batch_size
        batch_end = min(batch_start + batch_size, len(image_paths))
        batch_paths = image_paths[batch_start:batch_end]
        
        log.info(f"OCR batch {batch_idx+1}/{total_batches}: {len(batch_paths)} images")
        results = _run_ocr_on_images(batch_paths)
        all_results.extend(results)
    
    return all_results


def _is_ocr_garbage(text: str, min_chars: int = 10) -> bool:
    """Check if OCR result is garbled or too short to be useful.

    Criteria:
    - Too short (< min_chars)
    - High ratio of non-alphanumeric characters (garbage/encoding errors)
    - Repetitive characters (OCR artifacts)
    - No meaningful words detected

    Returns True if text should be skipped (is garbage).
    """
    if not text or len(text.strip()) < min_chars:
        return True

    text = text.strip()

    # Count meaningful characters
    alnum_count = sum(1 for c in text if c.isalnum())
    total_count = len(text.replace('\n', '').replace(' ', ''))

    if total_count == 0:
        return True

    alnum_ratio = alnum_count / total_count

    # If less than 30% alphanumeric, likely garbage
    if alnum_ratio < 0.3:
        return True

    # Check for repetitive OCR artifacts (same char repeated 10+ times)
    # Allow legitimate patterns like separators, table fillers
    # 用正则优化，避免 O(n) 循环
    if re.search(r'([a-zA-Z\u4e00-\u9fff])\1{9,}', text):
        # 中文/字母重复10次以上才算垃圾(允许分隔线如 '------')
        return True

    # Check for excessive unique characters (random byte noise)
    unique_chars = len(set(text.replace('\n', '').replace(' ', '')))
    if total_count > 50 and unique_chars / total_count > 0.90:
        # Almost every char is unique - likely noise
        return True

    return False


# --- whisper.cpp subprocess (faster fallback if available) ---
_WHISPER_CPP_PATH = None

def _init_whisper_cpp():
    """Find whisper-cli binary. Returns path or None."""
    configured=get_config().get('audio',{}).get('cpp_executable','')
    if configured:return configured if Path(configured).is_file() else None
    global _WHISPER_CPP_PATH
    if _WHISPER_CPP_PATH is not None:
        return _WHISPER_CPP_PATH
    for candidate in [
        shutil.which('whisper-cli'),
        shutil.which('main'),
        os.path.expanduser('~/whisper.cpp/build/bin/main'),
        os.path.expanduser('~/whisper.cpp/build/bin/whisper-cli'),
    ]:
        if candidate and os.path.isfile(candidate):
            _WHISPER_CPP_PATH = candidate
            return candidate
    _WHISPER_CPP_PATH = False
    return None


def _transcribe_with_whisper_cpp(audio_path: str) -> Optional[Tuple[str, str]]:
    """Transcribe audio using whisper.cpp subprocess."""
    settings=get_config().get('audio',{})
    explicit=bool(settings.get('cpp_executable') or settings.get('cpp_model_path'))
    cpp_path = _init_whisper_cpp()
    if not cpp_path:
        if explicit:raise ExtractionError('Configured whisper.cpp executable is unavailable')
        return None
    model_path = settings.get('cpp_model_path') or os.path.expanduser("~/models/ggml-small.bin")
    if not explicit and not os.path.isfile(model_path):
        model_path = os.path.expanduser("~/models/ggml-small.en.bin")
    if not os.path.isfile(model_path):
        if explicit:raise ExtractionError('Configured whisper.cpp model is unavailable')
        return None
    try:
        with tempfile.TemporaryDirectory(prefix='rag_whisper_') as temporary:
            output_base = str(Path(temporary) / 'transcript')
            r = subprocess.run(
                [cpp_path, '-m', model_path, '-f', audio_path, '-otxt', '-of', output_base, '-l', 'auto','-t',str(settings.get('cpu_threads',2)),'-ng','-sns'],
                capture_output=True, text=True, timeout=get_config()['audio']['timeout']
            )
            if r.returncode == 0:
                txt_path = output_base + '.txt'
                if os.path.isfile(txt_path):
                    with open(txt_path, 'r', encoding='utf-8', errors='replace') as f:
                        text = f.read().strip()
                    if text:
                        return (text, 'unknown')
            if explicit:raise ExtractionError(f'Configured whisper.cpp failed or produced no text (exit {r.returncode})')
    except (subprocess.TimeoutExpired, OSError) as e:
        if explicit:raise ExtractionError('Configured whisper.cpp failed or timed out') from e
        log.debug(f"whisper.cpp failed for {audio_path}: {e}")
    return None


# --- Data Types ---
@dataclass
class ChunkData:
    """A single chunk ready for embedding.
    
    Jina v5 pipeline: all chunks are text (OCR/Whisper converts media to text).
    """
    file_id: int
    chunk_index: int
    text: str
    char_count: int = 0
    meta: dict = field(default_factory=dict)  # {'source': 'pdf_ocr', 'ocr_pages': 5, 'language': 'zh'}


@dataclass
class ExtractionOutcome:
    """Chunks and diagnostics only; no extractor owns database state."""
    chunks: List[ChunkData] = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    error: Optional[ExtractionError] = None
    recovery_eligible: bool = False

    @property
    def status(self):
        if isinstance(self.error,UnsupportedFormatError):return 'unsupported'
        return 'error' if self.error else 'done'


@configured_operation
def extract_outcome(file_id,file_path,ext,file_type,media_meta=None,file_size=0):
    diagnostics=[]
    token=_extraction_diagnostics.set(diagnostics)
    try:
        try:
            chunks=_extract_content(file_id,file_path,ext,file_type,media_meta,file_size)
            return ExtractionOutcome(chunks=chunks,diagnostics=diagnostics)
        except Exception as exc:
            error=exc if isinstance(exc,ExtractionError) else ExtractionError(f'{type(exc).__name__}: {exc}')
            recoverable=bool(diagnostics) or file_type in ('image','pdf_image','pdf_text','pdf_hybrid','office','djvu')
            return ExtractionOutcome(diagnostics=diagnostics,error=error,recovery_eligible=recoverable and not isinstance(error,UnsupportedFormatError))
    finally:
        _extraction_diagnostics.reset(token)


@configured_operation
def extract(file_id,file_path,ext,file_type,media_meta=None,file_size=0):
    """Compatible list/exception adapter around the structured extraction outcome."""
    outcome=extract_outcome(file_id,file_path,ext,file_type,media_meta,file_size)
    if outcome.error is not None:raise outcome.error
    return outcome.chunks


# Public routing names remain import-compatible; policy lives in one light module.
from format_capabilities import (classify_file, FORMATS, TEXT_EXTS, CODE_EXTS, CONFIG_EXTS,
    PDF_EXTS, IMAGE_EXTS, VIDEO_EXTS, AUDIO_EXTS, BINARY_EXTS, EBOOK_EXTS,
    DJVU_EXTS, OFFICE_EXTS, OFFICE_INPUT_FILTERS, ARCHIVE_EXTS)


# File types that should use lower OCR threshold (scanned images, screenshots, logos, business cards)
LOW_OCR_THRESHOLD_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.ico',
                          '.pdf'}  # Also lower threshold for PDFs (charts, forms, scanned docs)


_extraction_diagnostics = ContextVar('extraction_diagnostics',default=None)


def _mark_ocr_insufficient(file_id: int, path: str, reason: str):
    """Record an operation-local diagnostic; persistence belongs to the caller."""
    diagnostics=_extraction_diagnostics.get()
    if diagnostics is not None:
        diagnostics.append({'kind':'ocr_insufficient','reason':reason})


def _extract_unbounded(file_id: int, file_path: str, ext: str, file_type: str,
            media_meta: Optional[str] = None, file_size: int = 0) -> List[ChunkData]:
    """Main extraction dispatcher."""
    if not os.path.exists(file_path):
        raise ExtractionError('Source file is unavailable; rescan to reconcile deletion')

    # Pre-filter: Skip tiny images/audio (save GPU resources)
    if file_type in ('image', 'audio') and file_size < get_limits_config()['min_media_bytes']:
        return []

    # Pre-filter: Skip huge images (avoid OCR timeout/GPU memory)
    # 100MB threshold: larger images cause PaddleOCR to timeout or crash
    if file_type == 'image' and file_size > get_limits_config()['max_image_bytes']:
        log.debug(f"Image too large ({file_size//1024//1024}MB > {get_limits_config()['max_image_bytes']//1024//1024}MB): {file_path}")
        return []

    # Skip video files - not processed in Jina v5 pipeline
    if file_type == 'video':
        return []

    try:
        if file_type in ('text', 'code', 'config'):
            return _extract_text(file_id, file_path, ext, media_meta, file_type=file_type)
        elif file_type in ('pdf_text', 'pdf_hybrid'):
            return _extract_pdf_text(file_id, file_path, media_meta)
        elif file_type == 'pdf_image':
            return _extract_pdf_image_ocr(file_id, file_path, media_meta)
        elif file_type == 'image':
            return _extract_image_ocr(file_id, file_path, media_meta)
        elif file_type == 'djvu':
            return _extract_djvu(file_id, file_path, media_meta)
        elif file_type == 'video':
            return []  # Skip videos entirely
        elif file_type == 'audio':
            return _extract_audio(file_id, file_path, file_size, media_meta)
        elif file_type == 'ebook':
            return _extract_ebook(file_id, file_path, ext, media_meta)
        elif file_type == 'office':
            return _extract_office(file_id, file_path, ext, media_meta)
        elif file_type == 'archive':
            return _extract_archive(file_id, file_path, media_meta)
        elif file_type == 'binary':
            return []  # Skip binary files (already filtered by classify_file)
        else:
            return _extract_text_fallback(file_id, file_path, media_meta)
    except Exception as e:
        raise ExtractionError(f"Extraction failed for {Path(file_path).name}: {e}") from e


def _extract_content(file_id, file_path, ext, file_type, media_meta=None, file_size=0):
    """Extract content and enforce the hard chunk budget after all context prefixes."""
    if not Path(file_path).is_file(): raise ExtractionError('Source file is unavailable')
    file_type = classify_file(ext, media_meta, file_path)
    if file_type in ('video', 'binary', 'unknown'):
        raise UnsupportedFormatError(f'Content extraction is not supported for {file_type}: {ext}')
    if file_type == 'office' and not FORMATS[ext.lower()]['extract']:
        raise UnsupportedFormatError('OpenDocument extraction is not supported; convert to DOCX/XLSX/PPTX')
    limits=get_limits_config()
    maximum=get_limits_config()['max_image_bytes'] if file_type=='image' else get_limits_config()['max_text_file_bytes']
    if Path(file_path).stat().st_size>maximum:raise ExtractionError('Source exceeds configured byte limit')
    if ext.lower() in ('.docx','.pptx','.xlsx','.epub'):
        import zipfile
        from pathlib import PurePosixPath
        with zipfile.ZipFile(file_path) as archive:
            entries=archive.infolist()
            if sum(entry.file_size for entry in entries)>limits['max_archive_uncompressed_bytes']:
                raise ExtractionError('Archive exceeds configured uncompressed byte limit')
            for entry in entries:
                name=PurePosixPath(entry.filename.replace('\\','/'))
                if name.is_absolute() or '..' in name.parts or ':' in entry.filename:
                    raise ExtractionError('Archive member has an unsafe path')
    if file_type == 'image' and Path(file_path).stat().st_size > get_limits_config()['max_image_bytes']:
        raise ExtractionError('Image exceeds configured byte limit')
    chunks = _extract_unbounded(file_id, file_path, ext, file_type, media_meta, file_size)
    if sum(len(chunk.text) for chunk in chunks)>limits['max_extracted_chars']:
        raise ExtractionError('Extracted text exceeds configured character limit; split the document')
    result = []
    maximum = get_chunking_config()['max_chars']
    for chunk in chunks:
        for content in chunk_greedy_semantic(chunk.text, ext=ext, max_chars=maximum):
            meta = dict(chunk.meta); meta.setdefault('source', file_type)
            meta['original_chunk_index'] = chunk.chunk_index
            result.append(ChunkData(file_id, len(result), content, len(content), meta))
    if not result:
        raise ExtractionError('No content extracted; inspect missing dependency, OCR or unsupported content')
    return result


# ============================================================
# Individual Extractors
# ============================================================

def _build_context_text(path: str, media_meta: Optional[str] = None, short: bool = False) -> str:
    """Build context text: path + filename + metadata."""
    parts = []

    if short:
        path_parts = path.rsplit('/', 3)
        short_path = '/' + '/'.join(path_parts[-3:]) if len(path_parts) > 3 else path
        parts.append(f"File: ...{short_path}")
    else:
        parts.append(f"File: {path}")

    if media_meta:
        try:
            meta = json.loads(media_meta) if isinstance(media_meta, str) else media_meta
            if isinstance(meta, dict):
                meta_lines = []
                for k, v in meta.items():
                    if isinstance(v, dict):
                        for k2, v2 in v.items():
                            meta_lines.append(f"  {k}.{k2}: {v2}")
                    elif v is not None:
                        meta_lines.append(f"  {k}: {v}")
                if meta_lines:
                    parts.append("Metadata:\n" + "\n".join(meta_lines))
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    return "\n".join(parts)


def _read_file(path: str, max_chars: int = 0):
    """Decode supported text strictly, including BOM-less UTF-16."""
    limit = get_limits_config()['max_text_file_bytes']
    if os.path.getsize(path) > limit:
        raise ExtractionError(f'Text exceeds configured {limit}-byte limit')
    data = Path(path).read_bytes()
    if not data: return ''
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings = ['utf-32']
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ['utf-16']
    elif data.startswith(codecs.BOM_UTF8):
        encodings = ['utf-8-sig']
    elif b'\x00' in data:
        even = data[0::2].count(0); odd = data[1::2].count(0)
        if odd > even and odd >= max(1, len(data) // 100): encodings = ['utf-16-le']
        elif even > odd and even >= max(1, len(data) // 100): encodings = ['utf-16-be']
        else: return None
    else:
        encodings = ['utf-8', 'gb18030', 'cp1252']
    for encoding in encodings:
        try:
            text = data.decode(encoding, errors='strict')
            if '\x00' in text or any(ord(c) < 32 and c not in '\n\r\t\f' for c in text): continue
            return text[:max_chars] if max_chars > 0 else text
        except UnicodeError: continue
    return None



def _extract_text(file_id, path, ext, media_meta=None, file_type='text'):
    text = _read_file(path)
    if not text or not text.strip():
        raise ExtractionError('Text is empty, binary or uses an unsupported encoding')
    context = _build_context_text(path, media_meta)
    return [ChunkData(file_id, i, content, len(content), {'source': file_type})
            for i, content in enumerate(chunk_greedy_semantic(context + '\n\n' + text, ext=ext))]



def _extract_pdf_text(file_id, path, media_meta=None):
    return _extract_pdf_pages(file_id, path, media_meta)



def _extract_pdf_image_ocr(file_id, path, media_meta=None):
    return _extract_pdf_pages(file_id, path, media_meta)


def _extract_pdf_pages(file_id, path, media_meta=None):
    """Preserve native text and OCR raster-bearing pages when OCR is enabled."""
    try:
        import fitz
    except ImportError as exc:
        raise ExtractionError('PDF extraction requires the optional pymupdf package') from exc
    result, missed_pages = [], []
    max_pages = get_limits_config().get('max_pdf_pages', get_limits_config().get('max_describe_pages', 500))
    with fitz.open(path) as doc:
        if len(doc) > max_pages:
            raise ExtractionError(f'PDF has {len(doc)} pages; configured limit is {max_pages}')
        for page_number, page in enumerate(doc, 1):
            text = page.get_text('text').strip()
            source = 'pdf_text'
            has_raster = bool(page.get_image_info())
            ocr_enabled = get_config()['ocr'].get('enabled', True)
            if not text or (has_raster and ocr_enabled):
                if page.rect.width*page.rect.height*(150/72)**2>get_limits_config()['max_render_pixels']:
                    raise ExtractionError('Rendered PDF page exceeds pixel limit')
                with tempfile.TemporaryDirectory(prefix='rag_pdf_') as temporary:
                    image = os.path.join(temporary, 'page.png')
                    page.get_pixmap(dpi=150, alpha=False).save(image)
                    recognized = (_ocr_image(image) or '').strip()
                if text:
                    # Keep precise native characters even when OCR misses them.
                    # Full-page OCR is bounded by the same page/pixel limits as scans.
                    if recognized and ' '.join(recognized.split()) != ' '.join(text.split()):
                        text += '\n\n[Page image OCR]\n' + recognized
                    source = 'pdf_mixed'
                else:
                    text = recognized
                    source = 'pdf_ocr'
            if not text.strip():
                missed_pages.append(page_number)
                continue
            full_text = _build_context_text(path, media_meta, short=True) + f'\n\n[Page {page_number}]\n' + text
            for content in chunk_greedy_semantic(full_text, '.txt'):
                result.append(ChunkData(file_id, len(result), content, len(content),
                                        {'source': source, 'pages': [page_number], 'page': page_number,
                                         'raster_ocr_skipped': has_raster and not ocr_enabled}))
    if missed_pages:
        _mark_ocr_insufficient(file_id, path, 'pdf_missing_pages_' + ','.join(map(str, missed_pages)))
        raise ExtractionError('PDF has pages without extracted content: '+','.join(map(str,missed_pages)))
    if not result:
        raise ExtractionError('No PDF page text or OCR content available; visual fallback is pending')
    return result



def _extract_djvu(file_id: int, path: str, media_meta: Optional[str]) -> List[ChunkData]:
    """Extract every native page with exact provenance or reject the document."""
    from djvu_reader import page_count,render_page
    count=page_count(path,get_limits_config()['max_djvu_pages'])
    result=[];missed=[]
    with tempfile.TemporaryDirectory(prefix='rag_djvu_') as temporary:
        for number in range(1,count+1):
            response=subprocess.run(['djvutxt',f'-page={number}',path],capture_output=True,text=True,encoding='utf8',timeout=30)
            text=response.stdout.strip() if response.returncode==0 else ''
            source='djvu_text'
            if not text or _is_ocr_garbage(text,min_chars=1):
                source='djvu_ocr'
                image=render_page(path,number,temporary,get_limits_config()['max_render_pixels'])
                text=_ocr_image(str(image)) or ''
            if not text.strip():
                missed.append(number);continue
            content=_build_context_text(path,media_meta,short=True)+f'\n\n[Page {number}]\n'+text
            for chunk in chunk_greedy_semantic(content,'.txt'):
                result.append(ChunkData(file_id,len(result),chunk,len(chunk),{'source':source,'page':number,'pages':[number]}))
    if missed:
        _mark_ocr_insufficient(file_id,path,'djvu_missing_pages_'+','.join(map(str,missed)))
        raise ExtractionError('DjVu has pages without extracted content: '+','.join(map(str,missed)))
    return result



def _extract_image_ocr(file_id: int, path: str, media_meta: str = None) -> List[ChunkData]:
    """Image: PaddleOCR CPU -> text chunks.

    Filter: Use lower threshold for images/screenshots/logos. If insufficient,
    mark in DB as OCR-insufficient and skip (no VLM fallback).
    """
    ext = os.path.splitext(path)[1].lower()
    threshold = get_chunking_config()['min_text_chars_low'] if ext in LOW_OCR_THRESHOLD_EXTS else get_chunking_config()['min_text_chars']

    ocr_text = _ocr_image(path)

    if not ocr_text:
        log.debug(f"Image OCR empty: {path}")
        _mark_ocr_insufficient(file_id, path, 'ocr_empty')
        return []

    # Filter: insufficient text
    if len(ocr_text.strip()) < threshold:
        log.debug(f"Image OCR too short ({len(ocr_text.strip())} chars < {threshold}): {path}")
        _mark_ocr_insufficient(file_id, path, f'insufficient_text_{len(ocr_text.strip())}chars')
        return []

    if _is_ocr_garbage(ocr_text.strip(), min_chars=threshold):
        log.debug(f"Image OCR garbled/low quality: {path}")
        _mark_ocr_insufficient(file_id, path, 'garbled_ocr')
        return []

    # OCR produced useful text -> chunk it
    ctx = _build_context_text(path, media_meta, short=True)
    full_text = f"{ctx}\n\nOCR extracted text:\n\n{ocr_text}"

    chunks = chunk_greedy_semantic(full_text, ext='.txt')


    return [ChunkData(file_id=file_id, chunk_index=i, text=c, char_count=len(c)) for i, c in enumerate(chunks)]


def _get_audio_duration(path: str) -> float:
    """Get audio duration in seconds using ffprobe."""
    try:
        import subprocess
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout.strip():
            import math
            duration=float(r.stdout.strip())
            if math.isfinite(duration) and duration>0:return duration
    except (OSError,subprocess.SubprocessError,ValueError) as exc:
        raise ExtractionError('Unable to verify audio duration; ffprobe must be available and input decodable') from exc
    raise ExtractionError('Unable to verify a positive finite audio duration')

def _extract_audio(file_id, path, file_size=0, media_meta=None):
    """Transcribe in one owned portable subprocess, without daemon/fork nesting."""
    settings = get_config().get('audio', {})
    if _get_audio_duration(path) > settings.get('max_duration', 600):
        raise ExtractionError('Audio exceeds configured transcription duration limit')
    transcript = _transcribe_with_whisper_cpp(path)
    engine = 'whisper.cpp'
    if transcript is None:
        model_path = settings.get('model_path', '')
        if not model_path or not Path(model_path).is_dir():
            raise ExtractionError('Audio requires an explicit local faster-whisper model_path or whisper.cpp model')
        python=settings.get('python_executable') or sys.executable
        if not Path(python).is_file():raise ExtractionError('Configured audio Python interpreter is unavailable')
        code = """import json,sys
from faster_whisper import WhisperModel
model=WhisperModel(sys.argv[2],device='cpu',compute_type='int8',local_files_only=True,cpu_threads=int(sys.argv[3]))
segments,info=model.transcribe(sys.argv[1],beam_size=1)
print(json.dumps({'text':' '.join(s.text for s in segments),'language':info.language}))
"""
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        try:
            process = subprocess.run([python, '-c', code, path, model_path,str(settings.get('cpu_threads',2))],
                                     capture_output=True, text=True, encoding='utf-8',
                                     timeout=settings.get('timeout', 300), check=True, env=env)
            data = json.loads(process.stdout.strip().splitlines()[-1])
            transcript = (data['text'], data.get('language', 'unknown'))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise ExtractionError('Local audio transcription failed or timed out') from exc
        engine = 'faster-whisper'
    text, language = transcript
    if not text or not text.strip(): raise ExtractionError('Audio transcription produced no text')
    text = _build_context_text(path, media_meta) + '\n\n' + text
    return [ChunkData(file_id, i, content, len(content),
                      {'source': 'audio', 'language': language, 'engine': engine})
            for i, content in enumerate(chunk_greedy_semantic(text))]



def _extract_ebook(file_id: int, path: str, ext: str, media_meta: str = None) -> List[ChunkData]:
    # Every parser/OCR branch owns the same directory, including exceptions.
    with tempfile.TemporaryDirectory(prefix=f'rag_ebook_{file_id}_') as temporary:
        return _extract_ebook_in_directory(file_id,path,ext,media_meta,temporary)


def _extract_ebook_in_directory(file_id, path, ext, media_meta, tmp_dir):
    """Ebook: extract text + embedded images OCR.
    
    Strategy:
    1. Extract text from ebook format
    2. If text is sufficient -> chunk and return
    3. If text is insufficient -> extract embedded images and OCR
    4. If OCR yields text -> chunk and return
    5. If all fails -> mark as insufficient (same as PDF)
    """
    text = ""
    image_paths = []

    try:
        if ext.lower() == '.epub':
            text, image_paths = _extract_epub_content(path, tmp_dir)

        elif ext.lower() == '.mobi':
            text, image_paths = _extract_mobi_content(path, tmp_dir)
            
        elif ext.lower() == '.azw3':
            text, image_paths = _extract_azw3_content(path, tmp_dir)
            
        elif ext.lower() == '.fb2':
            text, image_paths = _extract_fb2_content(path, tmp_dir)
            
        else:
            text = _read_file(path) or ""

    except Exception as e:
        raise ExtractionError(f'Ebook parser failed: {type(e).__name__}: {e}') from e

    # P1: Check if text extraction was sufficient
    if len(text.strip()) >= get_chunking_config()['min_text_chars'] and not _is_ocr_garbage(text.strip(), min_chars=get_chunking_config()['min_text_chars']):
        chunks = chunk_greedy_semantic(text, ext='.txt')
        result = []
        short_ctx = _build_context_text(path, media_meta, short=True)
        for i, chunk_text in enumerate(chunks):
            full_chunk = f"{short_ctx}\n\n{chunk_text}"
            result.append(ChunkData(
                file_id=file_id, chunk_index=i,
                text=full_chunk, char_count=len(full_chunk),
                meta={'source': 'ebook'}
            ))
        return result

    # P2: Text insufficient, try OCR on embedded images
    if image_paths:
        log.info(f"Ebook text insufficient ({len(text.strip())} chars), OCRing {len(image_paths)} images: {path}")
        
        # Batch OCR (100 images per batch)
        BATCH_SIZE = 100
        ocr_results = []
        for batch_start in range(0, len(image_paths), BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, len(image_paths))
            batch_paths = image_paths[batch_start:batch_end]
            log.info(f"Ebook OCR pages {batch_start+1}-{batch_end}/{len(image_paths)}")
            batch_results = _ocr_images_batch(batch_paths)
            ocr_results.extend(batch_results)
        
        # Collect OCR text
        all_ocr_text = []
        for i, ocr_text in enumerate(ocr_results):
            if ocr_text:
                all_ocr_text.append(f"[Page {i+1}]\n{ocr_text}")
        
        combined_ocr = "\n\n".join(all_ocr_text)
        
        # Clean up temp images
        for tmp_path in image_paths:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        
        # Check OCR results
        if not combined_ocr.strip():
            _mark_ocr_insufficient(file_id, path, 'ebook_ocr_empty')
            return []
        
        if len(combined_ocr.strip()) < get_chunking_config()['min_text_chars']:
            _mark_ocr_insufficient(file_id, path, f'ebook_ocr_short_{len(combined_ocr.strip())}chars')
            return []
        
        if _is_ocr_garbage(combined_ocr.strip(), min_chars=get_chunking_config()['min_text_chars']):
            _mark_ocr_insufficient(file_id, path, 'ebook_ocr_garbled')
            return []
        
        # OCR succeeded, chunk the text
        chunks = chunk_greedy_semantic(combined_ocr, ext='.txt')
        result = []
        short_ctx = _build_context_text(path, media_meta, short=True)
        for i, chunk_text in enumerate(chunks):
            full_chunk = f"{short_ctx}\n\n{chunk_text}"
            result.append(ChunkData(
                file_id=file_id, chunk_index=i,
                text=full_chunk, char_count=len(full_chunk),
                meta={'source': 'ebook_ocr'}
            ))
        return result
    
    # P3: No images to OCR, text was insufficient
    
    if len(text.strip()) < get_chunking_config()['min_text_chars']:
        _mark_ocr_insufficient(file_id, path, f'ebook_short_{len(text.strip())}chars')
        return []
    
    if _is_ocr_garbage(text.strip(), min_chars=get_chunking_config()['min_text_chars']):
        _mark_ocr_insufficient(file_id, path, 'ebook_garbled')
        return []
    
    # Should not reach here, but just in case
    return []

def _extract_epub_content(epub_path: str, tmp_dir: str) -> tuple:
    """Extract text and images from EPUB.
    
    Returns: (text, image_paths)
    """
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup
    import uuid
    
    text = ""
    image_paths = []
    epub_tmp = None
    
    try:
        # ebooklib 不支持非 ASCII 路径
        epub_tmp = os.path.join(tmp_dir, f'temp_{uuid.uuid4().hex}.epub')
        shutil.copyfile(epub_path, epub_tmp)
        
        book = epub.read_epub(epub_tmp)
        
        # The package's spine owns reading order. Navigation/other archive
        # items must not inflate text sufficiency or replace missing chapters.
        for idref,linear in book.spine:
            if linear=='no':continue
            item=book.get_item_with_id(idref)
            if item is None or item.get_type()!=ebooklib.ITEM_DOCUMENT:
                raise ExtractionError('EPUB spine references a missing document')
            if isinstance(item,epub.EpubNav):continue
            content_bytes = item.get_content().decode('utf-8', errors='replace')
            if not content_bytes.strip():
                continue
            
            # Remove style/script but keep img tags for counting
            content_bytes = re.sub(r'<style[^>]*>.*?</style>', '', content_bytes, flags=re.DOTALL | re.IGNORECASE)
            content_bytes = re.sub(r'<script[^>]*>.*?</script>', '', content_bytes, flags=re.DOTALL | re.IGNORECASE)
            content_bytes = re.sub(r'<link[^>]*/?>', '', content_bytes, flags=re.IGNORECASE)
            
            soup = BeautifulSoup(content_bytes, 'html.parser')
            
            # Count images in this document
            img_count = len(soup.find_all('img'))
            
            # Extract text
            for tag in soup.find_all(['h1', 'h2', 'h3', 'h4']):
                tag.replace_with(f"\n\n{tag.get_text()}\n\n")
            for tag in soup.find_all('li'):
                tag.replace_with(f"\n- {tag.get_text()}")
            clean = soup.get_text(separator='\n')
            clean = re.sub(r'\n{3,}', '\n\n', clean)
            text += clean + "\n"
        
        # If text is insufficient, extract images for OCR
        if len(text.strip()) < get_chunking_config()['min_text_chars']:
            # Extract all images
            for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
                img_data = item.get_content()
                img_name = item.get_name()
                # Save to temp dir
                img_path = os.path.join(tmp_dir, os.path.basename(img_name))
                with open(img_path, 'wb') as f:
                    f.write(img_data)
                image_paths.append(img_path)
    
    except Exception as e:
        raise ExtractionError(f'EPUB parser failed: {type(e).__name__}: {e}') from e
    
    return text, image_paths


def _extract_mobi_content(mobi_path: str, tmp_dir: str) -> tuple:
    """Extract text and images from MOBI.
    
    MOBI structure:
    - mobi7/book.html: fallback HTML (may be image-heavy)
    - mobi8/: KF8 format with part*.xhtml and images
    
    Returns: (text, image_paths)
    """
    import mobi
    from bs4 import BeautifulSoup
    
    text = ""
    image_paths = []
    tempdir = None
    
    try:
        tempdir, filepath = mobi.extract(mobi_path)
        
        # P1: Try mobi8 (KF8) first - has better structure
        mobi8_dir = os.path.join(tempdir, 'mobi8')
        if os.path.isdir(mobi8_dir):
            # Extract text from all xhtml files
            xhtml_files = []
            for root, dirs, files in os.walk(mobi8_dir):
                for f in files:
                    if f.endswith(('.xhtml', '.html', '.htm')):
                        xhtml_files.append(os.path.join(root, f))
            
            # Sort by name to maintain order
            xhtml_files.sort()
            
            for xhtml_path in xhtml_files:
                try:
                    with open(xhtml_path, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    soup = BeautifulSoup(content, 'html.parser')
                    page_text = soup.get_text(separator='\n').strip()
                    if page_text:
                        text += page_text + "\n"
                except Exception:
                    pass
        
        # P2: Fallback to mobi7 if mobi8 text is insufficient
        if len(text.strip()) < get_chunking_config()['min_text_chars']:
            mobi7_dir = os.path.join(tempdir, 'mobi7')
            if os.path.isdir(mobi7_dir):
                for f in os.listdir(mobi7_dir):
                    if f.endswith(('.html', '.htm')):
                        html_path = os.path.join(mobi7_dir, f)
                        try:
                            with open(html_path, 'r', encoding='utf-8', errors='replace') as fh:
                                soup = BeautifulSoup(fh.read(), 'html.parser')
                                text += soup.get_text(separator='\n') + "\n"
                        except Exception:
                            pass
        
        # P3: If text still insufficient, extract images for OCR
        if len(text.strip()) < get_chunking_config()['min_text_chars']:
            # Find all images in extracted directory
            for root, dirs, files in os.walk(tempdir):
                for f in files:
                    if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                        src_path = os.path.join(root, f)
                        # Copy to tmp_dir to avoid cleanup issues
                        dst_path = os.path.join(tmp_dir, f)
                        try:
                            shutil.copy2(src_path, dst_path)
                            image_paths.append(dst_path)
                        except Exception:
                            pass
            
            # Sort images by name
            image_paths.sort()
    
    except Exception as e:
        raise ExtractionError(f'MOBI parser failed: {type(e).__name__}: {e}') from e
    
    finally:
        # Clean up mobi extract directory
        if tempdir and os.path.isdir(tempdir):
            shutil.rmtree(tempdir, ignore_errors=True)
    
    return text, image_paths


def _extract_azw3_content(azw3_path: str, tmp_dir: str) -> tuple:
    """Extract text and images from AZW3.
    
    Uses ebook-convert if available, otherwise falls back to mobi extraction.
    
    Returns: (text, image_paths)
    """
    import subprocess
    
    text = ""
    image_paths = []
    
    # Try ebook-convert first
    out_txt = os.path.join(tmp_dir, 'converted.txt')
    try:
        r = subprocess.run(['ebook-convert', azw3_path, out_txt],
                          capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out_txt):
            with open(out_txt, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    
    # Fallback: try mobi extraction (azw3 is similar to mobi)
    if len(text.strip()) < get_chunking_config()['min_text_chars']:
        try:
            import mobi
            tempdir, filepath = mobi.extract(azw3_path)
            
            # Extract text from HTML files
            for root, dirs, files in os.walk(tempdir):
                for f in files:
                    if f.endswith(('.html', '.htm', '.xhtml')):
                        html_path = os.path.join(root, f)
                        try:
                            from bs4 import BeautifulSoup
                            with open(html_path, 'r', encoding='utf-8', errors='replace') as fh:
                                soup = BeautifulSoup(fh.read(), 'html.parser')
                                text += soup.get_text(separator='\n') + "\n"
                        except Exception:
                            pass
            
            # If text still insufficient, extract images
            if len(text.strip()) < get_chunking_config()['min_text_chars']:
                for root, dirs, files in os.walk(tempdir):
                    for f in files:
                        if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                            src_path = os.path.join(root, f)
                            dst_path = os.path.join(tmp_dir, f)
                            try:
                                shutil.copy2(src_path, dst_path)
                                image_paths.append(dst_path)
                            except Exception:
                                pass
                image_paths.sort()
            
            shutil.rmtree(tempdir, ignore_errors=True)
        except Exception as e:
            log.warning(f"AZW3 extraction error: {e}")
    
    return text, image_paths


def _extract_fb2_content(fb2_path: str, tmp_dir: str) -> tuple:
    """Read well-formed FictionBook XML; never treat parser errors as text."""
    import xml.etree.ElementTree as ET
    import base64
    root=ET.fromstring(Path(fb2_path).read_bytes())
    local=lambda tag:tag.rsplit('}',1)[-1]
    if local(root.tag)!='FictionBook':raise ExtractionError('Invalid FictionBook XML root')
    body=next((node for node in root if local(node.tag)=='body'),None)
    if body is None:raise ExtractionError('FictionBook has no body')
    text='\n'.join(body.itertext());images=[]
    if len(text.strip())<get_chunking_config()['min_text_chars']:
        binaries={node.get('id'):node for node in root if local(node.tag)=='binary'}
        for number,node in enumerate(item for item in body.iter() if local(item.tag)=='image'):
            reference=node.get('{http://www.w3.org/1999/xlink}href') or node.get('href') or ''
            binary=binaries.get(reference.lstrip('#'))
            if binary is None:raise ExtractionError('FictionBook image references a missing binary')
            payload=''.join(''.join(binary.itertext()).split())
            data=base64.b64decode(payload,validate=True)
            if len(data)>get_limits_config()['max_image_bytes']:raise ExtractionError('FictionBook image exceeds byte limit')
            extension='.jpg' if 'jpeg' in binary.get('content-type','') else '.png'
            path=Path(tmp_dir)/f'fb2_img_{number}{extension}';path.write_bytes(data);images.append(str(path))
    return text,images



def _ocr_embedded_office_images(zip_path: str, media_prefix: str, results: list):
    """P2: Extract embedded images from Office file and OCR them.
    
    Args:
        zip_path: Path to the Office file (docx/pptx are zip files)
        media_prefix: Subdirectory prefix (e.g., 'word/media/', 'ppt/media/')
        results: List to append OCR results to
    
    Size filtering (same as standalone image files):
        - Skip < MIN_MEDIA_BYTES (10KB): too small, likely icons/decorations
        - Skip > MAX_IMAGE_BYTES (100MB): too large, OCR timeout risk
    """
    tmp_dir = None
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, 'r') as zf:
            img_names = [n for n in zf.namelist()
                         if n.startswith(media_prefix)
                         and n.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff', '.tif'))]
            if img_names:
                tmp_dir = tempfile.mkdtemp(prefix='rag_office_img_')
                img_paths = []
                skipped_small = 0
                skipped_large = 0
                for img_name in img_names:
                    info = zf.getinfo(img_name)
                    # Size filtering: same rules as standalone image files
                    if info.file_size < get_limits_config()['min_media_bytes']:
                        skipped_small += 1
                        continue
                    if info.file_size > get_limits_config()['max_image_bytes']:
                        skipped_large += 1
                        continue
                    zf.extract(img_name, tmp_dir)
                    img_paths.append(os.path.join(tmp_dir, img_name))
                if skipped_small or skipped_large:
                    log.debug(f"Office embedded images filtered: {skipped_small} too small (<{get_limits_config()['min_media_bytes']}B), {skipped_large} too large (>{get_limits_config()['max_image_bytes']//1024//1024}MB)")
                if img_paths:
                    ocr_results = _ocr_images_batch(img_paths)
                    for i, ocr_t in enumerate(ocr_results):
                        if ocr_t and len(ocr_t.strip()) >= get_chunking_config()['min_text_chars_low']:
                            results.append(f"[Embedded Image {i+1}: {os.path.basename(img_paths[i])}]\n{ocr_t}")
    except Exception as e:
        log.debug(f"Office embedded image OCR failed: {e}")
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _extract_office(file_id: int, path: str, ext: str, media_meta: str = None) -> List[ChunkData]:
    """Office documents: extract text -> greedy chunk.
    
    P2: Also extracts embedded images from docx/pptx and OCRs them.
    """
    text = ""
    ext = ext.lower()
    embedded_ocr_texts = []  # P2: Collect OCR text from embedded images

    if ext == '.docx':
        try:
            from docx import Document
            from docx.oxml.ns import qn
            doc = Document(path)
            body = doc.element.body
            parts = []
            para_idx = 0
            for child in body.iterchildren():
                if child.tag == qn('w:tbl'):
                    for row_elem in child.iterchildren(qn('w:tr')):
                        cells = []
                        for cell_elem in row_elem.iterchildren(qn('w:tc')):
                            cell_texts = []
                            for p in cell_elem.iterchildren(qn('w:p')):
                                for r in p.iterchildren(qn('w:r')):
                                    for t in r.iterchildren(qn('w:t')):
                                        if t.text:
                                            cell_texts.append(t.text)
                            cells.append(''.join(cell_texts))
                        if cells:
                            parts.append(' | '.join(cells))
                    parts.append('')
                elif child.tag == qn('w:p'):
                    if para_idx < len(doc.paragraphs):
                        parts.append(doc.paragraphs[para_idx].text)
                        para_idx += 1
            text = '\n'.join(parts) if parts else '\n'.join(p.text for p in doc.paragraphs)
            
            # P2: Extract embedded images from docx (word/media/*.png etc.) and OCR them
            _ocr_embedded_office_images(path, 'word/media/', embedded_ocr_texts)
        except Exception as e:
            raise ExtractionError(f'DOCX parser failed: {type(e).__name__}: {e}') from e
    elif ext == '.doc':
        try:
            r = subprocess.run(['antiword', '-m', 'UTF-8.txt', path], capture_output=True, text=True, encoding='utf8', timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                text = r.stdout
            else:
                raise ExtractionError('antiword failed or returned no DOC text')
        except (FileNotFoundError,subprocess.TimeoutExpired) as exc:
            raise ExtractionError('DOC requires a working local antiword converter') from exc
    elif ext == '.xlsx':
        from openpyxl import load_workbook
        workbook=load_workbook(path,read_only=True,data_only=True)
        try:
            for sheet in workbook.worksheets:
                text+=f'\n--- Sheet: {sheet.title} ---\n'
                for row in sheet.iter_rows(values_only=True):
                    text+=' | '.join(str(value) for value in row if value is not None)+'\n'
        finally:
            workbook.close()
    elif ext == '.xls':
        import xlrd
        workbook=xlrd.open_workbook(path,on_demand=True)
        try:
            for sheet in workbook.sheets():
                text+=f'\n--- Sheet: {sheet.name} ---\n'
                for row in range(sheet.nrows):
                    text+=' | '.join(str(sheet.cell_value(row,column)) for column in range(sheet.ncols))+'\n'
        finally:
            workbook.release_resources()
    elif ext == '.pptx':
        try:
            from pptx import Presentation
            prs = Presentation(path)
            for idx, slide in enumerate(prs.slides, 1):
                text += f"\n--- Slide {idx} ---\n"
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        text += shape.text_frame.text + "\n"
            
            # P2: Extract embedded images from pptx (ppt/media/*.png etc.) and OCR them
            _ocr_embedded_office_images(path, 'ppt/media/', embedded_ocr_texts)
        except ImportError:
            # Fallback: try libreoffice or catppt
            try:
                r = subprocess.run(['catppt', '-d', 'utf-8', path], capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and r.stdout.strip():
                    text = r.stdout
                else:
                    raise ExtractionError('PPTX fallback converter returned no text')
            except Exception as exc:
                raise ExtractionError('PPTX requires python-pptx or a working catppt converter') from exc

    elif ext == '.ppt':
        import tempfile, glob
        from office_process import run_office_process
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                r = run_office_process(
                    [shutil.which('soffice.com') or shutil.which('libreoffice') or shutil.which('soffice') or 'libreoffice', '-env:UserInstallation='+Path(tmpdir,'profile').as_uri(), '--headless', '--infilter='+OFFICE_INPUT_FILTERS[ext], '--convert-to', 'pptx', '--outdir', tmpdir, path],
                    timeout=60
                )
                if r.returncode == 0:
                    pptx_files = glob.glob(os.path.join(tmpdir, '*.pptx'))
                    if pptx_files:
                        from pptx import Presentation
                        prs = Presentation(pptx_files[0])
                        for slide in prs.slides:
                            for shape in slide.shapes:
                                if shape.has_text_frame:
                                    text += shape.text_frame.text + "\n"
            if not text.strip():
                raise ValueError("No text")
        except Exception:
            try:
                r2 = subprocess.run(['catppt', '-d', 'utf-8', path], capture_output=True, text=True, timeout=10)
                if r2.returncode == 0 and r2.stdout.strip():
                    text = r2.stdout
                else:
                    raise ExtractionError('PPT converter returned no text')
            except Exception as exc:
                raise ExtractionError('PPT requires a working LibreOffice or catppt converter') from exc
    elif ext == '.rtf':
        text = _read_file(path) or ""
        if not re.match(r'^\s*\{\\rtf[12]\b',text):
            raise ExtractionError('Invalid RTF header')
        if re.search(r"\\u-?\d+|\\'[0-9a-fA-F]{2}",text):
            raise ExtractionError('Encoded RTF text requires conversion to DOCX or plain UTF8 text')
        text = re.sub(r'\\(?:par|line)\b ?', '\n', text)
        text = re.sub(r'\\[a-z]+\d*\s?', '', text)
        text = re.sub(r'[{}]', '', text)
    else:
        text = _read_file(path) or ""

    # P2: Append embedded image OCR text to main text
    if embedded_ocr_texts:
        text += "\n\n=== Embedded Images (OCR) ===\n" + "\n\n".join(embedded_ocr_texts)

    # P3: Use lower threshold for office docs with embedded images (charts, diagrams)
    effective_threshold = get_chunking_config()['min_text_chars_low'] if embedded_ocr_texts else get_chunking_config()['min_text_chars']
    if len(text.strip()) < effective_threshold:
        _mark_ocr_insufficient(file_id, path, f'office_short_{len(text.strip())}chars')
        return []

    # Garbled text check
    if _is_ocr_garbage(text.strip(), min_chars=effective_threshold):
        _mark_ocr_insufficient(file_id, path, 'office_garbled')
        return []

    ctx = _build_context_text(path, media_meta)
    full_text = ctx + "\n\n" + text
    chunks = chunk_greedy_semantic(full_text, ext='.txt')


    return [ChunkData(file_id=file_id, chunk_index=i, text=c, char_count=len(c)) for i, c in enumerate(chunks)]


def _safe_run_archive(args, timeout=15):
    """subprocess.run for archive tools — uses bytes mode to avoid UTF-8 decode errors.
    
    Windows ZIP files often contain GBK-encoded filenames in their listing output.
    Python's text=True with default UTF-8 encoding will crash on these.
    """
    r = subprocess.run(args, capture_output=True, timeout=timeout)
    stdout = r.stdout.decode('utf-8', errors='replace') if r.stdout else ''
    stderr = r.stderr.decode('utf-8', errors='replace') if r.stderr else ''
    # Simulate CompletedProcess with decoded strings
    r.stdout_text = stdout
    r.stderr_text = stderr
    return r


def _extract_archive(file_id: int, path: str, media_meta: str = None) -> List[ChunkData]:
    """Index complete bounded listings, or complete bounded compressed text."""
    import zipfile,tarfile,gzip,bz2,lzma
    ext=Path(path).suffix.lower();names=[];total_bytes=0;listing_chars=0
    budget=get_limits_config()['max_archive_uncompressed_bytes']
    def member(name,size):
        nonlocal total_bytes,listing_chars
        total_bytes+=size
        if total_bytes>budget:raise ExtractionError('Archive exceeds configured uncompressed byte limit')
        names.append(f'{name} | {size} bytes')
        listing_chars+=len(names[-1])+1
        if listing_chars>40000:raise ExtractionError('Archive listing exceeds the 40000-character limit')
    source='archive_listing'
    if ext=='.zip':
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.flag_bits&1:raise ExtractionError('Encrypted archive contents are unavailable')
                member(info.filename,info.file_size)
        listing='\n'.join(names) or '[Empty archive: 0 members]'
    elif ext=='.tar' or str(path).lower().endswith(('.tar.gz','.tar.bz2','.tar.xz')):
        with tarfile.open(path,'r:*') as archive:
            for info in archive:member(info.name,info.size)
        listing='\n'.join(names) or '[Empty archive: 0 members]'
    elif ext in ('.gz','.bz2','.xz'):
        opener={'.gz':gzip.open,'.bz2':bz2.open,'.xz':lzma.open}[ext]
        maximum=min(budget,get_limits_config()['max_text_file_bytes'],40000)
        with opener(path,'rb') as stream:content=stream.read(maximum+1)
        if len(content)>maximum:raise ExtractionError(f'Compressed text exceeds the {maximum}-byte limit')
        with tempfile.TemporaryDirectory(prefix='rag_archive_') as temporary:
            raw=Path(temporary)/'content.txt';raw.write_bytes(content);listing=_read_file(str(raw))
        if not listing or not listing.strip():raise ExtractionError('Compressed payload is not readable text')
        source='archive_text'
    elif ext in ('.7z','.rar'):
        result=_safe_run_archive(['7z','l','-slt','-sccUTF-8','-p-','--',path])
        if result.returncode!=0:raise ExtractionError('7-Zip could not list the archive; verify the file and local converter')
        if re.search(r'^Encrypted = \+\s*$',result.stdout_text,re.M):raise ExtractionError('Encrypted archive contents are unavailable')
        if sum(map(int,re.findall(r'^Size = (\d+)\s*$',result.stdout_text,re.M)))>budget:
            raise ExtractionError('Archive exceeds configured uncompressed byte limit')
        listing=result.stdout_text.strip()
        if not listing:raise ExtractionError('Archive converter returned no listing')
        if len(listing)>40000:raise ExtractionError('Archive listing exceeds the 40000-character limit')
    else:
        raise UnsupportedFormatError(f'Archive adapter unavailable for {ext}')
    text=_build_context_text(path,media_meta)+'\n\n=== '+('Compressed Text' if source=='archive_text' else 'Archive Member Listing')+' ===\n'+listing
    chunks=chunk_greedy_semantic(text,ext='.txt')
    return [ChunkData(file_id,i,value,len(value),{'source':source,'member_contents_extracted':source=='archive_text'}) for i,value in enumerate(chunks)]



def _extract_text_fallback(file_id: int, path: str, media_meta: str = None) -> List[ChunkData]:
    """Unknown file type: try reading as text."""
    ctx = _build_context_text(path, media_meta)
    text = _read_file(path)
    if not text or len(text.strip()) < get_chunking_config()['min_text_chars']:
        return [ChunkData(
            file_id=file_id, chunk_index=0,
            text=ctx, char_count=len(ctx),
            meta={'source': 'unknown'}
        )]

    # Binary content check: null bytes indicate non-text file
    if '\x00' in text:
        log.debug(f"Skipping binary content (null bytes): {path}")
        return []

    # Garbled text check
    if _is_ocr_garbage(text.strip(), min_chars=get_chunking_config()['min_text_chars']):
        _mark_ocr_insufficient(file_id, path, 'fallback_garbled')
        return []

    full_text = ctx + "\n\n" + text
    chunks = chunk_greedy_semantic(full_text, ext='.txt')
    return [ChunkData(
        file_id=file_id, chunk_index=i, text=c, char_count=len(c),
        meta={'source': 'unknown'}
    ) for i, c in enumerate(chunks)]
