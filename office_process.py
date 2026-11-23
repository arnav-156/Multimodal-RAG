"""Own blocking Office converter commands, including their process tree."""
import os
from pathlib import Path
import signal
import subprocess


def run_office_process(args,timeout=60):
    windows=os.name=='nt'
    process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP) if windows else 0,
        start_new_session=not windows)
    try:
        stdout,stderr=process.communicate(timeout=timeout)
    except BaseException:
        try:
            if windows:
                taskkill=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/taskkill.exe'
                result=subprocess.run([str(taskkill),'/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=5,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode and process.poll() is None:result.check_returncode()
            else:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
        finally:
            if process.poll() is None:process.kill()
            # Once writers in the owned tree are gone, inherited pipes can
            # finish. A launcher-only kill can otherwise wait indefinitely.
            process.communicate(timeout=5)
        raise
    return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
