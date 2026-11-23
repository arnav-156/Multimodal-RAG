"""Static regression for native semantics; focus/reflow are also checked in browser."""
from html.parser import HTMLParser
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]/'static'
class Elements(HTMLParser):
    def __init__(self,text):super().__init__();self.nodes=[];self.feed(text)
    def handle_starttag(self,tag,attrs):self.nodes.append((tag,dict(attrs)))

@pytest.mark.parametrize('page,current',[('index.html','/'),('config.html','/config')])
def test_routes_have_skip_target_named_navigation_and_current_page(page,current):
    nodes=Elements((ROOT/page).read_text(encoding='utf8')).nodes
    assert any(tag=='a' and a.get('href')=='#main' for tag,a in nodes)
    assert any(tag=='main' and a.get('id')=='main' and a.get('tabindex')=='-1' for tag,a in nodes)
    assert any(tag=='nav' and a.get('aria-label') for tag,a in nodes)
    assert [a['href'] for tag,a in nodes if tag=='a' and a.get('aria-current')=='page']==[current]

def test_native_preview_is_named_and_initial_focus_is_before_its_long_text():
    nodes=Elements((ROOT/'index.html').read_text(encoding='utf8')).nodes
    dialog=next(a for tag,a in nodes if tag=='dialog')
    title=next(a for tag,a in nodes if a.get('id')==dialog.get('aria-labelledby'))
    assert title.get('tabindex')=='-1' and 'autofocus' in title
    preview=next(a for tag,a in nodes if a.get('id')=='previewText')
    assert preview.get('tabindex')=='0' and preview.get('role')=='region' and preview.get('aria-label')
