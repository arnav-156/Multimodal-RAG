"""Single SQLite schema, explicit migration, and closing connection contexts."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading

SCHEMA_VERSION=2
_migration_lock=threading.RLock()

class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def connect(db_path=None):
    if db_path is None:
        from config_loader import get_db_path
        db_path=get_db_path()
    db_path=str(db_path)
    if db_path!=':memory:':Path(db_path).expanduser().resolve().parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(db_path,timeout=30,factory=Connection)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


class SourceVersionChanged(ValueError):
    """An attempted result belongs to an obsolete or deleted source version."""


@contextmanager
def source_transaction(db_path,file_id,expected_hash):
    """Own an atomic source-version check and its dependent writes."""
    with connect(db_path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        current=connection.execute('SELECT file_hash,is_deleted FROM files WHERE id=?',(file_id,)).fetchone()
        if not current or current['is_deleted'] or current['file_hash']!=expected_hash:
            raise SourceVersionChanged('Source version changed before commit; obsolete result discarded')
        yield connection

FILES_COLUMNS={
    'path':'TEXT','name':'TEXT','ext':'TEXT','size':'INTEGER','mtime':'REAL','ctime':'REAL','atime':'REAL',
    'is_dir':'INTEGER DEFAULT 0','depth':'INTEGER','scanned_at':'TEXT','is_deleted':'INTEGER DEFAULT 0',
    'file_hash':'TEXT','mime_type':'TEXT','media_meta':'TEXT','root_path':'TEXT',
}
TABLES={
 'rag_chunks':'''id INTEGER PRIMARY KEY AUTOINCREMENT,file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
 chunk_index INTEGER NOT NULL,meta_json TEXT,vector BLOB,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(file_id,chunk_index)''',
 'rag_status':'''file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,status TEXT DEFAULT 'pending',
 chunks_written INTEGER DEFAULT 0,error TEXT,source_hash TEXT,index_fingerprint TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP''',
 'rag_descriptions':'''file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,description TEXT,
 source TEXT DEFAULT 'unknown',embedded INTEGER DEFAULT 0,retry_count INTEGER DEFAULT 0,error TEXT,
 source_hash TEXT,generation_fingerprint TEXT,embedding_fingerprint TEXT,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP''',
 'rag_ocr_status':'''file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,status TEXT,reason TEXT,
 checked_at TEXT DEFAULT CURRENT_TIMESTAMP''',
}

def initialize(db_path=None):
    """Upgrade version 0 transactionally; retain raw legacy child rows in archive tables.

    Unknown legacy vectors remain unavailable to search until explicit reindex.
    No process cleanup, temp-directory deletion, or personal-directory scan occurs.
    """
    with _migration_lock,connect(db_path) as c:
        version=c.execute('PRAGMA user_version').fetchone()[0]
        if version>SCHEMA_VERSION:raise ValueError('Database schema is newer than this application')
        c.execute('PRAGMA foreign_keys=OFF')
        c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY AUTOINCREMENT,path TEXT UNIQUE,name TEXT)')
        existing={r['name'] for r in c.execute('PRAGMA table_info(files)')}
        for name,definition in FILES_COLUMNS.items():
            if name not in existing:c.execute(f'ALTER TABLE files ADD COLUMN {name} {definition}')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_files_path ON files(path)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_files_root ON files(root_path,is_deleted)')
        for name,definition in TABLES.items():
            exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
            if exists and version<1:
                # Preserve every original row (including invalid/orphan/duplicate rows) for review.
                archive=f'legacy_{name}_v0'
                c.execute(f'CREATE TABLE IF NOT EXISTS {archive} AS SELECT * FROM {name}')
                c.execute(f'DROP TABLE {name}')
                c.execute(f'CREATE TABLE {name}({definition})')
                old={r['name'] for r in c.execute(f'PRAGMA table_info({archive})')}
                columns=[r['name'] for r in c.execute(f'PRAGMA table_info({name})') if r['name'] in old]
                if 'file_id' in columns:
                    names=','.join(columns);selected=','.join('r.'+n for n in columns)
                    c.execute(f'INSERT OR IGNORE INTO {name}({names}) SELECT {selected} FROM {archive} r JOIN files f ON f.id=r.file_id')
            else:c.execute(f'CREATE TABLE IF NOT EXISTS {name}({definition})')
        description_columns={r['name'] for r in c.execute('PRAGMA table_info(rag_descriptions)')}
        for name in ('source_hash','generation_fingerprint','embedding_fingerprint'):
            if name not in description_columns:c.execute(f'ALTER TABLE rag_descriptions ADD COLUMN {name} TEXT')
        c.execute('CREATE INDEX IF NOT EXISTS idx_chunks_file ON rag_chunks(file_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_status_status ON rag_status(status)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_descriptions_pending ON rag_descriptions(embedded,created_at)')
        c.execute('CREATE TABLE IF NOT EXISTS index_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        c.execute('''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,
          owner_pid INTEGER,error TEXT,result_json TEXT,progress_json TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_job ON jobs((1)) WHERE status IN ('queued','running')")
        c.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        c.commit()
        c.execute('PRAGMA foreign_keys=ON')
        if c.execute('PRAGMA foreign_key_check').fetchall():raise RuntimeError('Database foreign-key integrity check failed')


def ensure_initialized(db_path=None):
    """Read-only fast path; migrate/create only when schema ownership requires it."""
    with connect(db_path) as connection:
        version=connection.execute('PRAGMA user_version').fetchone()[0]
        names={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        required={'files','jobs','index_metadata','idx_one_active_job','idx_files_path',*TABLES}
        if version==SCHEMA_VERSION and required<=names:return
    initialize(db_path)

def schema_status(db_path=None):
    with connect(db_path) as c:
        version=c.execute('PRAGMA user_version').fetchone()[0]
        names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={'files','jobs','index_metadata',*TABLES}
        return {'connected':version==SCHEMA_VERSION and required<=names,
                'schema_version':version,'expected_version':SCHEMA_VERSION,'missing_tables':sorted(required-names)}
