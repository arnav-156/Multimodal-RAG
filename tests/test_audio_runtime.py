"""Audio admission/runtime selection regressions; subprocess substitutes only."""
from pathlib import Path
import json,sys,subprocess
import pytest
import config_loader as cl
import extractors


def test_explicit_audio_runtime_is_used_for_owned_child(tmp_path,monkeypatch):
    model=tmp_path/'model';model.mkdir();python=tmp_path/'python.exe';python.write_bytes(b'fixture executable')
    source=tmp_path/'clip.wav';source.write_bytes(b'fixture waveform')
    cfg=cl.validate_config({'audio':{'model_path':str(model),'python_executable':str(python),'cpu_threads':2}})
    monkeypatch.setattr(extractors,'get_config',lambda:cfg)
    monkeypatch.setattr(extractors,'_get_audio_duration',lambda _:1)
    monkeypatch.setattr(extractors,'_transcribe_with_whisper_cpp',lambda _:None)
    calls=[]
    def run(command,**kw):
        calls.append((command,kw));return type('Result',(),{'stdout':'{"text":"CONTROLLED transcript","language":"en"}'})()
    monkeypatch.setattr(extractors.subprocess,'run',run)
    assert extractors._extract_audio(1,str(source))
    assert calls[0][0][0]==str(python) and 'local_files_only=True' in calls[0][0][2]
    assert calls[0][1]['env']['HF_HUB_OFFLINE']=='1' and calls[0][1]['timeout']>0


@pytest.mark.parametrize('output',['nan','inf','-1','0','not-a-duration'])
def test_unverifiable_duration_never_bypasses_admission(output,monkeypatch):
    monkeypatch.setattr(extractors.subprocess,'run',lambda *a,**kw:type('Result',(),{'returncode':0,'stdout':output})())
    with pytest.raises(extractors.ExtractionError,match='duration'):
        extractors._get_audio_duration('owned-fixture.wav')


def test_explicit_cpp_failure_is_not_silent_fallback(tmp_path,monkeypatch):
    binary=tmp_path/'whisper-cli.exe';binary.write_bytes(b'fixture executable')
    model=tmp_path/'model.bin';model.write_bytes(b'fixture model')
    cfg=cl.validate_config({'audio':{'cpp_executable':str(binary),'cpp_model_path':str(model)}})
    monkeypatch.setattr(extractors,'get_config',lambda:cfg)
    monkeypatch.setattr(extractors.subprocess,'run',lambda *a,**kw:type('Result',(),{'returncode':1})())
    with pytest.raises(extractors.ExtractionError,match='whisper.cpp'):
        extractors._transcribe_with_whisper_cpp('owned-fixture.wav')
