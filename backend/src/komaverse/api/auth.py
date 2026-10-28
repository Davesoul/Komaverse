"""Auth endpoints: register / login / logout / me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse.auth import (
    create_session,
    destroy_session,
    get_current_user,
    hash_password,
    verify_password,
)
from komaverse.db import get_session
from komaverse.db_models import UserRow

router = APIRouter(prefix="/api/v1")


class RegisterBody(BaseModel):
    email: str = ""
    password: str = ""
    name: str = ""


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class UserOut(BaseModel):
    id: str
    email: str
    name: str


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


@router.post("/auth/register", response_model=UserOut, status_code=201)
def register(
    body: RegisterBody,
    response: Response,
    session: Session = Depends(get_session),
):
    email = _normalize_email(body.email)
    if "@" not in email or len(email) < 3 or len(email) > 320:
        raise HTTPException(400, "invalid email")
    if len(body.password) < 8 or len(body.password) > 200:
        raise HTTPException(400, "password must be 8–200 characters")
    exists = session.execute(
        select(UserRow).where(UserRow.email == email)
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(409, "email already registered")
    user = UserRow(
        email=email, name=(body.name or "").strip()[:200],
        password_hash=hash_password(body.password),
    )
    session.add(user)
    session.commit()
    create_session(session, user, response)
    return UserOut(id=user.id, email=user.email, name=user.name)


@router.post("/auth/login", response_model=UserOut)
def login(
    body: LoginBody,
    response: Response,
    session: Session = Depends(get_session),
):
    email = _normalize_email(body.email)
    user = session.execute(
        select(UserRow).where(UserRow.email == email)
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid email or password")
    create_session(session, user, response)
    return UserOut(id=user.id, email=user.email, name=user.name)


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    destroy_session(request, response, session)
    return {"status": "ok"}


@router.get("/auth/me", response_model=UserOut)
def me(user: UserRow = Depends(get_current_user)):
    return UserOut(id=user.id, email=user.email, name=user.name)
