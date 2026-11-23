"""Owned subprocess output contract; no Whisper executable or model is run."""
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import extractors

@pytest.fixture
def whisper_fixture(tmp_path,monkeypatch):
    audio=tmp_path/'recording with spaces.wav';audio.write_bytes(b'generated test audio')
    adjacent=Path(str(audio)+'.txt');adjacent.write_text('PREEXISTING USER NOTE',encoding='utf8')
    model=tmp_path/'fixture-model.bin';model.write_bytes(b'not a real model')
    monkeypatch.setattr(extractors,'_init_whisper_cpp',lambda:'fixture-only-whisper-cli')
    monkeypatch.setattr(extractors.os.path,'expanduser',lambda value:str(model))
    monkeypatch.setattr(extractors,'get_config',lambda:{'audio':{'timeout':17}})
    return audio,adjacent

def test_adjacent_user_note_is_never_consumed_or_deleted(whisper_fixture,monkeypatch):
    audio,adjacent=whisper_fixture
    monkeypatch.setattr(extractors.subprocess,'run',lambda *args,**kw:SimpleNamespace(returncode=0))
    result=extractors._transcribe_with_whisper_cpp(str(audio))
    assert adjacent.exists(),'Adapter deleted an input-adjacent file it did not create'
    assert adjacent.read_text(encoding='utf8')=='PREEXISTING USER NOTE'
    assert result is None,'Missing generated output must not consume an existing adjacent note'

@pytest.mark.parametrize('mode',['success','nonzero','timeout'])
def test_owned_output_and_cleanup_on_every_exit(whisper_fixture,monkeypatch,mode):
    audio,adjacent=whisper_fixture;calls=[];directories=[]
    def subprocess_substitute(args,**kwargs):
        calls.append((args,kwargs))
        if '-of' not in args:return SimpleNamespace(returncode=0)
        base=Path(args[args.index('-of')+1]);directories.append(base.parent)
        Path(str(base)+'.txt').write_text('FRESH GENERATED TRANSCRIPT',encoding='utf8')
        if mode=='timeout':raise subprocess.TimeoutExpired('fixture',17)
        return SimpleNamespace(returncode=1 if mode=='nonzero' else 0)
    monkeypatch.setattr(extractors.subprocess,'run',subprocess_substitute)
    result=extractors._transcribe_with_whisper_cpp(str(audio))
    assert '-of' in calls[0][0],'Output basename must belong to an owned directory'
    assert calls[0][1]['timeout']==17
    assert directories and all(not directory.exists() for directory in directories)
    assert adjacent.read_text(encoding='utf8')=='PREEXISTING USER NOTE'
    assert result==('FRESH GENERATED TRANSCRIPT','unknown') if mode=='success' else result is None
