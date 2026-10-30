"""Sync DB engine + session wiring (SQLite dev, Postgres prod).

Sync SQLAlchemy is a deliberate choice (see DECISIONS ADR-5): the heavy work
(OpenCV detection, ffmpeg export) is CPU-bound and sync anyway, and sync code
is directly testable without an event loop. Uvicorn runs sync endpoints in
its threadpool.

M3 adds Alembic migrations; until then create_all on startup.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from komaverse.config import settings


def get_engine():
    url = settings.database_url
    kwargs = (
        {"connect_args": {"check_same_thread": False}}
        if url.startswith("sqlite")
        else {}
    )
    return create_engine(url, **kwargs)


def session_factory_for(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


_engine = None
_sessions: sessionmaker[Session] | None = None


def session_factory() -> sessionmaker[Session]:
    global _engine, _sessions
    if _sessions is None:
        _engine = get_engine()
        _sessions = session_factory_for(_engine)
    return _sessions


def get_session() -> Generator[Session, None, None]:
    factory = session_factory()
    with factory() as session:
        yield session


def init_db() -> None:
    from komaverse.db_models import Base  # noqa: PLC0415 — register tables

    engine = get_engine()
    Base.metadata.create_all(engine)
    # No Alembic yet (ADR-5): backfill columns that create_all cannot add
    # to existing tables. Each ALTER is a no-op when the column exists.
    for ddl in (
        "ALTER TABLE audio_cues ADD COLUMN end_sec FLOAT",
        "ALTER TABLE audio_cues ADD COLUMN lane INTEGER",
        "ALTER TABLE export_reports ADD COLUMN subtitles_vtt_url VARCHAR(500)",
        "ALTER TABLE jobs ADD COLUMN model VARCHAR(200)",
        "ALTER TABLE jobs ADD COLUMN seed INTEGER",
        "ALTER TABLE jobs ADD COLUMN negative_prompt TEXT",
        "ALTER TABLE jobs ADD COLUMN style VARCHAR(32)",
        "ALTER TABLE jobs ADD COLUMN camera_move VARCHAR(32)",
        "ALTER TABLE jobs ADD COLUMN final_prompt TEXT",
        "ALTER TABLE jobs ADD COLUMN entities_json TEXT",
        "ALTER TABLE jobs ADD COLUMN enhance BOOLEAN",
        "ALTER TABLE jobs ADD COLUMN reference_frame_id VARCHAR(12)",
        "ALTER TABLE frames ADD COLUMN sequence_id VARCHAR(12)",
    ):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(ddl)
        except Exception:
            pass
