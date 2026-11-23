"""Canonical routing and real metadata no-op/worker acceptance."""
import json
from pathlib import Path
import pytest
import config_loader as config
import database
import metadata_extractor as metadata


@pytest.fixture
def selected_root(tmp_path,monkeypatch):
    cfg=config.validate_config({'scanner':{'scan_paths':[str(tmp_path)]}})
    monkeypatch.setattr(config,'get_config',lambda *a,**kw:cfg)
    monkeypatch.setattr(config,'get_live_config',lambda:cfg)
    return tmp_path


def test_two_txt_passes_never_start_a_child(tmp_path,monkeypatch,selected_root):
    db=tmp_path/'index.db';database.initialize(db)
    with database.connect(db) as conn:
        for i in range(12):
            p=tmp_path/f'{i}.txt';p.write_text('Generated note')
            conn.execute('INSERT INTO files(path,name,ext,mime_type) VALUES(?,?,?,?)',(str(p),p.name,'.txt','text/plain'))
    def forbidden(*args,**kwargs):raise AssertionError('TXT must be skipped before submitting a worker')
    monkeypatch.setattr(metadata,'_run_with_timeout',forbidden)
    for _ in range(2):
        result=metadata.extract_metadata(db_path=db,workers=1)
        assert result=={'total':12,'done':0,'errors':0,'skipped':12,'timeout':0,'cancelled':0,'stale':0,'details':[]}


def test_catalog_has_explicit_independent_capabilities():
    import format_capabilities as formats
    import extractors,describer
    assert extractors.classify_file is formats.classify_file
    for ext,cap in formats.FORMATS.items():
        assert cap['kind'] and cap['metadata'] in (None,'image','audio','video','pdf')
        assert cap['render'] in (None,'image','office','pdf','djvu')
        assert isinstance(cap['extract'],bool)
    for ext in ('.heic','.heif','.avif','.svg'):
        assert formats.classify_file(ext)=='image'
    assert formats.FORMATS['.svg']['render'] is None
    assert formats.FORMATS['.odt']['extract'] is False
    assert formats.metadata_kind('unknown.image','image/png')=='image'


def test_routing_exceptions_preserve_typescript_and_pdf(tmp_path):
    import format_capabilities as formats
    ts=tmp_path/'source.ts';ts.write_text('export const answer = 42')
    assert formats.classify_file('.TS',file_path=ts)=='code'
    ts.write_bytes(bytes([0x47])+bytes(187)+bytes([0x47])+bytes(187)+bytes([0x47]))
    assert formats.classify_file('.ts',file_path=ts)=='video'
    for meta,kind in [({'type':'text_pdf'},'pdf_text'),({'type':'hybrid_pdf'},'pdf_hybrid'),({},'pdf_image')]:
        assert formats.classify_file('.pdf',json.dumps(meta))==kind
    assert formats.classify_file('.pdf','bad json')=='pdf_image'


def test_recognized_missing_parser_is_error_not_skip(monkeypatch,tmp_path):
    monkeypatch.setattr(metadata,'Image',None)
    result=metadata._inspect_file((1,str(tmp_path/'missing.png'),'image/png'))
    assert result[2]=='error' and 'parser' in result[3]


def test_nonrenderable_pending_rows_cannot_starve_images(tmp_path,selected_root):
    import describer
    db=tmp_path/'index.db';database.initialize(db)
    with database.connect(db) as conn:
        for name in ['a.txt','b.svg','c.png']:
            fid=conn.execute('INSERT INTO files(path,name,ext) VALUES(?,?,?)',(str(tmp_path/name),name,Path(name).suffix)).lastrowid
            conn.execute("INSERT INTO rag_ocr_status(file_id,status) VALUES(?,'insufficient')",(fid,))
    rows=describer.get_ocr_failed_files(limit=1,db_path=db)
    assert len(rows)==1 and rows[0][1].endswith('c.png')


def test_search_does_not_load_extractors():
    # A fresh process tests the function's lazy imports before inference.
    import subprocess,sys
    code = "import search_numpy; import sys\ntry: search_numpy.search_documents('')\nexcept ValueError: pass\nassert 'extractors' not in sys.modules"
    run=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
