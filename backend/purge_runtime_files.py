from __future__ import annotations

"""Privacy-safe maintenance utility for historical ReDOCX runtime files.

Dry-run is the default. The command prints aggregate counts and byte totals only;
it never prints customer filenames or storage paths.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os


@dataclass(frozen=True)
class PurgeSummary:
    files: int
    bytes: int


_INTERNAL_NAMES = {
    ".artifact_owners.json",
    ".artifact_owners.lock",
    ".upload_retention.json",
    ".upload_retention.lock",
}
_DATABASE_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".wal", ".shm"}


def _roots(base_dir: Path) -> list[Path]:
    upload_root = Path(os.getenv("UPLOAD_BASE_DIR", "uploads"))
    artifact_root = Path(os.getenv("ARTIFACT_STORAGE_DIR", "artifacts"))
    return [
        (base_dir / upload_root).resolve() if not upload_root.is_absolute() else upload_root.resolve(),
        (base_dir / artifact_root).resolve() if not artifact_root.is_absolute() else artifact_root.resolve(),
        (base_dir / "outputs").resolve(),
        (base_dir / "runtime" / "pdf_compression" / "compression_sources").resolve(),
    ]


def _is_customer_file(path: Path) -> bool:
    if path.name in _INTERNAL_NAMES or path.name.startswith(".artifact_owners."):
        return False
    if path.name.startswith(".upload_retention."):
        return False
    lower = path.name.lower()
    if any(lower.endswith(suffix) for suffix in _DATABASE_SUFFIXES):
        return False
    return path.is_file()


def _eligible_files(roots: list[Path], *, older_than: datetime) -> list[Path]:
    selected: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            try:
                resolved = candidate.resolve()
                if resolved in seen or not _is_customer_file(resolved):
                    continue
                modified = datetime.fromtimestamp(resolved.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified <= older_than:
                seen.add(resolved)
                selected.append(resolved)
    return selected


def _reset_metadata(roots: list[Path]) -> None:
    for root in roots:
        for name in (".artifact_owners.json", ".upload_retention.json"):
            metadata = root / name
            if metadata.exists():
                metadata.write_text("{}", encoding="utf-8")
                metadata.chmod(0o600)


def _remove_empty_directories(roots: list[Path]) -> None:
    for root in roots:
        if not root.exists():
            continue
        directories = sorted(
            (item for item in root.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                continue


def purge(*, base_dir: Path, older_than_minutes: int, apply: bool) -> PurgeSummary:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(0, older_than_minutes))
    roots = _roots(base_dir.resolve())
    candidates = _eligible_files(roots, older_than=cutoff)
    total_bytes = 0
    for candidate in candidates:
        try:
            total_bytes += candidate.stat().st_size
        except OSError:
            pass

    if apply:
        for candidate in candidates:
            candidate.unlink(missing_ok=True)
        _reset_metadata(roots)
        _remove_empty_directories(roots)

    return PurgeSummary(files=len(candidates), bytes=total_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or purge historical ReDOCX source uploads and generated artifacts."
    )
    parser.add_argument(
        "--base-dir",
        default=".",
        help="Backend working directory. Railway Docker deployments normally use /app/backend.",
    )
    parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=0,
        help="Only select customer files at least this old. Default 0 selects all historical files.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform deletion. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()

    result = purge(
        base_dir=Path(args.base_dir),
        older_than_minutes=args.older_than_minutes,
        apply=args.apply,
    )
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry_run",
                "selected_file_count": result.files,
                "selected_bytes": result.bytes,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
