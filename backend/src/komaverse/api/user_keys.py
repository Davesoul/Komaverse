"""Own provider keys (BYOK): per-user storage for generation calls.

A saved key means generations on that provider bill the user's own
provider account instead of studio credits. Values are Fernet-encrypted
at rest and never returned by any endpoint — reads report set-or-not
plus a masked tail. Chat, voice, and detection keep using studio keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from komaverse.auth import get_current_user
from komaverse.db import get_session
from komaverse.db_models import UserRow
from komaverse.user_keys import (
    BYOK_PROVIDERS,
    VaultUnavailable,
    delete_user_key,
    list_user_keys,
    set_user_key,
)

router = APIRouter(prefix="/api/v1")


class KeyOut(BaseModel):
    provider: str
    masked: str
    updated_at: str | None = None


class KeySaveBody(BaseModel):
    provider: str = ""
    key: str = ""


@router.get("/user/keys", response_model=list[KeyOut])
def list_keys(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Your saved provider keys (masked tails only, never values)."""
    return [KeyOut(**k) for k in list_user_keys(session, user.id)]


@router.put("/user/keys", response_model=KeyOut)
def save_key(
    body: KeySaveBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Save (or replace) your own key for one provider."""
    try:
        masked = set_user_key(session, user.id, body.provider, body.key)
    except KeyError as exc:
        raise HTTPException(
            400, f"{exc} (want {', '.join(BYOK_PROVIDERS)})"
        ) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except VaultUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    rows = list_user_keys(session, user.id)
    saved = next((k for k in rows if k["provider"] == body.provider.strip().lower()), None)
    return KeyOut(
        provider=body.provider.strip().lower(),
        masked=masked,
        updated_at=saved["updated_at"] if saved else None,
    )


@router.delete("/user/keys/{provider}", status_code=204)
def remove_key(
    provider: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Forget your own key: generations fall back to studio credits."""
    if provider.strip().lower() not in BYOK_PROVIDERS:
        raise HTTPException(400, f"unknown provider (want {', '.join(BYOK_PROVIDERS)})")
    if not delete_user_key(session, user.id, provider):
        raise HTTPException(404, "no saved key for that provider")
