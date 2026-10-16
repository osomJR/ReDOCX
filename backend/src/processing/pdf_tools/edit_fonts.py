"""Bounded font discovery for Edit PDF only; no network font downloads.

Use a usable embedded program first. Missing / subset glyphs use an installed
font, preserving weight and slant. Operators can register licensed fonts with
fontconfig. Restricted-embedding programs are never exported to the browser.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import shutil
import subprocess

import fitz
from fontTools.ttLib import TTFont, TTLibError

MAX_FONT_BYTES = 8 * 1024 * 1024


@dataclass
class FontProgram:
    key: str
    name: str
    font: fitz.Font
    buffer: bytes
    extension: str
    codepoints: set[int]
    browser_embeddable: bool
    source_name: str = ""
    substituted: bool = False

    def supports(self, text: str) -> bool:
        return all(char in "\n\r\t" or ord(char) in self.codepoints for char in text)


def normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^[A-Z]{6}\+", "", name).lower())


@lru_cache(maxsize=16)
def font_program(buffer: bytes, extension: str, name: str) -> FontProgram | None:
    if not buffer or len(buffer) > MAX_FONT_BYTES:
        return None
    export = False
    codepoints: set[int] = set()
    if extension in {"ttf", "otf", "woff", "woff2"}:
        try:
            with TTFont(BytesIO(buffer), lazy=True) as tt:
                # Editing needs installable or editable embedding. Preview-
                # only, restricted and bitmap-only programs cannot supply new
                # text. Story may subset fonts, so also exclude no-subset fonts.
                flags = int(getattr(tt.get("OS/2"), "fsType", 0))
                if flags & (0x0002 | 0x0100 | 0x0200) or (flags & 0x0004 and not flags & 0x0008):
                    return None
                codepoints = set((tt.getBestCmap() or {}).keys())
                if not codepoints:
                    # MuPDF may synthesize character codes for a PDF subset
                    # without a Unicode cmap. Those are not evidence that the
                    # original Unicode characters address the right glyphs.
                    # The caller can recover proven pairs from the PDF trace.
                    return None
                if "OS/2" not in tt and "hhea" in tt:
                    # PDF subsets may omit OS/2: MuPDF accepts these but web
                    # browsers reject them. Restore metrics from the actual
                    # font; keep the existing outlines and glyph addressing.
                    from fontTools.fontBuilder import FontBuilder
                    hhea = tt["hhea"]
                    FontBuilder(font=tt).setupOS2(
                        sTypoAscender=hhea.ascent, sTypoDescender=hhea.descent,
                        sTypoLineGap=hhea.lineGap,
                        usWinAscent=max(0, tt["head"].yMax),
                        usWinDescent=max(0, -tt["head"].yMin), fsType=0)
                    tt.recalcTimestamp = False
                    output = BytesIO()
                    tt.save(output)
                    buffer = output.getvalue()
                export = True
        except (TTLibError, ValueError, OSError, KeyError):
            return None
    try:
        font = fitz.Font(fontbuffer=buffer)
    except Exception:
        return None
    if not codepoints:
        codepoints = set(font.valid_codepoints())
    return FontProgram(sha256(buffer).hexdigest()[:24], name, font, buffer,
                       extension, codepoints, export)


def family_request(name: str) -> str:
    clean = re.sub(r"^[A-Z]{6}\+", "", str(name))
    normalized = normalized_name(clean)
    if "times" in normalized or "nimbusroman" in normalized:
        family = "Times New Roman"
    elif "courier" in normalized or "nimbusmono" in normalized:
        family = "Courier New"
    elif "calibri" in normalized or "carlito" in normalized:
        family = "Carlito"
    elif "cambria" in normalized or "caladea" in normalized:
        family = "Caladea"
    elif any(part in normalized for part in ("arial", "helvetica", "nimbussans")):
        family = "Arial"
    elif normalized in {"glyphlessfont", "", "sansserif"}:
        family = "Noto Sans"
    else:
        # A fontconfig pattern is data, not a shell command. Strip pattern
        # delimiters so PDF font names cannot change our requested properties.
        family = re.sub(r"[:=,\\\n\r]", " ", clean)[:100]
    style = []
    if any(part in normalized for part in ("bold", "black", "demi")):
        style.append("Bold")
    if any(part in normalized for part in ("italic", "oblique")):
        style.append("Italic")
    return family + (":style=" + " ".join(style) if style else "")


@lru_cache(maxsize=32)
def installed_font(name: str, codepoint: int = 0, *, bold: bool = False, italic: bool = False) -> FontProgram:
    normalized = normalized_name(name)
    bold = bold or any(part in normalized for part in ("bold", "black", "demi"))
    italic = italic or any(part in normalized for part in ("italic", "oblique"))
    request = family_request(name)
    if bold:
        request += ":weight=bold"
    if italic:
        request += ":slant=italic"
    if codepoint:
        request += f":charset={codepoint:x}"
    binary = shutil.which("fc-match")
    if binary:
        try:
            result = subprocess.run([binary, "--format", "%{file}\n%{family}\n", request],
                                    capture_output=True, text=True, timeout=3, check=True)
            lines = result.stdout.splitlines()
            path = Path(lines[0]) if lines else None
            if path and path.is_file() and path.suffix.lower() in {".ttf", ".otf"} and path.stat().st_size <= MAX_FONT_BYTES:
                program = font_program(path.read_bytes(), path.suffix[1:].lower(), lines[1] if len(lines) > 1 else path.stem)
                if (program and (not codepoint or program.font.has_glyph(codepoint))
                        and (not bold or program.font.is_bold)
                        and (not italic or program.font.is_italic)):
                    # Never mutate the cached embedded FontProgram instance.
                    return FontProgram(**{**vars(program), "source_name": name,
                                          "substituted": normalized_name(name) != normalized_name(program.name)})
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    # MuPDF's bundled fonts provide an offline last resort. CJK requests use
    # Droid Sans Fallback. The exact result is disclosed in the source profile.
    if codepoint:
        from fontTools import unicodedata as unicode_data
        script_name = unicode_data.script_name(unicode_data.script(chr(codepoint)))
        script = getattr(fitz, "UCDN_SCRIPT_" + script_name.upper().replace(" ", "_"), 0)
        font = fitz.Font(script=script)
        if font.has_glyph(codepoint):
            program = font_program(font.buffer, "ttf", font.name)
            if program is not None:
                return FontProgram(**{**vars(program), "source_name": name, "substituted": True})
    family = "times" if "times" in normalized else "courier" if "courier" in normalized else "sans"
    variants = {"times": ("tiro", "tibo", "tiit", "tibi"),
                "courier": ("cour", "cobo", "coit", "cobi"),
                "sans": ("helv", "hebo", "heit", "hebi")}
    alias = "cjk" if codepoint >= 0x2E80 else variants[family][int(bold) + 2 * int(italic)]
    font = fitz.Font(fontname=alias)
    program = font_program(font.buffer, "cff" if alias != "cjk" else "ttf", font.name)
    if program is None or (codepoint and not program.font.has_glyph(codepoint)):
        raise ValueError("No installed font can represent the requested character.")
    return FontProgram(**{**vars(program), "source_name": name, "substituted": True})


def font_for_character(program: FontProgram, character: str) -> FontProgram:
    if program.supports(character):
        return program
    return installed_font(program.source_name or program.name, ord(character))


@lru_cache(maxsize=8)
def recover_embedded_cmap(buffer: bytes, extension: str, name: str,
                          glyph_pairs: tuple[tuple[int, int], ...]) -> FontProgram | None:
    """Recover Unicode addressing without replacing an embedded font's outlines.

    Some PDF producers strip a TrueType cmap because the PDF carries its own
    Unicode map. MuPDF's text trace supplies the actual Unicode/glyph-id pairs.
    Reconstruct only those proven pairs; never guess missing character outlines.
    """
    if extension not in {"ttf", "otf"} or len(buffer) > MAX_FONT_BYTES:
        return None
    try:
        from fontTools.ttLib import newTable
        from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
        with TTFont(BytesIO(buffer), recalcTimestamp=False) as font:
            if "cmap" in font and font.getBestCmap():
                return None
            order = font.getGlyphOrder()
            mapping = {code: order[glyph] for code, glyph in glyph_pairs
                       if 0 < code <= 0x10FFFF and 0 < glyph < len(order)}
            if not mapping:
                return None
            cmap = newTable("cmap")
            cmap.tableVersion = 0
            cmap.tables = []
            for fmt, encoding, characters in ((4, 1, {k: v for k, v in mapping.items() if k <= 0xFFFF}),
                                                (12, 10, mapping)):
                table = CmapSubtable.newSubtable(fmt)
                table.platformID, table.platEncID, table.language = 3, encoding, 0
                table.cmap = characters
                cmap.tables.append(table)
            font["cmap"] = cmap
            output = BytesIO()
            font.save(output)
        return font_program(output.getvalue(), extension, name)
    except (TTLibError, ValueError, OSError, KeyError, IndexError):
        return None


@lru_cache(maxsize=1)
def available_families() -> list[str]:
    binary = shutil.which("fc-list")
    if not binary:
        return ["Helvetica", "Times-Roman", "Courier"]
    try:
        result = subprocess.run([binary, "--format", "%{family}\n"], capture_output=True, text=True, check=True, timeout=5)
        names = {line.split(",")[0].strip() for line in result.stdout.splitlines()}
        return sorted(name for name in names if name and len(name) <= 100)[:512]
    except (OSError, subprocess.SubprocessError):
        return ["Helvetica", "Times-Roman", "Courier"]
