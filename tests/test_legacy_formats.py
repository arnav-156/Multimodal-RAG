"""Structured format failures must not become successful raw-text extraction."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import extractors
from ebooklib import epub

@pytest.mark.parametrize('ext',['.doc','.ppt','.mobi','.fb2','.rtf'])
def test_corrupt_legacy_container_never_indexes_raw_text(tmp_path,ext):
    source=tmp_path/('corrupt'+ext)
    source.write_text('This is ordinary readable text pretending to be a structured document. '*20,encoding='utf8')
    outcome=extractors.extract_outcome(1,str(source),ext,extractors.classify_file(ext),file_size=source.stat().st_size)
    assert outcome.error and outcome.status=='error' and outcome.chunks==[]


def make_epub(path,with_image=False):
    book=epub.EpubBook();book.set_identifier('owned-legacy-fixture');book.set_title('Reading order');book.set_language('en')
    chapters=[]
    for number in (2,1):
        chapter=epub.EpubHtml(title=f'Chapter {number}',file_name=f'chapter-{number}.xhtml',lang='en')
        chapter.content=f'<h1>STEP {number} MARKER</h1><p>'+('Verify the backup and record the inspection result. '*20)+'</p>'
        if with_image:chapter.content='<p>Short.</p><img src="image.png"/>'
        book.add_item(chapter);chapters.append(chapter)
    if with_image:
        import io
        from PIL import Image
        image=io.BytesIO();Image.new('RGB',(20,20),'white').save(image,format='PNG')
        book.add_item(epub.EpubItem(uid='image',file_name='image.png',media_type='image/png',content=image.getvalue()))
    book.spine=list(reversed(chapters));book.add_item(epub.EpubNcx());book.add_item(epub.EpubNav());epub.write_epub(path,book)


def test_epub_follows_reading_spine_not_zip_insertion_order(tmp_path):
    source=tmp_path/'ordered.epub';make_epub(source)
    text,_=extractors._extract_epub_content(str(source),str(tmp_path))
    assert text.index('STEP 1 MARKER')<text.index('STEP 2 MARKER')


def test_ebook_ocr_exception_removes_only_its_owned_directory(tmp_path,monkeypatch):
    source=tmp_path/'short.epub';make_epub(source,with_image=True);adjacent=tmp_path/'adjacent.txt';adjacent.write_text('preserve',encoding='utf8')
    original=extractors.tempfile.TemporaryDirectory;owned=[]
    def observe(*args,**kwargs):
        temporary=original(*args,**kwargs);owned.append(Path(temporary.name));return temporary
    monkeypatch.setattr(extractors.tempfile,'TemporaryDirectory',observe)
    def fail(*args,**kwargs):raise extractors.ExtractionError('Controlled OCR service failure; not quality evidence')
    monkeypatch.setattr(extractors,'_ocr_images_batch',fail)
    import config_loader as cl
    cfg=cl.get_config();cfg['chunking']['min_text_chars']=1000
    with cl.operation_settings(cfg):outcome=extractors.extract_outcome(1,str(source),'.epub','ebook')
    assert outcome.error and not outcome.chunks and owned and all(not path.exists() for path in owned)
    assert adjacent.read_text(encoding='utf8')=='preserve'


def test_basic_rtf_preserves_paragraph_boundaries(tmp_path):
    source=tmp_path/'plain.rtf';source.write_text('{\\rtf1\\ansi '+('First instruction.\\par Second instruction.\\par '*20)+'}',encoding='ascii')
    outcome=extractors.extract_outcome(1,str(source),'.rtf','office')
    assert outcome.status=='done'
    assert any('First instruction.\nSecond instruction.' in chunk.text for chunk in outcome.chunks)


@pytest.mark.parametrize('encoded',[r'\u20013?',r"\'e9"])
def test_encoded_rtf_fails_explicitly_instead_of_losing_characters(tmp_path,encoded):
    source=tmp_path/'encoded.rtf';source.write_text('{\\rtf1\\ansi '+('A meaningful sentence '+encoded+' end. '*10)*10+'}',encoding='ascii')
    outcome=extractors.extract_outcome(1,str(source),'.rtf','office')
    assert outcome.error and not outcome.chunks and 'conversion' in str(outcome.error)


def djvu_commands(monkeypatch,second_empty=False,large=False):
    """CLI/ownership substitutes; actual native acceptance is recorded separately."""
    from types import SimpleNamespace
    from PIL import Image
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        if args[0]=='djvused':
            value='width=10000 height=10000' if large else 'width=64 height=32'
            return SimpleNamespace(returncode=0,stdout='2' if args[2]=='n' else value)
        if args[0]=='djvutxt':
            empty=second_empty and '-page=2' in args
            return SimpleNamespace(returncode=0,stdout='' if empty else 'A real page must remain visible in the complete output. '*20)
        if args[0]=='ddjvu':
            if '-format=png' in args or any(arg.startswith('-resolution=') for arg in args):
                raise extractors.subprocess.CalledProcessError(10,args)
            Image.new('RGB',(64,32),'white').save(args[-1],format='PPM')
            return SimpleNamespace(returncode=0,stdout=b'')
        raise AssertionError(args)
    monkeypatch.setattr(extractors.subprocess,'run',run)
    return calls


def test_djvu_page_limit_cannot_be_bypassed_by_text(tmp_path,monkeypatch):
    import config_loader as cl
    source=tmp_path/'book.djvu';source.write_bytes(b'CLI PROTOCOL SUBSTITUTE');djvu_commands(monkeypatch)
    cfg=cl.get_config();cfg['limits']['max_djvu_pages']=1
    with cl.operation_settings(cfg):outcome=extractors.extract_outcome(1,str(source),'.djvu','djvu')
    assert outcome.error and not outcome.chunks and 'limit' in str(outcome.error).lower()


def test_djvu_missing_page_rejects_partial_document(tmp_path,monkeypatch):
    source=tmp_path/'book.djvu';source.write_bytes(b'CLI PROTOCOL SUBSTITUTE');djvu_commands(monkeypatch,second_empty=True)
    monkeypatch.setattr(extractors,'_ocr_image',lambda *args:'')
    outcome=extractors.extract_outcome(1,str(source),'.djvu','djvu')
    assert outcome.error and not outcome.chunks and outcome.recovery_eligible


def test_djvu_renderer_uses_supported_native_format_and_emits_png(tmp_path,monkeypatch):
    import describer
    source=tmp_path/'book.djvu';source.write_bytes(b'CLI PROTOCOL SUBSTITUTE');djvu_commands(monkeypatch)
    pages=list(describer.render_djvu_to_images(str(source)))
    assert len(pages)==2 and all(page.startswith(b'\x89PNG\r\n\x1a\n') for page in pages)


def test_djvu_pixel_limit_precedes_decode(tmp_path,monkeypatch):
    import config_loader as cl
    import describer
    source=tmp_path/'book.djvu';source.write_bytes(b'CLI PROTOCOL SUBSTITUTE');calls=djvu_commands(monkeypatch,large=True)
    cfg=cl.get_config();cfg['limits']['max_render_pixels']=1000
    with cl.operation_settings(cfg),pytest.raises(ValueError,match='pixel'):
        list(describer.render_djvu_to_images(str(source)))
    assert all(args[0]!='ddjvu' for args in calls)


@pytest.mark.parametrize('ext',['.zip','.tar','.gz','.bz2','.xz','.7z','.rar'])
def test_broken_archive_cannot_succeed_with_context_only(tmp_path,ext):
    source=tmp_path/('broken'+ext);source.write_bytes(b'Not a valid archive. '*100)
    outcome=extractors.extract_outcome(1,str(source),ext,'archive')
    assert outcome.error and not outcome.chunks and outcome.status=='error'


@pytest.mark.parametrize('ext',['.gz','.bz2','.xz'])
def test_complete_compressed_text_has_a_hard_expansion_limit(tmp_path,ext):
    import gzip,bz2,lzma,config_loader as cl
    source=tmp_path/('notes'+ext);compress={'.gz':gzip.compress,'.bz2':bz2.compress,'.xz':lzma.compress}[ext]
    source.write_bytes(compress(b'This text has useful content. '*100))
    cfg=cl.get_config();cfg['limits']['max_archive_uncompressed_bytes']=100
    with cl.operation_settings(cfg):outcome=extractors.extract_outcome(1,str(source),ext,'archive')
    assert outcome.error and not outcome.chunks and 'limit' in str(outcome.error).lower()


@pytest.mark.parametrize('wrapper',['<FictionBook><body><section><p>{text}','<html><body><p>{text}</p></body></html>'])
def test_fb2_rejects_broken_xml_or_wrong_container(tmp_path,wrapper):
    source=tmp_path/'broken.fb2';source.write_text(wrapper.format(text='A meaningful document sentence. '*30),encoding='utf8')
    outcome=extractors.extract_outcome(1,str(source),'.fb2','ebook')
    assert outcome.error and not outcome.chunks


def test_xlsx_reader_failure_closes_book_and_rejects_partial_rows(tmp_path,monkeypatch):
    from openpyxl import Workbook
    import openpyxl
    source=tmp_path/'partial.xlsx';actual=Workbook();actual.save(source);actual.close()
    class Sheet:
        title='First'
        def iter_rows(self,**kwargs):
            yield ['A meaningful instruction that must not be partially committed. '*30]
            raise ValueError('Controlled failure after a successfully read row')
    class Book:
        worksheets=[Sheet()]
        closed=False
        def close(self):self.closed=True
    book=Book();monkeypatch.setattr(openpyxl,'load_workbook',lambda *args,**kwargs:book)
    outcome=extractors.extract_outcome(1,str(source),'.xlsx','office')
    assert outcome.error and not outcome.chunks and book.closed


@pytest.mark.parametrize('ext,expected',[('.doc','MS Word 97'),('.ppt','MS PowerPoint 97'),('.xls','MS Excel 97')])
def test_office_vision_converter_cannot_autodetect_plaintext(tmp_path,monkeypatch,ext,expected):
    """CLI contract only; real valid/truncated Word evidence is in T18."""
    import describer
    source=tmp_path/('source'+ext);source.write_bytes(b'DECLARED CONVERTER INPUT SUBSTITUTE');calls=[]
    def run(args,**kwargs):
        calls.append(args);output=Path(args[args.index('--outdir')+1]);(output/'converted.pdf').write_bytes(b'PDF OUTPUT SUBSTITUTE')
        return extractors.subprocess.CompletedProcess(args,0,b'',b'')
    monkeypatch.setattr(describer,'run_office_process',run)
    monkeypatch.setattr(describer,'render_pdf_to_images',lambda path:[b'PNG OUTPUT SUBSTITUTE'])
    assert list(describer.render_office_to_images(str(source)))
    assert '--infilter='+expected in calls[0]
