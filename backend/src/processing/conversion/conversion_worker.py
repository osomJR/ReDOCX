"""Private subprocess entry point. No customer artifact ownership is transferred."""

from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from backend.src.processing.conversion.convert import RealConversionBackend


def main():
    request_file, response_file = map(Path, sys.argv[1:3])
    root = response_file.parent.resolve()

    class StagingStorage:
        def persist(self, *, source_file_path, artifact_name, **kwargs):
            destination = root / Path(artifact_name).name
            if destination.name in ("request.json", "response.json"):
                raise ValueError("Reserved output file name.")
            shutil.copy2(source_file_path, destination)
            return SimpleNamespace(
                stored_path=str(destination), storage_key=None, download_url=None
            )

    try:
        artifact = RealConversionBackend(StagingStorage(), _in_worker=True).convert(
            **json.loads(request_file.read_text(encoding="utf-8"))
        )
        output = Path(artifact.file_path)
        result = {
            "ok": True,
            "path": str(output),
            "format": artifact.file_extension,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        }
    except Exception as exc:
        result = {"ok": False, "type": type(exc).__name__, "error": str(exc)}
    response_file.write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
