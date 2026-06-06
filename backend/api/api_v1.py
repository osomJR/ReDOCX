from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv
import os
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH)
from contextlib import asynccontextmanager
from backend.routes.account import router as account_router
from backend.organizations import router as organizations_router
from backend.team_communications import router as team_communications_router
from backend.billing import router as billing_router
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.errors import install_error_handlers
from backend.routes.route_v1 import router as analyzer_router
from backend.routes.billing_webhooks import router as billing_webhooks_router

API_V1_PREFIX = "/api/v1"


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Intentionally minimal.
    # Auth0 JWKS, Redis, and rate-limiter state are initialized lazily by their own modules.
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Analyzer API v1",
        version="1.0.0",
        lifespan=lifespan,
    )
    install_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_csv_env("CORS_ALLOW_ORIGINS", "*"),
        allow_credentials=os.getenv("CORS_ALLOW_CREDENTIALS", "true").strip().lower()
        not in {"0", "false", "no"},
        allow_methods=_csv_env("CORS_ALLOW_METHODS", "*"),
        allow_headers=_csv_env("CORS_ALLOW_HEADERS", "*"),
    )

    v1_router = APIRouter(prefix=API_V1_PREFIX)
    v1_router.include_router(analyzer_router)
    v1_router.include_router(account_router)
    v1_router.include_router(organizations_router)
    v1_router.include_router(team_communications_router)
    v1_router.include_router(billing_router)
    app.include_router(v1_router)
    v1_router.include_router(billing_webhooks_router)

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