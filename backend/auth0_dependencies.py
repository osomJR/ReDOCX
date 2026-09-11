from __future__ import annotations

"""
Shared Auth0 authentication dependencies for FastAPI.

Responsibilities:
- validate Bearer JWTs issued by Auth0
- fetch and cache JWKS for signature verification
- expose FastAPI dependencies for optional and required authentication
- return normalized authenticated-user context only
- enforce timeout / JWKS refresh safeguards
- remain free of feature-specific business rules

Non-responsibilities:
- route authorization policy beyond scope checks
- request/response envelope construction
- user persistence or profile lookup
"""

import os
import time
from dataclasses import dataclass
from typing import Any, Optional, Set
from urllib.parse import quote

import requests
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from requests import RequestException


DEFAULT_JWKS_CACHE_TTL_SECONDS = int(os.getenv("AUTH0_JWKS_CACHE_TTL_SECONDS", "600"))
DEFAULT_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AUTH0_TIMEOUT_SECONDS", "5"))
DEFAULT_USERINFO_CACHE_TTL_SECONDS = int(os.getenv("AUTH0_USERINFO_CACHE_TTL_SECONDS", "300"))
DEFAULT_USER_EXISTENCE_CACHE_TTL_SECONDS = int(os.getenv("AUTH0_USER_EXISTENCE_CACHE_TTL_SECONDS", "30"))
DEFAULT_MANAGEMENT_TOKEN_SKEW_SECONDS = 60


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Auth0Config:
    """
    Low-level Auth0 verification configuration.

    Notes:
    - domain defaults to AUTH0_DOMAIN
    - audience defaults to AUTH0_AUDIENCE
    - issuer defaults to AUTH0_ISSUER
    - client_id is optional and enables azp validation when present
    """

    domain: Optional[str] = os.getenv("AUTH0_DOMAIN")
    audience: Optional[str] = os.getenv("AUTH0_AUDIENCE")
    issuer: Optional[str] = os.getenv("AUTH0_ISSUER")
    client_id: Optional[str] = os.getenv("AUTH0_CLIENT_ID")
    management_client_id: Optional[str] = (
        os.getenv("AUTH0_MANAGEMENT_CLIENT_ID")
        or os.getenv("AUTH0_MGMT_CLIENT_ID")
        or os.getenv("AUTH0_M2M_CLIENT_ID")
    )
    management_client_secret: Optional[str] = (
        os.getenv("AUTH0_MANAGEMENT_CLIENT_SECRET")
        or os.getenv("AUTH0_MGMT_CLIENT_SECRET")
        or os.getenv("AUTH0_M2M_CLIENT_SECRET")
    )
    validate_user_exists: bool = _env_flag("AUTH0_VALIDATE_USER_EXISTS", default=False)
    jwks_cache_ttl_seconds: int = DEFAULT_JWKS_CACHE_TTL_SECONDS
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    user_existence_cache_ttl_seconds: int = DEFAULT_USER_EXISTENCE_CACHE_TTL_SECONDS


@dataclass(frozen=True)
class AuthenticatedUser:
    """
    Normalized authenticated-user context returned by dependencies.
    """

    user_id: str
    claims: dict[str, Any]
    scopes: Set[str]


class Auth0DependencyProvider:
    """
    Shared low-level Auth0 dependency provider.

    Public contract:
    - get_current_user_optional(...) -> AuthenticatedUser | None
    - get_current_user(...) -> AuthenticatedUser
    - require_scopes(*required_scopes) -> dependency callable
    """

    def __init__(self, config: Optional[Auth0Config] = None) -> None:
        self.config = config or Auth0Config()

        self._domain = self._normalize_domain(self.config.domain)
        self._audience = self._normalize_required_setting(
            self.config.audience,
            field_name="AUTH0_AUDIENCE",
        )
        self._issuer = self._normalize_issuer(self.config.issuer)
        self._client_id = self._normalize_optional_setting(self.config.client_id)
        self._management_client_id = self._normalize_optional_setting(
            self.config.management_client_id
        )
        self._management_client_secret = self._normalize_optional_setting(
            self.config.management_client_secret
        )
        self._validate_user_exists = bool(self.config.validate_user_exists)
        if self._validate_user_exists and (
            not self._management_client_id or not self._management_client_secret
        ):
            raise RuntimeError(
                "AUTH0_VALIDATE_USER_EXISTS is enabled, but Auth0 Management API "
                "credentials are missing. Set AUTH0_MANAGEMENT_CLIENT_ID and "
                "AUTH0_MANAGEMENT_CLIENT_SECRET with read:users permission."
            )

        self._management_token: str = ""
        self._management_token_expires_at: float = 0
        self._request_timeout_seconds = self._normalize_timeout(
            self.config.request_timeout_seconds
        )
        self._jwks_cache = TTLCache(
            maxsize=2,
            ttl=self._normalize_cache_ttl(self.config.jwks_cache_ttl_seconds),
        )
        self._userinfo_cache = TTLCache(
            maxsize=1024,
            ttl=self._normalize_cache_ttl(DEFAULT_USERINFO_CACHE_TTL_SECONDS),
        )
        self._user_existence_cache = TTLCache(
            maxsize=4096,
            ttl=self._normalize_cache_ttl(self.config.user_existence_cache_ttl_seconds),
        )
        self._bearer = HTTPBearer(auto_error=False)

    @property
    def jwks_url(self) -> str:
        return f"https://{self._domain}/.well-known/jwks.json"

    def authenticate_access_token(
        self,
        token: str,
        request: Request | None = None,
    ) -> AuthenticatedUser:
        """
        Validate a raw Auth0 access token and return the normalized user context.

        This is intentionally shared by HTTP dependencies and WebSocket routes so
        both paths enforce the same issuer, audience, signing-key, azp, user, and
        account-lifecycle rules.
        """
        normalized_token = self._normalize_token(token)
        rsa_key = self._get_rsa_key(normalized_token)

        try:
            payload = jwt.decode(
                normalized_token,
                rsa_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except JWTError as exc:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Token is invalid or expired.",
                },
            ) from exc

        payload = self._merge_userinfo_claims(payload, normalized_token)

        user_id = self._extract_subject(payload)
        self._validate_authorized_party(payload)
        self._validate_canonical_user_exists(user_id)
        self._validate_account_lifecycle(user_id, request)
        scopes = self._extract_scopes(payload)

        return AuthenticatedUser(
            user_id=user_id,
            claims=payload,
            scopes=scopes,
        )

    def get_current_user_optional(
        self,
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    ) -> AuthenticatedUser | None:
        """
        Returns:
        - None when Authorization header is absent
        - AuthenticatedUser when a valid Bearer token is present
        """
        if not creds or not creds.credentials:
            return None

        return self.authenticate_access_token(creds.credentials, request=request)

    def get_current_user(
        self,
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    ) -> AuthenticatedUser:
        """
        Returns:
        - AuthenticatedUser when a valid Bearer token is present

        Raises:
        - 401 when Authorization header is missing or invalid
        """
        user = self.get_current_user_optional(request, creds)
        if user is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "authorization_required",
                    "message": "Authorization credentials are required.",
                },
            )
        return user

    def require_scopes(self, *required_scopes: str):
        """
        Build a FastAPI dependency that enforces one or more scopes.

        Usage:
            @router.get("/private")
            def private_route(
                current_user: AuthenticatedUser = Depends(auth0.require_scopes("read:items"))
            ):
                ...
        """
        normalized_required_scopes = {
            self._normalize_scope(scope) for scope in required_scopes if str(scope).strip()
        }

        def dependency(
            current_user: AuthenticatedUser = Depends(self.get_current_user),
        ) -> AuthenticatedUser:
            missing_scopes = normalized_required_scopes - current_user.scopes
            if missing_scopes:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "insufficient_scope",
                        "message": "Token does not include the required scopes.",
                        "required_scopes": sorted(normalized_required_scopes),
                        "granted_scopes": sorted(current_user.scopes),
                        "missing_scopes": sorted(missing_scopes),
                    },
                )
            return current_user

        return dependency

    def _get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh:
            cached = self._jwks_cache.get("jwks")
            if cached:
                return cached

        try:
            response = requests.get(
                self.jwks_url,
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            jwks = response.json()
        except RequestException as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "jwks_unavailable",
                    "message": "Auth key service unavailable.",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "jwks_invalid",
                    "message": "Auth key service returned invalid JWKS content.",
                },
            ) from exc

        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "jwks_invalid",
                    "message": "Auth key service returned malformed JWKS content.",
                },
            )

        self._jwks_cache["jwks"] = jwks
        return jwks

    def _get_rsa_key(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Token header is unreadable.",
                },
            ) from exc

        alg = header.get("alg")
        if alg != "RS256":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Invalid token algorithm.",
                },
            )

        kid = header.get("kid")
        if not kid:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Missing kid header.",
                },
            )

        jwks = self._get_jwks()
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key

        jwks = self._get_jwks(force_refresh=True)
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return key

        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "Unknown signing key (kid).",
            },
        )

    def _fetch_userinfo(self, token: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """
        Fetch Auth0 /userinfo profile claims using the existing access token.

        This is intentionally best-effort. Authentication should not fail just
        because /userinfo is temporarily unavailable; the backend will keep
        using the verified JWT claims it already has.
        """
        subject = payload.get("sub")
        cache_key = subject.strip() if isinstance(subject, str) and subject.strip() else token

        cached = self._userinfo_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            response = requests.get(
                f"https://{self._domain}/userinfo",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            userinfo = response.json()
        except (RequestException, ValueError):
            return None

        if not isinstance(userinfo, dict):
            return None

        self._userinfo_cache[cache_key] = userinfo
        return userinfo

    def _get_management_token(self) -> str:
        if not self._validate_user_exists:
            return ""

        now = time.time()
        if (
            self._management_token
            and now < self._management_token_expires_at - DEFAULT_MANAGEMENT_TOKEN_SKEW_SECONDS
        ):
            return self._management_token

        try:
            response = requests.post(
                f"https://{self._domain}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": self._management_client_id,
                    "client_secret": self._management_client_secret,
                    "audience": f"https://{self._domain}/api/v2/",
                },
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except RequestException as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_unavailable",
                    "message": "Could not validate the Auth0 user account.",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_invalid_response",
                    "message": "Auth0 Management API returned an invalid token response.",
                },
            ) from exc

        token = payload.get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_invalid_response",
                    "message": "Auth0 Management API did not return an access token.",
                },
            )

        try:
            expires_in = int(payload.get("expires_in") or 3600)
        except (TypeError, ValueError):
            expires_in = 3600

        self._management_token = token.strip()
        self._management_token_expires_at = now + max(expires_in, 1)
        return self._management_token

    @staticmethod
    def _account_lifecycle_allows_request(request: Request | None) -> bool:
        if request is None:
            return False

        path = str(getattr(request.url, "path", "") or "")
        method = str(getattr(request, "method", "") or "").upper()
        return (
            (path.endswith("/account/me") and method in {"GET", "DELETE"})
            or (path.endswith("/account/restore") and method == "POST")
        )

    def _validate_account_lifecycle(self, user_id: str, request: Request | None) -> None:
        """
        Block restricted lifecycle states at the auth dependency boundary while
        allowing read-only account inspection, idempotent deletion retry, and the
        explicit restore mutation.
        Lifecycle verification is fail-closed because deletion state is an access
        control boundary.
        """
        try:
            from backend.account_lifecycle import (
                account_access_is_restricted,
                account_lifecycle_table_exists,
                get_account_lifecycle,
            )
            from backend.database import get_db

            with get_db() as conn:
                if not account_lifecycle_table_exists(conn):
                    raise RuntimeError("account_lifecycle migration is not applied")
                lifecycle = get_account_lifecycle(conn, user_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "account_lifecycle_unavailable",
                    "message": (
                        "Account access cannot be verified safely right now. "
                        "Please retry shortly."
                    ),
                },
            ) from exc

        if lifecycle is None:
            return

        status = str(lifecycle.get("status") or "").strip().lower()
        if status == "purged":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "account_deleted",
                    "message": "This account no longer exists.",
                },
            )

        if account_access_is_restricted(lifecycle) and not self._account_lifecycle_allows_request(request):
            deactivation_in_progress = status == "deactivation_requested"
            raise HTTPException(
                status_code=403,
                detail={
                    "error": (
                        "account_deactivation_in_progress"
                        if deactivation_in_progress
                        else "account_deactivated_pending_deletion"
                    ),
                    "message": (
                        "Account deactivation is being finalized. Only account status, "
                        "deletion retry, logout, and explicit restoration are available."
                        if deactivation_in_progress
                        else (
                            "This account is deactivated pending deletion. Use the explicit "
                            "restore action before the restore deadline to restore it."
                        )
                    ),
                    "restore_deadline": lifecycle.get("restore_deadline"),
                    "purge_after": lifecycle.get("purge_after"),
                },
            )

    def _validate_canonical_user_exists(self, user_id: str) -> None:
        """
        Validate that the authenticated subject still exists in Auth0.

        JWTs and application sessions can outlive a user record deleted from the
        Auth0 dashboard. When enabled, this check makes Auth0 the canonical
        source of truth and blocks deleted users even if their token has not yet
        expired.
        """
        if not self._validate_user_exists:
            return

        normalized_user_id = (user_id or "").strip()
        if not normalized_user_id:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Token missing subject (sub).",
                },
            )

        cached = self._user_existence_cache.get(normalized_user_id)
        if cached is True:
            return

        encoded_user_id = quote(normalized_user_id, safe="")
        token = self._get_management_token()

        try:
            response = requests.get(
                f"https://{self._domain}/api/v2/users/{encoded_user_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self._request_timeout_seconds,
            )
        except RequestException as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_unavailable",
                    "message": "Could not validate the Auth0 user account.",
                },
            ) from exc

        if response.status_code == 404:
            self._userinfo_cache.pop(normalized_user_id, None)
            self._user_existence_cache.pop(normalized_user_id, None)
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "account_deleted",
                    "message": "This account no longer exists.",
                },
            )

        if response.status_code in {401, 403}:
            self._management_token = ""
            self._management_token_expires_at = 0
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_forbidden",
                    "message": (
                        "Auth0 Management API credentials cannot validate users. "
                        "Ensure read:users permission is granted."
                    ),
                },
            )

        if response.status_code >= 500:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_unavailable",
                    "message": "Auth0 Management API is temporarily unavailable.",
                },
            )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "auth0_management_validation_failed",
                    "message": "Could not validate the Auth0 user account.",
                },
            )

        self._user_existence_cache[normalized_user_id] = True

    def _merge_userinfo_claims(
        self,
        payload: dict[str, Any],
        token: str,
    ) -> dict[str, Any]:
        """
        Merge common profile fields from Auth0 /userinfo when they are missing
        from the access token JWT.

        This keeps the existing Auth0 login/signup setup intact while allowing
        app features such as email invitations to work with tokens that only
        expose email through /userinfo.
        """
        profile_fields = ("email", "email_verified", "name", "nickname", "picture")

        if all(payload.get(field) is not None for field in ("email", "name", "picture")):
            return payload

        userinfo = self._fetch_userinfo(token, payload)
        if not userinfo:
            return payload

        merged = dict(payload)

        for field in profile_fields:
            if merged.get(field) is None and userinfo.get(field) is not None:
                merged[field] = userinfo[field]

        return merged

    def _extract_subject(self, payload: dict[str, Any]) -> str:
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Token missing subject (sub).",
                },
            )
        return subject.strip()

    def _validate_authorized_party(self, payload: dict[str, Any]) -> None:
        if not self._client_id:
            return

        azp = payload.get("azp")
        if azp is None:
            return

        if not isinstance(azp, str) or azp.strip() != self._client_id:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Invalid authorized party.",
                },
            )

    @staticmethod
    def _extract_scopes(payload: dict[str, Any]) -> Set[str]:
        """
        Extract authorization grants from Auth0 access-token claims.

        Auth0 may expose API permissions in either:
        - scope: a space-delimited string, e.g. "openid profile email write:items"
        - permissions: an RBAC list, e.g. ["write:items"]

        The app treats both as scopes so require_scopes(...) works with either
        Auth0 token shape.
        """
        scopes: set[str] = set()

        raw_scope = payload.get("scope")
        if isinstance(raw_scope, str):
            scopes.update(
                scope.strip()
                for scope in raw_scope.split()
                if scope.strip()
            )

        raw_permissions = payload.get("permissions")
        if isinstance(raw_permissions, list):
            scopes.update(
                permission.strip()
                for permission in raw_permissions
                if isinstance(permission, str) and permission.strip()
            )

        return scopes

    @staticmethod
    def _normalize_token(token: str) -> str:
        if not isinstance(token, str):
            raise TypeError("token must be a string.")

        normalized = token.strip()
        if not normalized:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "invalid_token",
                    "message": "Bearer token is empty.",
                },
            )
        return normalized

    @staticmethod
    def _normalize_domain(domain: Optional[str]) -> str:
        normalized = Auth0DependencyProvider._normalize_required_setting(
            domain,
            field_name="AUTH0_DOMAIN",
        )
        normalized = normalized.replace("https://", "").replace("http://", "").strip().strip("/")
        if not normalized:
            raise RuntimeError(
                "AUTH0_DOMAIN is not configured. Set it in the environment before using Auth0DependencyProvider."
            )
        return normalized

    @staticmethod
    def _normalize_issuer(issuer: Optional[str]) -> str:
        normalized = Auth0DependencyProvider._normalize_required_setting(
            issuer,
            field_name="AUTH0_ISSUER",
        )
        return normalized if normalized.endswith("/") else f"{normalized}/"

    @staticmethod
    def _normalize_required_setting(value: Optional[str], *, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(
                f"{field_name} is not configured. Set it in the environment before using Auth0DependencyProvider."
            )
        return value.strip()

    @staticmethod
    def _normalize_optional_setting(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _normalize_timeout(value: float) -> float:
        try:
            normalized = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("request_timeout_seconds must be a numeric value.") from exc

        if normalized <= 0:
            raise ValueError("request_timeout_seconds must be > 0.")
        return normalized

    @staticmethod
    def _normalize_cache_ttl(value: int) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("jwks_cache_ttl_seconds must be an int-like value.") from exc

        if normalized < 1:
            raise ValueError("jwks_cache_ttl_seconds must be >= 1.")
        return normalized

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        if not isinstance(scope, str):
            raise TypeError("scope must be a string.")

        normalized = scope.strip()
        if not normalized:
            raise ValueError("scope must not be empty.")
        return normalized


bearer = HTTPBearer(auto_error=False)

_auth0_provider: Auth0DependencyProvider | None = None


def get_auth0_provider() -> Auth0DependencyProvider:
    global _auth0_provider
    if _auth0_provider is None:
        _auth0_provider = Auth0DependencyProvider()
    return _auth0_provider


def get_current_user_optional(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedUser | None:
    if not creds or not creds.credentials:
        return None
    return get_auth0_provider().get_current_user_optional(request, creds)


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedUser:
    if not creds or not creds.credentials:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authorization_required",
                "message": "Authorization credentials are required.",
            },
        )
    return get_auth0_provider().get_current_user(request, creds)


def authenticate_access_token(
    token: str,
    request: Request | None = None,
) -> AuthenticatedUser:
    return get_auth0_provider().authenticate_access_token(token, request=request)


def require_scopes(*required_scopes: str):
    def dependency(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        provider = get_auth0_provider()
        normalized_required_scopes = {
            provider._normalize_scope(scope)
            for scope in required_scopes
            if str(scope).strip()
        }
        missing_scopes = normalized_required_scopes - current_user.scopes
        if missing_scopes:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "insufficient_scope",
                    "message": "Token does not include the required scopes.",
                    "required_scopes": sorted(normalized_required_scopes),
                    "granted_scopes": sorted(current_user.scopes),
                    "missing_scopes": sorted(missing_scopes),
                },
            )
        return current_user

    return dependency


__all__ = [
    "Auth0Config",
    "AuthenticatedUser",
    "Auth0DependencyProvider",
    "bearer",
    "get_auth0_provider",
    "get_current_user_optional",
    "get_current_user",
    "require_scopes",
]
