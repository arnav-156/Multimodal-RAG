"""Explicit local OCR identity; fake constructor tests wiring, not recognition."""
import sys,types
import pytest
import yaml
import ocr_server


def settings(tmp_path):
    for kind,name in [('det','PP-OCRv5_mobile_det'),('rec','PP-OCRv5_mobile_rec')]:
        folder=tmp_path/kind;folder.mkdir()
        (folder/'inference.yml').write_text(yaml.safe_dump({'Global':{'model_name':name}}),encoding='utf8')
    return {'ocr':{'model_dir':str(tmp_path),'device':'cpu','lang':'ch','cpu_threads':2}}


def test_local_asset_names_and_offline_policy_reach_constructor(tmp_path,monkeypatch):
    cfg=settings(tmp_path);seen={}
    monkeypatch.setattr(ocr_server,'get_config',lambda:cfg)
    def constructor(**kwargs):
        import os
        seen.update(kwargs)
        assert os.environ['HF_HUB_OFFLINE']=='1'
        assert os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']=='True'
        return object()
    monkeypatch.setitem(sys.modules,'paddleocr',types.SimpleNamespace(PaddleOCR=constructor))
    monkeypatch.setattr(ocr_server,'_ocr',None)
    info=ocr_server.init_worker()
    assert seen['text_detection_model_name']=='PP-OCRv5_mobile_det'
    assert seen['text_recognition_model_name']=='PP-OCRv5_mobile_rec'
    assert seen['cpu_threads']==2 and 'lang' not in seen
    assert info['configured_language']=='ch' and info['language_source']=='local recognition checkpoint'


def test_missing_identity_fails_before_optional_engine_import(tmp_path,monkeypatch):
    cfg=settings(tmp_path);(tmp_path/'rec/inference.yml').unlink()
    monkeypatch.setattr(ocr_server,'get_config',lambda:cfg)
    monkeypatch.setattr(ocr_server,'_ocr',None)
    with pytest.raises(RuntimeError,match='inference.yml|identity'):
        ocr_server.init_worker()
    assert ocr_server._ocr is None
