"""T05 actual pinned graph and archive validation, with real files."""
from pathlib import Path
import hashlib
import io
import json
import tarfile
import pytest
from tools.provision_runtime import provision
from tools.verify_locked_environment import verify


def archive_manifest(tmp_path,member='python/python.exe'):
    archive=tmp_path/'runtime.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        info=tarfile.TarInfo(member);info.size=7;t.addfile(info,io.BytesIO(b'fixture'))
    spec={'url':'https://example.invalid/runtime.tar.gz','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'bytes':archive.stat().st_size,'executable':'python/python.exe'}
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'platforms':{'test':spec}}),encoding='utf8')
    return manifest,spec


def test_bad_download_never_extracts(tmp_path):
    manifest,spec=archive_manifest(tmp_path)
    spec['sha256']='0'*64;manifest.write_text(json.dumps({'platforms':{'test':spec}}),encoding='utf8')
    target=tmp_path/'runtime'
    with pytest.raises(ValueError,match='checksum'):provision(target,manifest,'test')
    assert not target.exists()


def test_archive_traversal_rejected(tmp_path):
    manifest,_=archive_manifest(tmp_path,'../escape')
    with pytest.raises(tarfile.OutsideDestinationError):provision(tmp_path/'runtime',manifest,'test')
    assert not (tmp_path/'escape').exists()


def test_actual_locked_graph_matches():
    report=verify(Path(__file__).resolve().parents[1]/'requirements.lock')
    assert report['matches'],report['mismatches']
    assert report['locked_packages']==40
