"""Assert every exact lock pin against the actually installed distribution metadata."""
import argparse
from importlib import metadata
import json
from pathlib import Path
import re


def verify(lock):
    pins={}
    for line in Path(lock).read_text(encoding='utf8').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        name,version=line.split('==');pins[re.sub('[-_.]+','-',name).lower()]=version
    installed={re.sub('[-_.]+','-',d.metadata['Name']).lower():d.version for d in metadata.distributions()}
    mismatch={name:{'expected':version,'actual':installed.get(name)} for name,version in pins.items() if installed.get(name)!=version}
    return {'locked_packages':len(pins),'mismatches':mismatch,'matches':not mismatch,'installed':installed}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lock',default='requirements.lock');parser.add_argument('--output',required=True);args=parser.parse_args();report=verify(args.lock)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8');print(json.dumps(report,indent=2));return 0 if report['matches'] else 1

if __name__=='__main__':raise SystemExit(main())
