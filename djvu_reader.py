"""Bounded native DjVu page metadata/rendering shared by OCR and vision."""
from pathlib import Path
import re
import subprocess


def page_count(path,maximum):
    result=subprocess.run(['djvused','-e','n',str(path)],check=True,capture_output=True,text=True,encoding='utf8',timeout=30)
    count=int(result.stdout.strip())
    if not 0<count<=maximum:raise ValueError(f'DjVu has {count} pages; configured limit is {maximum}')
    return count


def render_page(path,page,temporary,max_pixels):
    result=subprocess.run(['djvused','-e',f'select {page}; size',str(path)],check=True,capture_output=True,text=True,encoding='utf8',timeout=30)
    dimensions=re.search(r'width=(\d+)\s+height=(\d+)',result.stdout)
    if dimensions is None:raise ValueError('DjVu page dimensions are unavailable')
    width,height=map(int,dimensions.groups())
    if width<1 or height<1 or width*height>max_pixels:raise ValueError('Rendered DjVu page exceeds pixel limit')
    ppm=Path(temporary)/'page.ppm';png=Path(temporary)/'page.png'
    # ddjvu supports PPM, not PNG or -resolution. Pin output dimensions before
    # decoding; native pixel size is part of the declared generation policy.
    subprocess.run(['ddjvu',f'-page={page}','-format=ppm',f'-size={width}x{height}','-aspect=no',str(path),str(ppm)],check=True,capture_output=True,timeout=30)
    from PIL import Image
    with Image.open(ppm) as image:
        if image.size!=(width,height):raise ValueError('DjVu renderer returned unexpected dimensions')
        image.convert('RGB').save(png,format='PNG')
    ppm.unlink()
    return png
