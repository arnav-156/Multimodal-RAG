"""Report actual interpreter/SQLite provenance; reject known-unpatched WAL runtimes."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import sys


def sqlite_wal_fix_present(version):
    """Upstream fix: 3.51.3+, with maintained 3.44.6/3.50.7 backports."""
    version=tuple(version)
    return version >= (3,51,3) or (version[:2]==(3,44) and version[2]>=6) or (version[:2]==(3,50) and version[2]>=7)


def runtime_provenance():
    with sqlite3.connect(':memory:') as connection:
        source_id=connection.execute('SELECT sqlite_source_id()').fetchone()[0]
    executable=Path(sys.executable).resolve()
    return {'executable':str(executable),'python_version':platform.python_version(),
        'implementation':platform.python_implementation(),'platform':platform.platform(),
        'machine':platform.machine(),'base_prefix':sys.base_prefix,
        'sqlite_version':sqlite3.sqlite_version,'sqlite_source_id':source_id,
        'sqlite_wal_fix_present':sqlite_wal_fix_present(sqlite3.sqlite_version_info),
        'python_executable_sha256':hashlib.sha256(executable.read_bytes()).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();report=runtime_provenance()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,indent=2))
    if not report['sqlite_wal_fix_present']:
        print('This interpreter has no verified upstream SQLite WAL-reset fix; use the documented project-local runtime.',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
