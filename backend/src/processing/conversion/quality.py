"""Content and appearance gates shared by the isolated conversion worker."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

import fitz
from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


def canonical_number(value: str) -> str:
    value = re.sub(r"(?<=\d)[,\u00a0\u202f ](?=\d{3}(?:\D|$))", "", value)
    try:
        return format(Decimal(value).normalize(), "f")
    except InvalidOperation:
        return value


def tokens(value: str) -> list[str]:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return [
        canonical_number(x) if x[0].isdigit() else x
        for x in re.findall(r"[^\W\d_]+|\d+(?:[.,]\d+)*", value, re.UNICODE)
    ]


def numbers(value: str) -> Counter:
    value = unicodedata.normalize("NFKC", str(value or ""))
    return Counter(
        canonical_number(x)
        for x in re.findall(r"[+-]?\d+(?:[.,]\d+)*", value.replace("−", "-"))
    )


def assert_text(
    source: str,
    output: str,
    *,
    minimum: float = 0.98,
    label: str = "Conversion",
    unicode_exact: bool = False,
) -> None:
    source_tokens, output_tokens = Counter(tokens(source)), Counter(tokens(output))
    total = sum(source_tokens.values())
    matched = sum((source_tokens & output_tokens).values())
    missing_numbers = numbers(source) - numbers(output)
    currency = lambda s: Counter(c for c in s if unicodedata.category(c) == "Sc")
    if (
        missing_numbers
        or currency(source) - currency(output)
        or Counter(c for c in source if c in "%‰")
        - Counter(c for c in output if c in "%‰")
    ):
        raise RuntimeError(f"{label} lost numeric values or currency symbols.")
    if total and matched / total + 1e-9 < minimum:
        raise RuntimeError(
            f"{label} retained {matched}/{total} source tokens; required {minimum:.1%}."
        )
    if unicode_exact:
        special = lambda s: Counter(
            c
            for c in unicodedata.normalize("NFC", s)
            if ord(c) > 127 and not c.isspace()
        )
        if special(source) - special(output) or any(
            unicodedata.category(c) == "Cc" and c not in "\t\r\n\f" for c in output
        ):
            raise RuntimeError(
                f"{label} lost Unicode characters or introduced control codes."
            )


def visible_word_text(path: Path) -> str:
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    disabled = {"0", "false", "off"}
    with zipfile.ZipFile(path) as package:
        styles = {}
        if "word/styles.xml" in package.namelist():
            for style in ET.fromstring(package.read("word/styles.xml")).findall(
                ns + "style"
            ):
                styles[style.get(ns + "styleId")] = style

        def hidden(props, seen=None):
            if props is None:
                return False
            for name in ("vanish", "webHidden"):
                tag = props.find(ns + name)
                if tag is not None:
                    return tag.get(ns + "val", "true").lower() not in disabled
            seen = set() if seen is None else seen
            for name in ("rStyle", "pStyle", "basedOn"):
                tag = props.find(ns + name)
                key = tag.get(ns + "val") if tag is not None else None
                if key and key not in seen and key in styles:
                    seen.add(key)
                    style = styles[key]
                    if hidden(style.find(ns + "rPr"), seen) or hidden(style, seen):
                        return True
            return False

        chunks = []
        for name in package.namelist():
            if not re.fullmatch(
                r"word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml", name
            ):
                continue
            root = ET.fromstring(package.read(name))
            parents = {child: parent for parent in root.iter() for child in parent}
            for node in root.iter(ns + "t"):
                ancestor, omit = parents.get(node), False
                while ancestor is not None:
                    if ancestor.tag == ns + "del":
                        omit = True
                    if ancestor.tag == ns + "r" and hidden(ancestor.find(ns + "rPr")):
                        omit = True
                    if ancestor.tag == ns + "p" and hidden(ancestor.find(ns + "pPr")):
                        omit = True
                    ancestor = parents.get(ancestor)
                if not omit:
                    chunks.append(node.text or "")
        return "\n".join(chunks)


def rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        background = Image.new("RGBA", image.size, "white")
        background.alpha_composite(image.convert("RGBA"))
        return background.convert("RGB")
    return image.convert("RGB")


def assert_pixels(
    source: Image.Image,
    output: Image.Image,
    *,
    label="Raster conversion",
    minimum_ink=0.94,
    maximum_error=0.045,
) -> None:
    source, output = rgb(source), rgb(output)
    if abs(source.width / source.height - output.width / output.height) > 0.015:
        raise RuntimeError(f"{label} changed the image aspect ratio.")
    size = (720, max(1, round(720 * source.height / source.width)))
    source, output = (
        x.resize(size, Image.Resampling.LANCZOS) for x in (source, output)
    )
    error = sum(ImageStat.Stat(ImageChops.difference(source, output)).mean) / (255 * 3)
    ink = source.convert("L").point(lambda p: 255 if p < 238 else 0)
    found = (
        output.convert("L")
        .point(lambda p: 255 if p < 238 else 0)
        .filter(ImageFilter.MaxFilter(5))
    )
    count = sum(ink.histogram()[1:])
    retained = (
        sum(ImageChops.multiply(ink, found).histogram()[1:]) / count if count else 1
    )
    if error > maximum_error or retained < minimum_ink:
        raise RuntimeError(
            f"{label} failed appearance validation (error={error:.3f}, ink={retained:.1%})."
        )


def page_image(page, dpi=120):
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def assert_text_regions(source, output, regions):
    """Large graphics cannot compensate for missing or misplaced OCR glyphs."""
    source, output = rgb(source), rgb(output)
    output = output.resize(source.size, Image.Resampling.LANCZOS)
    scores = []
    for x, y, w, h in regions:
        box = (
            max(0, x - 3),
            max(0, y - 3),
            min(source.width, x + w + 3),
            min(source.height, y + h + 3),
        )
        expected, actual = source.crop(box), output.crop(box)
        background = max(
            expected.getcolors(expected.width * expected.height), key=lambda p: p[0]
        )[1]
        solid = Image.new("RGB", expected.size, background)
        mask = (
            ImageChops.difference(expected, solid)
            .convert("L")
            .point(lambda p: 255 if p > 35 else 0)
        )
        found = (
            ImageChops.difference(actual, solid)
            .convert("L")
            .point(lambda p: 255 if p > 35 else 0)
        )
        count = sum(mask.histogram()[1:])
        if count < 6:
            continue
        matched = sum(
            ImageChops.multiply(
                mask, found.filter(ImageFilter.MaxFilter(7))
            ).histogram()[1:]
        )
        scores.append(matched / count)
    if scores and (min(scores) < 0.60 or statistics_mean(scores) < 0.90):
        raise RuntimeError("OCR Word export failed text-region appearance validation.")


def statistics_mean(values):
    return sum(values) / len(values)


def assert_pdf_jpegs(source: Path, output: Path, physical_format: str):
    with fitz.open(source) as pdf:
        if physical_format == "zip":
            with zipfile.ZipFile(output) as package:
                names = sorted(
                    n
                    for n in package.namelist()
                    if n.lower().endswith((".jpg", ".jpeg"))
                )
                if len(names) != len(pdf):
                    raise RuntimeError("PDF-to-JPG lost pages.")
                for page, name in zip(pdf, names):
                    with Image.open(BytesIO(package.read(name))) as image:
                        assert_pixels(
                            page_image(page),
                            image,
                            label=f"PDF-to-JPG page {page.number+1}",
                        )
        else:
            if len(pdf) != 1:
                raise RuntimeError("Multi-page PDF-to-JPG requires a complete ZIP.")
            with Image.open(output) as image:
                assert_pixels(page_image(pdf[0]), image, label="PDF-to-JPG")


def assert_image_pdf(source: Path, output: Path):
    with Image.open(source) as image, fitz.open(output) as pdf:
        if len(pdf) != 1:
            raise RuntimeError("Image-to-PDF must produce one page.")
        assert_pixels(image, page_image(pdf[0]), label="Image-to-PDF")


def assert_legacy_word_images(source: Path, output: Path):
    with zipfile.ZipFile(source) as package, fitz.open(output) as pdf:
        media = [n for n in package.namelist() if n.startswith("word/media/")]
        images = []
        for name in media:
            try:
                with Image.open(BytesIO(package.read(name))) as image:
                    images.append(rgb(image).copy())
            except OSError:
                continue
        if not images:
            return  # A truly blank native document is legitimate.
        # Compare each page image at its actual Word export placement. Word can
        # add a small drawing offset; clipping only white margins is acceptable.
        for expected in images:
            if expected.width < 300 or expected.height < 300:
                continue
            found = False
            for page in pdf:
                for info in page.get_image_info(xrefs=True):
                    bbox = fitz.Rect(info["bbox"])
                    visible = bbox & page.rect
                    if visible.is_empty or bbox.width <= 0 or bbox.height <= 0:
                        continue
                    crop = (
                        max(
                            0,
                            round((visible.x0 - bbox.x0) / bbox.width * expected.width),
                        ),
                        max(
                            0,
                            round(
                                (visible.y0 - bbox.y0) / bbox.height * expected.height
                            ),
                        ),
                        min(
                            expected.width,
                            round((visible.x1 - bbox.x0) / bbox.width * expected.width),
                        ),
                        min(
                            expected.height,
                            round(
                                (visible.y1 - bbox.y0) / bbox.height * expected.height
                            ),
                        ),
                    )
                    piece = expected.crop(crop)
                    all_ink = expected.convert("L").point(
                        lambda p: 255 if p < 238 else 0
                    )
                    kept_ink = all_ink.crop(crop)
                    total = sum(all_ink.histogram()[1:])
                    if total and sum(kept_ink.histogram()[1:]) / total < 0.995:
                        continue
                    pix = page.get_pixmap(dpi=120, clip=visible, alpha=False)
                    rendered = Image.frombytes(
                        "RGB", (pix.width, pix.height), pix.samples
                    )
                    try:
                        assert_pixels(piece, rendered, label="Legacy Word page image")
                        found = True
                        break
                    except RuntimeError:
                        continue
                if found:
                    break
            if not found:
                raise RuntimeError("Word-to-PDF lost an embedded page image.")


def restore_pdf_links(source: Path, output: Path):
    with fitz.open(source) as original, fitz.open(output) as converted:
        if len(original) != len(converted):
            raise RuntimeError("PDF/A conversion changed page count.")
        for old, new in zip(original, converted):
            existing = new.get_links()
            for link in old.get_links():
                kind = link.get("kind")
                if kind not in (fitz.LINK_URI, fitz.LINK_GOTO):
                    continue
                if not any(
                    x.get("kind") == kind
                    and x.get("uri") == link.get("uri")
                    and x.get("page") == link.get("page")
                    and fitz.Rect(x["from"]).intersects(link["from"])
                    for x in existing
                ):
                    new.insert_link(
                        {k: v for k, v in link.items() if k not in ("xref", "id")}
                    )
        converted.saveIncr()
    with fitz.open(output) as pdf:
        for page in pdf:
            for link in page.get_links():
                if link.get("xref", 0):
                    _, value = pdf.xref_get_key(link["xref"], "F")
                    flags = int(value) if value.lstrip("-").isdigit() else 0
                    pdf.xref_set_key(
                        link["xref"], "F", str((flags | 4) & ~(1 | 2 | 32))
                    )
        pdf.saveIncr()


def assert_pdfa_content(source: Path, output: Path):
    with fitz.open(source) as original, fitz.open(output) as converted:
        if len(original) != len(converted):
            raise RuntimeError("PDF/A conversion changed page count.")
        for old, new in zip(original, converted):
            assert_text(
                old.get_text(),
                new.get_text(),
                minimum=0.99,
                unicode_exact=True,
                label="PDF/A",
            )
            for link in old.get_links():
                if link.get("kind") == fitz.LINK_URI and not any(
                    item.get("uri") == link.get("uri")
                    and fitz.Rect(item["from"]).intersects(link["from"])
                    for item in new.get_links()
                ):
                    raise RuntimeError("PDF/A conversion lost a URI annotation.")
