"""OCR transport boundary tests; numerical OCR acceptance is recorded separately."""
import base64
import json
from pathlib import Path
import pytest
import requests
import config_loader as cl


def test_client_sends_bounded_bytes_without_shared_temp_files(tmp_path,monkeypatch):
    import extractors,security
    image=tmp_path/'image.png';image.write_bytes(b'owned protocol fixture')
    seen=[]
    class Session:
        trust_env=True
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            seen.append(kwargs['json'])
            return type('Response',(),{'raise_for_status':lambda _:None,'json':lambda _:{'results':['PROTOCOL SUBSTITUTE']}})()
    monkeypatch.setattr(requests,'Session',Session)
    monkeypatch.setattr(security,'get_api_token',lambda:'owned-test-token')
    cfg=cl.validate_config({'ocr':{'enabled':True,'temp_dir':str(tmp_path/'staging'),'max_request_bytes':1024}})
    with cl.operation_settings(cfg):
        assert extractors._run_ocr_on_images([str(image)])==['PROTOCOL SUBSTITUTE']
    assert 'images' not in seen[0] and base64.b64decode(seen[0]['image_data'][0])==image.read_bytes()
    assert not (tmp_path/'staging').exists()


def test_inline_input_validation_rejects_invalid_and_over_budget(tmp_path):
    import ocr_server
    cfg=cl.validate_config({'limits':{'max_image_bytes':3}})
    assert ocr_server.validate_image_data([base64.b64encode(b'abc').decode()],cfg)==[b'abc']
    for value in ['bad!',base64.b64encode(b'abcd').decode()]:
        with pytest.raises(ValueError):ocr_server.validate_image_data([value],cfg)
    with pytest.raises(ValueError):ocr_server.validate_image_data('not a list',cfg)


def test_byte_input_decodes_to_bgr_without_file_handles(monkeypatch):
    import io,ocr_server
    from PIL import Image
    class Engine:
        def predict(self,input):
            assert input.shape==(1,2,3) and input[0,0].tolist()==[0,0,255]
            return [{'rec_texts':['PROTOCOL SUBSTITUTE']}]
    monkeypatch.setattr(ocr_server,'_ocr',Engine())
    data=io.BytesIO();Image.new('RGB',(2,1),'red').save(data,format='PNG')
    assert ocr_server.ocr_image_bytes(data.getvalue())=='PROTOCOL SUBSTITUTE'
