"""Canonical format routing, with adapter applicability separate from availability.

Recognition means an adapter may be attempted, not that its dependency is
installed or that real model/format acceptance passed. Verification is tracked
in MODEL_EVALUATION_STATUS.md; optional adapters return explicit failures.
This module imports no parsers, model clients or database code.
"""
import json
from pathlib import Path

TEXT_EXTS = {'.txt', '.md', '.rst', '.log', '.csv', '.tsv', '.lrc'}
CODE_EXTS = {'.py', '.js', '.ts', '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go',
             '.rs', '.rb', '.php', '.lua', '.sh', '.sql', '.r', '.m', '.scala', '.kt',
             '.swift', '.dart', '.ps1', '.bat', '.css', '.scss', '.html', '.vue', '.jsx', '.tsx'}
CONFIG_EXTS = {'.json', '.yaml', '.yml', '.toml', '.xml', '.ini', '.cfg', '.conf',
               '.env', '.properties', '.gitignore', '.dockerignore', '.editorconfig'}
PDF_EXTS = {'.pdf'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.tif', '.svg', '.ico', '.heic', '.heif', '.avif'}
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.3gp', '.ts', '.asf', '.mpeg', '.mpg', '.mts', '.m2ts'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.wem', '.amr', '.aiff', '.aif', '.sfk', '.mid', '.midi', '.rmi', '.wv', '.ape', '.alac'}
# Binary/garbage extensions: skip entirely (never try to read as text)
BINARY_EXTS = {
    '.bin', '.exe', '.elf', '.dll', '.so', '.dylib', '.o', '.obj', '.pyc', '.pyo',
    '.dat', '.tar.enc', '.dds', '.tga', '.bundle', '.ocl', '.majset', '.psobin',
    '.slp', '.dxs', '.w3x', '.idx', '.fxo', '.blob', '.shot', '.thumb',
    '.null', '.ds_store', '.3dmark-result', '.fastresume',
    '.gz.cdx', '.gz.krt',
}
# Numbered extensions (e.g. .0, .1, .2 ... .13) — game cache / partition data
BINARY_EXTS.update(f'.{i}' for i in range(20))

EBOOK_EXTS = {'.epub', '.mobi', '.azw3', '.fb2'}
DJVU_EXTS = {'.djvu'}  # djvu 跟 PDF 相同逻辑：文本 → OCR → 视觉模型
OFFICE_EXTS = {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf'}
ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz'}


def classify_file(ext: str, media_meta=None, file_path=None) -> str:
    """Textual .ts is TypeScript; MPEG sync packets are video."""
    ext = (ext or '').lower()
    if ext == '.ts' and file_path:
        try:
            with open(file_path, 'rb') as stream: sample = stream.read(1024)
            if len(sample) >= 377 and sample[0] == sample[188] == sample[376] == 0x47:
                return 'video'
        except OSError:
            pass
    if ext in FORMATS and ext != '.pdf':return FORMATS[ext]['kind']
    if ext == '.pdf':
        try: meta = json.loads(media_meta) if isinstance(media_meta, str) else (media_meta or {})
        except (ValueError, TypeError): meta = {}
        return {'text_pdf': 'pdf_text', 'hybrid_pdf': 'pdf_hybrid'}.get(meta.get('type') if isinstance(meta,dict) else None, 'pdf_image')
    return 'unknown'




# Capabilities intentionally differ: SVG has OCR routing but no Pillow renderer;
# OpenDocument is recognized Office data but has no supported text adapter.
AUDIO_ONLY_EXTS = AUDIO_EXTS
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS
RASTER_EXTS = IMAGE_EXTS - {'.svg'}
OFFICE_INPUT_FILTERS = {'.doc':'MS Word 97','.docx':'Office Open XML Text',
    '.xls':'MS Excel 97','.xlsx':'Calc MS Excel 2007 XML',
    '.ppt':'MS PowerPoint 97','.pptx':'Impress MS PowerPoint 2007 XML'}
RENDER_OFFICE_EXTS = set(OFFICE_INPUT_FILTERS)
FORMAT_GROUPS = (('text', TEXT_EXTS), ('code', CODE_EXTS), ('config', CONFIG_EXTS),
    ('image', IMAGE_EXTS), ('video', VIDEO_EXTS), ('audio', AUDIO_EXTS),
    ('ebook', EBOOK_EXTS), ('djvu', DJVU_EXTS), ('office', OFFICE_EXTS),
    ('archive', ARCHIVE_EXTS), ('binary', BINARY_EXTS), ('pdf', PDF_EXTS))
FORMATS = {}
for kind, extensions in FORMAT_GROUPS:
    for ext in extensions:
        if ext in FORMATS:continue  # .ts defaults to source; signature can override.
        FORMATS[ext] = {
            'kind': kind,
            'metadata': 'image' if ext in RASTER_EXTS else 'audio' if kind=='audio' else 'video' if kind=='video' else 'pdf' if kind=='pdf' else None,
            'render': 'image' if ext in RASTER_EXTS else 'office' if ext in RENDER_OFFICE_EXTS else kind if kind in ('pdf','djvu') else None,
            'extract': kind not in ('binary','video') and ext not in ('.odt','.ods','.odp'),
        }


def metadata_kind(path, mime=None):
    """Cheap applicability only; missing/corrupt parsers are never a skip."""
    ext=Path(path).suffix.lower()
    kind=FORMATS.get(ext,{}).get('metadata')
    if kind:return kind
    for prefix, fallback in (('image/','image'),('audio/','audio'),('video/','video')):
        if (mime or '').startswith(prefix):return fallback
    return None


def render_kind(path):
    return FORMATS.get(Path(path).suffix.lower(),{}).get('render')
