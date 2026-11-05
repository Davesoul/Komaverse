"""Export latency: wall time for a minimal MP4 render.

Builds a 1-project / 1-scene / 1-text-clip timeline in a throwaway SQLite
DB, runs the same export_project + run_export_job path the UI uses, and
reports wall seconds plus output size. Fails (exit 1) past 300 s, which
means a hang, not a slow box — this tiny render should take seconds.
The 160x128 frame is a multiple of 16 on purpose (H.264 macroblocks).

Usage:  cd backend && PYTHONPATH=src python ../eval/export_latency.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from fastapi import BackgroundTasks  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from komaverse.api.animate import (  # noqa: E402
    ClipCreate,
    ExportRequest,
    SceneCreate,
    add_clip,
    create_scene,
    export_project,
    run_export_job,
)
from komaverse.api.scans import ProjectCreate, create_project  # noqa: E402
from komaverse.config import settings  # noqa: E402
from komaverse.db_models import Base, UserRow  # noqa: E402


def create_user(s, email: str = "eval@komaverse.local") -> UserRow:  # noqa: E402
    u = UserRow(email=email, name="Eval", password_hash="eval-not-a-login")
    s.add(u)
    s.commit()
    return u

TIMEOUT_S = 300.0


def main() -> int:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        settings.media_dir = Path(tmp) / "media"
        engine = create_engine(f"sqlite:///{tmp}/eval.db")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)

        with factory() as s:
            user = create_user(s)
            project = create_project(ProjectCreate(name="Eval"), s, user)
            scene = create_scene(project.id, SceneCreate(name="S1"), s, user)
            add_clip(
                scene.id,
                ClipCreate(kind="text", text="benchmark", duration=2.0),
                s, user,
            )
        with factory() as s:
            job = export_project(
                project.id,
                ExportRequest(fps=5, width=160, height=128),
                BackgroundTasks(), s, user,
            )
        t0 = time.perf_counter()
        run_export_job(job.id, factory)
        seconds = time.perf_counter() - t0

        with factory() as s:
            from komaverse.api.ai import get_job

            done = get_job(job.id, s, user)
        out = settings.media_dir / (done.result_url or "").removeprefix("/media/")
        size = out.stat().st_size if out.exists() else 0
        report = {
            "status": done.status,
            "seconds": round(seconds, 2),
            "bytes": size,
            "timeline": "1 scene, 1 text clip (2.0 s), 160x128 @ 5 fps",
        }
        print(json.dumps(report, indent=2))
        if done.status != "done" or not size:
            print("FAIL: export did not produce a file", file=sys.stderr)
            return 1
        if seconds > TIMEOUT_S:
            print(f"FAIL: {seconds:.1f}s > {TIMEOUT_S:.0f}s budget", file=sys.stderr)
            return 1
        print(f"PASS: export finished in {seconds:.1f}s")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
