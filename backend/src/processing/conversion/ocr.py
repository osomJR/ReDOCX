"""Multilingual OCR with geometric consensus, orientation and visible text.

Recognition is probabilistic. Conflicting readings are rejected rather than
quietly certified as correct. Models are versioned and installed at image build.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from html import escape
from io import BytesIO
from pathlib import Path
import os
import re
import statistics
import subprocess
import unicodedata

import cv2
import fitz
import numpy as np
import pytesseract
from PIL import Image, ImageOps

from . import quality


@dataclass(frozen=True)
class Word:
    text: str
    bbox: tuple[float,float,float,float]
    confidence: float
    model: str
    line: tuple = ()


@dataclass
class Recognition:
    image: Image.Image
    words: list[Word]
    script: str
    languages: tuple[str,...]
    rotation: int = 0

    @property
    def text(self):
        return ' '.join(word.text for word in self.words)


def _config(psm):
    root = os.getenv('REDOCX_OCR_TESSDATA','/opt/redocx-tessdata')
    if not Path(root).is_dir():
        raise RuntimeError('The versioned OCR model directory is missing; rebuild the backend image.')
    return f'--tessdata-dir "{root}" --oem 1 --psm {psm} -c preserve_interword_spaces=1'


def installed_models():
    root=Path(os.getenv('REDOCX_OCR_TESSDATA','/opt/redocx-tessdata'))
    return {p.stem for p in root.glob('*.traineddata')}


def models_for(script, available=None):
    available=installed_models() if available is None else available
    configured=os.getenv('REDOCX_OCR_LANGUAGES','auto').strip()
    if configured!='auto':
        chosen=tuple(x.strip() for x in configured.split('+') if x.strip())
        if not chosen or any(x not in available for x in chosen):
            raise RuntimeError('A configured OCR language model is missing.')
        return chosen
    choices={'Latin':('Latin','fra','eng'),'Arabic':('Arabic','ara'),
             'Devanagari':('Devanagari','hin'),'Cyrillic':('Cyrillic','rus'),
             'Greek':('Greek',),'Hebrew':('Hebrew',),'Thai':('Thai',),
             'Han':('chi_sim',),'Japanese':('jpn',),'Hangul':('kor',)}
    chosen=tuple(x for x in choices.get(script,('Latin','eng')) if x in available)
    if not chosen:
        raise RuntimeError('No OCR model for the detected writing system is installed.')
    return chosen


def _detect(image):
    try:
        data=pytesseract.image_to_osd(image,config=_config(0).replace("--oem 1","--oem 3"),
            output_type=pytesseract.Output.DICT,timeout=15)
        script=str(data.get('script','Latin'))
        rotate=int(data.get('rotate',0)) if float(data.get('orientation_conf',0))>=3 else 0
        return script,rotate
    except (pytesseract.TesseractError,RuntimeError):
        return 'Latin',0


def _data(image, model, psm=3):
    result=pytesseract.image_to_data(image,lang=model,config=_config(psm),
        output_type=pytesseract.Output.DICT,
        timeout=max(5,min(90,int(os.getenv('REDOCX_OCR_PASS_TIMEOUT','40')))))
    words=[]
    for i,text in enumerate(result['text']):
        text=unicodedata.normalize('NFC',text.strip())
        confidence=float(result['conf'][i])
        if not text or confidence<45:
            continue
        x,y,w,h=(int(result[k][i]) for k in ('left','top','width','height'))
        if w<=0 or h<=0:
            continue
        words.append(Word(text,(x,y,x+w,y+h),confidence,model,
            tuple(result[k][i] for k in ('block_num','par_num','line_num'))))
    return words


def _same_region(a,b):
    a,b=fitz.Rect(a),fitz.Rect(b)
    intersection=(a&b).get_area()
    return intersection/max(1,min(a.get_area(),b.get_area()))>.45 and (
        abs((a.y0+a.y1-b.y0-b.y1)/2)<max(a.height,b.height)*.7)


def _plain(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',value).casefold()
                   if not unicodedata.combining(c) and c.isalnum())


def choose_reading(readings):
    """Consensus is on actual strings, so accents are never silently stripped."""
    by_text=defaultdict(list)
    for word in readings:
        by_text[word.text].append(word)
    ranked=sorted(by_text.items(),key=lambda pair:(len({w.model for w in pair[1]}),
                  len(pair[1]),statistics.mean(w.confidence for w in pair[1])),reverse=True)
    text,group=ranked[0]
    variants=[value for value in by_text if value!=text]
    votes=len({w.model for w in group})
    if variants and votes<2:
        # Two segmentation passes of one model corroborate an unambiguous crop,
        # but do not settle a disagreement between language models.
        other_models={w.model for value in variants for w in by_text[value]}
        if other_models- {w.model for w in group}:
            raise RuntimeError('OCR models disagree on a text region; recognition requires review.')
    if variants:
        next_votes=max(len({w.model for w in by_text[value]}) for value in variants)
        if next_votes==votes and any(_plain(value)!=_plain(text) for value in variants):
            raise RuntimeError('OCR has conflicting word or numeric readings; recognition requires review.')
        # Diacritic differences require two agreeing models, even if confidence
        # in the unaccented alternative happens to be high.
        if any(_plain(value)==_plain(text) for value in variants) and votes<2:
            raise RuntimeError('OCR has an unresolved accent disagreement.')
    candidate=max(group,key=lambda w:w.confidence)
    if candidate.confidence<70:
        raise RuntimeError('OCR confidence is insufficient for editable output.')
    return candidate


def _missing_regions(image, words):
    """Find small dark text components omitted by full-page segmentation."""
    gray=cv2.cvtColor(np.array(image),cv2.COLOR_RGB2GRAY)
    _,mask=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV|cv2.THRESH_OTSU)
    median=statistics.median(fitz.Rect(w.bbox).height for w in words) if words else 24
    count,labels,stats,centers=cv2.connectedComponentsWithStats(mask,8)
    candidates=[]
    for x,y,w,h,area in stats[1:]:
        if (area<8 or h<max(8,median*.4) or h>median*2.0 or w>median*8
                or w<2 or area/(w*h)>.85 and w>h*2):
            continue
        rect=fitz.Rect(int(x),int(y),int(x+w),int(y+h))
        if any((rect&fitz.Rect(word.bbox)).get_area()/max(1,rect.get_area())>.5 for word in words):
            continue
        candidates.append(rect)
    groups=[]
    for rect in sorted(candidates,key=lambda r:(round(r.y1/max(1,median*.4)),r.x0)):
        for i,old in enumerate(groups):
            if abs(old.y1-rect.y1)<median*.35 and -median*.3<rect.x0-old.x1<median*.8:
                groups[i]=old|rect
                break
        else:
            groups.append(rect)
    return groups[:64]


def recognize(image: Image.Image) -> Recognition:
    cv2.setNumThreads(1)
    image=quality.rgb(image)
    if image.width*image.height>40_000_000:
        raise ValueError('Image exceeds the editable OCR pixel limit.')
    # Use enough source samples for small accented print, without unbounded
    # enlargement of high-resolution scans.
    factor=min(3,max(1,2600/max(image.size)))
    scan=image.resize((round(image.width*factor),round(image.height*factor)),Image.Resampling.LANCZOS)
    script,rotate=_detect(scan)
    if rotate:
        scan=scan.rotate(-rotate,expand=True,fillcolor='white')
        image=image.rotate(-rotate,expand=True,fillcolor='white')
    models=models_for(script)
    passes=[]
    for model in models:
        passes.extend(_data(scan,model,3))
    passes.extend(_data(scan,models[0],11))
    if not passes:
        raise RuntimeError('No reliably readable text was found for editable conversion.')
    # Start with geometric regions from the script-wide model. Other models are
    # votes on those regions rather than an independent insertion of overlapping
    # words (one engine may split "raster image" while another merges it).
    primary=[w for w in passes if w.model==models[0]]
    regions=[]
    for word in sorted(primary,key=lambda w:(w.bbox[1],w.bbox[0],-w.confidence)):
        if any(_same_region(word.bbox,old.bbox) for old in regions):
            continue
        r=fitz.Rect(word.bbox)
        if len(word.text)<3 and r.height>statistics.median(fitz.Rect(x.bbox).height for x in passes)*2:
            continue
        regions.append(word)
    selected=[]
    for region in regions:
        readings=[w for w in passes if _same_region(w.bbox,region.bbox)
                  and .45<fitz.Rect(w.bbox).width/max(1,fitz.Rect(region.bbox).width)<2.2]
        try:
            chosen=choose_reading(readings)
        except RuntimeError:
            # Re-read the actual crop with two layout modes. A disagreement is
            # retained as an error if local recognition cannot settle it.
            r=fitz.Rect(region.bbox)
            box=(max(0,round(r.x0-8)),max(0,round(r.y0-8)),min(scan.width,round(r.x1+8)),min(scan.height,round(r.y1+8)))
            crop=scan.crop(box)
            reread=[]
            for model in models:
                for w in _data(crop,model,7):
                    if len(_plain(w.text))>=max(1,len(_plain(region.text))//2):
                        reread.append(Word(w.text,region.bbox,w.confidence,model,region.line))
            chosen=choose_reading(reread or readings)
        selected.append(chosen)
    # Recover labels such as the lone "4" above a bar. Only independently
    # corroborated crop readings become new text; graphic shapes are not guessed.
    for r in _missing_regions(scan,selected):
        box=(max(0,round(r.x0-10)),max(0,round(r.y0-10)),min(scan.width,round(r.x1+10)),min(scan.height,round(r.y1+10)))
        crop=scan.crop(box)
        readings=[]
        for model in models[:2] or models:
            for word in _data(crop,model,7):
                if word.confidence>=75 and len(word.text)<=12:
                    readings.append(Word(word.text,tuple(r),word.confidence,model,('crop',round(r.y0))))
        if len({w.model for w in readings})>=2:
            try:
                chosen=choose_reading(readings)
            except RuntimeError:
                continue
            if any(c.isalnum() for c in chosen.text) and not any(_same_region(chosen.bbox,w.bbox) for w in selected):
                selected.append(chosen)
    if not selected:
        raise RuntimeError('No corroborated editable text could be recovered.')
    selected=[Word(w.text,tuple(value/factor for value in w.bbox),w.confidence,w.model,w.line) for w in selected]
    selected.sort(key=lambda w:((w.bbox[1]+w.bbox[3])/2,w.bbox[0]))
    return Recognition(image,selected,script,models,rotate)


def font_file(script, bold=False):
    families={'Latin':'DejaVu Sans','Arabic':'Noto Naskh Arabic','Devanagari':'Noto Sans Devanagari',
              'Cyrillic':'DejaVu Sans','Greek':'DejaVu Sans','Hebrew':'Noto Sans Hebrew',
              'Thai':'Noto Sans Thai','Han':'Noto Sans CJK SC','Japanese':'Noto Sans CJK JP','Hangul':'Noto Sans CJK KR'}
    family=families.get(script,'Noto Sans')
    query=family+(':style=Bold' if bold else ':style=Regular')
    result=subprocess.run(['fc-match','-f','%{file}',query],capture_output=True,text=True,timeout=5,check=True)
    path=Path(result.stdout.strip())
    if not path.is_file():
        raise RuntimeError('A font for the detected writing system is missing.')
    return path


def editable_pdf(result: Recognition, output: Path, *, dpi=150):
    from .native import _glyph_metrics
    image=result.image
    background=np.array(image).copy()
    mask=np.zeros(background.shape[:2],dtype=np.uint8)
    for word in result.words:
        x0,y0,x1,y1=(int(round(v)) for v in word.bbox)
        x0,y0=max(0,x0-1),max(0,y0-1)
        x1,y1=min(image.width,x1+2),min(image.height,y1+2)
        mask[y0:y1,x0:x1]=255
    # Inpaint just the accepted word rectangles. Rejected/ambiguous words never
    # become a hidden OCR layer or an unmarked fabricated replacement.
    background=cv2.inpaint(background,mask,3,cv2.INPAINT_TELEA)
    stream=BytesIO();Image.fromarray(background).save(stream,'PNG')
    scale=72/dpi
    pdf=fitz.open()
    page=pdf.new_page(width=image.width*scale,height=image.height*scale)
    page.insert_image(page.rect,stream=stream.getvalue())
    complex_script=result.script in {'Arabic','Devanagari','Hebrew','Thai','Han','Japanese','Hangul'}
    for i,word in enumerate(result.words):
        x0,y0,x1,y1=word.bbox
        crop=np.array(image.crop((max(0,round(x0-2)),max(0,round(y0-2)),min(image.width,round(x1+2)),min(image.height,round(y1+2)))))
        pixels=crop.reshape(-1,3)
        bg=Counter(map(tuple,pixels.tolist())).most_common(1)[0][0]
        distances=np.abs(pixels.astype(int)-np.array(bg)).sum(axis=1)
        foreground=pixels[distances>90]
        if len(foreground):
            foreground=foreground[np.argsort(distances[distances>90])[-max(1,len(foreground)//4):]]
            color=tuple(float(x)/255 for x in np.median(foreground,axis=0))
        else:
            color=(0,0,0)
        bold=(y1-y0>=24 or word.text.isupper() and y1-y0>=18)
        path=font_file(result.script,bold)
        font=fitz.Font(fontfile=str(path))
        if any(not font.has_glyph(ord(c)) for c in word.text if not c.isspace()):
            raise RuntimeError('The selected OCR font cannot represent every recognized character.')
        if complex_script:
            # MuPDF's HTML text engine shapes Indic and bidirectional scripts.
            # Text is visible and selectable; it is not render_mode=3 OCR.
            size=(y1-y0)*scale*.9
            rect=fitz.Rect(x0*scale,(y0-2)*scale,(x1+4)*scale,(y1+5)*scale)
            rtl=result.script in {'Arabic','Hebrew'}
            html=f'<div dir="{"rtl" if rtl else "ltr"}" style="font-family:ocr;font-size:{size}pt;line-height:1;color:rgb({",".join(str(round(c*255)) for c in color)})">{escape(word.text)}</div>'
            css=f'@font-face{{font-family:ocr;src:url("{path.name}");}}*{{margin:0;padding:0;}}'
            spare,shrink=page.insert_htmlbox(rect,html,css=css,archive=fitz.Archive(str(path.parent)),scale_low=.5)
            if spare<0:
                raise RuntimeError('Complex-script OCR text could not be laid out without clipping.')
        else:
            xmin,ymin,xmax,ymax=_glyph_metrics(str(path),word.text)
            size=min((x1-x0)*scale/max(.1,xmax-xmin),(y1-y0)*scale/max(.1,ymax-ymin)*1.08)
            page.insert_text(fitz.Point(x0*scale-xmin*size,y0*scale+ymax*size),word.text,
                fontname='ocr'+str(i),fontfile=str(path),fontsize=size,color=color)
    pdf.set_metadata({'keywords':'redocx-visible-ocr-v2','subject':'Visible editable OCR reconstruction'})
    pdf.save(output,garbage=4,deflate=True)
    pdf.close()


def image_to_pdf(source: Path, output: Path, *, dpi=150):
    with Image.open(source) as image:
        result=recognize(image)
    editable_pdf(result,output,dpi=dpi)
    return result


def prepare_pdf(source: Path, output: Path) -> bool:
    """OCR scanned/hidden-OCR pages; preserve already-visible native pages."""
    with fitz.open(source) as original:
        needs=[]
        for page in original:
            visible=sum(len(x.get('chars',())) for x in page.get_texttrace() if x.get('type')!=3)
            background=any(fitz.Rect(x['bbox']).get_area()>page.rect.get_area()*.55 for x in page.get_image_info())
            needs.append(background and visible<20)
        if not any(needs):
            return False
        if sum(needs)>max(1,int(os.getenv('REDOCX_OCR_MAX_PAGES','100'))):
            raise RuntimeError('Scanned PDF exceeds the OCR page budget.')
        result=fitz.open()
        try:
            import tempfile
            with tempfile.TemporaryDirectory(prefix='pdf-visible-ocr-') as work:
                for index,page in enumerate(original):
                    if not needs[index]:
                        result.insert_pdf(original,from_page=index,to_page=index)
                        continue
                    dpi=250
                    pix=page.get_pixmap(dpi=dpi,alpha=False)
                    recognition=recognize(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
                    candidate=Path(work)/f'page-{index}.pdf'
                    editable_pdf(recognition,candidate,dpi=dpi)
                    with fitz.open(candidate) as recognized:
                        result.insert_pdf(recognized)
                    if not recognition.rotation:
                        for link in page.get_links():
                            if link.get('kind')==fitz.LINK_URI:
                                result[-1].insert_link({k:v for k,v in link.items() if k not in ('xref','id')})
                result.save(output,garbage=4,deflate=True)
        finally:
            result.close()
        return True
