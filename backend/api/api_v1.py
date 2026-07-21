from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv
import logging
import os
import re
from contextlib import asynccontextmanager

from backend.routes.account import router as account_router
from backend.organizations import router as organizations_router
from backend.team_communications import (
    router as team_communications_router,
    start_team_realtime_services,
    stop_team_realtime_services,
)
from backend.team_attachment_http import TeamAttachmentRequestSizeLimitMiddleware
from backend.team_attachment_routes import router as team_attachment_router
from backend.team_governance import router as team_governance_router
from backend.billing import router as billing_router
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.errors import install_error_handlers
from backend.routes.route_v1 import router as analyzer_router
from backend.routes.billing_webhooks import router as billing_webhooks_router


ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH)

API_V1_PREFIX = "/api/v1"

TOKEN_QUERY_RE = re.compile(r"([?&](?:token|access_token|id_token)=)[^&\s\"]+", re.IGNORECASE)

DEFAULT_ROBOTS_TXT = """User-agent: *
Allow: /

# Keep private application, authentication, and API surfaces out of search results.
Disallow: /api/
Disallow: /auth/
Disallow: /account/
Disallow: /dashboard/
Disallow: /settings/
Disallow: /team/messages

Sitemap: https://redocx.app/sitemap.xml
"""


def _load_robots_txt() -> str:
    configured_path = os.getenv("ROBOTS_TXT_PATH", "").strip()
    candidates: list[Path] = []

    if configured_path:
        candidates.append(Path(configured_path).expanduser())

    current_file = Path(__file__).resolve()
    candidates.extend(
        [
            current_file.parent / "robots.txt",
            current_file.parent.parent / "robots.txt",
            Path.cwd() / "robots.txt",
            Path("/app/backend/robots.txt"),
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")

    return DEFAULT_ROBOTS_TXT


class RedactAuthTokenFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        def redact(value: object) -> object:
            if isinstance(value, str):
                return TOKEN_QUERY_RE.sub(r"\1[REDACTED]", value)
            return value

        record.msg = redact(record.msg)

        if isinstance(record.args, tuple):
            record.args = tuple(redact(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: redact(value) for key, value in record.args.items()}

        return True


def _normalize_http_exception_detail(detail: object) -> dict[str, object]:
    if isinstance(detail, dict):
        return detail

    message = str(detail or "Request failed.")
    return {
        "error": "invalid_request",
        "message": message,
    }


async def _http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_normalize_http_exception_detail(exc.detail),
        headers=getattr(exc, "headers", None),
    )


def install_auth_log_redaction() -> None:
    redaction_filter = RedactAuthTokenFilter()
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(item, RedactAuthTokenFilter) for item in logger.filters):
            logger.addFilter(redaction_filter)


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_production() -> bool:
    environment = (
        os.getenv("APP_ENV", "").strip()
        or os.getenv("ENVIRONMENT", "").strip()
        or os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip()
    ).lower()
    return environment in {"production", "prod"}


def _origin_from_url_env(name: str) -> str | None:
    raw = os.getenv(name, "").strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        return None
    return raw


def _cors_origins() -> list[str]:
    configured = [origin for origin in _csv_env("CORS_ALLOW_ORIGINS", "") if origin != "*"]
    inferred = [
        _origin_from_url_env("APP_BASE_URL"),
        _origin_from_url_env("FRONTEND_URL"),
        _origin_from_url_env("NEXT_PUBLIC_APP_URL"),
    ]
    defaults = (
        ["https://redocx.app", "https://www.redocx.app"]
        if _is_production()
        else ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    return list(dict.fromkeys([*configured, *(item for item in inferred if item), *defaults]))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_team_realtime_services()
    try:
        yield
    finally:
        await stop_team_realtime_services()


def create_app() -> FastAPI:
    install_auth_log_redaction()

    app = FastAPI(
        title="Analyzer API v1",
        version="1.0.0",
        lifespan=lifespan,
    )

    install_error_handlers(app)

    # Preserve FastAPI/Starlette HTTPException statuses even if backend.errors
    # installs a broad Exception handler for unexpected failures.
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=os.getenv("CORS_ALLOW_CREDENTIALS", "true").strip().lower()
        not in {"0", "false", "no"},
        # Preserve the existing method/header compatibility surface. The
        # security boundary is the explicit origin list, not a restrictive
        # header allowlist that could break analyzer or upload clients.
        allow_methods=_csv_env("CORS_ALLOW_METHODS", "*"),
        allow_headers=_csv_env("CORS_ALLOW_HEADERS", "*"),
    )

    # This limit is deliberately scoped to team-message attachments. Feature
    # processing uploads keep their existing limits and middleware behavior.
    app.add_middleware(TeamAttachmentRequestSizeLimitMiddleware)

    @app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
    def robots_txt() -> PlainTextResponse:
        return PlainTextResponse(_load_robots_txt(), media_type="text/plain")

    v1_router = APIRouter(prefix=API_V1_PREFIX)
    v1_router.include_router(analyzer_router)
    v1_router.include_router(account_router)
    v1_router.include_router(organizations_router)
    v1_router.include_router(team_communications_router)
    v1_router.include_router(team_attachment_router)
    v1_router.include_router(team_governance_router)
    v1_router.include_router(billing_router)

    # Webhooks must be registered before the parent router is mounted.
    # FastAPI copies routes into the app at include_router(...) time.
    v1_router.include_router(billing_webhooks_router)
    app.include_router(v1_router)

    @app.get("/", tags=["system"])
    def root() -> dict[str, str]:
        return {
            "service": "analyzer-api",
            "status": "ok",
        }

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
        }

    return app


app = create_app()

__all__ = ["app", "create_app"]
