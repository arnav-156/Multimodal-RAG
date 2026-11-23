#!/usr/bin/env python3
"""Explicit-root, content-versioned local file discovery.

Only a successfully enumerated root is authoritative for deletions. Everything
configuration is retained for compatibility but deliberately uses the scoped,
portable os.walk backend; no whole-drive export or WSL path conversion occurs.
"""
from config_loader import configured_operation
import fnmatch
import hashlib
import logging
import mimetypes
import os
from pathlib import Path
import stat
import sys
import tempfile
import sqlite3
from datetime import datetime, timezone
from contextlib import closing

log = logging.getLogger(__name__)

EXTRA_MIME = {
    '.md':'text/markdown','.json':'application/json','.yaml':'application/yaml',
    '.yml':'application/yaml','.toml':'application/toml','.log':'text/plain',
    '.cfg':'text/plain','.ini':'text/plain','.conf':'text/plain',
    '.sh':'text/x-shellscript','.py':'text/x-python','.js':'application/javascript',
    '.ts':'application/typescript','.tsx':'application/typescript',
    '.jsx':'application/javascript','.c':'text/x-c','.cpp':'text/x-c++',
    '.h':'text/x-c','.hpp':'text/x-c++','.cs':'text/x-csharp',
    '.java':'text/x-java','.kt':'text/x-kotlin','.rs':'text/x-rust',
    '.go':'text/x-go','.rb':'text/x-ruby','.php':'text/x-php',
    '.lua':'text/x-lua','.sql':'application/sql','.swift':'text/x-swift',
    '.dart':'application/dart','.vue':'text/x-vue','.svelte':'text/x-svelte',
    '.exe':'application/x-msdos-program','.dll':'application/x-msdownload',
    '.ttf':'font/ttf','.otf':'font/otf','.woff':'font/woff',
    '.woff2':'font/woff2','.torrent':'application/x-bittorrent',
    '.apk':'application/vnd.android.package-archive',
    '.7z':'application/x-7z-compressed','.rar':'application/x-rar',
    '.zip':'application/zip','.gz':'application/gzip',
    '.xz':'application/x-xz','.bz2':'application/x-bzip2',
    '.tar':'application/x-tar','.iso':'application/x-iso9660-image',
    '.wav':'audio/wav','.flac':'audio/flac','.mp3':'audio/mpeg',
    '.ogg':'audio/ogg','.aac':'audio/aac','.m4a':'audio/mp4','.opus':'audio/opus',
    '.mp4':'video/mp4','.mkv':'video/x-matroska','.avi':'video/x-msvideo',
    '.mov':'video/quicktime','.wmv':'video/x-ms-wmv','.webm':'video/webm',
    '.flv':'video/x-flv','.rmvb':'application/vnd.rn-realmedia-vbr',
    '.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png',
    '.gif':'image/gif','.webp':'image/webp','.bmp':'image/bmp',
    '.svg':'image/svg+xml','.ico':'image/x-icon','.tiff':'image/tiff',
    '.heic':'image/heic','.heif':'image/heif','.psd':'image/vnd.adobe.photoshop',
    '.doc':'application/msword',
    '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls':'application/vnd.ms-excel',
    '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.ppt':'application/vnd.ms-powerpoint',
    '.pptx':'application/vnd.openxmlformats-officedocument.presentationml.sheet',
    '.pdf':'application/pdf',
    '.epub':'application/epub+zip','.mobi':'application/x-mobipocket-ebook',
    '.bat':'application/x-bat','.cmd':'application/x-bat','.ps1':'application/x-powershell',
    '.jar':'application/x-java-archive','.pem':'application/x-x509-ca-cert',
    '.crt':'application/x-x509-ca-cert','.nfo':'text/x-nfo',
    '.m3u':'application/x-mpegurl','.m3u8':'application/x-mpegurl',
    '.msi':'application/x-msi','.dmg':'application/x-apple-diskimage',
    '.deb':'application/vnd.debian-binary-package',
    '.rpm':'application/x-rpm','.chm':'application/x-chm',
    '.djvu':'image/vnd.djvu',
}


for _ext, _mime in EXTRA_MIME.items():
    mimetypes.add_type(_mime, _ext)


def convert_path(value: str) -> str:
    """Compatibility helper: native paths must never be rewritten as WSL paths."""
    return value


def normalize_path(value) -> str:
    if not str(value).strip():
        raise ValueError("A scan root must be an explicit nonempty directory")
    return os.path.normcase(str(Path(os.path.expandvars(str(value))).expanduser().resolve()))


def is_within_root(path, root) -> bool:
    try:
        Path(normalize_path(path)).relative_to(Path(normalize_path(root)))
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def get_mime(ext: str | None) -> str:
    return EXTRA_MIME.get((ext or '').lower()) or mimetypes.guess_type('file' + (ext or ''))[0] or 'application/octet-stream'


def file_fingerprint(path) -> str:
    """Streaming SHA-256, rejecting symlinks and files modified while hashing."""
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise OSError("Only regular, non-symlink files can be indexed")
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if before.st_ino != opened.st_ino:
            raise OSError("File changed before fingerprinting")
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
        after = os.fstat(stream.fileno())
    current = os.lstat(path)
    signature = lambda s: (s.st_size, s.st_mtime_ns, s.st_ino)
    if signature(before) != signature(after) or signature(after) != signature(current):
        raise OSError("File changed during fingerprinting; retry the scan")
    return digest.hexdigest()


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _raise_walk_error(error):
    raise error


class ScanCancelled(RuntimeError):pass


def _snapshot(root: str, skip_dirs: set, skip_files: list, excluded=(),stop_event=None) -> list:
    directory = Path(root)
    if not directory.is_dir():
        raise OSError("Configured root is unavailable or is not a directory")
    for base, dirs, names in os.walk(root, followlinks=False, onerror=_raise_walk_error):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs and normalize_path(Path(base)/d) not in excluded and not _is_link(Path(base) / d))
        for name in sorted(names):
            if stop_event is not None and stop_event.is_set():raise ScanCancelled()
            if any(fnmatch.fnmatch(name, pattern) for pattern in skip_files):
                continue
            candidate = Path(base) / name
            if normalize_path(candidate) in excluded:
                continue
            if _is_link(candidate):
                continue
            source = os.lstat(candidate)
            if not stat.S_ISREG(source.st_mode):
                continue
            resolved = normalize_path(candidate)
            if not is_within_root(resolved, root):
                raise OSError("Discovered file escaped its configured root")
            fingerprint = file_fingerprint(candidate)
            if stop_event is not None and stop_event.is_set():raise ScanCancelled()
            # Recheck the stat used in this record, not only the hash stream.
            latest = os.lstat(candidate)
            if (source.st_size, source.st_mtime_ns, source.st_ino) != (latest.st_size, latest.st_mtime_ns, latest.st_ino):
                raise OSError("File changed while building the root snapshot")
            ext = candidate.suffix.lower() or None
            yield (resolved, name, ext, source.st_size, source.st_mtime,
                          source.st_ctime, source.st_atime,
                          len(candidate.parent.relative_to(directory).parts),
                          get_mime(ext), fingerprint)


@configured_operation
def scan_path_oswalk(scan_path: str) -> list:
    """Legacy listing helper, sharing the same root and exclusion policy."""
    from config_loader import get_config
    cfg = get_config().get('scanner', {})
    rows = _snapshot(normalize_path(scan_path), set(cfg.get('skip_dirs', [])), cfg.get('skip_files', []))
    return [row[:9] for row in rows]


def _invalidate(conn, fid):
    for table in ('rag_chunks', 'rag_status', 'rag_descriptions', 'rag_ocr_status'):
        conn.execute(f'DELETE FROM {table} WHERE file_id=?', (fid,))


def _roots(paths):
    result = []
    for value in sorted({normalize_path(p) for p in paths}, key=lambda p: len(Path(p).parts)):
        if not any(is_within_root(value, previous) for previous in result):
            result.append(value)
    return result


@configured_operation
def sync(dry_run=False, *, paths=None, db_path=None,stop_event=None):
    """Reconcile only explicit, successfully scanned roots; return job outcomes.

    Unavailable/error roots preserve every previously indexed record. A changed
    content hash invalidates all derived data atomically. Intentional deletion
    keeps a tombstone and clears its children, so every search path can agree.
    No filesystem content is deleted by this function.
    """
    from config_loader import get_config, get_db_path
    import database
    cfg = get_config().get('scanner', {})
    roots = _roots(cfg.get('scan_paths', []) if paths is None else paths)
    db_path = db_path or get_db_path()
    result = {'scanned': 0, 'inserted': 0, 'updated': 0, 'deleted': 0,
              'unchanged': 0, 'roots': [], 'errors': [], 'dry_run': bool(dry_run),'cancelled':False}
    if not dry_run:
        database.ensure_initialized(db_path)
    for root in roots:
        if stop_event is not None and stop_event.is_set():result['cancelled']=True;break
        counts = {'inserted':0,'updated':0,'deleted':0,'unchanged':0}
        scanned=0
        now=datetime.now(timezone.utc).isoformat()
        try:
            excluded={normalize_path(str(db_path)+suffix) for suffix in ('','-wal','-shm','-journal')}
            if dry_run:
                records=_snapshot(root,set(cfg.get('skip_dirs',[])),cfg.get('skip_files',[]),excluded,stop_event=stop_event)
                scanned=sum(1 for _ in records)
            else:
                # Stage only metadata in an owned disk-backed spool. No main DB
                # writer is held while enumerating or hashing source content.
                with tempfile.TemporaryDirectory(prefix='rag-scan-') as directory:
                    spool_path=Path(directory)/'snapshot.sqlite'
                    excluded.add(normalize_path(directory))
                    records=_snapshot(root,set(cfg.get('skip_dirs',[])),cfg.get('skip_files',[]),excluded,stop_event=stop_event)
                    with closing(sqlite3.connect(spool_path)) as spool:
                        spool.execute('CREATE TABLE records(path TEXT PRIMARY KEY,name,ext,size,mtime,ctime,atime,depth,mime,fingerprint)')
                        spool.executemany('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)',records)
                        spool.commit()
                        scanned=spool.execute('SELECT COUNT(*) FROM records').fetchone()[0]
                    if stop_event is not None and stop_event.is_set():raise ScanCancelled()
                    with database.connect(db_path) as conn:
                        conn.execute('ATTACH DATABASE ? AS scan_stage',(str(spool_path),))
                        conn.execute('BEGIN IMMEDIATE')
                        for filepath,name,ext,size,mtime,ctime,atime,depth,mime,fingerprint in conn.execute('SELECT * FROM scan_stage.records ORDER BY rowid'):
                            if stop_event is not None and stop_event.is_set():raise ScanCancelled()
                            previous=conn.execute('SELECT id,file_hash,is_deleted FROM files WHERE path=?',(filepath,)).fetchone()
                            if previous is None:
                                conn.execute("INSERT INTO files(path,name,ext,size,mtime,ctime,atime,is_dir,depth,scanned_at,is_deleted,file_hash,mime_type,media_meta,root_path) VALUES (?,?,?,?,?,?,?,0,?,?,0,?,?,NULL,?)",
                                    (filepath,name,ext,size,mtime,ctime,atime,depth,now,fingerprint,mime,root))
                                counts['inserted']+=1
                            else:
                                changed=previous['file_hash']!=fingerprint or previous['is_deleted']
                                if changed:_invalidate(conn,previous['id'])
                                conn.execute("UPDATE files SET name=?,ext=?,size=?,mtime=?,ctime=?,atime=?,depth=?,scanned_at=?,is_deleted=0,file_hash=?,mime_type=?,root_path=?,media_meta=CASE WHEN ? THEN NULL ELSE media_meta END WHERE id=?",
                                    (name,ext,size,mtime,ctime,atime,depth,now,fingerprint,mime,root,bool(changed),previous['id']))
                                counts['updated' if changed else 'unchanged']+=1
                        if stop_event is not None and stop_event.is_set():raise ScanCancelled()
                        candidates=conn.execute("SELECT f.id,f.path FROM files f LEFT JOIN scan_stage.records s ON s.path=f.path WHERE f.is_deleted=0 AND (f.root_path=? OR f.root_path IS NULL) AND s.path IS NULL",(root,))
                        for previous in candidates:
                            if stop_event is not None and stop_event.is_set():raise ScanCancelled()
                            if is_within_root(previous['path'],root):
                                _invalidate(conn,previous['id'])
                                conn.execute('UPDATE files SET is_deleted=1,root_path=?,scanned_at=? WHERE id=?',(root,now,previous['id']))
                                counts['deleted']+=1
        except ScanCancelled:
            result['cancelled']=True;result['roots'].append({'path':root,'status':'cancelled'});break
        except (OSError,ValueError) as exc:
            message=str(exc)
            result['roots'].append({'path':root,'status':'unavailable','error':message})
            result['errors'].append({'path':root,'error':message})
            continue
        result['scanned']+=scanned
        if not dry_run:
            for key,value in counts.items():result[key]+=value
        result['roots'].append({'path':root,'status':'dry_run' if dry_run else 'scanned','files':scanned,**counts})
    return result


if __name__ == '__main__':
    import json
    print(json.dumps(sync(dry_run='--dry-run' in sys.argv), indent=2, ensure_ascii=False))
