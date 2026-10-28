"""Auth tests: register/login/logout/me + session cookies. All sync."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.api.auth import LoginBody, RegisterBody, login, logout, me, register
from komaverse.auth import SESSION_COOKIE, get_current_user
from komaverse.config import settings
from komaverse.db_models import Base


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def authed_request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{SESSION_COOKIE}={token}".encode())],
        }
    )


def token_from(response: Response) -> str:
    raw = response.headers["set-cookie"]
    assert "httponly" in raw.lower()
    assert "samesite=lax" in raw.lower()
    return raw.split(";")[0].split("=", 1)[1]


def test_register_login_me_logout(factory):
    with factory() as s:
        resp = Response()
        out = register(
            RegisterBody(email="Ada@x.io", password="password123", name="Ada"), resp, s
        )
    assert out.email == "ada@x.io"  # normalized
    token = token_from(resp)

    with factory() as s:
        who = me(get_current_user(authed_request(token), s))
    assert who.email == "ada@x.io"

    with factory() as s:
        resp = Response()
        assert logout(authed_request(token), resp, s) == {"status": "ok"}
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            get_current_user(authed_request(token), s)
    assert exc.value.status_code == 401

    # Login again with the same credentials.
    with factory() as s:
        resp = Response()
        out = login(LoginBody(email="ada@x.io", password="password123"), resp, s)
    assert out.name == "Ada"
    token_from(resp)


def test_login_rejects_bad_credentials(factory):
    with factory() as s:
        register(RegisterBody(email="b@x.io", password="password123"), Response(), s)
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            login(LoginBody(email="b@x.io", password="wrongpass1"), Response(), s)
    assert exc.value.status_code == 401
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            login(LoginBody(email="nobody@x.io", password="password123"), Response(), s)
    assert exc.value.status_code == 401  # same code: no user enumeration


def test_register_validates_input(factory):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            register(RegisterBody(email="x@y.io", password="short"), Response(), s)
    assert exc.value.status_code == 400
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            register(RegisterBody(email="not-an-email", password="password123"), Response(), s)
    assert exc.value.status_code == 400
    with factory() as s:
        register(RegisterBody(email="dup@x.io", password="password123"), Response(), s)
        with pytest.raises(HTTPException) as exc:
            register(RegisterBody(email="dup@x.io", password="password123"), Response(), s)
    assert exc.value.status_code == 409


def test_me_requires_auth(factory):
    with factory() as s:
        with pytest.raises(HTTPException) as exc:
            get_current_user(Request({"type": "http", "headers": []}), s)
    assert exc.value.status_code == 401
