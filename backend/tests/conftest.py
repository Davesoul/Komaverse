"""Shared test helpers: fast password hashing + user factory."""

import pytest

import komaverse.auth as auth
from komaverse.auth import hash_password
from komaverse.db_models import UserRow


@pytest.fixture(autouse=True)
def _fast_passwords(monkeypatch):
    monkeypatch.setattr(auth, "PBKDF2_ROUNDS", 1000)


def create_user(session, email="u@test.local", password="password123", name="T"):
    row = UserRow(email=email, name=name, password_hash=hash_password(password))
    session.add(row)
    session.commit()
    return row
