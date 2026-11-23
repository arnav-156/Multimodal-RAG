"""Real PDF routing and limits; model output is substituted only in unit tests."""
import io
import fitz
from PIL import Image
import pytest
import extractors


def pdf(tmp_path, raster=True):
    path = tmp_path/'mixed.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=500,height=400)
        page.insert_text((30,40),'Native header')
        if raster:
            image = Image.new('RGB',(100,100),'white')
            stream = io.BytesIO()
            image.save(stream,format='PNG')
            page.insert_image(fitz.Rect(20,100,480,350),stream=stream.getvalue())
        doc.save(path)
    return path


@pytest.mark.parametrize('raster,enabled,calls',[(False,True,0),(True,False,0),(True,True,1)])
def test_native_header_does_not_suppress_enabled_raster_ocr(tmp_path,monkeypatch,raster,enabled,calls):
    seen = []
    monkeypatch.setattr(extractors,'get_config',lambda:{'ocr':{'enabled':enabled}})
    monkeypatch.setattr(extractors,'_ocr_image',lambda path:seen.append(path) or 'Never delete the sole backup.')
    chunks = extractors._extract_pdf_pages(1,str(pdf(tmp_path,raster)))
    text = '\n'.join(c.text for c in chunks)
    assert 'Native header' in text and len(seen) == calls
    assert ('Never delete' in text) == bool(calls)
    assert all(c.meta['page'] == 1 and c.meta['raster_ocr_skipped'] == (raster and not enabled) for c in chunks)


def test_mixed_page_obeys_render_budget_before_ocr(tmp_path,monkeypatch):
    monkeypatch.setattr(extractors,'get_config',lambda:{'ocr':{'enabled':True}})
    monkeypatch.setattr(extractors,'get_limits_config',lambda:{'max_pdf_pages':100,'max_render_pixels':1000})
    monkeypatch.setattr(extractors,'_ocr_image',lambda path:pytest.fail('OCR called beyond render budget'))
    with pytest.raises(extractors.ExtractionError,match='pixel limit'):
        extractors._extract_pdf_pages(1,str(pdf(tmp_path)))


def test_mixed_page_does_not_hide_ocr_service_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(extractors,'get_config',lambda:{'ocr':{'enabled':True}})
    def unavailable(path):
        raise extractors.ExtractionError('OCR unavailable')
    monkeypatch.setattr(extractors,'_ocr_image',unavailable)
    with pytest.raises(extractors.ExtractionError,match='OCR unavailable'):
        extractors._extract_pdf_pages(1,str(pdf(tmp_path)))
