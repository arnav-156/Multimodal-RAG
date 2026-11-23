"""Durable single-writer jobs shared by HTTP and CLI."""
import json
from dataclasses import dataclass,field
from typing import Callable
from contextvars import copy_context
import os
import threading
import time
from pathlib import Path
import uuid
from config_loader import configuration_lock, operation_settings, get_config
from database import connect, ensure_initialized

class JobConflict(RuntimeError): pass


class JobCancelled(RuntimeError): pass

@dataclass
class WorkerHandle:
    thread: threading.Thread
    stop_event: threading.Event
    db_path: str
    owns_thread: bool = True
    done: threading.Event = field(default_factory=threading.Event)

_workers={}
_workers_lock=threading.RLock()
_closing=False


def startup():
    global _closing
    with _workers_lock:
        if any(handle.thread.is_alive() for handle in _workers.values()):
            raise JobConflict('Owned jobs are still running')
        _closing=False


def cancel(jid,db_path=None):
    from config_loader import get_db_path
    db_path=str(Path(db_path or get_db_path()).resolve())
    with _workers_lock:
        handle=_workers.get(jid)
        if handle is not None and handle.db_path==db_path:
            handle.stop_event.set()
            return {'job_id':jid,'requested':True}
    with connect(db_path) as connection:
        row=connection.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()
    if row is None:raise KeyError('Job not found')
    if row['status'] in ('queued','running'):
        raise JobConflict('Job is owned by another process; this server cannot signal it')
    return {'job_id':jid,'requested':False,'status':row['status']}


def shutdown(timeout=30):
    """Cooperatively stop owned work; report live owners truthfully at the deadline."""
    global _closing
    if timeout<0:raise ValueError('Shutdown timeout must be nonnegative')
    deadline=time.monotonic()+timeout
    with _workers_lock:
        _closing=True
        for handle in _workers.values():handle.stop_event.set()
    coordinator=configuration_lock()
    acquired=coordinator.acquire(timeout=max(0,deadline-time.monotonic()))
    try:
        with _workers_lock:
            handles=list(_workers.items())
            for _,handle in handles:handle.stop_event.set()
    finally:
        if acquired:coordinator.release()
    for _,handle in handles:
        if handle.thread is not threading.current_thread():
            wait=max(0,deadline-time.monotonic())
            if handle.owns_thread:handle.thread.join(wait)
            else:handle.done.wait(wait)
    remaining=[jid for jid,handle in handles if (handle.thread.is_alive() if handle.owns_thread else not handle.done.is_set())]
    complete=acquired and not remaining
    with _workers_lock:
        # Permit later independent library/app sessions after a complete stop.
        if complete:_closing=False
    return {'remaining':remaining,'deadline_seconds':timeout,'stopped':len(handles)-len(remaining),
            'admission_pending':not acquired,'complete':complete}


def _admit(kind,db_path):
    with _workers_lock:
        if _closing:raise JobConflict('Job shutdown is in progress')
    # The caller holds the configuration coordinator until registration/start.
    # Do not hold the worker lock across SQLite, so cancellation stays available.
    jid=reserve(kind,db_path);stop_event=threading.Event()
    with _workers_lock:
        if _closing:stop_event.set()
    return jid,stop_event


@dataclass
class JobContext:
    progress: Callable[[dict],None]
    stop_event: threading.Event = field(default_factory=threading.Event)


@dataclass
class JobOutcome:
    result: dict
    failed: int = 0
    cancelled: bool = False

    def __post_init__(self):
        if not isinstance(self.result,dict) or not isinstance(self.failed,int) or self.failed<0:
            raise TypeError('JobOutcome requires a result mapping and nonnegative failure count')

    @property
    def status(self):
        return 'cancelled' if self.cancelled else 'partial' if self.failed else 'completed'



def stage_outcome(kind,result):
    """One stage-to-job mapping shared by CLI exit status and durable jobs."""
    cancelled=bool(result.get('cancelled') or result.get('cancellation_requested'))
    if kind=='scan':return JobOutcome(result,failed=len(result.get('errors',[])),cancelled=cancelled)
    if kind=='extract':return JobOutcome(result,failed=result['errors']+result['stale'],cancelled=cancelled)
    if kind=='process':return JobOutcome(result,failed=max(result['failed'],result.get('vision',{}).get('failed',0),result.get('descriptions',{}).get('stale_at_start',0)),cancelled=cancelled)
    raise ValueError('Unknown pipeline stage')

def stage_task(kind,db_path=None,options=None):
    """Explicit adapters keep stage-specific dictionaries out of job execution."""
    if kind not in ('scan','extract','process'):raise ValueError('Unknown pipeline stage')
    options=dict(options or {})
    def task(context):
        if kind=='scan':
            from file_scanner import sync
            result=sync(db_path=db_path,stop_event=context.stop_event)
        elif kind=='extract':
            from metadata_extractor import extract_metadata
            result=extract_metadata(db_path=db_path,stop_event=context.stop_event)
        else:
            from run_pipeline import process_files
            result=process_files(db_path=db_path,progress=context.progress,stop_event=context.stop_event,**options)
        context.progress(result)
        return stage_outcome(kind,result)
    return task

def process_alive(pid):
    if not pid:return False
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()==5
        code=ctypes.c_ulong()
        try:return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def recover_interrupted(db_path=None):
    ensure_initialized(db_path)
    with connect(db_path) as connection:
        rows=connection.execute("SELECT id,owner_pid FROM jobs WHERE status IN ('queued','running')").fetchall()
    dead=[row for row in rows if not process_alive(row['owner_pid'])]
    if dead:
        with connect(db_path) as connection:
            for row in dead:
                connection.execute("UPDATE jobs SET status='interrupted',error='Owner process exited',updated_at=CURRENT_TIMESTAMP WHERE id=? AND owner_pid IS ? AND status IN ('queued','running')",(row['id'],row['owner_pid']))
    return len(rows)>len(dead)


def reserve(kind,db_path=None):
    if recover_interrupted(db_path):raise JobConflict('Another ingestion job is active')
    jid=uuid.uuid4().hex
    import sqlite3
    try:
        with connect(db_path) as c:
            c.execute("INSERT INTO jobs(id,kind,status,owner_pid) VALUES (?,?,'queued',?)",(jid,kind,os.getpid()))
    except sqlite3.IntegrityError as exc: raise JobConflict('Another ingestion job is active') from exc
    return jid

def execute(jid,function,db_path=None,stop_event=None):
    stop_event=stop_event if stop_event is not None else threading.Event()
    try:
        if stop_event.is_set():raise JobCancelled('Cancelled before task start')
        with connect(db_path) as c:c.execute("UPDATE jobs SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",(jid,))
        def progress(value):
            with connect(db_path) as c:c.execute('UPDATE jobs SET progress_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(json.dumps(value),jid))
        outcome=function(JobContext(progress,stop_event))
        if not isinstance(outcome,JobOutcome):raise TypeError('Job task must return JobOutcome')
        outcome.cancelled=outcome.cancelled or stop_event.is_set()
        result=outcome.result
        if stop_event.is_set():result['cancellation_requested']=True
        status=outcome.status
        with connect(db_path) as c:
            c.execute('UPDATE jobs SET status=?,result_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,json.dumps(result),jid))
        return result
    except JobCancelled:
        result={'cancelled':True}
        with connect(db_path) as c:c.execute("UPDATE jobs SET status='cancelled',result_json=?,error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",(json.dumps(result),jid))
        return result
    except BaseException as exc:
        stop_event.set()
        status='interrupted' if isinstance(exc,(KeyboardInterrupt,SystemExit)) else 'failed'
        with connect(db_path) as c:c.execute("UPDATE jobs SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,f'{type(exc).__name__}: {exc}',jid))
        raise

def run_sync(kind,function,db_path=None):
    with configuration_lock(), operation_settings():
        db_path=str(Path(db_path or get_config()['database']['path']).resolve())
        jid,stop_event=_admit(kind,db_path)
        context=copy_context()
        handle=WorkerHandle(threading.current_thread(),stop_event,db_path,owns_thread=False)
        with _workers_lock:_workers[jid]=handle
    previous=None
    if threading.current_thread() is threading.main_thread():
        import signal
        previous=signal.signal(signal.SIGINT,lambda *_:stop_event.set())
    try:return context.run(execute,jid,function,db_path,stop_event)
    finally:
        if previous is not None:signal.signal(signal.SIGINT,previous)
        handle.done.set()
        with _workers_lock:_workers.pop(jid,None)


def submit(kind,function,db_path=None):
    with configuration_lock(), operation_settings():
        db_path=str(Path(db_path or get_config()['database']['path']).resolve())
        jid,stop_event=_admit(kind,db_path)
        context=copy_context()
        def worker():
            try:context.run(execute,jid,function,db_path,stop_event)
            except Exception:pass  # Persisted by execute, visible to the caller.
            finally:
                handle.done.set()
                with _workers_lock:_workers.pop(jid,None)
        thread=threading.Thread(target=worker,name='ingest-'+jid,daemon=False)
        handle=WorkerHandle(thread,stop_event,db_path)
        with _workers_lock:_workers[jid]=handle
        try:thread.start()
        except Exception as exc:
            with _workers_lock:_workers.pop(jid,None)
            with connect(db_path) as c:c.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",(f'Worker start failed: {exc}',jid))
            raise
    return {'job_id':jid,'status':'queued'}


def list_jobs(db_path=None):
    with connect(db_path) as c:rows=[dict(r) for r in c.execute('SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT 100')]
    for row in rows:
        for key in ('result','progress'):row[key]=json.loads(row.pop(key+'_json') or 'null')
    return rows
