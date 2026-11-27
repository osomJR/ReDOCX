from __future__ import annotations

"""
Paid-plan batch upload entitlement policy.

This module is intentionally small and framework-light: route handlers call it
before any file stream is persisted, then the existing single-file upload
security pipeline still validates every file individually.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from fastapi import UploadFile
from backend.subscriptions import UserEntitlement


BATCH_UPLOAD_LIMITS_BY_PLAN: dict[str, int] = {
    "personal": 20,
    "business": 50,
    "enterprise": 100,
}

# Conservative per-request worker counts. These make B2B batches materially faster
# without letting one tenant fan out enough LLM/file work to starve the process.
# Override with REDOCX_BATCH_CONCURRENCY_{PLAN} or REDOCX_BATCH_CONCURRENCY_MAX.
BATCH_UPLOAD_CONCURRENCY_BY_PLAN: dict[str, int] = {
    "personal": 2,
    "business": 5,
    "enterprise": 10,
}
DEFAULT_BATCH_CONCURRENCY_MAX = 12

PAID_BATCH_PLANS = frozenset(BATCH_UPLOAD_LIMITS_BY_PLAN)
PLAN_ALIASES = {
    "individual": "personal",
    "starter": "personal",
    "pro": "personal",
    "professional": "personal",
    "team": "business",
    "teams": "business",
    "organization": "business",
    "organisation": "business",
    "corp": "business",
    "company": "business",
    "enterprise_plus": "enterprise",
    "enterprise-plus": "enterprise",
}


@dataclass(frozen=True)
class BatchUploadPolicy:
    plan: str
    max_uploads: int
    extension: str
    file_count: int
    feature: str
    max_concurrency: int = 1


class BatchUploadPolicyError(ValueError):
    status_code = 400
    error_code = "invalid_batch_upload"


class BatchUploadEntitlementError(BatchUploadPolicyError):
    status_code = 403
    error_code = "batch_upload_plan_required"


class BatchUploadLimitError(BatchUploadPolicyError):
    status_code = 403
    error_code = "batch_upload_limit_exceeded"


class DuplicateBatchUploadError(BatchUploadPolicyError):
    status_code = 400
    error_code = "duplicate_batch_upload"


_BATCH_HASH_CHUNK_SIZE = 1024 * 1024


def _upload_display_name(upload: UploadFile, index: int) -> str:
    filename = (getattr(upload, "filename", "") or "").strip()
    return filename or f"upload-{index}"


def _upload_size(upload: UploadFile) -> int | None:
    size = getattr(upload, "size", None)
    if isinstance(size, int) and size >= 0:
        return size
    return None


def _upload_content_hash(upload: UploadFile) -> str:
    file_obj = getattr(upload, "file", None)
    if file_obj is None:
        raise BatchUploadPolicyError("Could not read an uploaded file for duplicate checking.")

    original_position: int | None = None
    try:
        original_position = file_obj.tell()
    except (AttributeError, OSError):
        original_position = None

    try:
        file_obj.seek(0)
        digest = hashlib.sha256()

        while True:
            chunk = file_obj.read(_BATCH_HASH_CHUNK_SIZE)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            digest.update(chunk)

        return digest.hexdigest()
    finally:
        try:
            file_obj.seek(original_position if original_position is not None else 0)
        except (AttributeError, OSError):
            # The normal FastAPI UploadFile stream is seekable. If a custom stream
            # is not, later single-file validation will surface the read issue.
            pass


def find_duplicate_upload_content(
    files: Sequence[UploadFile] | None,
) -> dict[int, int]:
    """Return duplicate upload indexes mapped to their first matching index.

    Indexes are one-based so callers can use them directly in batch response
    items. File streams are restored after hashing.
    """
    indexed_files = list(enumerate(list(files or []), start=1))
    if len(indexed_files) < 2:
        return {}

    # Exact duplicates must have the same byte size. When Starlette exposes size,
    # hash only same-size groups to avoid unnecessary reads. Unknown-size uploads
    # are conservatively hashed because they may still be duplicates.
    size_groups: dict[int | None, list[tuple[int, UploadFile]]] = {}
    for index, upload in indexed_files:
        size_groups.setdefault(_upload_size(upload), []).append((index, upload))

    duplicate_of: dict[int, int] = {}
    for group in size_groups.values():
        if len(group) < 2:
            continue

        seen_hashes: dict[str, int] = {}
        for index, upload in group:
            content_hash = _upload_content_hash(upload)
            original_index = seen_hashes.get(content_hash)
            if original_index is not None:
                duplicate_of[index] = original_index
                continue
            seen_hashes[content_hash] = index

    return duplicate_of


def _ensure_no_duplicate_upload_content(files: Sequence[UploadFile]) -> None:
    """Reject exact duplicate files inside a single batch request."""
    file_list = list(files or [])
    duplicate_of = find_duplicate_upload_content(file_list)
    if not duplicate_of:
        return

    duplicate_index = min(duplicate_of)
    original_index = duplicate_of[duplicate_index]
    duplicate_upload = file_list[duplicate_index - 1]
    original_upload = file_list[original_index - 1]
    raise DuplicateBatchUploadError(
        "Duplicate file rejected: "
        f"{_upload_display_name(duplicate_upload, duplicate_index)!r} has the same content as "
        f"{_upload_display_name(original_upload, original_index)!r}. "
        "Remove one copy before starting the batch."
    )


def ensure_no_duplicate_upload_content(files: Sequence[UploadFile] | None) -> None:
    """Reject byte-for-byte duplicate uploads without applying batch-plan rules.

    Multi-input features such as Compliance, Structured Extraction, and PDF
    Combine intentionally allow document sets outside the paid batch product,
    but they still must not process the same file twice.
    """
    _ensure_no_duplicate_upload_content(list(files or []))


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def batch_upload_concurrency_for_plan(plan: str, file_count: int) -> int:
    """Return the bounded worker count for one batch request.

    This is intentionally per-request concurrency, not a global queue. It improves
    perceived speed for B2B batches while still respecting per-plan limits and a
    deployment-wide cap.
    """
    normalized_plan = _normalize_plan(plan)
    default_concurrency = BATCH_UPLOAD_CONCURRENCY_BY_PLAN.get(normalized_plan, 1)
    env_name = f"REDOCX_BATCH_CONCURRENCY_{normalized_plan.upper()}" if normalized_plan else ""
    configured = _read_positive_int_env(env_name, default_concurrency) if env_name else default_concurrency
    hard_cap = _read_positive_int_env("REDOCX_BATCH_CONCURRENCY_MAX", DEFAULT_BATCH_CONCURRENCY_MAX)
    safe_file_count = max(1, int(file_count or 1))
    return max(1, min(configured, hard_cap, safe_file_count))


def require_batch_upload_entitlement(
    user: Any,
    *,
    feature: str,
    files: Sequence[UploadFile] | None,
) -> BatchUploadPolicy:
    """
    Validate paid-plan batch access and same-extension batch constraints.

    Rules:
    - batch processing is available only to paid plan users;
    - personal can process up to 20 files per batch;
    - business can process up to 50 files per batch;
    - enterprise can process up to 100 files per batch;
    - every file in one batch must use the same normalized extension;
    - exact duplicate files are identified per item so unique files can proceed.
    """
    plan = require_paid_batch_plan(user)

    file_list = list(files or [])
    if not file_list:
        raise BatchUploadPolicyError("At least one file is required for batch processing.")

    duplicate_of = find_duplicate_upload_content(file_list)
    unique_files = [
        upload
        for index, upload in enumerate(file_list, start=1)
        if index not in duplicate_of
    ]
    unique_file_count = len(unique_files)

    extensions = [_normalized_upload_extension(file) for file in unique_files]
    unique_extensions = sorted(set(extensions))
    if len(unique_extensions) != 1:
        raise BatchUploadPolicyError(
            "All unique files in a batch must use the same file extension. "
            f"Received: {', '.join(unique_extensions)}."
        )

    max_uploads = BATCH_UPLOAD_LIMITS_BY_PLAN[plan]
    if unique_file_count > max_uploads:
        raise BatchUploadLimitError(
            f"Your {plan.title()} plan supports up to {max_uploads} unique uploads with the same file extension "
            f"per feature batch. You submitted {unique_file_count} unique files."
        )

    return BatchUploadPolicy(
        plan=plan,
        max_uploads=max_uploads,
        extension=unique_extensions[0],
        file_count=len(file_list),
        feature=str(feature or "").strip(),
        max_concurrency=batch_upload_concurrency_for_plan(plan, unique_file_count),
    )


def resolve_paid_plan(user: Any) -> str:
    """Read a normalized entitlement, not an Auth0 identity or raw billing status.

    The route supplies UserEntitlement from get_user_entitlement. A canonical
    mapping is supported for internal callers using serialized entitlements.
    is_paid already incorporates paid-through dates, grace and revocations.
    """
    if isinstance(user, UserEntitlement):
        plan, is_paid = user.plan, user.is_paid
    elif isinstance(user, Mapping):
        entitlement = user.get("entitlement", user)
        if not isinstance(entitlement, Mapping):
            return "free"
        plan, is_paid = entitlement.get("plan"), entitlement.get("is_paid")
    else:
        return "free"
    return plan if is_paid is True and isinstance(plan, str) and plan in PAID_BATCH_PLANS else "free"


def require_paid_batch_plan(entitlement: Any) -> str:
    plan = resolve_paid_plan(entitlement)
    if plan not in PAID_BATCH_PLANS:
        raise BatchUploadEntitlementError(
            "Batch processing is available only on Personal, Business, and Enterprise plans."
        )
    return plan


def _normalize_plan(value: str) -> str:
    token = _normalize_token(value)
    if not token:
        return ""
    return PLAN_ALIASES.get(token, token)


def _normalize_token(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
    )


def _normalized_upload_extension(upload: UploadFile) -> str:
    filename = (getattr(upload, "filename", "") or "").strip()
    extension = Path(filename).suffix.strip().lower()
    if extension == ".jpe":
        extension = ".jpg"
    if not extension:
        raise BatchUploadPolicyError("Every file in a batch must include a valid file extension.")
    return extension
