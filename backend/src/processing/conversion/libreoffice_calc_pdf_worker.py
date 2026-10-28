from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import uno
from com.sun.star.beans import PropertyValue


def _prop(name: str, value):
    item = PropertyValue()
    item.Name = name
    item.Value = value
    return item


def _resolve_unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _used_range(sheet):
    cursor = sheet.createCursor()
    cursor.gotoEndOfUsedArea(True)
    address = cursor.RangeAddress
    return address, sheet.getCellRangeByPosition(
        address.StartColumn,
        address.StartRow,
        address.EndColumn,
        address.EndRow,
    )


def _normalize_calc_layout(document, profile: str) -> None:
    profile = (profile or "balanced").strip().lower()
    page_styles = document.StyleFamilies.getByName("PageStyles")

    for index in range(document.Sheets.Count):
        sheet = document.Sheets.getByIndex(index)
        address, used_range = _used_range(sheet)

        # Safe mode never trusts precomputed font metrics. Wrapping is applied by
        # the exact Calc process that will render the PDF.
        if profile == "safe":
            used_range.IsTextWrapped = True

        # Recalculate row geometry inside Calc, not by OOXML hints. OptimalHeight
        # is the authoritative layout primitive and accounts for the fonts actually
        # installed in this container.
        # XTableRows exposes TableRow properties across the entire row range.
        # Applying OptimalHeight in one UNO call avoids O(row_count) cross-process
        # calls on large spreadsheets while retaining hidden-row visibility.
        used_range.Rows.OptimalHeight = True

        # Enforce one-page-wide, unlimited-height pagination in Calc itself. The
        # OOXML patch remains a compatibility hint; these UNO properties are the
        # rendering authority.
        page_style = page_styles.getByName(sheet.PageStyle)
        info = page_style.PropertySetInfo
        names = {item.Name for item in info.Properties}
        if "ScaleToPagesX" in names:
            page_style.ScaleToPagesX = 1
        if "ScaleToPagesY" in names:
            page_style.ScaleToPagesY = 0
        if "ScaleToPages" in names:
            page_style.ScaleToPages = 0

    # Do not force formula recalculation here. UpdateDocMode.NO_UPDATE on load plus
    # omission of calculateAll preserves cached workbook results and prevents a
    # conversion from depending on unavailable external links/data connections.


def _run(source: Path, output: Path, soffice: str, profile: str, timeout: int) -> None:
    source = source.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError("XLSX source file is missing or empty.")

    port = _resolve_unused_port()
    with tempfile.TemporaryDirectory(prefix="redocx-lo-uno-") as profile_dir:
        profile_uri = Path(profile_dir).resolve().as_uri()
        accept = f"socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager"
        command = [
            soffice,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--nofirststartwizard",
            f"-env:UserInstallation={profile_uri}",
            f"--accept={accept}",
        ]
        environment = {
            **os.environ,
            "HOME": profile_dir,
            "TMPDIR": profile_dir,
            "SAL_USE_VCLPLUGIN": os.getenv("SAL_USE_VCLPLUGIN", "svp"),
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
        }
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )

        document = None
        desktop = None
        try:
            local_context = uno.getComponentContext()
            resolver = local_context.ServiceManager.createInstanceWithContext(
                "com.sun.star.bridge.UnoUrlResolver", local_context
            )
            context = None
            deadline = time.monotonic() + min(max(timeout, 10), 45)
            last_error = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=1)
                    detail = (stderr or stdout or "").strip()[-2000:]
                    raise RuntimeError(
                        "LibreOffice UNO process terminated before accepting a connection."
                        + (f" Details: {detail}" if detail else "")
                    )
                try:
                    context = resolver.resolve(
                        f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
                    )
                    break
                except Exception as exc:  # UNO raises implementation-specific errors.
                    last_error = exc
                    time.sleep(0.10)
            if context is None:
                raise RuntimeError(
                    f"Timed out connecting to LibreOffice UNO layout service: {last_error}"
                )

            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", context
            )
            load_properties = (
                _prop("Hidden", True),
                _prop("ReadOnly", False),
                _prop("MacroExecutionMode", uno.getConstantByName(
                    "com.sun.star.document.MacroExecMode.NEVER_EXECUTE"
                )),
                _prop("UpdateDocMode", uno.getConstantByName(
                    "com.sun.star.document.UpdateDocMode.NO_UPDATE"
                )),
            )
            document = desktop.loadComponentFromURL(
                uno.systemPathToFileUrl(str(source)), "_blank", 0, load_properties
            )
            if document is None:
                raise RuntimeError("LibreOffice UNO could not load the staged XLSX workbook.")

            _normalize_calc_layout(document, profile)

            filter_data = (
                _prop("UseLosslessCompression", True),
                _prop("ReduceImageResolution", False),
                _prop("UseTaggedPDF", True),
                _prop("ExportBookmarks", True),
            )
            document.storeToURL(
                uno.systemPathToFileUrl(str(output)),
                (
                    _prop("FilterName", "calc_pdf_Export"),
                    _prop("Overwrite", True),
                    _prop("FilterData", filter_data),
                ),
            )
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError("LibreOffice UNO completed without producing a PDF.")
        finally:
            if document is not None:
                try:
                    document.close(True)
                except Exception:
                    try:
                        document.dispose()
                    except Exception:
                        pass
            if desktop is not None:
                try:
                    desktop.terminate()
                except Exception:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--soffice", required=True)
    parser.add_argument("--profile", choices=("balanced", "safe"), default="balanced")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    try:
        _run(Path(args.source), Path(args.output), args.soffice, args.profile, args.timeout)
        print(json.dumps({"ok": True, "output": str(Path(args.output).resolve())}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
