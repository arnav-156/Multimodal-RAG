"""Actual process/pipe ownership. No converter/model response substitutes."""
from pathlib import Path
import sys,os,json,time,subprocess
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from office_process import run_office_process
from jobs import process_alive


def test_owned_command_preserves_stdout_stderr_and_exit_status():
    result=run_office_process([sys._base_executable,'-c',"import sys;print('owned output');print('diagnostic',file=sys.stderr)"])
    assert result.returncode==0 and result.stdout.strip()==b'owned output' and result.stderr.strip()==b'diagnostic'


def test_owned_command_returns_failure_for_the_caller_to_check():
    result=run_office_process([sys._base_executable,'-c','import sys;sys.exit(7)'])
    assert result.returncode==7
    with pytest.raises(subprocess.CalledProcessError):result.check_returncode()


def terminated(pid):
    if os.name=='posix':
        # A container PID1 test process may adopt the exited grandchild.
        try:
            if os.waitpid(pid,os.WNOHANG)[0]:return True
        except ChildProcessError:pass
        status=Path(f'/proc/{pid}/stat')
        try:
            if status.read_text().split(') ',1)[1].startswith('Z'):return True
        except FileNotFoundError:return True
    return not process_alive(pid)


def test_timeout_terminates_the_descendant_holding_output_pipes(tmp_path):
    marker=tmp_path/'owned-child.json'
    code="""import subprocess,sys,time,json
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(5)'])
Path(sys.argv[1]).write_text(json.dumps({'pid':child.pid}))
time.sleep(5)
"""
    pid=None
    bystander=subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(10)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        with pytest.raises(subprocess.TimeoutExpired):run_office_process([sys._base_executable,'-c',code,str(marker)],timeout=1.5)
        assert marker.exists(),'The real descendant must have started before the deadline'
        pid=json.loads(marker.read_text())['pid']
        end=time.monotonic()+.3
        while not terminated(pid) and time.monotonic()<end:time.sleep(.01)
        assert terminated(pid),'An owned descendant still runs after the command deadline'
        assert bystander.poll() is None,'Cleanup must not terminate a separate process'
    finally:
        # If a regression leaves it alive, this bounded fixture exits itself;
        # do not kill a PID that could have been reused by an unrelated process.
        if pid is None and marker.exists():pid=json.loads(marker.read_text())['pid']
        end=time.monotonic()+6
        while pid is not None and not terminated(pid) and time.monotonic()<end:time.sleep(.02)
        if bystander.poll() is None:bystander.terminate()
        bystander.wait(timeout=5)
