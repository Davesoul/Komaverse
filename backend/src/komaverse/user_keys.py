"""Bring-your-own-key vault: per-user provider keys, Fernet-encrypted at rest.

Only the generation path (image/video jobs) reads values, and only for
the job owner. Everywhere else the API reports set-or-not plus last-four
for identification — values never leave the server except to the
provider that was asked to generate.
"""

from __future__ import annotations

from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse.config import settings
from komaverse.db_models import UserProviderKeyRow

#: Keyed providers a user can bring on the generation path. local/fake
#: cost the studio nothing and comfyui is self-hosted: no key needed.
BYOK_PROVIDERS = ("openrouter", "meta", "cloud")

MIN_KEY_LEN = 8
MAX_KEY_LEN = 500


def requires_own_key(email: str | None) -> bool:
    """True when this login may only generate on its own vault keys.

    Shared demo/trial accounts (see OWN_KEYS_ONLY_EMAILS) never fall
    back to studio keys or studio credits.
    """
    allowed = {
        e.strip().lower()
        for e in (settings.own_keys_only_emails or "").split(",")
        if e.strip()
    }
    return (email or "").strip().lower() in allowed


class VaultUnavailable(Exception):
    """KEY_VAULT_SECRET is not configured (endpoints answer 503)."""


class UnreadableKey(Exception):
    """A stored key no longer decrypts (secret rotated): save it again."""


def _fernet() -> Fernet:
    secret = settings.key_vault_secret
    if not secret:
        raise VaultUnavailable(
            "own keys need KEY_VAULT_SECRET on the server (ask the studio admin)"
        )
    try:
        return Fernet(secret.encode())
    except (ValueError, TypeError) as exc:
        raise VaultUnavailable(f"KEY_VAULT_SECRET is not a valid Fernet key: {exc}") from None


def mask_key(raw: str) -> str:
    tail = raw[-4:] if len(raw) >= 4 else "••••"
    return f"••••{tail}"


def set_user_key(session: Session, user_id: str, provider: str, raw: str) -> str:
    """Store (or replace) one own key. Returns its masked form."""
    provider = (provider or "").strip().lower()
    if provider not in BYOK_PROVIDERS:
        raise KeyError(f"unknown provider for own keys: {provider!r} (want openrouter/meta/cloud)")
    raw = (raw or "").strip()
    if not MIN_KEY_LEN <= len(raw) <= MAX_KEY_LEN:
        raise ValueError(f"key must be {MIN_KEY_LEN}..{MAX_KEY_LEN} characters")
    token = _fernet().encrypt(raw.encode()).decode()
    row = session.get(UserProviderKeyRow, (user_id, provider))
    if row is None:
        row = UserProviderKeyRow(user_id=user_id, provider=provider)
        session.add(row)
    row.key_encrypted = token
    row.updated_at = datetime.now()
    session.commit()
    return mask_key(raw)


def get_user_key(session: Session, user_id: str, provider: str) -> str | None:
    """Decrypted own key for the generation path, or None when unset."""
    row = session.get(UserProviderKeyRow, (user_id, (provider or "").strip().lower()))
    if row is None or not row.key_encrypted:
        return None
    try:
        return _fernet().decrypt(row.key_encrypted.encode()).decode()
    except InvalidToken as exc:
        raise UnreadableKey(
            "saved key no longer decrypts (server secret changed) — save it again"
        ) from exc


def delete_user_key(session: Session, user_id: str, provider: str) -> bool:
    row = session.get(UserProviderKeyRow, (user_id, (provider or "").strip().lower()))
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_user_keys(session: Session, user_id: str) -> list[dict]:
    """Set-or-not plus masked tail per provider; never values."""
    rows = (
        session.execute(
            select(UserProviderKeyRow).where(UserProviderKeyRow.user_id == user_id)
        )
        .scalars()
        .all()
    )
    out = []
    for row in rows:
        try:
            masked = mask_key(_fernet().decrypt(row.key_encrypted.encode()).decode())
        except (InvalidToken, VaultUnavailable):
            masked = "••••????"
        out.append(
            {
                "provider": row.provider,
                "masked": masked,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
        )
    return out
