from __future__ import annotations

"""
Paid-plan batch upload entitlement policy.

This module is intentionally small and framework-light: route handlers call it
before any file stream is persisted, then the existing single-file upload
security pipeline still validates every file individually.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from fastapi import UploadFile


BATCH_UPLOAD_LIMITS_BY_PLAN: dict[str, int] = {
    "personal": 5,
    "business": 10,
    "enterprise": 20,
}

PAID_BATCH_PLANS = frozenset(BATCH_UPLOAD_LIMITS_BY_PLAN)
INACTIVE_ENTITLEMENT_STATUSES = frozenset(
    {
        "cancelled",
        "canceled",
        "expired",
        "inactive",
        "past_due",
        "unpaid",
        "free",
        "none",
        "disabled",
    }
)
PAID_BOOLEAN_KEYS = (
    "is_paid",
    "paid",
    "has_paid_plan",
    "has_active_subscription",
    "active_subscription",
)
PLAN_KEYS = (
    "plan",
    "plan_id",
    "plan_key",
    "plan_name",
    "plan_slug",
    "product_plan",
    "subscription_plan",
    "tier",
    "tier_id",
    "tier_name",
)
STATUS_KEYS = (
    "status",
    "subscription_status",
    "billing_status",
    "entitlement_status",
)
ENTITLEMENT_KEYS = (
    "entitlement",
    "billing_entitlement",
    "subscription",
    "plan_entitlement",
    "app_metadata",
    "user_metadata",
    "claims",
)
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


class BatchUploadPolicyError(ValueError):
    status_code = 400
    error_code = "invalid_batch_upload"


class BatchUploadEntitlementError(BatchUploadPolicyError):
    status_code = 403
    error_code = "batch_upload_plan_required"


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


def _ensure_no_duplicate_upload_content(files: Sequence[UploadFile]) -> None:
    """Reject exact duplicate files inside a single batch request."""
    indexed_files = list(enumerate(files, start=1))
    if len(indexed_files) < 2:
        return

    # Exact duplicates must have the same byte size. When Starlette exposes size,
    # hash only same-size groups to avoid unnecessary reads. Unknown-size uploads
    # are conservatively hashed because they may still be duplicates.
    size_groups: dict[int | None, list[tuple[int, UploadFile]]] = {}
    for index, upload in indexed_files:
        size_groups.setdefault(_upload_size(upload), []).append((index, upload))

    seen_hashes: dict[str, tuple[int, UploadFile]] = {}
    for group in size_groups.values():
        if len(group) < 2:
            continue

        for index, upload in group:
            content_hash = _upload_content_hash(upload)
            original = seen_hashes.get(content_hash)
            if original is not None:
                original_index, original_upload = original
                raise DuplicateBatchUploadError(
                    "Duplicate file rejected: "
                    f"{_upload_display_name(upload, index)!r} has the same content as "
                    f"{_upload_display_name(original_upload, original_index)!r}. "
                    "Remove one copy before starting the batch."
                )
            seen_hashes[content_hash] = (index, upload)


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
    - personal can process up to 5 files per batch;
    - business can process up to 10 files per batch;
    - enterprise can process up to 20 files per batch;
    - every file in one batch must use the same normalized extension;
    - exact duplicate files in the same batch are rejected by content hash.
    """
    plan = resolve_paid_plan(user)
    if plan not in PAID_BATCH_PLANS:
        raise BatchUploadEntitlementError(
            "Batch processing is available only on Personal, Business, and Enterprise plans."
        )

    file_list = list(files or [])
    if not file_list:
        raise BatchUploadPolicyError("At least one file is required for batch processing.")

    extensions = [_normalized_upload_extension(file) for file in file_list]
    unique_extensions = sorted(set(extensions))
    if len(unique_extensions) != 1:
        raise BatchUploadPolicyError(
            "All files in a batch must use the same file extension. "
            f"Received: {', '.join(unique_extensions)}."
        )

    max_uploads = BATCH_UPLOAD_LIMITS_BY_PLAN[plan]
    if len(file_list) > max_uploads:
        raise BatchUploadEntitlementError(
            f"Your {plan.title()} plan supports up to {max_uploads} uploads with the same file extension "
            f"per feature batch. You submitted {len(file_list)} files."
        )

    _ensure_no_duplicate_upload_content(file_list)

    return BatchUploadPolicy(
        plan=plan,
        max_uploads=max_uploads,
        extension=unique_extensions[0],
        file_count=len(file_list),
        feature=str(feature or "").strip(),
    )


def resolve_paid_plan(user: Any) -> str:
    """
    Extract the normalized billing plan from common Auth0/JWT/account shapes.

    The backend remains authoritative. Browser-side checks can improve UX, but
    cannot grant access to batch processing.
    """
    candidates = _candidate_sources(user)

    explicit_paid = _first_bool(candidates, PAID_BOOLEAN_KEYS)
    if explicit_paid is False:
        return "free"

    status = _normalize_token(_first_string(candidates, STATUS_KEYS))
    if status in INACTIVE_ENTITLEMENT_STATUSES:
        return "free"

    raw_plan = _first_string(candidates, PLAN_KEYS)
    plan = _normalize_plan(raw_plan)
    if not plan:
        return "free"

    if plan not in PAID_BATCH_PLANS:
        return plan

    return plan if explicit_paid is not False else "free"


def _candidate_sources(user: Any) -> list[Any]:
    sources: list[Any] = []

    def add(source: Any) -> None:
        if source is None:
            return
        if source in sources:
            return
        sources.append(source)

    add(user)
    for key in ENTITLEMENT_KEYS:
        value = _get_value(user, key)
        add(value)

    # Auth0 custom claims are often namespaced URLs. Include one nested mapping
    # level so deployments can store entitlement under app-specific claim names.
    for source in list(sources):
        if isinstance(source, Mapping):
            for key, value in source.items():
                lowered = str(key).lower()
                if any(token in lowered for token in ("plan", "tier", "entitlement", "subscription", "billing")):
                    add(value)

    return sources


def _first_string(sources: Sequence[Any], keys: Sequence[str]) -> str:
    for source in sources:
        for key in keys:
            value = _get_value(source, key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, Mapping):
                nested = _first_string([value], keys)
                if nested:
                    return nested
    return ""


def _first_bool(sources: Sequence[Any], keys: Sequence[str]) -> bool | None:
    for source in sources:
        for key in keys:
            value = _get_value(source, key)
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                return value.strip().lower() == "true"
    return None


def _get_value(source: Any, key: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        if key in source:
            return source[key]
        for existing_key, value in source.items():
            if str(existing_key).lower() == key.lower():
                return value
        return None
    return getattr(source, key, None)


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
