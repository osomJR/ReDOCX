from __future__ import annotations

"""
Centralized public API error normalization for ReDOCX.

This module is the boundary between backend failures and user-visible frontend
messages. It intentionally separates:

- internal diagnostic text (logs only),
- stable public error codes,
- safe user-facing messages, and
- transport semantics such as HTTP status and retryability.

Public response shape
---------------------
The canonical response is::

    {
        "success": False,
        "error": {
            "code": "SOURCE_FILE_NOT_FOUND",
            "message": "The required file is no longer available. Please upload it again.",
            "retryable": False,
        },
        "detail": {
            "error": "source_file_not_found",
            "message": "The required file is no longer available. Please upload it again.",
            "retryable": False,
        },
    }

``detail`` is a compatibility alias for existing ReDOCX browser clients and is
not a second source of truth. New clients should prefer the top-level ``error``
object.

Security rules
--------------
- Raw exception/provider/configuration messages are never returned by default.
- A source message is exposed only for explicitly allow-listed, user-actionable
  backend error tags whose attached route code already treats that message as
  public UI copy.
- Unknown failures stay generic rather than guessing at a cause.
- Original HTTP status codes and safe response headers are preserved.
"""

import logging
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    # Input / contract.
    INPUT_REQUIRED = "INPUT_REQUIRED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_ACTION = "INVALID_ACTION"
    INVALID_UPLOAD_METADATA = "INVALID_UPLOAD_METADATA"
    INVALID_FILE_ENCODING = "INVALID_FILE_ENCODING"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    UNSUPPORTED_CONVERSION_PAIR = "UNSUPPORTED_CONVERSION_PAIR"
    UNSUPPORTED_OUTPUT_FORMAT = "UNSUPPORTED_OUTPUT_FORMAT"

    # Files / upload safety.
    FILE_EMPTY = "FILE_EMPTY"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PASSWORD_PROTECTED_FILE = "PASSWORD_PROTECTED_FILE"
    UNSAFE_FILE = "UNSAFE_FILE"
    MALWARE_DETECTED = "MALWARE_DETECTED"
    UPLOAD_SECURITY_UNAVAILABLE = "UPLOAD_SECURITY_UNAVAILABLE"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    SOURCE_FILE_NOT_FOUND = "SOURCE_FILE_NOT_FOUND"
    UPLOAD_PERSIST_FAILED = "UPLOAD_PERSIST_FAILED"

    # Generic resources / HTTP state.
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    RESOURCE_GONE = "RESOURCE_GONE"
    REQUEST_CONFLICT = "REQUEST_CONFLICT"
    REQUEST_TOO_EARLY = "REQUEST_TOO_EARLY"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

    # Processing / workflows.
    PROCESSING_FAILED = "PROCESSING_FAILED"
    PROCESSING_OUTPUT_MISSING = "PROCESSING_OUTPUT_MISSING"
    PROCESSING_RESOURCE_LIMIT = "PROCESSING_RESOURCE_LIMIT"
    PREVIEW_UNAVAILABLE = "PREVIEW_UNAVAILABLE"
    WORKFLOW_PREREQUISITE_REQUIRED = "WORKFLOW_PREREQUISITE_REQUIRED"
    FEATURE_NOT_AVAILABLE = "FEATURE_NOT_AVAILABLE"
    FEATURE_NOT_CONFIGURED = "FEATURE_NOT_CONFIGURED"

    # Usage / plans.
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    RATE_LIMIT_UNAVAILABLE = "RATE_LIMIT_UNAVAILABLE"
    PLAN_REQUIRED = "PLAN_REQUIRED"
    PLAN_LIMIT_EXCEEDED = "PLAN_LIMIT_EXCEEDED"

    # Authentication / authorization / account lifecycle.
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    INVALID_TOKEN = "INVALID_TOKEN"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    AUTH_PROVIDER_UNAVAILABLE = "AUTH_PROVIDER_UNAVAILABLE"
    ACCOUNT_DELETED = "ACCOUNT_DELETED"
    ACCOUNT_DEACTIVATED = "ACCOUNT_DEACTIVATED"
    ACCOUNT_RECOVERY_EXPIRED = "ACCOUNT_RECOVERY_EXPIRED"
    ACCOUNT_OPERATION_FAILED = "ACCOUNT_OPERATION_FAILED"

    # Billing / subscriptions.
    BILLING_UNAVAILABLE = "BILLING_UNAVAILABLE"
    BILLING_CONFLICT = "BILLING_CONFLICT"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAYMENT_VERIFICATION_FAILED = "PAYMENT_VERIFICATION_FAILED"
    SUBSCRIPTION_OPERATION_FAILED = "SUBSCRIPTION_OPERATION_FAILED"
    INVALID_WEBHOOK = "INVALID_WEBHOOK"

    # Organization / team collaboration.
    ORGANIZATION_ACCESS_DENIED = "ORGANIZATION_ACCESS_DENIED"
    ORGANIZATION_PERMISSION_REQUIRED = "ORGANIZATION_PERMISSION_REQUIRED"
    ORGANIZATION_SEAT_LIMIT_REACHED = "ORGANIZATION_SEAT_LIMIT_REACHED"
    TEAM_SERVICE_UNAVAILABLE = "TEAM_SERVICE_UNAVAILABLE"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"

    # Attachments.
    ATTACHMENT_INVALID = "ATTACHMENT_INVALID"
    ATTACHMENT_NOT_FOUND = "ATTACHMENT_NOT_FOUND"
    ATTACHMENT_TOO_LARGE = "ATTACHMENT_TOO_LARGE"
    ATTACHMENT_QUOTA_EXCEEDED = "ATTACHMENT_QUOTA_EXCEEDED"
    ATTACHMENT_SECURITY_UNAVAILABLE = "ATTACHMENT_SECURITY_UNAVAILABLE"
    ATTACHMENT_INTEGRITY_FAILED = "ATTACHMENT_INTEGRITY_FAILED"

    # Batch processing.
    BATCH_UPLOAD_INVALID = "BATCH_UPLOAD_INVALID"
    BATCH_UPLOAD_PLAN_REQUIRED = "BATCH_UPLOAD_PLAN_REQUIRED"
    DUPLICATE_UPLOAD = "DUPLICATE_UPLOAD"

    # Calls / recording.
    CALL_NOT_FOUND = "CALL_NOT_FOUND"
    CALL_ACCESS_DENIED = "CALL_ACCESS_DENIED"
    CALL_STATE_CONFLICT = "CALL_STATE_CONFLICT"
    CALL_RECORDING_CONSENT_REQUIRED = "CALL_RECORDING_CONSENT_REQUIRED"

    # Upstream / platform.
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_SERVICE_ERROR = "UPSTREAM_SERVICE_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ErrorDefinition:
    code: ErrorCode
    status_code: int
    error_message: str
    friendly_message: str
    retryable: bool = False


ERRORS: dict[ErrorCode, ErrorDefinition] = {
    ErrorCode.INPUT_REQUIRED: ErrorDefinition(
        ErrorCode.INPUT_REQUIRED,
        400,
        "Required input is missing.",
        "Provide the required input and try again.",
    ),
    ErrorCode.INVALID_REQUEST: ErrorDefinition(
        ErrorCode.INVALID_REQUEST,
        422,
        "The request is invalid.",
        "Some request information is invalid. Please review it and try again.",
    ),
    ErrorCode.INVALID_ACTION: ErrorDefinition(
        ErrorCode.INVALID_ACTION,
        422,
        "The requested action is invalid.",
        "This action is not supported.",
    ),
    ErrorCode.INVALID_UPLOAD_METADATA: ErrorDefinition(
        ErrorCode.INVALID_UPLOAD_METADATA,
        400,
        "The uploaded file metadata is invalid.",
        "The uploaded file is missing required information. Please choose the file again.",
    ),
    ErrorCode.INVALID_FILE_ENCODING: ErrorDefinition(
        ErrorCode.INVALID_FILE_ENCODING,
        422,
        "The file encoding is invalid.",
        "This text file must use UTF-8 encoding.",
    ),
    ErrorCode.UNSUPPORTED_FILE_TYPE: ErrorDefinition(
        ErrorCode.UNSUPPORTED_FILE_TYPE,
        422,
        "The file type is not supported.",
        "That file type is not supported for this feature.",
    ),
    ErrorCode.UNSUPPORTED_CONVERSION_PAIR: ErrorDefinition(
        ErrorCode.UNSUPPORTED_CONVERSION_PAIR,
        422,
        "The requested conversion is not supported.",
        "That conversion is not supported.",
    ),
    ErrorCode.UNSUPPORTED_OUTPUT_FORMAT: ErrorDefinition(
        ErrorCode.UNSUPPORTED_OUTPUT_FORMAT,
        422,
        "The requested output format is not supported.",
        "That output format is not supported.",
    ),
    ErrorCode.FILE_EMPTY: ErrorDefinition(
        ErrorCode.FILE_EMPTY,
        422,
        "The file is empty.",
        "The selected file is empty.",
    ),
    ErrorCode.FILE_TOO_LARGE: ErrorDefinition(
        ErrorCode.FILE_TOO_LARGE,
        413,
        "The file is too large.",
        "The file is larger than the allowed limit for this feature.",
    ),
    ErrorCode.PASSWORD_PROTECTED_FILE: ErrorDefinition(
        ErrorCode.PASSWORD_PROTECTED_FILE,
        422,
        "The file is password protected or encrypted.",
        "Password-protected or encrypted files are not supported for this operation. Use an unlocked file.",
    ),
    ErrorCode.UNSAFE_FILE: ErrorDefinition(
        ErrorCode.UNSAFE_FILE,
        422,
        "The file failed a security or structural validation check.",
        "This file could not be accepted safely. Check the file and try a trusted copy.",
    ),
    ErrorCode.MALWARE_DETECTED: ErrorDefinition(
        ErrorCode.MALWARE_DETECTED,
        422,
        "Malware was detected in the uploaded file.",
        "This file was blocked because it failed the malware safety check.",
    ),
    ErrorCode.UPLOAD_SECURITY_UNAVAILABLE: ErrorDefinition(
        ErrorCode.UPLOAD_SECURITY_UNAVAILABLE,
        503,
        "Required upload security infrastructure is unavailable.",
        "Secure file checking is temporarily unavailable. Please try again later.",
        retryable=True,
    ),
    ErrorCode.EXTRACTION_FAILED: ErrorDefinition(
        ErrorCode.EXTRACTION_FAILED,
        422,
        "Usable content could not be extracted from the file.",
        "We couldn't read usable content from this file.",
    ),
    ErrorCode.SOURCE_FILE_NOT_FOUND: ErrorDefinition(
        ErrorCode.SOURCE_FILE_NOT_FOUND,
        404,
        "A required processing file could not be found.",
        "The required file is no longer available. Please upload it again.",
    ),
    ErrorCode.UPLOAD_PERSIST_FAILED: ErrorDefinition(
        ErrorCode.UPLOAD_PERSIST_FAILED,
        500,
        "The uploaded file could not be persisted.",
        "We couldn't save the uploaded file. Please try again.",
        retryable=True,
    ),
    ErrorCode.RESOURCE_NOT_FOUND: ErrorDefinition(
        ErrorCode.RESOURCE_NOT_FOUND,
        404,
        "The requested resource was not found.",
        "The requested item could not be found.",
    ),
    ErrorCode.RESOURCE_GONE: ErrorDefinition(
        ErrorCode.RESOURCE_GONE,
        410,
        "The requested resource is no longer available.",
        "This item is no longer available.",
    ),
    ErrorCode.REQUEST_CONFLICT: ErrorDefinition(
        ErrorCode.REQUEST_CONFLICT,
        409,
        "The request conflicts with the current resource state.",
        "This action can't be completed in the item's current state. Refresh and try again if appropriate.",
    ),
    ErrorCode.REQUEST_TOO_EARLY: ErrorDefinition(
        ErrorCode.REQUEST_TOO_EARLY,
        425,
        "The request was made before the operation became available.",
        "This action is not available yet.",
    ),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorDefinition(
        ErrorCode.METHOD_NOT_ALLOWED,
        405,
        "The HTTP method is not allowed for this endpoint.",
        "This operation is not available for the requested action.",
    ),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorDefinition(
        ErrorCode.PAYLOAD_TOO_LARGE,
        413,
        "The request payload is too large.",
        "The request is larger than the allowed limit.",
    ),
    ErrorCode.PROCESSING_FAILED: ErrorDefinition(
        ErrorCode.PROCESSING_FAILED,
        500,
        "Processing failed.",
        "We couldn't complete processing for this request.",
        retryable=True,
    ),
    ErrorCode.PROCESSING_OUTPUT_MISSING: ErrorDefinition(
        ErrorCode.PROCESSING_OUTPUT_MISSING,
        500,
        "Processing completed without the expected output.",
        "Processing finished, but the expected output could not be prepared.",
        retryable=True,
    ),
    ErrorCode.PROCESSING_RESOURCE_LIMIT: ErrorDefinition(
        ErrorCode.PROCESSING_RESOURCE_LIMIT,
        503,
        "Processing exceeded a server-side resource limit.",
        "This file could not be processed within the available resource limits.",
        retryable=False,
    ),
    ErrorCode.PREVIEW_UNAVAILABLE: ErrorDefinition(
        ErrorCode.PREVIEW_UNAVAILABLE,
        503,
        "A preview could not be prepared.",
        "The preview is temporarily unavailable.",
        retryable=True,
    ),
    ErrorCode.WORKFLOW_PREREQUISITE_REQUIRED: ErrorDefinition(
        ErrorCode.WORKFLOW_PREREQUISITE_REQUIRED,
        409,
        "A required workflow prerequisite is missing.",
        "Complete the required previous step before continuing.",
    ),
    ErrorCode.FEATURE_NOT_AVAILABLE: ErrorDefinition(
        ErrorCode.FEATURE_NOT_AVAILABLE,
        403,
        "The feature is not available for the current entitlement.",
        "This feature isn't available for the current account or plan.",
    ),
    ErrorCode.FEATURE_NOT_CONFIGURED: ErrorDefinition(
        ErrorCode.FEATURE_NOT_CONFIGURED,
        503,
        "A required feature dependency is not configured or available.",
        "This feature is temporarily unavailable.",
        retryable=True,
    ),
    ErrorCode.RATE_LIMIT_EXCEEDED: ErrorDefinition(
        ErrorCode.RATE_LIMIT_EXCEEDED,
        429,
        "A usage or concurrency limit was exceeded.",
        "You've reached a usage limit for this operation. Please try again later.",
        retryable=True,
    ),
    ErrorCode.RATE_LIMIT_UNAVAILABLE: ErrorDefinition(
        ErrorCode.RATE_LIMIT_UNAVAILABLE,
        503,
        "Usage-limit enforcement is unavailable.",
        "We can't verify usage limits right now. Please try again later.",
        retryable=True,
    ),
    ErrorCode.PLAN_REQUIRED: ErrorDefinition(
        ErrorCode.PLAN_REQUIRED,
        403,
        "An eligible paid plan is required.",
        "This feature requires an eligible ReDOCX plan.",
    ),
    ErrorCode.PLAN_LIMIT_EXCEEDED: ErrorDefinition(
        ErrorCode.PLAN_LIMIT_EXCEEDED,
        403,
        "The current account or plan limit was exceeded.",
        "The current account or plan limit has been reached.",
    ),
    ErrorCode.AUTHORIZATION_REQUIRED: ErrorDefinition(
        ErrorCode.AUTHORIZATION_REQUIRED,
        401,
        "Authentication is required.",
        "Please sign in to continue.",
    ),
    ErrorCode.INVALID_TOKEN: ErrorDefinition(
        ErrorCode.INVALID_TOKEN,
        401,
        "The access token is invalid or expired.",
        "Your session is invalid or expired. Please sign in again.",
    ),
    ErrorCode.PERMISSION_DENIED: ErrorDefinition(
        ErrorCode.PERMISSION_DENIED,
        403,
        "The authenticated user is not permitted to perform this operation.",
        "You don't have permission to perform this action.",
    ),
    ErrorCode.INSUFFICIENT_SCOPE: ErrorDefinition(
        ErrorCode.INSUFFICIENT_SCOPE,
        403,
        "The access token does not have the required scope.",
        "You don't have permission to use this feature.",
    ),
    ErrorCode.AUTH_PROVIDER_UNAVAILABLE: ErrorDefinition(
        ErrorCode.AUTH_PROVIDER_UNAVAILABLE,
        503,
        "The authentication provider or its management API is unavailable.",
        "Account authentication services are temporarily unavailable. Please try again later.",
        retryable=True,
    ),
    ErrorCode.ACCOUNT_DELETED: ErrorDefinition(
        ErrorCode.ACCOUNT_DELETED,
        410,
        "The account no longer exists.",
        "This account is no longer available.",
    ),
    ErrorCode.ACCOUNT_DEACTIVATED: ErrorDefinition(
        ErrorCode.ACCOUNT_DEACTIVATED,
        403,
        "The account is deactivated or being deactivated.",
        "This account is currently deactivated. Restore it before continuing, if restoration is still available.",
    ),
    ErrorCode.ACCOUNT_RECOVERY_EXPIRED: ErrorDefinition(
        ErrorCode.ACCOUNT_RECOVERY_EXPIRED,
        410,
        "The account recovery window has elapsed.",
        "The account restoration window has ended.",
    ),
    ErrorCode.ACCOUNT_OPERATION_FAILED: ErrorDefinition(
        ErrorCode.ACCOUNT_OPERATION_FAILED,
        500,
        "An account lifecycle operation could not be completed.",
        "We couldn't complete the account request. Please try again.",
        retryable=True,
    ),
    ErrorCode.BILLING_UNAVAILABLE: ErrorDefinition(
        ErrorCode.BILLING_UNAVAILABLE,
        503,
        "Billing is unavailable or not ready.",
        "Billing is temporarily unavailable. Please try again before starting another payment.",
        retryable=True,
    ),
    ErrorCode.BILLING_CONFLICT: ErrorDefinition(
        ErrorCode.BILLING_CONFLICT,
        409,
        "The billing request conflicts with the current subscription state.",
        "This billing change can't be completed in the current subscription state.",
    ),
    ErrorCode.PAYMENT_PENDING: ErrorDefinition(
        ErrorCode.PAYMENT_PENDING,
        409,
        "Payment confirmation or entitlement activation is still pending.",
        "Payment confirmation is still pending. Please don't make another payment; try the confirmation again shortly.",
        retryable=True,
    ),
    ErrorCode.PAYMENT_VERIFICATION_FAILED: ErrorDefinition(
        ErrorCode.PAYMENT_VERIFICATION_FAILED,
        502,
        "Payment verification failed or did not match the checkout record.",
        "We couldn't verify this payment safely. No entitlement was changed. Please retry verification or contact support if you were charged.",
        retryable=True,
    ),
    ErrorCode.SUBSCRIPTION_OPERATION_FAILED: ErrorDefinition(
        ErrorCode.SUBSCRIPTION_OPERATION_FAILED,
        502,
        "A subscription operation failed.",
        "We couldn't update the subscription safely. Please try again; don't start a second subscription.",
        retryable=True,
    ),
    ErrorCode.INVALID_WEBHOOK: ErrorDefinition(
        ErrorCode.INVALID_WEBHOOK,
        400,
        "A billing webhook could not be verified or processed.",
        "The payment notification could not be verified.",
    ),
    ErrorCode.ORGANIZATION_ACCESS_DENIED: ErrorDefinition(
        ErrorCode.ORGANIZATION_ACCESS_DENIED,
        403,
        "The user does not have access to the organization resource.",
        "You no longer have access to this organization or conversation.",
    ),
    ErrorCode.ORGANIZATION_PERMISSION_REQUIRED: ErrorDefinition(
        ErrorCode.ORGANIZATION_PERMISSION_REQUIRED,
        403,
        "An organization role or ownership permission is required.",
        "Your organization role doesn't permit this action.",
    ),
    ErrorCode.ORGANIZATION_SEAT_LIMIT_REACHED: ErrorDefinition(
        ErrorCode.ORGANIZATION_SEAT_LIMIT_REACHED,
        409,
        "The organization account or seat limit was reached.",
        "The organization has reached the account limit for its current plan.",
    ),
    ErrorCode.TEAM_SERVICE_UNAVAILABLE: ErrorDefinition(
        ErrorCode.TEAM_SERVICE_UNAVAILABLE,
        503,
        "A team collaboration service is unavailable.",
        "This team service is temporarily unavailable. Please try again later.",
        retryable=True,
    ),
    ErrorCode.CONVERSATION_NOT_FOUND: ErrorDefinition(
        ErrorCode.CONVERSATION_NOT_FOUND,
        404,
        "The conversation was not found.",
        "This conversation is no longer available.",
    ),
    ErrorCode.ATTACHMENT_INVALID: ErrorDefinition(
        ErrorCode.ATTACHMENT_INVALID,
        422,
        "The team attachment is invalid or unsafe.",
        "This attachment could not be accepted safely.",
    ),
    ErrorCode.ATTACHMENT_NOT_FOUND: ErrorDefinition(
        ErrorCode.ATTACHMENT_NOT_FOUND,
        404,
        "The team attachment was not found.",
        "This attachment is no longer available.",
    ),
    ErrorCode.ATTACHMENT_TOO_LARGE: ErrorDefinition(
        ErrorCode.ATTACHMENT_TOO_LARGE,
        413,
        "The team attachment exceeds its size limit.",
        "This attachment is larger than the allowed limit.",
    ),
    ErrorCode.ATTACHMENT_QUOTA_EXCEEDED: ErrorDefinition(
        ErrorCode.ATTACHMENT_QUOTA_EXCEEDED,
        413,
        "The organization secure-attachment storage quota was exceeded.",
        "The organization has reached its secure attachment storage limit.",
    ),
    ErrorCode.ATTACHMENT_SECURITY_UNAVAILABLE: ErrorDefinition(
        ErrorCode.ATTACHMENT_SECURITY_UNAVAILABLE,
        503,
        "Secure attachment infrastructure is unavailable.",
        "Secure attachment processing is temporarily unavailable. Please try again later.",
        retryable=True,
    ),
    ErrorCode.ATTACHMENT_INTEGRITY_FAILED: ErrorDefinition(
        ErrorCode.ATTACHMENT_INTEGRITY_FAILED,
        422,
        "The attachment failed integrity verification.",
        "This attachment could not be verified safely and cannot be opened.",
    ),
    ErrorCode.BATCH_UPLOAD_INVALID: ErrorDefinition(
        ErrorCode.BATCH_UPLOAD_INVALID,
        400,
        "The batch upload violates a batch-processing rule.",
        "The selected batch does not meet this feature's batch requirements.",
    ),
    ErrorCode.BATCH_UPLOAD_PLAN_REQUIRED: ErrorDefinition(
        ErrorCode.BATCH_UPLOAD_PLAN_REQUIRED,
        403,
        "The current plan does not permit the requested batch operation.",
        "Batch processing requires an eligible paid plan.",
    ),
    ErrorCode.DUPLICATE_UPLOAD: ErrorDefinition(
        ErrorCode.DUPLICATE_UPLOAD,
        400,
        "The batch contains duplicate upload content.",
        "One or more selected files duplicate another file in this batch.",
    ),
    ErrorCode.CALL_NOT_FOUND: ErrorDefinition(
        ErrorCode.CALL_NOT_FOUND,
        404,
        "The call or scheduled call link was not found.",
        "This call is no longer available.",
    ),
    ErrorCode.CALL_ACCESS_DENIED: ErrorDefinition(
        ErrorCode.CALL_ACCESS_DENIED,
        403,
        "The user is not permitted to perform the requested call operation.",
        "You don't have permission to perform this call action.",
    ),
    ErrorCode.CALL_STATE_CONFLICT: ErrorDefinition(
        ErrorCode.CALL_STATE_CONFLICT,
        409,
        "The call action conflicts with the current call state.",
        "This call action isn't available in the call's current state.",
    ),
    ErrorCode.CALL_RECORDING_CONSENT_REQUIRED: ErrorDefinition(
        ErrorCode.CALL_RECORDING_CONSENT_REQUIRED,
        409,
        "Recording consent is required.",
        "Recording can start only after the required participant consent is recorded.",
    ),
    ErrorCode.UPSTREAM_TIMEOUT: ErrorDefinition(
        ErrorCode.UPSTREAM_TIMEOUT,
        504,
        "An upstream service timed out.",
        "The processing service took too long to respond. Please try again.",
        retryable=True,
    ),
    ErrorCode.UPSTREAM_SERVICE_ERROR: ErrorDefinition(
        ErrorCode.UPSTREAM_SERVICE_ERROR,
        502,
        "An upstream service failed.",
        "A processing service could not complete the request. Please try again.",
        retryable=True,
    ),
    ErrorCode.SERVICE_UNAVAILABLE: ErrorDefinition(
        ErrorCode.SERVICE_UNAVAILABLE,
        503,
        "A required backend service is unavailable.",
        "This service is temporarily unavailable. Please try again later.",
        retryable=True,
    ),
    ErrorCode.INTERNAL_ERROR: ErrorDefinition(
        ErrorCode.INTERNAL_ERROR,
        500,
        "An unexpected internal error occurred.",
        "We couldn't complete the request.",
        retryable=False,
    ),
}


@dataclass(frozen=True)
class NormalizedError:
    definition: ErrorDefinition
    internal_message: str
    details: dict[str, Any] | None = None
    status_code: int | None = None
    headers: Mapping[str, str] | None = None

    @property
    def http_status(self) -> int:
        return int(self.status_code or self.definition.status_code)

    def payload(self) -> dict[str, Any]:
        code = self.definition.code.value
        message = self.definition.friendly_message
        retryable = self.definition.retryable
        return {
            "success": False,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
            },
            # Compatibility for existing Next/browser paths that currently read
            # detail.error/detail.message. Keep this derived from the canonical
            # public definition so the two shapes can never disagree.
            "detail": {
                "error": code.lower(),
                "message": message,
                "retryable": retryable,
            },
        }


class APIError(HTTPException):
    """HTTPException carrying a normalized, safe public error payload."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        internal_message: str | None = None,
        friendly_message: str | None = None,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        definition = _definition_with_overrides(
            ERRORS[code],
            friendly_message=friendly_message,
            retryable=retryable,
        )
        effective_status = int(status_code or definition.status_code)

        self.code = code
        self.definition = definition
        self.internal_message = internal_message or definition.error_message
        self.details = details or {}
        self.retryable = definition.retryable
        self.friendly_message = definition.friendly_message
        self.normalized_status_code = effective_status
        self.normalized_headers = dict(headers or {})

        normalized = NormalizedError(
            definition=definition,
            internal_message=self.internal_message,
            details=self.details,
            status_code=effective_status,
            headers=self.normalized_headers,
        )
        super().__init__(
            status_code=effective_status,
            detail=normalized.payload(),
            headers=self.normalized_headers or None,
        )


def raise_api_error(
    code: ErrorCode,
    *,
    internal_message: str | None = None,
    friendly_message: str | None = None,
    retryable: bool | None = None,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> APIError:
    return APIError(
        code,
        internal_message=internal_message,
        friendly_message=friendly_message,
        retryable=retryable,
        details=details,
        status_code=status_code,
        headers=headers,
    )


@dataclass(frozen=True)
class _RawErrorRule:
    code: ErrorCode
    friendly_message: str | None = None
    retryable: bool | None = None
    use_source_message: bool = False


def _definition_with_overrides(
    definition: ErrorDefinition,
    *,
    friendly_message: str | None = None,
    retryable: bool | None = None,
) -> ErrorDefinition:
    if friendly_message is not None:
        definition = replace(definition, friendly_message=friendly_message)
    if retryable is not None:
        definition = replace(definition, retryable=retryable)
    return definition


def _norm(
    code: ErrorCode,
    internal_message: str,
    *,
    friendly_message: str | None = None,
    retryable: bool | None = None,
    details: dict[str, Any] | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> NormalizedError:
    definition = _definition_with_overrides(
        ERRORS[code],
        friendly_message=friendly_message,
        retryable=retryable,
    )
    return NormalizedError(
        definition=definition,
        internal_message=internal_message or definition.error_message,
        details=details,
        status_code=status_code,
        headers=headers,
    )


def _build_raw_error_rules() -> dict[str, _RawErrorRule]:
    rules: dict[str, _RawErrorRule] = {}

    def add(
        code: ErrorCode,
        *raw_codes: str,
        friendly_message: str | None = None,
        retryable: bool | None = None,
        use_source_message: bool = False,
    ) -> None:
        rule = _RawErrorRule(
            code=code,
            friendly_message=friendly_message,
            retryable=retryable,
            use_source_message=use_source_message,
        )
        for raw_code in raw_codes:
            key = str(raw_code).strip().lower()
            if not key:
                continue
            if key in rules:
                raise RuntimeError(f"Duplicate ReDOCX raw error mapping: {key}")
            rules[key] = rule

    # Authentication / authorization.
    add(ErrorCode.AUTHORIZATION_REQUIRED, "authorization_required")
    add(ErrorCode.INVALID_TOKEN, "invalid_token")
    add(ErrorCode.INSUFFICIENT_SCOPE, "insufficient_scope")
    add(
        ErrorCode.PERMISSION_DENIED,
        "websocket_origin_denied",
        "websocket_origin_required",
        "realtime_identity_change_denied",
    )
    add(
        ErrorCode.AUTH_PROVIDER_UNAVAILABLE,
        "jwks_invalid",
        "jwks_unavailable",
        "auth0_delete_failed",
        "auth0_management_forbidden",
        "auth0_management_invalid_response",
        "auth0_management_not_configured",
        "auth0_management_unavailable",
        "auth0_management_validation_failed",
    )

    # Account lifecycle. These state messages are intentionally produced for UI
    # decisions in the attached account/auth lifecycle code, so preserve them.
    add(
        ErrorCode.ACCOUNT_DELETED,
        "account_deleted",
        "user_deleted",
        "user_not_found",
        use_source_message=True,
    )
    add(
        ErrorCode.ACCOUNT_DEACTIVATED,
        "account_deactivation_in_progress",
        "account_deactivated_pending_deletion",
        use_source_message=True,
    )
    add(
        ErrorCode.ACCOUNT_RECOVERY_EXPIRED,
        "account_restore_window_elapsed",
        use_source_message=True,
    )
    add(
        ErrorCode.ACCOUNT_OPERATION_FAILED,
        "account_deactivation_pending_retry",
        use_source_message=True,
        retryable=True,
    )
    add(
        ErrorCode.ACCOUNT_OPERATION_FAILED,
        "account_delete_failed",
        "account_lifecycle_not_configured",
        "account_lifecycle_unavailable",
        "account_load_failed",
        "account_restore_failed",
        "account_update_failed",
        "subscription_resume_failed",
        retryable=True,
    )

    # Usage / plan controls.
    add(
        ErrorCode.RATE_LIMIT_EXCEEDED,
        "rate_limit_exceeded",
        "paid_concurrency_limit_exceeded",
        "pdf_tool_free_window_exceeded",
        use_source_message=True,
        retryable=True,
    )
    add(
        ErrorCode.RATE_LIMIT_UNAVAILABLE,
        "rate_limit_anonymous_device_secret_missing",
        "rate_limit_device_secret_missing",
        retryable=True,
    )
    add(
        ErrorCode.FEATURE_NOT_AVAILABLE,
        "feature_not_available",
        use_source_message=True,
    )
    add(
        ErrorCode.PLAN_LIMIT_EXCEEDED,
        "free_account_device_limit_exceeded",
        "free_device_already_bound",
        "paid_plan_account_limit_exceeded",
        use_source_message=True,
    )
    add(
        ErrorCode.AUTHORIZATION_REQUIRED,
        "pdf_tool_guest_trial_used",
        use_source_message=True,
    )
    add(
        ErrorCode.PLAN_REQUIRED,
        "team_communications_unavailable",
        use_source_message=True,
    )

    # Batch routes expose these stable codes from BatchUploadPolicyError classes.
    add(
        ErrorCode.BATCH_UPLOAD_INVALID,
        "invalid_batch_upload",
        use_source_message=True,
    )
    add(
        ErrorCode.BATCH_UPLOAD_PLAN_REQUIRED,
        "batch_upload_plan_required",
        use_source_message=True,
    )
    add(
        ErrorCode.DUPLICATE_UPLOAD,
        "duplicate_batch_upload",
        use_source_message=True,
    )

    # Billing / checkout / subscriptions.
    add(
        ErrorCode.BILLING_UNAVAILABLE,
        "billing_plans_failed",
        "billing_provider_not_configured",
        "upgrade_intent_failed",
        retryable=True,
    )
    add(
        ErrorCode.BILLING_UNAVAILABLE,
        "billing_schema_not_ready",
        friendly_message="Billing is temporarily unavailable. No payment was started. Please try again later.",
        retryable=True,
    )
    add(
        ErrorCode.BILLING_CONFLICT,
        "active_subscription_required",
        "already_on_plan",
        "billing_access_revoked",
        "downgrade_not_allowed",
        "external_subscription_cancellation_unsupported",
        "external_subscription_reference_missing",
        "invalid_checkout_seat_count",
        "paid_subscription_required",
        "provider_switch_requires_cancellation",
        "seat_count_below_active_members",
        "subscription_period_locked",
        "billing_handoff_pending",
        "billing_handoff_expired",
        "billing_handoff_authorization_required",
        "billing_period_reconciliation_required",
        "target_plan_required",
        "upgrade_not_allowed",
        use_source_message=True,
    )
    # These are legitimate conflicts, but their attached messages describe
    # implementation mechanics (operation reservation/provider references or an
    # HTTP idempotency key). Keep the UI diagnosis accurate and non-technical.
    add(
        ErrorCode.BILLING_CONFLICT,
        "billing_operation_conflict",
        "billing_subscription_reference_missing",
        "idempotency_key_reused",
    )
    add(
        ErrorCode.PAYMENT_PENDING,
        "paystack_payment_not_confirmed",
        "paystack_activation_not_reflected",
        "paystack_confirmation_failed",
        use_source_message=True,
        retryable=True,
    )
    add(
        ErrorCode.PAYMENT_VERIFICATION_FAILED,
        "checkout_provider_failed",
        "paystack_verification_failed",
        "paystack_activation_failed",
        "paystack_checkout_amount_mismatch",
        "paystack_checkout_amount_missing",
        "paystack_checkout_email_mismatch",
        "paystack_checkout_identity_mismatch",
        "paystack_checkout_not_found",
        "paystack_checkout_plan_mismatch",
        retryable=True,
    )
    add(
        ErrorCode.SUBSCRIPTION_OPERATION_FAILED,
        "external_subscription_cancellation_failed",
        "subscription_change_failed",
        "subscription_load_failed",
        "subscription_update_failed",
        retryable=True,
    )
    add(
        ErrorCode.INVALID_WEBHOOK,
        "invalid_billing_webhook",
        "invalid_webhook_signature",
        "billing_webhook_processing_failed",
        "billing_webhook_failed",
    )

    # Organizations / invitations / members.
    add(
        ErrorCode.ORGANIZATION_ACCESS_DENIED,
        "organization_access_denied",
        "conversation_access_denied",
        use_source_message=True,
    )
    add(
        ErrorCode.ORGANIZATION_PERMISSION_REQUIRED,
        "organization_admin_required",
        "organization_owner_required",
        "owner_invitation_cancel_denied",
        "invitation_cancel_denied",
        "plan_owner_immutable",
        use_source_message=True,
    )
    add(
        ErrorCode.ORGANIZATION_SEAT_LIMIT_REACHED,
        "seat_limit_reached",
        "max_accounts_below_active_members",
        use_source_message=True,
    )
    add(
        ErrorCode.RESOURCE_NOT_FOUND,
        "organization_not_found",
        "member_not_found",
        "invitation_not_found",
        "signing_link_invalid",
        use_source_message=True,
    )
    add(
        ErrorCode.REQUEST_CONFLICT,
        "email_required",
        "invitation_acceptance_required",
        "last_owner_required",
        "new_owner_must_be_active_member",
        "ownership_transfer_or_member_removal_required",
        "self_invite_not_allowed",
        use_source_message=True,
    )
    add(
        ErrorCode.REQUEST_CONFLICT,
        "presence_provider_controlled",
        friendly_message="Call presence is managed automatically and can't be changed manually.",
    )
    add(
        ErrorCode.INVALID_REQUEST,
        "invalid_account_delete_request",
        "invalid_billing_request",
        "invalid_billing_state",
        "invalid_conversation",
        "invalid_conversation_members",
        "invalid_forward_request",
        "invalid_idempotency_key",
        "invalid_invitation",
        "invalid_member",
        "invalid_member_update",
        "invalid_message",
        "invalid_message_cursor",
        "invalid_organization",
        "invalid_organization_name",
        "invalid_ownership_transfer",
        "invalid_paystack_confirmation",
        "invalid_request",
        "invalid_seat_count",
        "invalid_setting",
        "invalid_subscription",
        "organization_name_required",
        "participant_limit_exceeds_membership",
        "empty_policy_update",
        "subgroup_member_limit_exceeded",
        "subgroup_members_required",
    )

    # Messaging / conversations.
    add(
        ErrorCode.CONVERSATION_NOT_FOUND,
        "conversation_not_found",
        use_source_message=True,
    )
    add(
        ErrorCode.RESOURCE_NOT_FOUND,
        "message_not_found",
        use_source_message=True,
    )
    add(
        ErrorCode.REQUEST_CONFLICT,
        "client_message_id_conflict",
        "message_not_forwardable",
        use_source_message=True,
    )

    # Team attachment route errors.
    add(
        ErrorCode.ATTACHMENT_INVALID,
        "attachment_required",
        "invalid_attachment",
        use_source_message=True,
    )
    add(
        ErrorCode.ATTACHMENT_NOT_FOUND,
        "attachment_not_found",
        use_source_message=True,
    )
    add(
        ErrorCode.ATTACHMENT_QUOTA_EXCEEDED,
        "attachment_storage_quota_exceeded",
        use_source_message=True,
    )
    add(
        ErrorCode.PAYLOAD_TOO_LARGE,
        "too_many_attachments",
        "attachment_request_too_large",
        use_source_message=True,
    )
    add(
        ErrorCode.REQUEST_CONFLICT,
        "attachment_not_forwardable",
        use_source_message=True,
    )
    add(
        ErrorCode.ATTACHMENT_SECURITY_UNAVAILABLE,
        "attachment_security_schema_not_ready",
        "unknown_attachment_encryption_key",
        retryable=True,
    )

    # TeamAttachmentSecurityError codes. Its public_message field is explicitly
    # designed for a client response; safe validation messages are preserved.
    add(
        ErrorCode.ATTACHMENT_TOO_LARGE,
        "attachment_too_large",
        use_source_message=True,
    )
    add(
        ErrorCode.ATTACHMENT_INTEGRITY_FAILED,
        "attachment_integrity_failure",
        use_source_message=True,
    )
    add(
        ErrorCode.ATTACHMENT_SECURITY_UNAVAILABLE,
        "attachment_not_secured",
        friendly_message="This attachment is temporarily unavailable while its security state is being verified.",
        retryable=True,
    )
    add(
        ErrorCode.ATTACHMENT_SECURITY_UNAVAILABLE,
        "attachment_decryption_key_unavailable",
        "attachment_encryption_configuration_invalid",
        "attachment_encryption_not_configured",
        "attachment_encryption_unsupported",
        "attachment_image_validator_unavailable",
        "attachment_media_validator_unavailable",
        "attachment_scanner_database_stale",
        "attachment_scanner_database_unavailable",
        "attachment_scanner_unavailable",
        "attachment_security_storage_unavailable",
        retryable=True,
    )
    add(
        ErrorCode.MALWARE_DETECTED,
        "malware_detected",
        use_source_message=True,
    )
    add(
        ErrorCode.ATTACHMENT_INVALID,
        "attachment_content_mismatch",
        "dangerous_double_extension",
        "empty_attachment",
        "invalid_attachment_filename",
        "invalid_image",
        "invalid_json_attachment",
        "invalid_media_attachment",
        "invalid_office_document",
        "invalid_pdf",
        "invalid_text_attachment",
        "media_duration_limit_exceeded",
        "pdf_page_limit_exceeded",
        "unsafe_image_dimensions",
        "unsafe_image_frames",
        "unsafe_media_content",
        "unsafe_office_archive",
        "unsafe_office_content",
        "unsafe_pdf_content",
        "unsafe_pdf_structure",
        "unsupported_attachment_type",
        "unsupported_encrypted_document",
        use_source_message=True,
    )

    # Calls / scheduled links / recording.
    add(
        ErrorCode.CALL_NOT_FOUND,
        "call_not_found",
        "call_link_not_found",
        "call_participant_not_found",
        use_source_message=True,
    )
    add(
        ErrorCode.CALL_ACCESS_DENIED,
        "call_invitation_required",
        "call_participant_required",
        "call_recording_disabled",
        "call_participant_not_joinable",
        "call_host_required",
        "call_link_owner_required",
        use_source_message=True,
    )
    add(
        ErrorCode.CALL_RECORDING_CONSENT_REQUIRED,
        "recording_consent_required",
        use_source_message=True,
    )
    add(
        ErrorCode.CALL_STATE_CONFLICT,
        "call_already_active",
        "call_already_joined",
        "call_expired",
        "call_link_cancelled",
        "call_link_completed",
        "call_link_expired",
        "call_link_has_active_call",
        "call_link_too_early",
        "call_not_active",
        "call_not_joinable",
        "call_participant_limit_reached",
        "joined_call_participant_required",
        "not_enough_call_participants",
        "not_enough_organization_members",
        "recording_already_open",
        "recording_not_open",
        "recording_requires_two_participants",
        use_source_message=True,
    )
    add(
        ErrorCode.RESOURCE_NOT_FOUND,
        "recording_not_found",
        "compression_job_not_found",
        use_source_message=True,
    )

    # Team service infrastructure and operation failures. Never return their raw
    # messages because several contain deployment/provider diagnostics.
    add(
        ErrorCode.TEAM_SERVICE_UNAVAILABLE,
        "attachment_download_failed",
        "attachment_send_failed",
        "conversation_create_failed",
        "conversations_load_failed",
        "invitation_accept_failed",
        "invitation_deny_failed",
        "member_invite_failed",
        "member_remove_failed",
        "member_update_failed",
        "message_forward_failed",
        "message_notifications_load_failed",
        "message_replay_failed",
        "message_send_failed",
        "messages_load_failed",
        "organization_create_failed",
        "organization_leave_failed",
        "organization_load_failed",
        "organization_update_failed",
        "organizations_load_failed",
        "ownership_transfer_failed",
        "presence_load_failed",
        "call_decline_failed",
        "call_end_failed",
        "call_join_failed",
        "call_leave_failed",
        "call_replay_failed",
        "call_start_failed",
        "call_telemetry_failed",
        "call_telemetry_not_ready",
        "livekit_not_configured",
        "livekit_sdk_missing",
        "presence_service_unavailable",
        "web_push_not_configured",
        retryable=True,
    )

    # E-signature recipient links.
    add(
        ErrorCode.RESOURCE_GONE,
        "signing_link_expired",
        use_source_message=True,
    )
    add(ErrorCode.REQUEST_CONFLICT, "signing_not_available")
    add(
        ErrorCode.SERVICE_UNAVAILABLE,
        "signing_service_unavailable",
        friendly_message="The signing service is temporarily unavailable.",
        retryable=True,
    )

    # Realtime protocol errors. These are primarily emitted over WebSocket, but
    # retain stable translation rules if they surface through an HTTP boundary.
    add(
        ErrorCode.INVALID_REQUEST,
        "invalid_realtime_frame",
        friendly_message="The realtime message could not be processed.",
    )
    add(
        ErrorCode.INVALID_ACTION,
        "unsupported_account_realtime_event",
        "unsupported_realtime_event",
        friendly_message="That realtime action is not supported.",
    )

    # Analyzer / providers / preview. Preserve ASR codes already supported by the
    # original ReDOCX error layer even though the current attached transcription
    # path does not emit them directly.
    add(ErrorCode.UPSTREAM_TIMEOUT, "ai_timeout", "asr_timeout", retryable=True)
    add(
        ErrorCode.UPSTREAM_SERVICE_ERROR,
        "asr_provider_http_error",
        retryable=True,
    )
    add(
        ErrorCode.PREVIEW_UNAVAILABLE,
        "print_preview_unavailable",
        retryable=True,
    )

    return rules


RAW_ERROR_RULES = _build_raw_error_rules()


def _detail_to_message(detail: Any) -> str:
    if isinstance(detail, Mapping):
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

        error = detail.get("error")
        if isinstance(error, Mapping):
            nested_message = error.get("message")
            if isinstance(nested_message, str) and nested_message.strip():
                return nested_message.strip()
        if isinstance(error, str) and error.strip():
            return error.strip()

        nested_detail = detail.get("detail")
        if nested_detail is not detail and nested_detail is not None:
            nested_message = _detail_to_message(nested_detail)
            if nested_message:
                return nested_message
        return ""
    if isinstance(detail, str):
        return detail.strip()
    return ""


def _raw_error_code(detail: Any) -> str | None:
    if not isinstance(detail, Mapping):
        return None
    value = detail.get("error")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def _error_code_from_string(value: Any) -> ErrorCode | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().upper()
    try:
        return ErrorCode(normalized)
    except ValueError:
        return None


def _normalized_payload_from_detail(
    detail: Any,
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> NormalizedError | None:
    """Recognize an already normalized payload without discarding overrides."""

    if not isinstance(detail, Mapping):
        return None

    candidates: list[Mapping[str, Any]] = []
    error_block = detail.get("error")
    if isinstance(error_block, Mapping):
        candidates.append(error_block)

    nested_detail = detail.get("detail")
    if isinstance(nested_detail, Mapping):
        nested_error = nested_detail.get("error")
        if isinstance(nested_error, Mapping):
            candidates.append(nested_error)

    # APIError.detail contains the entire normalized payload.
    if isinstance(nested_detail, Mapping) and isinstance(nested_detail.get("error"), str):
        outer_error = detail.get("error")
        if isinstance(outer_error, Mapping):
            candidates.append(outer_error)

    for block in candidates:
        code = _error_code_from_string(block.get("code"))
        if code is None:
            continue
        message = block.get("message")
        friendly = message.strip() if isinstance(message, str) and message.strip() else None
        retryable_value = block.get("retryable")
        retryable = retryable_value if isinstance(retryable_value, bool) else None
        return _norm(
            code,
            _detail_to_message(detail) or ERRORS[code].error_message,
            friendly_message=friendly,
            retryable=retryable,
            status_code=status_code,
            headers=headers,
        )
    return None


def _public_source_message(message: str) -> str | None:
    """Return bounded source copy only after a caller explicitly allow-lists it."""

    normalized = " ".join(str(message or "").split()).strip()
    if not normalized:
        return None
    # Route-level public messages are short. Refuse unexpectedly large content so
    # an exception object, provider payload, SQL string, or document body cannot
    # be reflected into the UI accidentally.
    if len(normalized) > 700:
        return None
    return normalized


def _normalized_from_raw_rule(
    raw_error: str,
    message: str,
    *,
    status_code: int,
    headers: Mapping[str, str] | None,
) -> NormalizedError | None:
    rule = RAW_ERROR_RULES.get(raw_error)
    if rule is None:
        return None

    friendly = rule.friendly_message
    if friendly is None and rule.use_source_message:
        friendly = _public_source_message(message)

    return _norm(
        rule.code,
        message or f"Backend error: {raw_error}",
        friendly_message=friendly,
        retryable=rule.retryable,
        status_code=status_code,
        headers=headers,
    )


def _fallback_for_http_status(
    status_code: int,
    internal_message: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> NormalizedError:
    """Conservative status-only fallback for unknown structured HTTP errors."""

    if status_code == 400:
        code = ErrorCode.INVALID_REQUEST
    elif status_code == 401:
        code = ErrorCode.INVALID_TOKEN
    elif status_code == 403:
        code = ErrorCode.PERMISSION_DENIED
    elif status_code == 404:
        code = ErrorCode.RESOURCE_NOT_FOUND
    elif status_code == 405:
        code = ErrorCode.METHOD_NOT_ALLOWED
    elif status_code == 409:
        code = ErrorCode.REQUEST_CONFLICT
    elif status_code == 410:
        code = ErrorCode.RESOURCE_GONE
    elif status_code in {413, 431}:
        code = ErrorCode.PAYLOAD_TOO_LARGE
    elif status_code == 415:
        code = ErrorCode.UNSUPPORTED_FILE_TYPE
    elif status_code == 422:
        code = ErrorCode.INVALID_REQUEST
    elif status_code == 425:
        code = ErrorCode.REQUEST_TOO_EARLY
    elif status_code == 429:
        code = ErrorCode.RATE_LIMIT_EXCEEDED
    elif status_code == 502:
        code = ErrorCode.UPSTREAM_SERVICE_ERROR
    elif status_code == 503:
        code = ErrorCode.SERVICE_UNAVAILABLE
    elif status_code == 504:
        code = ErrorCode.UPSTREAM_TIMEOUT
    elif 400 <= status_code < 500:
        code = ErrorCode.INVALID_REQUEST
    else:
        code = ErrorCode.INTERNAL_ERROR

    return _norm(
        code,
        internal_message or f"HTTP {status_code}",
        status_code=status_code,
        headers=headers,
    )


def _normalized_http_error_from_http_exception(
    exc: HTTPException | StarletteHTTPException,
) -> NormalizedError:
    detail = exc.detail if hasattr(exc, "detail") else str(exc)
    status_code = int(getattr(exc, "status_code", 500) or 500)
    headers = getattr(exc, "headers", None)
    message = _detail_to_message(detail)

    already_normalized = _normalized_payload_from_detail(
        detail,
        status_code=status_code,
        headers=headers,
    )
    if already_normalized is not None:
        return already_normalized

    raw_error = _raw_error_code(detail)
    if raw_error:
        # route_v1 historically wraps processing exceptions in these two generic
        # tags. Classify their message first so a known upload/processing condition
        # can become a precise public code. Unknown wording still falls back to the
        # original transport status without exposing the raw message.
        if raw_error in {"invalid_request", "service_unavailable"}:
            return _normalized_error_from_message(
                message,
                status_code_hint=status_code,
                headers=headers,
            )

        matched = _normalized_from_raw_rule(
            raw_error,
            message,
            status_code=status_code,
            headers=headers,
        )
        if matched is not None:
            return matched

    return _fallback_for_http_status(status_code, message, headers=headers)


def _contains_any(value: str, tokens: tuple[str, ...]) -> bool:
    return any(token in value for token in tokens)


def _normalized_error_from_message(
    message: str,
    *,
    status_code_hint: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> NormalizedError:
    """Classify only message patterns explicitly supported by attached ReDOCX code.

    Deliberately avoid broad tokens such as ``invalid``, ``requires`` or
    ``payload``. Those words appear in internal invariants as well as user input
    errors and previously caused misleading UI messages.
    """

    text = str(message or "").strip()
    lowered = text.lower()
    status = status_code_hint

    # Workflow prerequisite.
    if (
        "not a standalone backend action" in lowered
        or "prior generate_questions completion proof" in lowered
    ):
        return _norm(
            ErrorCode.WORKFLOW_PREREQUISITE_REQUIRED,
            text,
            friendly_message="Generate questions first, then generate answers.",
            status_code=status,
            headers=headers,
        )

    # Authentication phrases used by Auth0 dependencies.
    if "authorization credentials are required" in lowered:
        return _norm(
            ErrorCode.AUTHORIZATION_REQUIRED,
            text,
            status_code=status,
            headers=headers,
        )
    if "token is invalid or expired" in lowered:
        return _norm(ErrorCode.INVALID_TOKEN, text, status_code=status, headers=headers)
    if "required scopes" in lowered or "insufficient_scope" in lowered:
        return _norm(ErrorCode.INSUFFICIENT_SCOPE, text, status_code=status, headers=headers)

    # Upload malware/security infrastructure must be checked before generic file
    # validation so fail-closed scanner failures are never described as bad files.
    if "malware detected" in lowered:
        return _norm(ErrorCode.MALWARE_DETECTED, text, status_code=status, headers=headers)
    if _contains_any(
        lowered,
        (
            "file security scanning is temporarily unavailable",
            "file security scanning timed out",
            "upload security is temporarily unavailable",
            "malware scanning is misconfigured",
        ),
    ):
        return _norm(
            ErrorCode.UPLOAD_SECURITY_UNAVAILABLE,
            text,
            status_code=status,
            headers=headers,
        )

    # Rate limiting / concurrency. Infrastructure wording must be checked first:
    # "rate limiter" contains the substring "rate limit" and was previously easy
    # to mislabel as a user quota violation.
    if _contains_any(
        lowered,
        (
            "rate limiter",
            "usage limits right now",
            "rate-limit infrastructure",
        ),
    ):
        return _norm(
            ErrorCode.RATE_LIMIT_UNAVAILABLE,
            text,
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "rate limit exceeded",
            "rate limit has been exceeded",
            "usage limit",
            "concurrency limit",
        ),
    ):
        return _norm(
            ErrorCode.RATE_LIMIT_EXCEEDED,
            text,
            status_code=status,
            headers=headers,
        )

    # Missing processing files. All FileNotFoundError call sites in the attached
    # processing code refer to source/artifact/workflow files, not arbitrary host
    # filesystem paths.
    if _contains_any(
        lowered,
        (
            "source file not found",
            "media file not found",
            "generated artifact not found",
            "uploaded file not found",
            "pdf source was not found",
            "source pdf was not found",
            "source file was not found",
            "edit asset was not found",
            "complete edited pdf was not found",
            "file not found",
        ),
    ):
        return _norm(
            ErrorCode.SOURCE_FILE_NOT_FOUND,
            text,
            status_code=status,
            headers=headers,
        )

    # Upload persistence / metadata.
    if _contains_any(
        lowered,
        (
            "failed to persist uploaded file",
            "failed to persist uploaded pdf",
            "failed to persist uploaded edit image",
        ),
    ):
        return _norm(
            ErrorCode.UPLOAD_PERSIST_FAILED,
            text,
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "uploaded file must have a filename",
            "uploaded file must include a valid extension",
            "every file in a batch must include a valid file extension",
            "missing its persisted file path",
            "filename is missing",
            "file reference is missing",
            "output filename is missing",
            "file stream is closed",
        ),
    ):
        return _norm(
            ErrorCode.INVALID_UPLOAD_METADATA,
            text,
            status_code=status,
            headers=headers,
        )

    # Required / mutually-exclusive input.
    if _contains_any(
        lowered,
        (
            "provide either file or text, not both",
            "provide either file_path or inline_text, not both",
        ),
    ):
        return _norm(
            ErrorCode.INPUT_REQUIRED,
            text,
            friendly_message="Provide one input source only and try again.",
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "either file or text is required",
            "either file_path or inline_text must be provided",
        ),
    ):
        return _norm(
            ErrorCode.INPUT_REQUIRED,
            text,
            friendly_message="Add a file or enter text to continue.",
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "no upload file was provided",
            "no pdf edit asset was provided",
            "at least one file is required for batch processing",
        ),
    ):
        return _norm(
            ErrorCode.INPUT_REQUIRED,
            text,
            friendly_message="Choose the required file to continue.",
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "inline text cannot be empty",
            "empty content cannot be processed",
            "empty content cannot be written",
            "empty prompt passed",
            "empty file_path passed",
        ),
    ):
        return _norm(
            ErrorCode.INPUT_REQUIRED,
            text,
            friendly_message="Enter some text to continue.",
            status_code=status,
            headers=headers,
        )

    # File size / emptiness / text encoding.
    if _contains_any(lowered, ("uploaded file is empty", "file is empty", "empty pdf")):
        return _norm(ErrorCode.FILE_EMPTY, text, status_code=status, headers=headers)
    if _contains_any(
        lowered,
        (
            "exceeds maximum allowed size",
            "exceeds the maximum allowed size",
            "file is too large",
            "exceeds maximum size",
        ),
    ):
        return _norm(ErrorCode.FILE_TOO_LARGE, text, status_code=status, headers=headers)
    if "utf-8" in lowered and _contains_any(lowered, ("txt", "text file", "encoding")):
        return _norm(
            ErrorCode.INVALID_FILE_ENCODING,
            text,
            status_code=status,
            headers=headers,
        )

    # Encrypted/password-protected inputs are an explicit unsupported input state,
    # not a processing malfunction.
    if _contains_any(
        lowered,
        (
            "password-protected",
            "password protected",
            "encrypted pdf",
            "encrypted package entries",
            "encrypted document",
        ),
    ):
        return _norm(
            ErrorCode.PASSWORD_PROTECTED_FILE,
            text,
            status_code=status,
            headers=headers,
        )

    # Unsafe/malformed upload structures from upload_security.py.
    if _contains_any(
        lowered,
        (
            "dangerous double extension",
            "content does not match declared extension",
            "contains a null byte",
            "contains null bytes",
            "too many control characters",
            "could not be safely parsed",
            "unsafe path traversal",
            "unsafe external",
            "invalid external hyperlink",
            "unsafe external hyperlink",
            "forbidden xml declarations",
            "malformed relationship metadata",
            "macros, embedded objects, activex, or custom ui",
            "suspicious compression ratio",
            "expands to an unsafe size",
            "not a valid ooxml zip package",
            "invalid relationship metadata",
            "duplicate relationship identifiers",
            "duplicate or case-colliding package entries",
            "unsafe active content",
            "active content",
        ),
    ):
        return _norm(ErrorCode.UNSAFE_FILE, text, status_code=status, headers=headers)

    # Extraction / OCR.
    if _contains_any(
        lowered,
        (
            "document text could not be extracted",
            "document contains no words",
            "contains no pages",
            "no usable text",
            "could not extract usable text",
        ),
    ):
        return _norm(ErrorCode.EXTRACTION_FAILED, text, status_code=status, headers=headers)
    if "ocr language token cannot be empty" in lowered:
        return _norm(
            ErrorCode.INVALID_REQUEST,
            text,
            friendly_message="One of the OCR language settings is invalid.",
            status_code=status,
            headers=headers,
        )
    if _contains_any(lowered, ("traineddata is not installed", "no ocr languages are installed")):
        return _norm(
            ErrorCode.FEATURE_NOT_CONFIGURED,
            text,
            friendly_message="OCR is temporarily unavailable.",
            status_code=status,
            headers=headers,
        )

    # Unsupported formats / conversion combinations.
    if "unsupported conversion pair" in lowered:
        return _norm(
            ErrorCode.UNSUPPORTED_CONVERSION_PAIR,
            text,
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "unsupported writer output format",
            "output_format must be one of: pdf, docx",
            "unsupported output format",
        ),
    ):
        return _norm(
            ErrorCode.UNSUPPORTED_OUTPUT_FORMAT,
            text,
            status_code=status,
            headers=headers,
        )
    if _contains_any(
        lowered,
        (
            "unsupported file extension",
            "unsupported file format",
            "audio uploads must be mp3",
            "video uploads must be one of",
            "only supports input formats",
            "only pdf uploads are accepted",
            "convert only supports",
            "redact/data_mask only support",
            "unsupported redact input format",
            "unsupported data_mask input format",
            "unsupported media container",
            "must be one of: mp3, mp4, mkv, mov, wav",
            "format must be one of: pdf, docx, jpg, jpeg, png",
        ),
    ):
        return _norm(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            text,
            status_code=status,
            headers=headers,
        )

    # Invalid action/routing contract.
    if _contains_any(
        lowered,
        (
            "unsupported action",
            "unsupported document upload action",
            "unsupported document action for extraction",
            "unsupported pdf tool action",
            "requires action='redact'",
            "requires action='data_mask'",
            "only supports action='redact' or action='data_mask'",
        ),
    ):
        return _norm(ErrorCode.INVALID_ACTION, text, status_code=status, headers=headers)

    # Known feature infrastructure messages. Never echo their configuration data.
    if _contains_any(
        lowered,
        (
            "openai_api_key is not configured",
            "google_sdp_project_id",
            "google sensitive data protection",
            "client library is required",
            "persistent compression queue",
            "pymupdf version",
            "ghostscript",
            "qpdf",
            "libreoffice",
            "tesseract",
            "ffmpeg",
            "pillow is required",
        ),
    ) and _contains_any(
        lowered,
        (
            "not configured",
            "not installed",
            "not found",
            "required",
            "unavailable",
            "cannot",
            "version",
        ),
    ):
        return _norm(
            ErrorCode.FEATURE_NOT_CONFIGURED,
            text,
            status_code=status,
            headers=headers,
        )

    # Output missing after processing.
    if _contains_any(
        lowered,
        (
            "completed without producing an output file",
            "completed without producing the expected output",
            "expected output file was not found",
            "completed but the expected output file was not found",
            "without producing a pdf output file",
            "document writing completed without producing an output file",
            "audio extraction completed without producing an output file",
            "generated output is empty",
            "generated file is empty",
        ),
    ):
        return _norm(
            ErrorCode.PROCESSING_OUTPUT_MISSING,
            text,
            status_code=status,
            headers=headers,
        )

    # Provider runtime failures.
    if _contains_any(
        lowered,
        (
            "provider returned empty output",
            "provider returned null or unreadable",
            "provider returned an unreadable result",
            "transcription service returned an error",
            "provider error",
        ),
    ):
        return _norm(
            ErrorCode.UPSTREAM_SERVICE_ERROR,
            text,
            status_code=status,
            headers=headers,
        )
    if _contains_any(lowered, ("timed out", "timeout")) and status == 504:
        return _norm(
            ErrorCode.UPSTREAM_TIMEOUT,
            text,
            status_code=status,
            headers=headers,
        )

    # Local processing failures that are explicitly described as failed work.
    if _contains_any(lowered, ("failed while converting", "failed while extracting audio")):
        return _norm(
            ErrorCode.PROCESSING_FAILED,
            text,
            status_code=status,
            headers=headers,
        )

    # Explicit user-edit geometry/selection failures from the attached PDF editor.
    if "signature" in lowered and "did not fit" in lowered and "rectangle" in lowered:
        return _norm(
            ErrorCode.INVALID_REQUEST,
            text,
            friendly_message="The selected signature area is too small. Choose a larger area and try again.",
            status_code=status,
            headers=headers,
        )
    if lowered.startswith("no ") and "was found in the selected region" in lowered:
        return _norm(
            ErrorCode.INVALID_REQUEST,
            text,
            friendly_message="No matching content was found in the selected PDF region.",
            status_code=status,
            headers=headers,
        )

    # Specific contract/input validators from schema.py/validation.py. Keep these
    # generic because they are developer-facing invariants as well as request
    # validators; do not expose their raw text.
    if _contains_any(
        lowered,
        (
            "questions list cannot be empty",
            "questions must be sequentially numbered",
            "answer count must exactly match",
            "classification mismatch for extracted_word_count",
            "question count out of range",
            "selected_pages",
            "page_ranges",
            "scheduled_start_at cannot be",
            "scheduled_start_at must include a timezone",
            "media_type must be one of: audio, video",
            "structure_preservation must be",
            "detected_language must not be provided",
        ),
    ):
        return _norm(
            ErrorCode.INVALID_REQUEST,
            text,
            status_code=status,
            headers=headers,
        )

    # Status hints are intentionally last. They provide transport semantics but
    # never turn unknown exception text into a specific user-visible diagnosis.
    if status is not None:
        return _fallback_for_http_status(status, text, headers=headers)

    return _norm(ErrorCode.INTERNAL_ERROR, text or "Unclassified backend exception.")


def normalize_exception(exc: Exception) -> NormalizedError:
    """Map a backend exception to a safe, normalized public ReDOCX error."""

    if isinstance(exc, APIError):
        # Preserve per-instance friendly/retryable/status overrides. The previous
        # implementation reconstructed ERRORS[exc.code] and silently lost them.
        return NormalizedError(
            definition=exc.definition,
            internal_message=exc.internal_message,
            details=exc.details,
            status_code=exc.normalized_status_code,
            headers=exc.normalized_headers,
        )

    if isinstance(exc, RequestValidationError):
        return _norm(
            ErrorCode.INVALID_REQUEST,
            "FastAPI request validation failed.",
            status_code=422,
        )

    if isinstance(exc, (HTTPException, StarletteHTTPException)):
        return _normalized_http_error_from_http_exception(exc)

    class_name = exc.__class__.__name__
    message = str(exc).strip()

    # Custom exceptions are matched by class name to avoid importing feature
    # modules here and creating circular dependencies in the global error layer.
    if class_name in {
        "UploadServiceUnavailableError",
        "UploadSecurityInfrastructureError",
        "MalwareScannerUnavailableError",
    }:
        return _norm(ErrorCode.UPLOAD_SECURITY_UNAVAILABLE, message, status_code=503)

    if class_name == "MalwareDetectedError":
        return _norm(ErrorCode.MALWARE_DETECTED, message, status_code=422)

    if class_name == "TeamAttachmentSecurityError":
        raw_code = str(getattr(exc, "code", "") or "").strip().lower()
        public_message = str(
            getattr(exc, "public_message", "") or message or ""
        ).strip()
        status_code = int(getattr(exc, "status_code", 422) or 422)
        matched = _normalized_from_raw_rule(
            raw_code,
            public_message,
            status_code=status_code,
            headers=None,
        )
        if matched is not None:
            return matched
        return _fallback_for_http_status(status_code, message)

    if class_name == "BatchUploadEntitlementError":
        return _norm(
            ErrorCode.BATCH_UPLOAD_PLAN_REQUIRED,
            message,
            friendly_message=_public_source_message(message),
            status_code=403,
        )
    if class_name == "DuplicateBatchUploadError":
        return _norm(
            ErrorCode.DUPLICATE_UPLOAD,
            message,
            friendly_message=_public_source_message(message),
            status_code=400,
        )
    if class_name == "BatchUploadPolicyError":
        return _norm(
            ErrorCode.BATCH_UPLOAD_INVALID,
            message,
            friendly_message=_public_source_message(message),
            status_code=400,
        )

    if class_name == "BillingSchemaError":
        return _norm(ErrorCode.BILLING_UNAVAILABLE, message, status_code=503)
    if class_name == "WebhookVerificationError":
        return _norm(ErrorCode.INVALID_WEBHOOK, message, status_code=400)
    if class_name == "BillingProviderError":
        return _norm(ErrorCode.UPSTREAM_SERVICE_ERROR, message, status_code=502)
    if class_name == "CompressionChildLimitError":
        return _norm(ErrorCode.PROCESSING_RESOURCE_LIMIT, message, status_code=503)

    if isinstance(exc, FileNotFoundError):
        return _norm(ErrorCode.SOURCE_FILE_NOT_FOUND, message, status_code=404)

    if isinstance(exc, NotImplementedError):
        # Analyzer uses NotImplementedError for actions intentionally delegated to
        # another workflow, so it must never be globally labelled a rate-limiter
        # outage. Only an explicit rate-limiter implementation message gets that
        # classification.
        if "rate limit" in message.lower() or "rate limiter" in message.lower():
            return _norm(ErrorCode.RATE_LIMIT_UNAVAILABLE, message, status_code=503)
        return _norm(ErrorCode.INTERNAL_ERROR, message, status_code=500)

    if class_name == "UploadError":
        return _normalized_error_from_message(message, status_code_hint=400)
    if class_name == "UploadSecurityError":
        classified = _normalized_error_from_message(message)
        if classified.definition.code is not ErrorCode.INTERNAL_ERROR:
            return classified
        return _norm(ErrorCode.UNSAFE_FILE, message, status_code=422)

    if isinstance(exc, RuntimeError):
        classified = _normalized_error_from_message(message)
        if classified.definition.code is not ErrorCode.INTERNAL_ERROR:
            return classified
        return _norm(ErrorCode.INTERNAL_ERROR, message, status_code=500)

    if isinstance(exc, (ValueError, TypeError)):
        classified = _normalized_error_from_message(message)
        if classified.definition.code is not ErrorCode.INTERNAL_ERROR:
            # Attached validators use these exceptions for explicit contract/input
            # checks. Preserve the classifier's definition status rather than
            # forcing every ValueError to 422.
            return classified
        # Unknown ValueError/TypeError text is not enough evidence to blame the
        # user's request. Treat it as internal rather than inventing a diagnosis.
        return _norm(ErrorCode.INTERNAL_ERROR, message, status_code=500)

    return _norm(
        ErrorCode.INTERNAL_ERROR,
        message or exc.__class__.__name__,
        status_code=500,
    )


def to_http_exception(exc: Exception) -> APIError:
    normalized = normalize_exception(exc)
    return APIError(
        normalized.definition.code,
        internal_message=normalized.internal_message,
        friendly_message=normalized.definition.friendly_message,
        retryable=normalized.definition.retryable,
        details=normalized.details,
        status_code=normalized.http_status,
        headers=normalized.headers,
    )


# ---------------------------------------------------------------------------
# FastAPI integration
# ---------------------------------------------------------------------------


def _log_normalized_error(
    request: Request,
    normalized: NormalizedError,
    exc: Exception,
) -> None:
    level = logging.WARNING
    if normalized.http_status >= 500:
        level = logging.ERROR

    logger.log(
        level,
        "Handled API error",
        extra={
            "path": str(request.url.path),
            "method": request.method,
            "error_code": normalized.definition.code.value,
            "status_code": normalized.http_status,
            "internal_message": normalized.internal_message,
        },
        exc_info=exc if normalized.http_status >= 500 else None,
    )


def _response_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    if not headers:
        return None
    # Preserve Starlette/FastAPI headers such as WWW-Authenticate and Retry-After.
    # Values are already controlled by backend code; cast defensively to strings.
    return {str(key): str(value) for key, value in headers.items()}


async def _json_error_response(request: Request, exc: Exception) -> JSONResponse:
    normalized = normalize_exception(exc)
    _log_normalized_error(request, normalized, exc)
    return JSONResponse(
        status_code=normalized.http_status,
        content=normalized.payload(),
        headers=_response_headers(normalized.headers),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return await _json_error_response(request, exc)

    # Register FastAPI's HTTPException explicitly, in addition to Starlette's
    # base class. api_v1.py installs a StarletteHTTPException handler after this
    # function to preserve framework statuses. ExceptionMiddleware resolves the
    # most specific exception class first, so this explicit registration keeps
    # ReDOCX route-level FastAPI HTTPExceptions on the centralized normalization
    # path while allowing framework-level Starlette errors (for example a 404 for
    # an unknown route) to retain api_v1's handler.
    @app.exception_handler(HTTPException)
    async def _handle_fastapi_http_error(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        return await _json_error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return await _json_error_response(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return await _json_error_response(request, exc)

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        return await _json_error_response(request, exc)


__all__ = [
    "APIError",
    "ERRORS",
    "ErrorCode",
    "ErrorDefinition",
    "NormalizedError",
    "install_error_handlers",
    "normalize_exception",
    "raise_api_error",
    "to_http_exception",
]
