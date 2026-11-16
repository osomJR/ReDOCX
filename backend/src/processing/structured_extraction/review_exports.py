"""Business views and downloads from a single, owner-scoped extraction."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any, Sequence

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from backend.src.schema import StructuredDataOutputFormat
from backend.src.storage.artifacts import (
    LocalArtifactStorage,
    StoredArtifact,
    get_artifact_owner,
    is_artifact_expired,
)

from .structured_extraction import (
    MAX_EXPORT_CELLS_PER_REQUEST,
    MAX_SPREADSHEET_CELL_CHARACTERS,
    StructuredExtractionOutput,
    _ROW_FIELD_ALIASES,
    _ROW_METADATA_KEYS,
    _normalize_field_name,
    _safe_filename,
    _safe_spreadsheet_cell,
    _truncate,
    limit_structured_extraction_preview,
)


REVIEW_ARTIFACT_FEATURE = "structured_extraction_review"
REVIEW_EXPORT_FEATURE = "structured_extraction_export"
REVIEW_CONTRACT_VERSION = "v2"

_INVOICE_ITEM_COLUMNS = {"description", "quantity", "unit_price", "amount"}
_NUMERIC_COLUMNS = {
    "age", "amount", "closing_balance", "count", "debit", "credit", "balance",
    "line_total", "opening_balance", "price", "quantity", "qty", "subtotal", "tax",
    "tax_amount", "total", "unit_price",
}
_NUMBER = re.compile(r"[+-]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")
_DATE_WITH_SLASHES = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})")
_REVIEW_KEY = re.compile(r"[0-9]{4}/[0-9]{2}/[0-9]{2}/[0-9a-f]{16}-structured-extraction-review\.json")


class ReviewLayout(str, Enum):
    fields = "fields"
    source_tables = "source_tables"
    records = "records"
    complete_workbook = "complete_workbook"
    technical_json = "technical_json"


@dataclass(frozen=True)
class BusinessTable:
    columns: list[str]
    rows: list[dict[str, str]]

    def values(self, row: dict[str, str]) -> list[str]:
        return [row.get(column, "") for column in self.columns]

    def select_columns(self, columns: Sequence[str]) -> BusinessTable:
        if not columns:
            return self
        selected = set(columns)
        if not selected.issubset(self.columns):
            raise ValueError("Choose columns from the active source table.")
        ordered = [column for column in self.columns if column in selected]
        return BusinessTable(ordered, [
            {column: row.get(column, "") for column in ordered}
            for row in self.rows
        ])


def build_review_snapshot(extraction: StructuredExtractionOutput) -> dict[str, Any]:
    snapshot = dict(extraction.payload)
    snapshot["contract_version"] = REVIEW_CONTRACT_VERSION
    snapshot.pop("selected_field_status", None)
    snapshot["documents"] = [
        {
            **document.to_machine_readable(),
            "field_status": document.field_status,
            "unreadable": document.unreadable,
        }
        for document in extraction.documents
    ]
    return snapshot


def save_review(snapshot: dict[str, Any], storage: LocalArtifactStorage) -> StoredArtifact:
    with TemporaryDirectory() as directory:
        source = Path(directory) / "structured-extraction-review.json"
        with source.open("w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False, separators=(",", ":"))
        return storage.persist(
            source_file_path=str(source),
            artifact_name=source.name,
            feature=REVIEW_ARTIFACT_FEATURE,
        )


def load_review(
    storage: LocalArtifactStorage,
    review_id: str,
    *,
    owner_user_id: str,
    organization_id: str | None,
) -> tuple[Path, dict[str, Any]]:
    unavailable = "This extraction is no longer available. Extract the documents again."
    if not _REVIEW_KEY.fullmatch(review_id):
        raise FileNotFoundError(unavailable)
    owner = get_artifact_owner(review_id, base_dir=str(storage.base_dir))
    if (
        owner is None
        or owner.feature != REVIEW_ARTIFACT_FEATURE
        or owner.owner_user_id != owner_user_id
        or owner.organization_id != organization_id
        or is_artifact_expired(owner)
    ):
        raise FileNotFoundError(unavailable)
    path = storage.resolve_storage_key(review_id)
    try:
        with path.open(encoding="utf-8") as stream:
            snapshot = json.load(stream)
    except FileNotFoundError as error:
        raise FileNotFoundError(unavailable) from error
    if snapshot.get("contract_version") != REVIEW_CONTRACT_VERSION:
        raise FileNotFoundError(unavailable)
    return path, snapshot


def _source_labels(documents: list[dict[str, Any]]) -> dict[int, str]:
    counts = Counter(document["filename"] for document in documents)
    return {
        document["source_document_index"]: (
            f'{document["filename"]} ({document["source_document_index"] + 1})'
            if counts[document["filename"]] > 1
            else document["filename"]
        )
        for document in documents
    }


def _table_collection(snapshot: dict[str, Any]) -> list[tuple[dict, dict]]:
    return [
        (document, table)
        for document in snapshot["documents"]
        for table in document["tables"]
    ]


def _with_sources(
    documents: list[dict[str, Any]],
    rows: list[tuple[int, dict[str, str]]],
    columns: list[str],
) -> BusinessTable:
    if len(documents) < 2 or not columns:
        return BusinessTable(columns, [row for _, row in rows])
    labels = _source_labels(documents)
    existing = {_normalize_field_name(column) for column in columns}
    source_column = "Source document"
    suffix = 2
    while _normalize_field_name(source_column) in existing:
        source_column = f"Source document {suffix}"
        suffix += 1
    return BusinessTable(
        [source_column, *columns],
        [{source_column: labels[index], **row} for index, row in rows],
    )


def _fields_projection(snapshot: dict[str, Any]) -> BusinessTable:
    documents = snapshot["documents"]
    requested = snapshot["selected_fields"]
    labels = {_normalize_field_name(name): name for name in requested}
    rows = []
    for document in documents:
        row: dict[str, str] = {}
        for item in document["fields"]:
            key = item["normalized_name"]
            if not requested or key in labels:
                label = labels.setdefault(key, item["name"])
                row[label] = item["value"]
        if requested or row:
            rows.append((document["source_document_index"], row))
    return _with_sources(documents, rows, list(labels.values()))


def _record_values(row: dict[str, Any]) -> dict[str, str]:
    values = {name: value for name, value in row.items() if name not in _ROW_METADATA_KEYS}
    if row["record_type"] not in {"table_row", "line_item"}:
        return values
    # Apply invoice aliases only to rows that actually have the four item
    # columns. Inventory and other table headers retain their source names.
    renamed = {}
    for name, value in values.items():
        normalized = _normalize_field_name(name)
        canonical = _ROW_FIELD_ALIASES.get(normalized, normalized)
        renamed[canonical if canonical in _INVOICE_ITEM_COLUMNS else name] = value
    if len(renamed) == len(values) and _INVOICE_ITEM_COLUMNS.issubset(renamed):
        return renamed
    return values


def _records_projection(snapshot: dict[str, Any]) -> BusinessTable:
    documents = snapshot["documents"]
    rows = []
    columns: dict[str, None] = {}
    for document in documents:
        for item in document["row_based_records"]:
            if item.get("record_type") in {"field_set", "empty"}:
                continue
            values = _record_values(item)
            if values:
                columns.update(dict.fromkeys(values))
                rows.append((document["source_document_index"], values))
    return _with_sources(documents, rows, list(columns))


def project_business_table(
    snapshot: dict[str, Any],
    layout: ReviewLayout,
    *,
    table_id: int | None = None,
    columns: Sequence[str] = (),
) -> BusinessTable:
    if layout == ReviewLayout.source_tables:
        tables = _table_collection(snapshot)
        if table_id is None and len(tables) == 1:
            table_id = 1
        if table_id is None or not 1 <= table_id <= len(tables):
            raise ValueError("Choose a source table.")
        _, table = tables[table_id - 1]
        return BusinessTable(table["columns"], table["rows"]).select_columns(columns)
    if table_id is not None or columns:
        raise ValueError("Column selection applies only to a source table.")
    if layout == ReviewLayout.fields:
        return _fields_projection(snapshot)
    if layout == ReviewLayout.records:
        return _records_projection(snapshot)
    raise ValueError("Choose Fields, Source tables, or Records.")


def review_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    documents = snapshot["documents"]
    labels = _source_labels(documents)
    tables = [
        {
            "id": index,
            "filename": labels[document["source_document_index"]],
            "table_index": table["table_index"],
            "row_count": len(table["rows"]),
            "columns": table["columns"],
        }
        for index, (document, table) in enumerate(_table_collection(snapshot), start=1)
    ]
    fields = _fields_projection(snapshot)
    records = _records_projection(snapshot)
    return {
        "has_data": bool(records.rows or tables or any(
            item["value"] for document in documents for item in document["fields"]
        )),
        "field_row_count": len(fields.rows),
        "record_row_count": len(records.rows),
        "source_tables": tables,
        "quality": snapshot["quality_summary"],
        "documents": [
            {
                "filename": labels[document["source_document_index"]],
                "source_document_index": document["source_document_index"],
                "quality": document["quality"],
                "warnings": document["warnings"],
                "fields": [
                    {
                        "name": item["name"],
                        "status": item["status"],
                        "value": _truncate(item["value"], 240),
                        "line_number": next(iter(item["evidence"]), {}).get("line_number"),
                        "candidates": [
                            _truncate(candidate["value"], 100) for candidate in item["candidates"][:3]
                        ],
                    }
                    for item in document["field_status"]
                ],
            }
            for document in documents
        ],
    }


def review_preview(
    snapshot: dict[str, Any],
    layout: ReviewLayout,
    *,
    table_id: int | None = None,
    columns: Sequence[str] = (),
) -> dict[str, Any]:
    if layout == ReviewLayout.technical_json:
        if table_id is not None or columns:
            raise ValueError("Technical JSON includes the complete extraction.")
        payload, _, shortened = limit_structured_extraction_preview(snapshot, ())
        return {"json": payload, "shortened": shortened}

    table = project_business_table(snapshot, layout, table_id=table_id, columns=columns)
    _, rows, shortened = limit_structured_extraction_preview({}, table.rows)
    return {
        "columns": table.columns,
        "rows": rows,
        "row_count": len(table.rows),
        "shortened": shortened,
    }


def _typed_value(column: str, value: str) -> tuple[Any, str | None]:
    name = _normalize_field_name(column)
    if name in _NUMERIC_COLUMNS and _NUMBER.fullmatch(value):
        decimal = Decimal(value.replace(",", ""))
        # Excel stores numbers with finite precision. Preserve the source
        # string when conversion could change significant digits or magnitude.
        fractional = max(0, -decimal.as_tuple().exponent)
        if len(decimal.as_tuple().digits) <= 15 and fractional <= 15:
            return decimal, "0" + ("." + "0" * fractional if fractional else "")
    if name == "date" or name.endswith("_date") or name in {"date_of_birth", "dob"}:
        try:
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                parsed = date.fromisoformat(value)
                if parsed.year >= 1900:
                    return parsed, "yyyy-mm-dd"
            match = _DATE_WITH_SLASHES.fullmatch(value)
            if match:
                first, second, year = (int(part) for part in match.groups())
                if year >= 1900 and (first > 12 or first == second):
                    return date(year, second, first), "dd/mm/yyyy"
                if year >= 1900 and second > 12:
                    return date(year, first, second), "mm/dd/yyyy"
        except ValueError:
            pass
    return value, None


def _xlsx_cell(sheet, column: str, raw: str):
    if len(raw) > MAX_SPREADSHEET_CELL_CHARACTERS:
        raise ValueError("A value is too long for an Excel cell. Choose JSON or CSV.")
    if raw == "":
        return None
    value, number_format = _typed_value(column, raw)
    cell = WriteOnlyCell(sheet, value=value)
    if isinstance(value, str):
        cell.data_type = "s"  # Source text must remain literal, including formula-like strings.
    if number_format:
        cell.number_format = number_format
    return cell


def _write_workbook(path: Path, sheets: list[tuple[str, BusinessTable]]) -> None:
    if any(len(table.columns) > 16_384 for _, table in sheets):
        raise ValueError("The result exceeds the Excel column limit. Choose JSON or CSV.")
    workbook = Workbook(write_only=True)
    try:
        for name, table in sheets:
            sheet = workbook.create_sheet(name)
            if not table.columns:
                continue
            sheet.freeze_panes = "A2"
            sheet.append([_xlsx_cell(sheet, "", column) for column in table.columns])
            for row in table.rows:
                sheet.append([_xlsx_cell(sheet, col, row.get(col, "")) for col in table.columns])
        workbook.save(path)
    finally:
        for sheet in workbook.worksheets:
            if not sheet.closed:
                sheet.close()
        workbook.close()


def _write_business_json(path: Path, table: BusinessTable) -> None:
    with path.open("w", encoding="utf-8") as stream:
        stream.write('{"columns":')
        json.dump(table.columns, stream, ensure_ascii=False)
        stream.write(',"rows":[')
        for index, row in enumerate(table.rows):
            if index:
                stream.write(",")
            json.dump(dict(zip(table.columns, table.values(row))), stream, ensure_ascii=False)
        stream.write("]}\n")


def _write_business_csv(path: Path, table: BusinessTable) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        if table.columns:
            writer.writerow([_safe_spreadsheet_cell(column) for column in table.columns])
        for row in table.rows:
            writer.writerow([
                raw if isinstance(_typed_value(column, raw)[0], Decimal) else _safe_spreadsheet_cell(raw)
                for column, raw in zip(table.columns, table.values(row))
            ])


def export_review(
    storage: LocalArtifactStorage,
    snapshot_path: Path,
    snapshot: dict[str, Any],
    layout: ReviewLayout,
    output_format: StructuredDataOutputFormat,
    *,
    table_id: int | None = None,
    columns: Sequence[str] = (),
) -> StoredArtifact:
    if layout in {ReviewLayout.complete_workbook, ReviewLayout.technical_json} and (
        table_id is not None or columns
    ):
        raise ValueError("Choose a source table to apply a column selection.")

    documents = snapshot["documents"]
    stem = Path(documents[0]["filename"]).stem if len(documents) == 1 else "document-set"
    suffix = (
        f"source_table_{table_id or 1}" if layout == ReviewLayout.source_tables else layout.value
    )
    filename = _safe_filename(f"{stem[:110]}_structured_extraction_{suffix}.{output_format.value}")

    if layout == ReviewLayout.technical_json:
        if output_format != StructuredDataOutputFormat.json:
            raise ValueError("Technical JSON is available as a JSON file.")
        return storage.persist(
            source_file_path=str(snapshot_path), artifact_name=filename, feature=REVIEW_EXPORT_FEATURE,
        )

    if layout == ReviewLayout.complete_workbook:
        if output_format != StructuredDataOutputFormat.xlsx:
            raise ValueError("The complete workbook is available as an Excel file.")
        sheets = [("Fields", _fields_projection(snapshot)), ("Records", _records_projection(snapshot))]
    else:
        table = project_business_table(snapshot, layout, table_id=table_id, columns=columns)
        title = "Source table" if layout == ReviewLayout.source_tables else layout.value.title()
        sheets = [(title, table)]

    if sum((len(table.rows) + 1) * len(table.columns) for _, table in sheets) > MAX_EXPORT_CELLS_PER_REQUEST:
        raise ValueError("The export exceeds the cell budget. Choose fewer documents or columns.")
    with TemporaryDirectory() as directory:
        path = Path(directory) / filename
        if output_format == StructuredDataOutputFormat.xlsx:
            _write_workbook(path, sheets)
        elif output_format == StructuredDataOutputFormat.csv:
            _write_business_csv(path, sheets[0][1])
        else:
            _write_business_json(path, sheets[0][1])
        return storage.persist(
            source_file_path=str(path), artifact_name=filename, feature=REVIEW_EXPORT_FEATURE,
        )
