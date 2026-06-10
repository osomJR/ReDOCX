from __future__ import annotations

from pydantic import BaseModel, field_validator
from fastapi import APIRouter, Depends, HTTPException

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.database import get_db
from backend.settings import ensure_user_settings, update_appearance
from backend.subscriptions import get_user_entitlement


router = APIRouter(prefix="/account", tags=["account-v1"])


class AppearanceSettingsUpdate(BaseModel):
    appearance: str

    @field_validator("appearance")
    @classmethod
    def validate_appearance(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"light", "dark", "system"}:
            raise ValueError("appearance must be one of: light, dark, system.")
        return normalized


@router.get("/me")
def get_account_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            settings = ensure_user_settings(conn, current_user.user_id)

        entitlement = get_user_entitlement(current_user.user_id)

        return {
            "user": {
                "id": current_user.user_id,
                "name": current_user.claims.get("name"),
                "email": current_user.claims.get("email"),
                "email_verified": current_user.claims.get("email_verified"),
                "picture": current_user.claims.get("picture"),
            },
            "settings": {
                "appearance": settings["appearance"],
            },
            "entitlement": {
                "plan": entitlement.plan,
                "account_count": entitlement.account_count,
                "status": entitlement.status,
                "is_paid": entitlement.is_paid,
                "source": entitlement.source,
                "organization_id": entitlement.organization_id,
                "organization_name": entitlement.organization_name,
                "organization_role": entitlement.organization_role,
            },
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_load_failed",
                "message": "Could not load account details.",
            },
        ) from exc


@router.patch("/settings")
def patch_account_settings(
    payload: AppearanceSettingsUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            ensure_user_settings(conn, current_user.user_id)
            settings = update_appearance(
                conn,
                current_user.user_id,
                payload.appearance,
            )

        entitlement = get_user_entitlement(current_user.user_id)

        return {
            "success": True,
            "settings": {
                "appearance": settings["appearance"],
            },
            "entitlement": {
                "plan": entitlement.plan,
                "account_count": entitlement.account_count,
                "status": entitlement.status,
                "is_paid": entitlement.is_paid,
                "source": entitlement.source,
                "organization_id": entitlement.organization_id,
                "organization_name": entitlement.organization_name,
                "organization_role": entitlement.organization_role,
            },
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_setting",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_update_failed",
                "message": "Could not update account settings.",
            },
        ) from exc