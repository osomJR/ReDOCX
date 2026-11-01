"""Vault-only aggregate upload limits; billing is resolved by the calling route.

All sizes use 1 MB = 1,048,576 bytes, consistent with existing ReDOCX limits.
The multipart parser's private seekable streams are measured directly, rather
than trusting Content-Length, filename extensions, or submitted size metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Mapping, Sequence

MB = 1024 * 1024
VAULT_UPLOAD_LIMITS = {
    "free": (5, 20 * MB),
    "personal": (20, 100 * MB),
    "business": (50, 250 * MB),
    "enterprise": (100, 500 * MB),
}


@dataclass(frozen=True)
class VaultUploadPolicy:
    plan: str
    max_files: int
    max_total_bytes: int
    file_count: int
    total_bytes: int

    def details(self) -> dict[str, Any]:
        return {"plan": self.plan, "max_files": self.max_files,
                "max_total_bytes": self.max_total_bytes,
                "file_count": self.file_count, "total_bytes": self.total_bytes}


class VaultUploadLimitError(ValueError):
    def __init__(self, policy: VaultUploadPolicy, dimension: str):
        self.policy = policy
        self.error_code = f"VAULT_{policy.plan.upper()}_UPLOAD_{dimension}_EXCEEDED"
        super().__init__(self.error_code)


def _value(entitlement: Any, key: str, default: Any = None) -> Any:
    return entitlement.get(key, default) if isinstance(entitlement, Mapping) else getattr(entitlement, key, default)


def validate_vault_uploads(entitlement: Any, files: Sequence[Any]) -> VaultUploadPolicy:
    """Check the whole selection before any item is scanned, encrypted, or saved.

    Pass only get_user_entitlement(user_id), never client-submitted plan claims.
    Failed/unknown entitlements cannot grant a paid Vault allowance.
    """
    plan = str(_value(entitlement, "plan", "free")).strip().lower()
    if plan not in VAULT_UPLOAD_LIMITS or (plan != "free" and _value(entitlement, "is_paid") is not True):
        plan = "free"
    max_files, max_bytes = VAULT_UPLOAD_LIMITS[plan]
    policy = VaultUploadPolicy(plan, max_files, max_bytes, len(files), 0)
    if not files:
        raise ValueError("Choose at least one Vault file to continue.")
    if len(files) > max_files:
        raise VaultUploadLimitError(policy, "COUNT")
    total = 0
    for upload in files:
        stream = upload.file
        position = stream.tell()
        try:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
        finally:
            stream.seek(position)
        if size <= 0:
            raise ValueError("Uploaded file is empty.")
        total += size
        if total > max_bytes:
            raise VaultUploadLimitError(
                VaultUploadPolicy(plan, max_files, max_bytes, len(files), total), "SIZE")
    return VaultUploadPolicy(plan, max_files, max_bytes, len(files), total)
