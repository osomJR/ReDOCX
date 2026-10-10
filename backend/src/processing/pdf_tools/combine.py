from __future__ import annotations

"""
ReDOCX PDF Tools - Combine PDF processing.

This module is intentionally a low-level processing layer:
- it performs real PDF combination work;
- it preserves frontend ordering exactly;
- it can persist the generated artifact through the existing storage layer;
- it does not build AnalyzerResponse objects directly.

Expected service-layer usage:
    artifact = combine_pdfs([...], output_filename="combined-document.pdf")
    result = build_combine_pdf_result(...artifact fields...)
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Optional, Protocol, Sequence
import base64
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import xml.etree.ElementTree as ET

import fitz  # PyMuPDF
from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject, TextStringObject


MAX_COMBINE_PDF_FILES = 25
SUPPORTED_PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}
logger = logging.getLogger(__name__)


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class PdfSource:
    """
    Real filesystem source used by the PDF processing engine.

    display_name is what you want shown back to the user; path is the actual
    readable local file path.
    """

    path: str
    display_name: Optional[str] = None


@dataclass(frozen=True)
class CombinedPdfArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    source_file_count: int
    source_filenames: list[str]
    combined_page_count: int
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


SourcePathResolver = Callable[[Any], str]


class PdfCombineBackend(Protocol):
    def combine(
        self,
        *,
        sources: Sequence[str | Path | PdfSource | Any],
        output_filename: str = "combined-document.pdf",
        preserve_bookmarks: bool = True,
        preserve_metadata: bool = False,
        source_path_resolver: Optional[SourcePathResolver] = None,
    ) -> CombinedPdfArtifact:
        ...


class PyMuPDFCombineBackend:
    """
    Production-ready local combine backend built on PyMuPDF.

    It rejects encrypted/password-protected PDFs because your validation contract
    currently rejects them too. Add an explicit unlock workflow before allowing
    encrypted sources here.
    """

    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/combine",
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)

    def combine(
        self,
        *,
        sources: Sequence[str | Path | PdfSource | Any],
        output_filename: str = "combined-document.pdf",
        preserve_bookmarks: bool = True,
        preserve_metadata: bool = False,
        source_path_resolver: Optional[SourcePathResolver] = None,
    ) -> CombinedPdfArtifact:
        normalized_sources = _normalize_sources(sources, resolver=source_path_resolver)
        _validate_source_count(normalized_sources)
        output_name = _normalize_pdf_filename(output_filename, default="combined-document.pdf")

        with TemporaryDirectory(prefix="redocx-combine-") as workdir:
            output_path = Path(workdir) / output_name
            combined_page_count = self._combine_to_path(
                normalized_sources,
                output_path=output_path,
                preserve_bookmarks=preserve_bookmarks,
                preserve_metadata=preserve_metadata,
            )

            persisted_path, storage_key, download_url = _persist_or_copy(
                output_path,
                output_name=output_name,
                storage_backend=self.storage_backend,
                artifacts_dir=self.artifacts_dir,
            )

        return CombinedPdfArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=_get_file_size_mb(persisted_path),
            file_path=str(persisted_path),
            source_file_count=len(normalized_sources),
            source_filenames=[item.display_name or Path(item.path).name for item in normalized_sources],
            combined_page_count=combined_page_count,
            storage_key=storage_key,
            download_url=download_url,
        )

    @staticmethod
    def _combine_to_path(
        sources: Sequence[PdfSource],
        *,
        output_path: Path,
        preserve_bookmarks: bool,
        preserve_metadata: bool,
    ) -> int:
        output_pdf = fitz.open()
        toc: list[list[Any]] = []
        bookmark_targets: list[dict[str, str]] = []
        metadata_sources: list[dict[str, Any]] = []
        page_offset = 0

        try:
            for source in sources:
                source_path = Path(source.path)
                with fitz.open(source_path) as src:
                    _reject_encrypted_pdf(src, source_path)
                    page_count = int(src.page_count)
                    output_pdf.insert_pdf(src)

                    # Metadata does not depend on the secondary PDF parser. This
                    # also lets PDFs with damaged outlines or Info objects merge
                    # normally when those optional features are not requested.
                    if preserve_metadata:
                        try:
                            metadata_sources.append(_source_metadata(src, source, page_offset))
                        except Exception as exc:
                            raise ValueError(
                                "Could not preserve PDF metadata. The combined PDF was not created."
                            ) from exc

                    if preserve_bookmarks:
                        try:
                            source_toc = _read_source_toc(src, source_path)
                            if source_toc:
                                # The secondary parser is optional: when it
                                # cannot read a damaged PDF, recover destinations
                                # from PyMuPDF's parsed outline details instead.
                                reader = None
                                try:
                                    with source_path.open("rb") as stream:
                                        reader = PdfReader(stream, strict=False)
                                        targets = _bookmark_targets(
                                            reader, source_toc, output_pdf, page_offset, src
                                        )
                                except Exception as exc:
                                    logger.warning("Falling back to PDF bookmark recovery: %s", type(exc).__name__)
                                    targets = _bookmark_targets(
                                        None, source_toc, output_pdf, page_offset, src
                                    )
                                toc.extend(_offset_toc(source_toc, page_offset))
                                bookmark_targets.extend(targets)
                        except Exception as exc:
                            raise ValueError(
                                "Could not preserve PDF bookmarks. The combined PDF was not created."
                            ) from exc

                    page_offset += page_count

            if preserve_metadata:
                try:
                    _preserve_metadata(output_pdf, metadata_sources)
                except Exception as exc:
                    raise ValueError(
                        "Could not preserve PDF metadata. The combined PDF was not created."
                    ) from exc
            else:
                output_pdf.set_metadata({})
                output_pdf.del_xml_metadata()

            if preserve_bookmarks and toc:
                try:
                    _preserve_bookmarks(output_pdf, toc, bookmark_targets)
                except Exception as exc:
                    raise ValueError(
                        "Could not preserve PDF bookmarks. The combined PDF was not created."
                    ) from exc

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.save(
                output_path,
                garbage=4,
                deflate=True,
                clean=True,
            )
            # A successful in-memory reconstruction is not sufficient: verify
            # the saved artifact before the storage layer can publish it.
            try:
                with fitz.open(output_path) as saved:
                    if saved.page_count != page_offset:
                        raise ValueError("Merged PDF page count changed during save.")
                    if preserve_bookmarks and toc:
                        _verify_bookmarks(saved, toc, bookmark_targets)
                    if preserve_metadata:
                        _verify_metadata(saved, metadata_sources)
            except Exception as exc:
                if output_path.exists():
                    output_path.unlink()
                if preserve_bookmarks and toc:
                    raise ValueError(
                        "Could not preserve PDF bookmarks. The combined PDF was not created."
                    ) from exc
                if preserve_metadata:
                    raise ValueError(
                        "Could not preserve PDF metadata. The combined PDF was not created."
                    ) from exc
                raise
            return page_offset
        finally:
            output_pdf.close()


# Public convenience API -----------------------------------------------------


def combine_pdfs(
    sources: Sequence[str | Path | PdfSource | Any],
    *,
    output_filename: str = "combined-document.pdf",
    preserve_bookmarks: bool = True,
    preserve_metadata: bool = False,
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/combine",
    source_path_resolver: Optional[SourcePathResolver] = None,
) -> CombinedPdfArtifact:
    backend = PyMuPDFCombineBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
    )
    return backend.combine(
        sources=sources,
        output_filename=output_filename,
        preserve_bookmarks=preserve_bookmarks,
        preserve_metadata=preserve_metadata,
        source_path_resolver=source_path_resolver,
    )


# Internal helpers -----------------------------------------------------------


def _normalize_sources(
    sources: Sequence[str | Path | PdfSource | Any],
    *,
    resolver: Optional[SourcePathResolver] = None,
) -> list[PdfSource]:
    if not sources:
        raise ValueError("Combine PDF requires at least 2 source PDFs.")

    normalized: list[PdfSource] = []
    seen_paths: set[str] = set()

    for raw in sources:
        if isinstance(raw, PdfSource):
            source = raw
        elif isinstance(raw, (str, Path)):
            source = PdfSource(path=str(raw), display_name=Path(raw).name)
        else:
            resolved_path = resolver(raw) if resolver else _source_path_from_payload_like(raw)
            display_name = getattr(raw, "filename", None) or Path(resolved_path).name
            source = PdfSource(path=resolved_path, display_name=str(display_name))

        path = _require_pdf_path(source.path)
        key = str(path.resolve())
        if key in seen_paths:
            raise ValueError("Combine PDF input documents must not contain duplicate source paths.")
        seen_paths.add(key)
        normalized.append(PdfSource(path=str(path), display_name=source.display_name or path.name))

    return normalized


def _source_path_from_payload_like(value: Any) -> str:
    """
    Best-effort resolver for PdfFilePayload-like objects.

    Your extraction.py may set filename to only a display name; in production,
    pdf_tools_service.py should pass a resolver that maps storage_key/upload_id
    to a real local file path. This fallback accepts only fields that already
    resolve to a readable path.
    """
    for field_name in ("local_path", "file_path", "path", "storage_key", "filename"):
        candidate = getattr(value, field_name, None)
        if isinstance(candidate, str) and candidate.strip() and Path(candidate).exists():
            return candidate.strip()
    raise ValueError(
        "Could not resolve PDF source path. Pass source paths directly or provide "
        "source_path_resolver to map PdfFilePayload storage_key/upload_id to a local file."
    )


def _validate_source_count(sources: Sequence[PdfSource]) -> None:
    if len(sources) < 2:
        raise ValueError("Combine PDF requires at least 2 PDF files.")
    if len(sources) > MAX_COMBINE_PDF_FILES:
        raise ValueError(f"Combine PDF supports at most {MAX_COMBINE_PDF_FILES} PDF files.")


def _require_pdf_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF source not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF source is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF source must end with .pdf: {path.name}")

    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and guessed.lower() not in SUPPORTED_PDF_MIME_TYPES:
        raise ValueError(f"PDF source has unsupported MIME type {guessed}: {path.name}")
    return path


def _reject_encrypted_pdf(document: fitz.Document, source_path: Path) -> None:
    if bool(getattr(document, "needs_pass", False)) or bool(getattr(document, "is_encrypted", False)):
        raise ValueError(
            f"Password-protected or encrypted PDFs are not supported yet: {source_path.name}"
        )


def _offset_toc(toc: list[list[Any]], page_offset: int) -> list[list[Any]]:
    """Offset only real internal destinations, preserving external / -1 targets."""
    adjusted: list[list[Any]] = []
    previous_level = 0
    for item in toc:
        if len(item) not in (3, 4):
            raise ValueError("Unrecoverable PDF bookmark entry structure.")
        cloned = list(item)
        try:
            level = int(cloned[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("Unrecoverable PDF bookmark hierarchy.") from exc
        # Broken nesting is recoverable without discarding any bookmark.
        level = max(1, min(level, previous_level + 1))
        previous_level = level
        cloned[0] = level
        cloned[1] = str(cloned[1]) if cloned[1] is not None else "Recovered bookmark"
        try:
            page = int(cloned[2])
        except (TypeError, ValueError):
            page = -1
        if len(cloned) == 4 and isinstance(cloned[3], dict):
            destination = dict(cloned[3])
            destination.pop("xref", None)
            cloned[3] = destination
            internal = destination.get("kind") in (fitz.LINK_GOTO, fitz.LINK_NAMED)
            if internal and page > 0:
                cloned[2] = page + page_offset
                if isinstance(destination.get("page"), int) and destination["page"] >= 0:
                    destination["page"] += page_offset
            else:
                cloned[2] = -1
        else:
            cloned[2] = page + page_offset if page > 0 else -1
        adjusted.append(cloned)
    return adjusted


def _pdf_object_text(value: Any, active: frozenset[tuple[int, int]] = frozenset()) -> str:
    """Serialize a PDF value without leaving references into the source file."""
    if isinstance(value, IndirectObject):
        key = (value.idnum, value.generation)
        if key in active:
            raise ValueError("Cyclic source metadata or bookmark action.")
        return _pdf_object_text(value.get_object(), active | {key})
    if isinstance(value, StreamObject):
        raise ValueError("A metadata or bookmark action contains an unsupported stream.")
    if isinstance(value, DictionaryObject):
        return "<< " + " ".join(
            _pdf_object_text(key, active) + " " + _pdf_object_text(item, active)
            for key, item in value.items()
        ) + " >>"
    if isinstance(value, ArrayObject):
        return "[ " + " ".join(_pdf_object_text(item, active) for item in value) + " ]"
    stream = io.BytesIO()
    value.write_to_stream(stream)
    return stream.getvalue().decode("latin-1")


def _local_destination_text(reader: PdfReader, destination: Any, output: fitz.Document, offset: int, pages: dict[tuple[int, int], int]) -> str:
    destination = destination.get_object()
    if not isinstance(destination, ArrayObject):
        # Resolve names in the source before combining: two PDFs can use the
        # same name for different pages, and insert_pdf does not copy name trees.
        named = reader.named_destinations.get(str(destination))
        if named is None:
            raise ValueError("Unresolved named PDF bookmark destination.")
        destination = named.dest_array
    if len(destination) < 2:
        raise ValueError("Invalid internal PDF bookmark destination.")
    target = destination[0]
    if isinstance(target, IndirectObject):
        page_index = pages.get((target.idnum, target.generation), -1)
    elif isinstance(target, int):
        page_index = int(target)
    else:
        raise ValueError("Invalid internal PDF bookmark page.")
    if not 0 <= page_index < len(reader.pages):
        raise ValueError("Internal PDF bookmark page is outside its source document.")
    tail = " ".join(_pdf_object_text(item) for item in destination[1:])
    return f"[{output.page_xref(offset + page_index)} 0 R {tail}]"


def _bookmark_action_text(reader: PdfReader, action: Any, output: fitz.Document, offset: int, pages: dict[tuple[int, int], int]) -> str:
    action = action.get_object()
    if isinstance(action, ArrayObject):
        return "[ " + " ".join(_bookmark_action_text(reader, item, output, offset, pages) for item in action) + " ]"
    if not isinstance(action, DictionaryObject):
        raise ValueError("Invalid PDF bookmark action.")
    kind = str(action.get("/S", ""))
    if kind not in ("/GoTo", "/GoToR", "/URI", "/Launch", "/Named"):
        raise ValueError("Unsupported PDF bookmark action: " + kind)
    entries: list[str] = []
    for key, value in action.items():
        if key == "/D" and kind == "/GoTo":
            text = _local_destination_text(reader, value, output, offset, pages)
        elif key == "/Next":
            text = _bookmark_action_text(reader, value, output, offset, pages)
        else:
            text = _pdf_object_text(value)
        entries.append(_pdf_object_text(key) + " " + text)
    if kind == "/GoTo" and "/D" not in action:
        raise ValueError("Internal PDF bookmark action has no destination.")
    return "<< " + " ".join(entries) + " >>"


def _source_has_outline_entries(document: fitz.Document) -> bool:
    try:
        kind, value = document.xref_get_key(document.pdf_catalog(), "Outlines")
        if kind == "null":
            return False
        if kind == "dict":
            return "/First" in value
        if kind != "xref":
            raise ValueError("Malformed PDF outline root reference.")
        root_xref = int(value.split()[0])
        return document.xref_get_key(root_xref, "First")[0] != "null"
    except Exception as exc:
        raise ValueError("Cannot determine whether the PDF contains bookmarks.") from exc


def _read_source_toc(document: fitz.Document, source_path: Path) -> list[list[Any]]:
    try:
        toc = document.get_toc(simple=False)
    except Exception as exc:
        logger.warning("Recovering a damaged PDF outline using a secondary parser: %s", type(exc).__name__)
        try:
            with source_path.open("rb") as stream:
                return _recover_outline_from_reader(PdfReader(stream, strict=False))
        except Exception as recovery_exc:
            raise ValueError("Unrecoverable PDF bookmark tree.") from recovery_exc
    if not toc and _source_has_outline_entries(document):
        try:
            with source_path.open("rb") as stream:
                return _recover_outline_from_reader(PdfReader(stream, strict=False))
        except Exception as exc:
            raise ValueError("Unrecoverable PDF bookmark tree.") from exc
    return toc


def _recover_outline_from_reader(reader: PdfReader) -> list[list[Any]]:
    """Salvage titles and hierarchy from the raw outline linked list.

    Malformed actions are handled independently when reconstructing targets;
    a corrupt sibling link does not discard already recovered entries.
    """
    root = reader.trailer["/Root"].get("/Outlines")
    if root is None:
        return []
    root = root.get_object()
    first = root.get("/First")
    if first is None:
        return []
    if not isinstance(first, IndirectObject):
        raise ValueError("Unrecoverable PDF outline root.")
    seen: set[tuple[int, int]] = set()
    toc: list[list[Any]] = []
    pages = {
        (page.indirect_reference.idnum, page.indirect_reference.generation): index
        for index, page in enumerate(reader.pages)
    }

    def walk(ref: IndirectObject, level: int) -> None:
        while isinstance(ref, IndirectObject):
            key = (ref.idnum, ref.generation)
            if key in seen:
                logger.warning("Recovered a cyclic PDF bookmark sibling or child link.")
                return
            seen.add(key)
            item = ref.get_object()
            if not isinstance(item, DictionaryObject):
                raise ValueError("Unrecoverable PDF outline object.")
            title = str(item.get("/Title") or "Recovered bookmark")
            page = -1
            try:
                dest = item.get("/Dest")
                if dest is None and "/A" in item:
                    action = item["/A"]
                    if isinstance(action, DictionaryObject) and action.get("/S") == "/GoTo":
                        dest = action.get("/D")
                if dest is not None:
                    dest = dest.get_object()
                    if not isinstance(dest, ArrayObject):
                        named = reader.named_destinations.get(str(dest))
                        dest = named.dest_array if named is not None else None
                    if isinstance(dest, ArrayObject) and dest:
                        ref_page = dest[0]
                        if isinstance(ref_page, IndirectObject):
                            page = pages.get((ref_page.idnum, ref_page.generation), -1) + 1
                        elif isinstance(ref_page, int):
                            page = int(ref_page) + 1
            except Exception:
                logger.warning("Recovered a PDF bookmark with an unreadable destination.")
            toc.append([level, title, page, {"xref": ref.idnum,
                "kind": fitz.LINK_GOTO if page > 0 else fitz.LINK_NONE,
                "page": page - 1 if page > 0 else -1}])
            child = item.get("/First")
            if isinstance(child, IndirectObject):
                walk(child, level + 1)
            ref = item.get("/Next")

    walk(first, 1)
    if not toc:
        raise ValueError("Unrecoverable PDF outline tree.")
    return toc


def _recovered_bookmark_target(
    item: list[Any], output: fitz.Document, offset: int,
    source_document: fitz.Document | None = None,
) -> dict[str, str]:
    """Recover navigation from the source's raw or parsed bookmark details."""
    page = item[2]
    detail = item[3] if len(item) == 4 and isinstance(item[3], dict) else {}
    kind = detail.get("kind")
    xref = detail.get("xref")
    if source_document is not None and isinstance(xref, int) and xref > 0:
        try:
            # A direct /Dest array can retain /Fit, /FitH, /XYZ, etc. without
            # the secondary parser; only the original page reference changes.
            dest_kind, dest_value = source_document.xref_get_key(xref, "Dest")
            if kind in (fitz.LINK_GOTO, fitz.LINK_NAMED) and isinstance(page, int) and page > 0 and dest_kind == "array":
                match = re.fullmatch(r"\[\s*(\d+)\s+(\d+)\s+R(?P<tail>.*?)\]", dest_value, re.S)
                if match and int(match.group(1)) == source_document.page_xref(page - 1):
                    new_xref = output.page_xref(offset + page - 1)
                    return {"Dest": f"[{new_xref} 0 R{match.group('tail')}]"}
            action_kind, action_value = source_document.xref_get_key(xref, "A")
            if action_kind == "dict" and action_value.startswith("<<") and action_value.endswith(">>"):
                if kind in (fitz.LINK_URI, fitz.LINK_GOTOR, fitz.LINK_LAUNCH, fitz.LINK_NAMED):
                    # External actions can be copied when self-contained.
                    # Indirect source references cannot be copied into a new PDF.
                    if not re.search(r"\b\d+\s+\d+\s+R\b", action_value):
                        return {"A": action_value}
                if kind == fitz.LINK_GOTO and isinstance(page, int) and page > 0:
                    pattern = r"(/D\s*\[)\s*(\d+)\s+\d+\s+R"
                    match = re.search(pattern, action_value)
                    if match and int(match.group(2)) == source_document.page_xref(page - 1):
                        new_xref = output.page_xref(offset + page - 1)
                        repaired = action_value[:match.start()] + match.group(1) + f"{new_xref} 0 R" + action_value[match.end():]
                        if not re.search(r"\b\d+\s+\d+\s+R\b", repaired.replace(f"{new_xref} 0 R", "", 1)):
                            return {"A": repaired}
        except Exception as exc:
            logger.warning("Recovering PDF bookmark from parsed destination: %s", type(exc).__name__)
    if kind in (fitz.LINK_GOTO, fitz.LINK_NAMED) and isinstance(page, int) and page > 0:
        index = offset + page - 1
        if not 0 <= index < output.page_count:
            raise ValueError("Recovered PDF bookmark points outside the merged document.")
        point = detail.get("to")
        if isinstance(point, fitz.Point):
            pdf_point = point * ~output[index].transformation_matrix
            zoom = detail.get("zoom", 0)
            zoom = zoom if isinstance(zoom, (float, int)) else 0
            return {"Dest": f"[{output.page_xref(index)} 0 R /XYZ {pdf_point.x:g} {pdf_point.y:g} {zoom:g}]"}
        return {"Dest": f"[{output.page_xref(index)} 0 R /Fit]"}
    if kind == fitz.LINK_URI and isinstance(detail.get("uri"), str) and detail["uri"]:
        return {"A": f"<< /S /URI /URI {_pdf_object_text(TextStringObject(detail['uri']))} >>"}
    if kind == fitz.LINK_GOTOR and isinstance(detail.get("file"), str) and detail["file"]:
        # The parsed destination still identifies the remote file and page.
        # /Fit is a safe fallback when the original zoom mode is unreadable.
        remote_page = detail.get("page")
        if isinstance(remote_page, int) and remote_page >= 0:
            file_value = _pdf_object_text(TextStringObject(detail["file"]))
            return {"A": f"<< /S /GoToR /F {file_value} /D [{remote_page} /Fit] >>"}
    # A bookmark with no safe destination can still preserve its title and
    # hierarchy, but must never gain a fabricated or dangerous action.
    return {}


def _bookmark_targets(
    reader: PdfReader | None, toc: list[list[Any]], output: fitz.Document,
    offset: int, source_document: fitz.Document | None = None,
) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    pages: dict[tuple[int, int], int] = {}
    if reader is not None:
        pages = {
            (page.indirect_reference.idnum, page.indirect_reference.generation): index
            for index, page in enumerate(reader.pages)
        }
    for index, item in enumerate(toc, 1):
        detail = item[3] if len(item) == 4 and isinstance(item[3], dict) else {}
        xref = detail.get("xref")
        target: dict[str, str] = {}
        if reader is not None and isinstance(xref, int) and xref > 0:
            try:
                bookmark = reader.get_object(IndirectObject(xref, 0, reader))
                if not isinstance(bookmark, DictionaryObject):
                    raise ValueError("Malformed PDF bookmark object.")
                if "/Dest" in bookmark:
                    target["Dest"] = _local_destination_text(reader, bookmark.raw_get("/Dest"), output, offset, pages)
                elif "/A" in bookmark:
                    target["A"] = _bookmark_action_text(reader, bookmark.raw_get("/A"), output, offset, pages)
                for key in ("/C", "/F"):
                    if key in bookmark:
                        try:
                            target[key[1:]] = _pdf_object_text(bookmark.raw_get(key))
                        except Exception:
                            logger.warning("Discarding a malformed PDF bookmark style property.")
                if "/Count" in bookmark:
                    try:
                        target["__collapsed__"] = "1" if int(bookmark["/Count"]) < 0 else "0"
                    except (TypeError, ValueError):
                        logger.warning("Repairing a malformed PDF bookmark collapse count.")
            except Exception as exc:
                logger.warning("Recovering malformed PDF bookmark %d: %s", index, type(exc).__name__)
                target = _recovered_bookmark_target(item, output, offset, source_document)
        else:
            target = _recovered_bookmark_target(item, output, offset, source_document)
        # Parsed style information is a safe fallback when raw style is broken.
        if "C" not in target and isinstance(detail.get("color"), (tuple, list)):
            color = detail["color"]
            if len(color) == 3 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in color):
                target["C"] = "[" + " ".join(f"{v:g}" for v in color) + "]"
        if "F" not in target:
            flags = (2 if detail.get("bold") else 0) | (1 if detail.get("italic") else 0)
            if flags:
                target["F"] = str(flags)
        if "__collapsed__" not in target and "collapse" in detail:
            target["__collapsed__"] = "1" if detail["collapse"] else "0"
        targets.append(target)
    return targets


def _verify_bookmarks(
    output: fitz.Document, toc: list[list[Any]],
    targets: list[dict[str, str]] | None = None,
) -> None:
    restored = output.get_toc(simple=False)
    if len(restored) != len(toc):
        raise ValueError("PDF bookmark count changed during reconstruction.")
    for index, (expected, actual) in enumerate(zip(toc, restored)):
        if actual[:2] != expected[:2]:
            raise ValueError("PDF bookmark hierarchy changed during reconstruction.")
        if expected[2] > 0 and actual[2] != expected[2]:
            raise ValueError("PDF bookmark page destination changed during reconstruction.")
        if targets is not None:
            for key in ("Dest", "A", "C", "F"):
                if key in targets[index] and output.xref_get_key(actual[3]["xref"], key)[0] == "null":
                    raise ValueError("PDF bookmark action or appearance was lost during save.")


def _preserve_bookmarks(output: fitz.Document, toc: list[list[Any]], targets: list[dict[str, str]]) -> None:
    if len(toc) != len(targets):
        raise ValueError("PDF bookmark targets do not match the outline.")
    skeleton = [[item[0], item[1], -1] for item in toc]
    if output.set_toc(skeleton, collapse=0) != len(toc):
        raise ValueError("PDF bookmark count changed during reconstruction.")
    entries = output.get_toc(simple=False)
    if len(entries) != len(targets):
        raise ValueError("PDF bookmark target count does not match its outline.")
    for entry, target in zip(entries, targets):
        xref = entry[3]["xref"]
        for key, value in target.items():
            if key == "__collapsed__":
                # Keep the correctly generated descendant count; only change
                # its sign to preserve the source's expanded/collapsed state.
                kind, current = output.xref_get_key(xref, "Count")
                if kind == "int":
                    count = abs(int(current))
                    if count:
                        output.xref_set_key(xref, "Count", str(-count if value == "1" else count))
            else:
                output.xref_set_key(xref, key, value)
    output.init_doc()
    _verify_bookmarks(output, toc, targets)


_METADATA_FIELDS = ("title", "author", "subject", "keywords", "creator", "producer")
_SOURCE_METADATA_NS = "https://redocx.com/ns/combine/1.0/"
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_PDF_NS = "http://ns.adobe.com/pdf/1.3/"
_XMP_NS = "http://ns.adobe.com/xap/1.0/"


def _source_metadata(document: fitz.Document, source: PdfSource, offset: int) -> dict[str, Any]:
    """Preserve readable values and archive raw/expanded Info and XMP sources.

    Malformed optional fields are isolated so that valid metadata from the
    same PDF is not lost. If an existing XMP stream cannot be read at all,
    preservation is unrecoverable and the merge must not be published.
    """
    try:
        standard = dict(document.metadata or {})
    except Exception as exc:
        logger.warning("Recovering malformed standard PDF metadata: %s", type(exc).__name__)
        standard = {}

    info_raw = ""
    try:
        info_kind, info_value = document.xref_get_key(-1, "Info")
        if info_kind == "xref":
            info_raw = document.xref_object(int(info_value.split()[0]), compressed=False)
        elif info_kind == "dict":
            info_raw = info_value
    except Exception as exc:
        logger.warning("Recovering malformed PDF Info dictionary: %s", type(exc).__name__)

    # Resolve indirect custom Info values where possible, as in the previous
    # implementation. Retain the raw dictionary separately even when the
    # expanded form contains unsupported streams or cyclic references.
    info_expanded = ""
    if info_raw:
        try:
            with Path(source.path).open("rb") as stream:
                reader = PdfReader(stream, strict=False)
                info = reader.trailer.get("/Info")
                if info is not None:
                    info_expanded = _pdf_object_text(info)
        except Exception as exc:
            logger.warning("Retaining raw PDF Info dictionary after partial recovery: %s", type(exc).__name__)
    info_encoding = "pdf-latin-1" if info_expanded else "utf-8"
    info_pdf_bytes = (
        info_expanded.encode("latin-1") if info_expanded
        else info_raw.encode("utf-8", errors="surrogatepass")
    )

    xmp_xref = 0
    try:
        xmp_xref = document.xref_xml_metadata()
    except Exception as exc:
        logger.warning("Recovering PDF XMP stream reference: %s", type(exc).__name__)
        kind, value = document.xref_get_key(document.pdf_catalog(), "Metadata")
        if kind == "xref":
            xmp_xref = int(value.split()[0])

    if not xmp_xref:
        # A damaged Metadata stream may be invisible to the high-level getter
        # even though the PDF catalog still points to it.
        kind, value = document.xref_get_key(document.pdf_catalog(), "Metadata")
        if kind == "xref":
            xmp_xref = int(value.split()[0])

    xmp_bytes = b""
    xmp_encoding = "decoded"
    xmp_object = ""
    if xmp_xref:
        try:
            xmp_bytes = document.xref_stream(xmp_xref)
        except Exception as exc:
            logger.warning("Recovering unreadable PDF XMP stream as raw bytes: %s", type(exc).__name__)
            xmp_bytes = document.xref_stream_raw(xmp_xref)
            xmp_encoding = "raw-pdf-stream"
            xmp_object = document.xref_object(xmp_xref, compressed=False)

    return {
        "filename": source.display_name or Path(source.path).name,
        "firstPage": offset + 1,
        "pageCount": document.page_count,
        "standard": standard,
        "infoPdfBase64": base64.b64encode(info_pdf_bytes).decode("ascii"),
        "infoEncoding": info_encoding,
        "infoRawPdfBase64": base64.b64encode(info_raw.encode("utf-8", errors="surrogatepass")).decode("ascii"),
        "xmpBase64": base64.b64encode(xmp_bytes).decode("ascii"),
        "xmpEncoding": xmp_encoding,
        "xmpObjectPdfBase64": base64.b64encode(xmp_object.encode("utf-8", errors="surrogatepass")).decode("ascii"),
        "xmpDisplay": _xmp_display_values(xmp_bytes) if xmp_encoding == "decoded" else {},
    }


def _xmp_display_values(packet: bytes) -> dict[str, list[str]]:
    """Read common XMP fields, retaining opaque or malformed packets unchanged."""
    if not packet or b"<!DOCTYPE" in packet or b"<!ENTITY" in packet:
        return {}
    try:
        root = ET.fromstring(packet)
    except (ET.ParseError, ValueError, UnicodeError):
        return {}
    fields = {
        "title": (_DC_NS, "title"), "subject": (_DC_NS, "description"),
        "author": (_DC_NS, "creator"), "keywords": (_PDF_NS, "Keywords"),
        "producer": (_PDF_NS, "Producer"), "creator": (_XMP_NS, "CreatorTool"),
    }
    result: dict[str, list[str]] = {}
    for field, (namespace, name) in fields.items():
        values: list[str] = []
        # XMP permits simple properties as rdf:Description attributes too.
        for node in root.iter():
            value = node.get(f"{{{namespace}}}{name}")
            if value and value.strip():
                values.append(value)
        for node in root.iter(f"{{{namespace}}}{name}"):
            items = list(node.iter(f"{{{_RDF_NS}}}li"))
            # An rdf:Alt contains translations of one value; display x-default
            # (or the first translation), keeping all translations in the archive.
            if field in ("title", "subject") and items:
                items = [next((item for item in items if item.get("{http://www.w3.org/XML/1998/namespace}lang") == "x-default"), items[0])]
            values.extend(item.text for item in (items or [node]) if item.text and item.text.strip())
        result[field] = values
    return result


def _xml_display_text(value: str) -> str:
    # Original strings remain lossless in Info and the JSON source record.
    # XML display fields must exclude characters forbidden by XML 1.0.
    return "".join(char if (char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF) else "\ufffd" for char in value)


def _preserve_metadata(output: fitz.Document, sources: list[dict[str, Any]]) -> None:
    """Merge display fields and retain every source Info/XMP record in XMP.

    A PDF has one document-level Info dictionary and one XMP packet. Distinct
    text values are joined in input order. Dates use the first nonempty source
    value for compatibility; all original dates remain in the source records.
    This deliberately does not copy source PDF/A certification or signatures
    onto a newly combined document.
    """
    values: dict[str, list[str]] = {}
    for field in (*_METADATA_FIELDS, "creationDate", "modDate"):
        candidates: list[str] = []
        for source in sources:
            value = source["standard"].get(field)
            if isinstance(value, str) and value.strip():
                candidates.append(value)
            candidates.extend(source["xmpDisplay"].get(field, []))
        values[field] = list(dict.fromkeys(candidates))
    merged = {field: "; ".join(values[field]) for field in _METADATA_FIELDS}
    for field in ("creationDate", "modDate"):
        merged[field] = next(iter(values[field]), "")
    output.set_metadata(merged)

    root = ET.Element("{adobe:ns:meta/}xmpmeta")
    rdf = ET.SubElement(root, f"{{{_RDF_NS}}}RDF")
    description = ET.SubElement(rdf, f"{{{_RDF_NS}}}Description", {f"{{{_RDF_NS}}}about": ""})
    for field, name in (("title", "title"), ("subject", "description")):
        if merged[field]:
            prop = ET.SubElement(description, f"{{{_DC_NS}}}{name}")
            alt = ET.SubElement(prop, f"{{{_RDF_NS}}}Alt")
            ET.SubElement(alt, f"{{{_RDF_NS}}}li", {"{http://www.w3.org/XML/1998/namespace}lang": "x-default"}).text = _xml_display_text(merged[field])
    if values["author"]:
        prop = ET.SubElement(description, f"{{{_DC_NS}}}creator")
        seq = ET.SubElement(prop, f"{{{_RDF_NS}}}Seq")
        for value in values["author"]:
            ET.SubElement(seq, f"{{{_RDF_NS}}}li").text = _xml_display_text(value)
    for field, namespace, name in (("keywords", _PDF_NS, "Keywords"), ("producer", _PDF_NS, "Producer"), ("creator", _XMP_NS, "CreatorTool")):
        if merged[field]:
            ET.SubElement(description, f"{{{namespace}}}{name}").text = _xml_display_text(merged[field])
    prop = ET.SubElement(description, f"{{{_SOURCE_METADATA_NS}}}sources")
    sequence = ET.SubElement(prop, f"{{{_RDF_NS}}}Seq")
    for index, source in enumerate(sources, 1):
        record = ET.SubElement(sequence, f"{{{_RDF_NS}}}li", {f"{{{_RDF_NS}}}parseType": "Resource"})
        for name, value in (("index", index), ("firstPage", source["firstPage"]), ("pageCount", source["pageCount"]), ("infoPdfBase64", source["infoPdfBase64"]), ("infoEncoding", source["infoEncoding"]), ("infoRawPdfBase64", source["infoRawPdfBase64"]), ("xmpBase64", source["xmpBase64"]), ("xmpEncoding", source["xmpEncoding"]), ("xmpObjectPdfBase64", source["xmpObjectPdfBase64"])):
            ET.SubElement(record, f"{{{_SOURCE_METADATA_NS}}}{name}").text = str(value)
        # JSON escaping keeps arbitrary filenames/Info text XML-safe, including
        # control characters which are not legal in XML 1.0 text nodes.
        ET.SubElement(record, f"{{{_SOURCE_METADATA_NS}}}filenameJson").text = json.dumps(source["filename"], ensure_ascii=True)
        ET.SubElement(record, f"{{{_SOURCE_METADATA_NS}}}standardJson").text = json.dumps(source["standard"], ensure_ascii=True)
    packet = ET.tostring(root, encoding="unicode")
    # Verify that the generated packet is valid XML before publishing.
    ET.fromstring(packet)
    output.set_xml_metadata(packet)


def _verify_metadata(output: fitz.Document, sources: list[dict[str, Any]]) -> None:
    saved_metadata = output.metadata or {}
    for field in _METADATA_FIELDS:
        candidates: list[str] = []
        for source in sources:
            value = source["standard"].get(field)
            if isinstance(value, str) and value.strip():
                candidates.append(value)
            candidates.extend(source["xmpDisplay"].get(field, []))
        expected = "; ".join(dict.fromkeys(candidates))
        if saved_metadata.get(field, "") != expected:
            raise ValueError("Merged PDF changed the combined metadata fields.")
    xref = output.xref_xml_metadata()
    if not xref:
        raise ValueError("Merged PDF lost its source metadata archive.")
    packet = output.xref_stream(xref)
    root = ET.fromstring(packet)
    source_tag = f"{{{_SOURCE_METADATA_NS}}}sources"
    records = root.find(f".//{source_tag}/{{{_RDF_NS}}}Seq")
    if records is None or len(records) != len(sources):
        raise ValueError("Merged PDF lost one or more source metadata records.")
    for record, source in zip(records, sources):
        for name in ("infoPdfBase64", "infoEncoding", "infoRawPdfBase64", "xmpBase64", "xmpEncoding", "xmpObjectPdfBase64"):
            field = record.find(f"{{{_SOURCE_METADATA_NS}}}{name}")
            if field is None or (field.text or "") != source[name]:
                raise ValueError("Merged PDF changed the original source metadata.")


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    name = Path(raw).name
    stem = _safe_stem(Path(name).stem)
    return f"{stem}.pdf"


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "document"


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated PDF is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/pdf"


def _persist_or_copy(
    source_path: Path,
    *,
    output_name: str,
    storage_backend: Optional[StorageBackend],
    artifacts_dir: str | Path,
) -> tuple[Path, Optional[str], Optional[str]]:
    if storage_backend is None:
        default_storage = _try_default_storage(base_dir=str(artifacts_dir))
        if default_storage is not None:
            storage_backend = default_storage

    if storage_backend is not None:
        stored = storage_backend.persist(
            source_file_path=str(source_path),
            artifact_name=output_name,
            content_type=_guess_content_type(source_path),
        )
        stored_path = Path(getattr(stored, "stored_path", source_path)).resolve()
        return (
            stored_path,
            getattr(stored, "storage_key", None),
            getattr(stored, "download_url", None),
        )

    artifacts = Path(artifacts_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    destination = artifacts / output_name
    if destination.exists():
        destination = artifacts / _dedupe_name(output_name)
    shutil.copy2(source_path, destination)
    return destination, None, None


def _try_default_storage(*, base_dir: str) -> Optional[StorageBackend]:
    try:
        from backend.src.storage.artifacts import LocalArtifactStorage  # type: ignore

        return LocalArtifactStorage(base_dir=base_dir)
    except Exception:
        return None


def _dedupe_name(file_name: str) -> str:
    path = Path(file_name)
    return f"{_safe_stem(path.stem)}-{os.urandom(4).hex()}{path.suffix or '.pdf'}"


__all__ = [
    "CombinedPdfArtifact",
    "PdfSource",
    "PyMuPDFCombineBackend",
    "combine_pdfs",
]
