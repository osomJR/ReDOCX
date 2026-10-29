"""Validate display names without turning customer filenames into storage paths."""
from __future__ import annotations

from pathlib import PurePath


def validate_vault_filename(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A non-empty Vault filename is required.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("The Vault filename contains a control character.")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("The Vault filename must not contain a path.")
    suffixes = [suffix.lower() for suffix in PurePath(value.rstrip()).suffixes]
    dangerous = {".exe", ".dll", ".bat", ".cmd", ".sh", ".js", ".mjs", ".php", ".py", ".jar", ".scr", ".vbs", ".ps1", ".msi", ".apk", ".com", ".pif"}
    if len(suffixes) > 1 and any(suffix in dangerous for suffix in suffixes[:-1]):
        raise ValueError("The Vault filename contains a dangerous double extension.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValueError("The Vault filename is not valid Unicode.") from exc
    if len(encoded) > 1024:
        raise ValueError("The Vault filename exceeds 1,024 UTF-8 bytes.")
    return value
