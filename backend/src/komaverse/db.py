"""Sync DB engine + session wiring (SQLite dev, Postgres prod).

Sync SQLAlchemy is a deliberate choice (see DECISIONS ADR-5): the heavy work
(OpenCV detection, ffmpeg export) is CPU-bound and sync anyway, and sync code
is directly testable without an event loop. Uvicorn runs sync endpoints in
its threadpool.

M3 adds Alembic migrations; until then create_all on startup.
"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from komaverse.config import settings


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    """WAL + a real busy timeout: concurrent chat turns, job polls, and
    background workers share one dev SQLite file without 500s."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA busy_timeout=10000;")
        cur.execute("PRAGMA synchronous=NORMAL;")
    finally:
        cur.close()


def _sqlite_file_for_url(url: str):
    """Local file for a sqlite URL, or None (memory/shared cache)."""
    path = url.split("sqlite:///", 1)[-1].split("?", 1)[0]
    if not path or path == ":memory:" or path.startswith("file:"):
        return None
    from pathlib import Path as _Path

    return _Path(path)


def get_engine():
    from pathlib import Path as _Path

    url = settings.database_url
    if url.startswith("sqlite"):
        target = _sqlite_file_for_url(url)
        # Docker mounts /data but dev defaults to ./komaverse.db: create the
        # parent so a volume-backed DATABASE_URL never fails on first boot.
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
    # MEDIA_DIR is volume-backed too (/data/media in Docker); ensure it here
    # so readiness and uploads share one creation path with main.py.
    try:
        _Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    kwargs = (
        {"connect_args": {"check_same_thread": False}}
        if url.startswith("sqlite")
        else {}
    )
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


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
        "ALTER TABLE audio_cues ADD COLUMN rate FLOAT",
        "ALTER TABLE export_reports ADD COLUMN subtitles_vtt_url VARCHAR(500)",
        "ALTER TABLE jobs ADD COLUMN model VARCHAR(200)",
        "ALTER TABLE jobs ADD COLUMN seed INTEGER",
        "ALTER TABLE jobs ADD COLUMN negative_prompt TEXT",
        "ALTER TABLE jobs ADD COLUMN style VARCHAR(32)",
        "ALTER TABLE jobs ADD COLUMN genre VARCHAR(32)",
        "ALTER TABLE jobs ADD COLUMN camera_move VARCHAR(32)",
        "ALTER TABLE jobs ADD COLUMN final_prompt TEXT",
        "ALTER TABLE jobs ADD COLUMN entities_json TEXT",
        "ALTER TABLE jobs ADD COLUMN enhance BOOLEAN",
        "ALTER TABLE jobs ADD COLUMN reference_frame_id VARCHAR(12)",
        "ALTER TABLE jobs ADD COLUMN layer_frame_id VARCHAR(12)",
        "ALTER TABLE jobs ADD COLUMN created_at DATETIME",
        "ALTER TABLE frames ADD COLUMN sequence_id VARCHAR(12)",
        "ALTER TABLE clips ADD COLUMN transition VARCHAR(32)",
        "ALTER TABLE clips ADD COLUMN transition_duration FLOAT",
        "ALTER TABLE clips ADD COLUMN fade_in FLOAT",
        "ALTER TABLE clips ADD COLUMN fade_out FLOAT",
        "ALTER TABLE clips ADD COLUMN text_content TEXT",
        "ALTER TABLE clips ADD COLUMN text_size INTEGER",
        "ALTER TABLE clips ADD COLUMN text_color VARCHAR(16)",
        "ALTER TABLE clips ADD COLUMN text_position VARCHAR(16)",
        "ALTER TABLE clips ADD COLUMN text_anim VARCHAR(16)",
        "ALTER TABLE clips ADD COLUMN clip_filter VARCHAR(32)",
        "ALTER TABLE clips ADD COLUMN lane INTEGER",
        "ALTER TABLE subtitle_tracks ADD COLUMN lane INTEGER",
        "ALTER TABLE pages ADD COLUMN name VARCHAR(200)",
        "ALTER TABLE panels ADD COLUMN name VARCHAR(200)",
        "ALTER TABLE panels ADD COLUMN dialogue TEXT",
        "ALTER TABLE panels ADD COLUMN sfx VARCHAR(500)",
        "ALTER TABLE panels ADD COLUMN cues TEXT",
        "ALTER TABLE cloned_voices ADD COLUMN engine VARCHAR(32)",
        "ALTER TABLE cloned_voices ADD COLUMN ref_audio VARCHAR(500)",
        "ALTER TABLE usage_events ADD COLUMN project_id VARCHAR(12) DEFAULT ''",
        "ALTER TABLE clips ADD COLUMN offset FLOAT DEFAULT 0.0",
        "ALTER TABLE chat_turns ADD COLUMN actions TEXT",
        "ALTER TABLE chat_turns ADD COLUMN thread_id VARCHAR(12)",
        "ALTER TABLE jobs ADD COLUMN cost_credits INTEGER",
        "ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(200)",
        "ALTER TABLE audio_cues ADD COLUMN speaker VARCHAR(200) DEFAULT ''",
        "ALTER TABLE audio_cues ADD COLUMN text TEXT DEFAULT ''",
        "ALTER TABLE entities ADD COLUMN voice VARCHAR(200) DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN used_own_key BOOLEAN",
        "ALTER TABLE audio_takes ADD COLUMN lipsync_json TEXT",
        "ALTER TABLE export_reports ADD COLUMN lipsync_url VARCHAR(500)",
        "ALTER TABLE layers ADD COLUMN kind VARCHAR(16)",
        "ALTER TABLE layers ADD COLUMN vector_data TEXT",
        "ALTER TABLE frames ADD COLUMN guides_visible BOOLEAN DEFAULT 0",
        "ALTER TABLE frames ADD COLUMN guides_json TEXT DEFAULT '[]'",
    ):
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(ddl)
        except Exception:
            pass
    # Subtitle lanes (ADR-29): one track per (scene, lane). Old DBs carry
    # UNIQUE(scene_id); rebuild the table once to the (scene_id, lane)
    # constraint, preserving rows on lane 0. Fresh DBs already match.
    try:
        with engine.begin() as conn:
            legacy = False
            for row in conn.exec_driver_sql(
                "PRAGMA index_list(subtitle_tracks)"
            ).all():
                if row[2] and row[1].startswith("sqlite_autoindex"):
                    cols = conn.exec_driver_sql(
                        f"PRAGMA index_info({row[1]})"
                    ).all()
                    if [c[2] for c in cols] == ["scene_id"]:
                        legacy = True
            if legacy:
                conn.exec_driver_sql(
                    "CREATE TABLE subtitle_tracks_new ("
                    "id VARCHAR(12) NOT NULL PRIMARY KEY, "
                    "scene_id VARCHAR(12) NOT NULL REFERENCES scenes(id), "
                    "lang VARCHAR(16) DEFAULT 'en', "
                    "lane INTEGER DEFAULT 0, "
                    "file_path VARCHAR(500) DEFAULT '', "
                    "CONSTRAINT uq_subtitle_scene_lane UNIQUE (scene_id, lane))"
                )
                conn.exec_driver_sql(
                    "INSERT INTO subtitle_tracks_new "
                    "(id, scene_id, lang, lane, file_path) "
                    "SELECT id, scene_id, lang, COALESCE(lane, 0), file_path "
                    "FROM subtitle_tracks"
                )
                conn.exec_driver_sql("DROP TABLE subtitle_tracks")
                conn.exec_driver_sql(
                    "ALTER TABLE subtitle_tracks_new RENAME TO subtitle_tracks"
                )
    except Exception:
        pass
    # Studio AI overrides (provider/models picked in the widget) overlay
    # the environment so the saved brain survives restarts.
    try:
        from komaverse.studio_settings import apply_ai_overrides

        with session_factory()() as session:
            apply_ai_overrides(session)
    except Exception:
        pass
