"""Fit mixed PDF pages onto one PowerPoint canvas without crop or stretching."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import fitz


@contextmanager
def slide_reference(source: Path):
    with fitz.open(source) as original:
        sizes=[(page.rect.width,page.rect.height) for page in original]
        if not sizes:
            raise ValueError('PDF contains no pages.')
        if all(abs(w-sizes[0][0])<.5 and abs(h-sizes[0][1])<.5 for w,h in sizes):
            yield source
            return
        width=max(w for w,h in sizes);height=max(h for w,h in sizes)
        if width>4032 or height>4032:
            # PowerPoint's 56-inch slide limit. A proportional global scale is
            # applied to every page rather than changing a page's aspect ratio.
            factor=4032/max(width,height);width*=factor;height*=factor
        transforms=[]
        for w,h in sizes:
            scale=min(width/w,height/h)
            transforms.append(fitz.Matrix(scale,0,0,scale,(width-w*scale)/2,(height-h*scale)/2))
        with TemporaryDirectory(prefix='pdf-slide-canvas-') as work:
            output=Path(work)/'canvas.pdf'
            normalized=fitz.open()
            try:
                for index,page in enumerate(original):
                    target=normalized.new_page(width=width,height=height)
                    target.show_pdf_page(page.rect*transforms[index],original,index,keep_proportion=True)
                for index,page in enumerate(original):
                    for link in page.get_links():
                        if link.get('kind') not in (fitz.LINK_URI,fitz.LINK_GOTO):continue
                        data={k:v for k,v in link.items() if k not in ('xref','id')}
                        data['from']=fitz.Rect(link['from'])*transforms[index]
                        if data.get('kind')==fitz.LINK_GOTO and 0<=data.get('page',-1)<len(transforms):
                            data['to']=fitz.Point(data.get('to',(0,0)))*transforms[data['page']]
                        normalized[index].insert_link(data)
                normalized.save(output,garbage=4,deflate=True)
            finally:
                normalized.close()
            yield output
