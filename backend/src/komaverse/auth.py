"""Local auth: PBKDF2 passwords + DB-backed sessions in httpOnly cookies.

Always on — there is no bypass switch. Sessions are revocable server-side
(logout deletes the row) and expire after 30 days.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from komaverse.config import settings
from komaverse.db import get_session
from komaverse.db_models import SessionRow, UserRow

SESSION_COOKIE = "komaverse_session"
SESSION_DAYS = 30
PBKDF2_ROUNDS = 600_000  # tests monkeypatch this down


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        _, rounds, salt, dk = hashed.split("$")
        cand = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
        )
        return hmac.compare_digest(cand.hex(), dk)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(session: Session, user: UserRow, response: Response) -> SessionRow:
    token = secrets.token_urlsafe(32)
    row = SessionRow(
        user_id=user.id,
        token_hash=_token_hash(token),
        expires_at=_utcnow() + timedelta(days=SESSION_DAYS),
    )
    session.add(row)
    session.commit()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
        path="/",
    )
    return row


def get_current_user(
    request: Request, session: Session = Depends(get_session)
) -> UserRow:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "not authenticated")
    row = session.execute(
        select(SessionRow).where(SessionRow.token_hash == _token_hash(token))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(401, "not authenticated")
    if row.expires_at < _utcnow():
        session.delete(row)
        session.commit()
        raise HTTPException(401, "session expired")
    user = session.get(UserRow, row.user_id)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


def destroy_session(request: Request, response: Response, session: Session) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session.execute(
            delete(SessionRow).where(SessionRow.token_hash == _token_hash(token))
        )
        session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


def own_project(session: Session, user: UserRow, project_id: str):
    """Load a project owned by the user or raise 404 (no existence leak)."""
    from komaverse.db_models import ProjectRow

    row = session.execute(
        select(ProjectRow).where(
            ProjectRow.id == project_id, ProjectRow.user_id == user.id
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "project not found")
    return row


def ensure_owner(session: Session, user: UserRow, project_id: str) -> None:
    own_project(session, user, project_id)
