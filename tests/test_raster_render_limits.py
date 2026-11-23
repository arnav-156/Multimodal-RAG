"""Real Pillow files verify raster vision admission before decoding/conversion."""
import io
from PIL import Image
import pytest
import config_loader as cl
import describer


def test_raster_over_pixel_limit_rejects_before_conversion(tmp_path,monkeypatch):
    source=tmp_path/'over.png';Image.new('RGB',(64,32)).save(source)
    cfg=cl.get_config();cfg['limits']['max_render_pixels']=1000
    def forbidden(*args,**kwargs):raise AssertionError('Oversized raster reached RGB conversion')
    monkeypatch.setattr(Image.Image,'convert',forbidden)
    with cl.operation_settings(cfg),pytest.raises(ValueError,match='pixel limit'):
        describer.render_file_to_images(str(source))


def test_raster_at_pixel_limit_renders_complete_png(tmp_path):
    source=tmp_path/'boundary.png';Image.new('RGB',(40,25),'blue').save(source)
    cfg=cl.get_config();cfg['limits']['max_render_pixels']=1000
    with cl.operation_settings(cfg):pages=describer.render_file_to_images(str(source))
    assert len(pages)==1
    with Image.open(io.BytesIO(pages[0])) as output:
        assert output.size==(40,25) and output.getpixel((0,0))==(0,0,255)


def test_render_failure_cause_is_stored_and_cleared_after_success(tmp_path,monkeypatch):
    from file_scanner import sync
    from database import connect
    import yaml
    source=tmp_path/'over.png';Image.new('RGB',(64,32)).save(source)
    cfg=cl.get_config();cfg['limits']['max_render_pixels']=1000
    cfg['database']['path']=str(tmp_path/'index.db');cfg['scanner']['scan_paths']=[str(tmp_path)]
    profile=tmp_path/'profile.yaml';profile.write_text(yaml.safe_dump(cfg),encoding='utf8')
    monkeypatch.setenv('RAG_CONFIG',str(profile));cl.reset_config_cache();cl.load_config(profile)
    try:
        sync()
        with connect() as c:row=c.execute("SELECT id,file_hash FROM files WHERE name='over.png'").fetchone()
        assert describer._process_file(row['id'],str(source),'fixture')==(False,'render_failed')
        with connect() as c:
            failed=c.execute('SELECT description,error FROM rag_descriptions WHERE file_id=?',(row['id'],)).fetchone()
        assert failed['description']=='' and failed['error']=='ValueError: Rendered image exceeds pixel limit'
        # Persistence transition only; no generated-content quality claim.
        assert describer._record(row['id'],'Persistence test text','fixture',expected_hash=row['file_hash'])
        with connect() as c:assert c.execute('SELECT error FROM rag_descriptions WHERE file_id=?',(row['id'],)).fetchone()[0] is None
    finally:cl.reset_config_cache()
