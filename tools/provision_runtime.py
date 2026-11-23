"""Install a checksum-pinned portable Python for local/CI verification; no system changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import tarfile
import urllib.request


def provision(destination,manifest_path,platform_key=None):
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf8'))
    key=platform_key or f'{platform.system()}-{platform.machine()}'
    spec=manifest['platforms'][key]
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Runtime destination already exists; choose a new directory')
    archive=destination.parent/Path(spec['url']).name
    archive.parent.mkdir(parents=True,exist_ok=True)
    if not archive.exists():
        with urllib.request.urlopen(spec['url'],timeout=120) as response,archive.open('xb') as stream:
            while chunk:=response.read(1024*1024):stream.write(chunk)
    if archive.stat().st_size!=spec['bytes'] or hashlib.sha256(archive.read_bytes()).hexdigest()!=spec['sha256']:
        raise ValueError('Runtime archive checksum/size mismatch; no extraction performed')
    # data filter rejects path traversal, external symlinks and device entries.
    with tarfile.open(archive,'r:gz') as bundle:bundle.extractall(destination,filter='data')
    executable=destination/spec['executable']
    if not executable.is_file():raise ValueError('Runtime archive did not contain the expected executable')
    if os.environ.get('GITHUB_PATH'):
        with open(os.environ['GITHUB_PATH'],'a',encoding='utf8') as stream:stream.write(str(executable.parent)+'\n')
    return {'platform':key,'archive_sha256':spec['sha256'],'executable':str(executable),'system_installation_changed':False}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',default='.runtime/ci-python');parser.add_argument('--manifest',default='PYTHON_DISTRIBUTIONS.json');parser.add_argument('--platform');parser.add_argument('--output',required=True);args=parser.parse_args()
    report=provision(args.destination,args.manifest,args.platform)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
