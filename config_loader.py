"""Validated UTF-8 configuration with defensive, operation-owned snapshots."""
from __future__ import annotations
import copy
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Literal, Optional
from urllib.parse import urlsplit
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / 'config.yaml'
REDACTED = '***REDACTED***'
_config_cache = None
_config_path = None
_env_loaded = False
_env_path = None
_dotenv_values = {}
_lock = threading.RLock()
_operation_config = ContextVar("operation_config", default=None)
_operation_env = ContextVar("operation_env", default=None)

class Section(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_default=True)

class ServerSettings(Section):
    host: str = '127.0.0.1'
    port: int = Field(8100, ge=1, le=65535)
    cors_origins: list[str] = Field(default_factory=lambda: ['http://127.0.0.1:8100', 'http://localhost:8100'])

class SecuritySettings(Section):
    local_only: bool = True
    allow_cloud: bool = False
    allowed_hosts: list[str] = Field(default_factory=lambda: ['localhost', '127.0.0.1', '::1'])

class DatabaseSettings(Section):
    path: str = './data/file_index.db'

class ScannerSettings(Section):
    scan_paths: list[str] = Field(default_factory=list)
    home_paths: list[str] = Field(default_factory=list)  # Deprecated, not implicit roots.
    everything_exe: str = ''
    skip_dirs: list[str] = Field(default_factory=lambda: ['.git','node_modules','__pycache__','.cache','.npm','.local','.venv','.ssh','.aws','.gnupg','.azure','.kube'])
    skip_files: list[str] = Field(default_factory=lambda: ['.env','.env.*','.api_token','id_rsa*','id_ed25519*','credentials','*.pem','*.key','*.p12','*.pfx'])

class MetadataSettings(Section):
    workers_per_disk: int = Field(2, ge=1, le=32)
    file_timeout: float = Field(20, gt=0, le=600)
    log_file: str = './logs/meta_extract.log'
    max_pending: int = Field(100, ge=1, le=10000)

class VLLMSettings(Section):
    api_url: str = 'http://127.0.0.1:8001'
    model_name: str = 'jinaai/jina-embeddings-v5-text-small-retrieval'
    port: int = Field(8001, ge=1, le=65535)
    dtype: str = 'bfloat16'
    gpu_memory_utilization: float = Field(0.25, gt=0, le=1)
    max_model_len: int = Field(16384, ge=1)

class EmbeddingSettings(Section):
    provider: Literal['openai', 'ollama', 'tei'] = 'openai'
    model: str = ''
    model_path: str = './models/jina-embeddings-v5-text-small-retrieval'
    revision: str = ''
    dimension: int = Field(512, ge=1, le=8192)
    native_dimension: Optional[int] = Field(1024, ge=1, le=8192)
    int8_scale: Literal[127.0] = 127.0
    query_prefix: str = 'Query: '
    document_prefix: str = 'Document: '
    api_url: str = ''
    api_key: str = ''
    allow_cloud: bool = False
    cloud_authorized: bool = False
    timeout: float = Field(120, gt=0, le=600)
    batch_size: int = Field(4, ge=1, le=128)
    max_input_chars: int = Field(32768, ge=64, le=128000)
    description_batch_size: int = Field(8, ge=1, le=128)
    vllm: VLLMSettings = Field(default_factory=VLLMSettings)

class OCRSettings(Section):
    api_url: str = 'http://127.0.0.1:8002'
    port: int = Field(8002, ge=1, le=65535)
    workers: int = Field(1, ge=1, le=16)
    cpu_threads: int = Field(2, ge=1, le=32)
    max_request_bytes: int = Field(16*1024*1024, ge=1024, le=128*1024*1024)
    max_batch_images: int = Field(20, ge=1, le=100)
    use_gpu: bool = False
    device: str = ''
    lang: str = 'ch'
    enabled: bool = False
    timeout: float = Field(120, gt=0, le=600)
    model_dir: str = ''
    detection_model_dir: str = ''
    recognition_model_dir: str = ''
    allowed_roots: list[str] = Field(default_factory=list)
    temp_dir: str = './data/ocr-temp'

class AudioSettings(Section):
    model_path: str = ''  # Explicit local checkpoint; never auto-download.
    python_executable: str = ''  # Optional isolated faster-whisper interpreter.
    cpp_executable: str = ''
    cpp_model_path: str = ''
    cpu_threads: int = Field(2, ge=1, le=32)
    timeout: float = Field(300, gt=0, le=1800)
    max_duration: float = Field(600, gt=0)

    @model_validator(mode='after')
    def paired_cpp_assets(self):
        if bool(self.cpp_executable)!=bool(self.cpp_model_path):
            raise ValueError('cpp_executable and cpp_model_path must be supplied together')
        return self

class ChunkingSettings(Section):
    max_chars: int = Field(6000, ge=64, le=64000)
    overlap_ratio: float = Field(0.05, ge=0, lt=0.5)
    min_text_chars: int = Field(1, ge=1)
    min_text_chars_low: int = Field(1, ge=1)

class LimitSettings(Section):
    min_media_bytes: int = Field(0, ge=0)
    max_image_bytes: int = Field(104857600, ge=1)
    max_text_file_bytes: int = Field(52428800, ge=1)
    max_djvu_pages: int = Field(100, ge=1)
    max_describe_pages: int = Field(20, ge=1)
    max_pdf_pages: int = Field(100, ge=1, le=10000)
    max_archive_uncompressed_bytes: int = Field(52428800, ge=1)
    max_extracted_chars: int = Field(2000000, ge=64)
    max_render_pixels: int = Field(20000000, ge=1000)

class VisionEndpoint(Section):
    api_base: str = 'http://127.0.0.1:1234/v1'
    api_key: str = ''
    api_key_env: str = ''
    model: str = ''
    revision: str = ''
    name: str = 'local'
    enabled: bool = True
    allow_cloud: bool = False
    cloud_authorized: bool = False
    max_tokens: int = Field(2048, ge=1, le=32768)
    timeout: float = Field(60, gt=0, le=600)
    max_parallel: int = Field(1, ge=1, le=16)
    rate_limit: float = Field(0, ge=0, le=3600)

class VisionSettings(Section):
    endpoints: list[VisionEndpoint] = Field(default_factory=list)
    max_workers: int = Field(1, ge=1, le=16)
    max_retries: int = Field(2, ge=0, le=5)

class PipelineSettings(Section):
    extractor_threads: int = Field(2, ge=1, le=32)
    audio_threads: int = Field(1, ge=1, le=8)
    chunk_queue_size: int = Field(1000, ge=1)
    stream_batch_size: int = Field(10, ge=1, le=1000)
    max_retries: int = Field(2, ge=0, le=5)
    fetch_limit: int = Field(20, ge=1, le=1000)

class SearchSettings(Section):
    default_top_k: int = Field(50, ge=1, le=1000)
    max_top_k: int = Field(200, ge=1, le=1000)
    @model_validator(mode='after')
    def check_limits(self):
        if self.default_top_k > self.max_top_k:
            raise ValueError('default_top_k must not exceed max_top_k')
        return self

class LoggingSettings(Section):
    dir: str = './logs'
    level: Literal['DEBUG','INFO','WARNING','ERROR','CRITICAL'] = 'INFO'

class MonitorSettings(Section):
    directories: list[str] = Field(default_factory=list)
    include_types: list[str] = Field(default_factory=list)
    exclude_types: list[str] = Field(default_factory=lambda: ['video'])

class Settings(Section):
    server: ServerSettings = Field(default_factory=ServerSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    scanner: ScannerSettings = Field(default_factory=ScannerSettings)
    metadata: MetadataSettings = Field(default_factory=MetadataSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    limits: LimitSettings = Field(default_factory=LimitSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)
    @model_validator(mode='after')
    def boundaries(self):
        if self.security.local_only and self.server.host not in ('127.0.0.1','localhost','::1'):
            raise ValueError('local_only requires a loopback server.host')
        if not self.security.allowed_hosts or '*' in self.security.allowed_hosts:
            raise ValueError('Explicit allowed_hosts are required')
        for origin in self.server.cors_origins:
            p = urlsplit(origin)
            if p.scheme not in ('http','https') or not p.hostname or p.path not in ('','/') or p.query or p.fragment or p.username:
                raise ValueError('CORS origins must be explicit HTTP origins')
            if self.security.local_only and p.hostname not in ('localhost','127.0.0.1','::1'):
                raise ValueError('local_only requires loopback CORS origins')
        return self

def load_env_file(env_path=None):
    """Read UTF-8 dotenv; existing process variables win."""
    global _env_loaded, _env_path
    p = Path(env_path or ((_config_path.parent if _config_path else Path(__file__).parent) / '.env')).resolve()
    if _env_loaded and p == _env_path:
        return
    for key, old in _dotenv_values.items():
        if os.environ.get(key) == old:
            os.environ.pop(key, None)
    _dotenv_values.clear()
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.removeprefix('export ').split('=',1)
            key, value = key.strip(), value.strip()
            if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',key):
                value = value[1:-1] if len(value)>=2 and value[0]==value[-1] and value[0] in '\"\'' else value
                if key not in os.environ:
                    os.environ[key] = value
                    _dotenv_values[key] = value
    _env_loaded = True
    _env_path = p

def _merge(base,update):
    for key,value in update.items():
        if isinstance(value,dict) and isinstance(base.get(key),dict):
            _merge(base[key],value)
        else:
            base[key] = copy.deepcopy(value)
    return base

def _migrate(data):
    if not isinstance(data, dict):
        raise ValueError('Configuration root must be a mapping')
    data = copy.deepcopy(data)
    for key in ('scan','chunk','scanner','chunking','metadata','embedding'):
        if key in data and not isinstance(data[key],dict):
            raise ValueError('Configuration section '+key+' must be a mapping')
    for old,new in (('scan','scanner'),('chunk','chunking')):
        if old in data:
            data[new] = _merge(data.pop(old),data.get(new,{}))
    scanner = data.get('scanner',{})
    old_db = scanner.pop('db_path',None)
    if old_db and 'database' not in data:
        data['database'] = {'path':old_db}
    for section,old,new in (('metadata','workers','workers_per_disk'),('metadata','timeout','file_timeout'),('embedding','dim','dimension')):
        value = data.get(section,{})
        if old in value:
            value.setdefault(new,value.pop(old))
    embedding = data.get('embedding',{})
    if 'vllm_url' in embedding:
        if 'vllm' in embedding and not isinstance(embedding['vllm'],dict):
            raise ValueError('Configuration section embedding.vllm must be a mapping')
        embedding.setdefault('vllm',{}).setdefault('api_url',embedding.pop('vllm_url'))
    return data

def _resolve_paths(cfg,parent):
    def resolve(value):
        p = Path(os.path.expandvars(os.path.expanduser(value)))
        return str((p if p.is_absolute() else parent/p).resolve())
    for section,name in (('database','path'),('logging','dir'),('metadata','log_file'),('embedding','model_path')):
        cfg[section][name] = resolve(cfg[section][name])
    for name in ('scan_paths','home_paths'):
        cfg['scanner'][name] = [resolve(v) for v in cfg['scanner'][name]]
    for section,name in (('audio','model_path'),('audio','python_executable'),('audio','cpp_executable'),('audio','cpp_model_path'),('ocr','model_dir'),('ocr','detection_model_dir'),('ocr','recognition_model_dir'),('ocr','temp_dir')):
        if cfg[section][name]:
            cfg[section][name] = resolve(cfg[section][name])
    cfg['ocr']['allowed_roots'] = [resolve(v) for v in cfg['ocr']['allowed_roots']]
    return cfg

def validate_config(data,parent=None):
    """Validation errors omit raw input, including API secrets."""
    try:
        cfg = Settings.model_validate(_migrate(data)).model_dump()
    except ValidationError as exc:
        errors = ['.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors(include_input=False)]
        raise ValueError('Invalid configuration: '+'; '.join(errors)) from None
    return _resolve_paths(cfg,Path(parent or DEFAULT_CONFIG_PATH.parent))

def _environment(cfg):
    mappings = {'DB_PATH':('database','path'),'LOG_DIR':('logging','dir'),'JINA_MODEL_PATH':('embedding','model_path'),'OCR_API_URL':('ocr','api_url'),'RAG_PORT':('server','port'),'RAG_HOST':('server','host')}
    for env,(section,key) in mappings.items():
        if os.environ.get(env):
            cfg.setdefault(section,{})[key] = os.environ[env]
    for env,key in (('VLLM_API_URL','api_url'),('VLLM_MODEL_NAME','model_name')):
        if os.environ.get(env):
            cfg.setdefault('embedding',{}).setdefault('vllm',{})[key] = os.environ[env]
    return cfg

def _load_config(config_path=None):
    global _config_cache,_config_path
    with _lock:
        p = Path(config_path or _config_path or os.environ.get('RAG_CONFIG') or DEFAULT_CONFIG_PATH).expanduser().resolve()
        if _config_cache is not None and p==_config_path:
            return _config_cache
        load_env_file(p.parent/'.env')
        with p.open('r',encoding='utf-8-sig') as stream:
            raw = yaml.safe_load(stream) or {}
        if not isinstance(raw,dict):
            raise ValueError('Configuration root must be a mapping')
        _config_cache = validate_config(_environment(_migrate(raw)),p.parent)
        _config_path = p
        return _config_cache

def load_config(config_path=None):
    return copy.deepcopy(_load_config(config_path))


def get_config(config_path=None):
    snapshot = _operation_config.get()
    if snapshot is not None and config_path is None:
        return copy.deepcopy(snapshot)
    return load_config(config_path)


def get_live_config():
    """Latest published revision, for admission and transport revocation checks."""
    return load_config()


def configuration_lock():
    """Serialize settings mutation with job admission in this process."""
    return _lock


@contextmanager
def operation_settings(settings=None):
    """Own one revision; nested calls inherit it, independent operations isolate it.

    Returned dictionaries are defensive copies. Spawn children receive explicit
    primitive parser arguments; job threads capture their snapshot at admission.
    """
    if settings is None and _operation_config.get() is not None:
        yield
        return
    with _lock:
        snapshot = copy.deepcopy(settings if settings is not None else _load_config())
        environment = dict(os.environ)
    token = _operation_config.set(snapshot)
    env_token = _operation_env.set(environment)
    try:
        yield
    finally:
        _operation_env.reset(env_token)
        _operation_config.reset(token)


def configured_operation(function):
    @wraps(function)
    def owned(*args, **kwargs):
        with operation_settings():
            return function(*args, **kwargs)
    return owned
def get_config_path(): return _config_path or Path(os.environ.get('RAG_CONFIG',DEFAULT_CONFIG_PATH)).resolve()

def reload_config(expected_db_path=None):
    global _config_cache, _env_loaded
    with _lock:
        previous=_config_cache
        _config_cache = None
        _env_loaded = False
        try:
            candidate=_load_config()
            if expected_db_path and candidate['database']['path']!=expected_db_path:
                raise ValueError('Database changed; restart the server to use the new path')
            return copy.deepcopy(candidate)
        except Exception:
            _config_cache=previous
            raise

def public_config(cfg=None):
    """Recursively redact write-only secrets, including endpoint lists."""
    def redact(value):
        if isinstance(value,dict):
            return {k:(REDACTED if v else '') if any(s in k.lower() for s in ('api_key','password','secret','token')) and k not in ('max_tokens','api_key_env') else redact(v) for k,v in value.items()}
        if isinstance(value,list):
            return [redact(v) for v in value]
        return value
    return redact(cfg if cfg is not None else get_config())

def _restore_redacted(update,current):
    if isinstance(update,dict):
        return {k:_restore_redacted(v,current.get(k) if isinstance(current,dict) else None) for k,v in update.items()}
    if isinstance(update,list):
        restored = []
        for index,item in enumerate(update):
            old = current[index] if isinstance(current,list) and index<len(current) else None
            if isinstance(item,dict) and isinstance(current,list):
                old = next((entry for entry in current if isinstance(entry,dict) and entry.get('api_base')==item.get('api_base') and entry.get('name')==item.get('name')),None)
            restored.append(_restore_redacted(item,old))
        return restored
    if update==REDACTED:
        if current is None:
            raise ValueError('Redacted secret has no existing value')
        return current
    return update

def save_config(update,config_path=None):
    """Atomically persist a validated partial update and return effective settings."""
    global _config_cache
    if not isinstance(update,dict):
        raise ValueError('Configuration update must be a mapping')
    with _lock:
        cfg = _load_config(config_path)
        p = get_config_path()
        update = copy.deepcopy(update)
        update.pop('env_status',None)
        candidate = _merge(copy.deepcopy(cfg),_restore_redacted(_migrate(update),cfg))
        validated = validate_config(candidate,p.parent)
        effective = validate_config(_environment(copy.deepcopy(validated)),p.parent)
        fd,name = tempfile.mkstemp(prefix='.config-',suffix='.yaml',dir=p.parent)
        try:
            with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as stream:
                yaml.safe_dump(validated,stream,allow_unicode=True,sort_keys=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(name,p)
        finally:
            if os.path.exists(name):os.unlink(name)
        _config_cache = effective
        return copy.deepcopy(_config_cache)

def get_env(key,default=None):
    environment = _operation_env.get()
    if environment is not None:return environment.get(key,default)
    load_env_file();return os.environ.get(key,default)
def get_env_required(key):
    value=get_env(key)
    if not value:raise ValueError(f'Required environment variable {key} is not configured')
    return value

def get_db_path(): return get_config()['database']['path']
def get_log_dir():
    p=get_config()['logging']['dir'];Path(p).mkdir(parents=True,exist_ok=True);return p
def get_embedding_dim():return get_config()['embedding']['dimension']
def get_ocr_api_url():return get_config()['ocr']['api_url']
def get_chunking_config():return get_config()['chunking']
def get_limits_config():return get_config()['limits']
def get_vision_config():return get_config()['vision']
def get_pipeline_config():return get_config()['pipeline']
def get_jina_model_path():return get_config()['embedding']['model_path']
def get_vllm_api_url():return get_config()['embedding']['vllm']['api_url']
def get_vllm_model_name():return get_config()['embedding']['vllm']['model_name']
def reset_config_cache():
    global _config_cache,_config_path,_env_loaded,_env_path
    with _lock:
        for key, old in _dotenv_values.items():
            if os.environ.get(key)==old:os.environ.pop(key,None)
        _dotenv_values.clear()
        _env_path=None
        _config_cache=_config_path=None;_env_loaded=False
