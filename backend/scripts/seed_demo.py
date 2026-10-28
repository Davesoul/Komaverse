"""One-command demo: user + project + synthetic manga page + detect + clips + export.

Usage (from backend/ with the venv active):
    python scripts/seed_demo.py

Login with demo@komaverse.local / demo1234.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fastapi import BackgroundTasks, Response, UploadFile  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402
from starlette.datastructures import Headers  # noqa: E402

from komaverse.api.ai import GrantRequest, grant  # noqa: E402
from komaverse.api.animate import (  # noqa: E402
    ClipCreate,
    ExportRequest,
    add_clip,
    export_project,
    get_export_report,
    list_scenes,
    run_export_job,
)
from komaverse.api.auth import RegisterBody, register  # noqa: E402
from komaverse.api.scans import (  # noqa: E402
    DetectRequest,
    ProjectCreate,
    create_project,
    detect_panels,
    upload_page,
)
from komaverse.db import init_db, session_factory  # noqa: E402

DEMO_EMAIL = "demo@komaverse.local"
DEMO_PASSWORD = "demo1234"


def synthetic_manga_page() -> bytes:
    """800x1000 page: 3 bordered panels with tonal 'art' inside."""
    img = Image.new("L", (800, 1000), 255)
    d = ImageDraw.Draw(img)
    panels = [(30, 30, 770, 400), (30, 430, 380, 970), (410, 430, 770, 970)]
    for i, (x0, y0, x1, y1) in enumerate(panels):
        d.rectangle([x0, y0, x1, y1], outline=0, width=6)
        for t in range(10, 200, 12):  # screentone-ish bands
            d.line([x0 + 8, y0 + 8 + t + i * 7, x1 - 8, y0 + 8 + t + i * 7], fill=200)
        d.ellipse(
            [x0 + 60, y0 + 60, x0 + 200, y0 + 200], outline=0, width=4
        )  # "character"
        d.ellipse(
            [x1 - 220, y1 - 160, x1 - 60, y1 - 40], outline=0, width=3
        )  # "bubble"
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    init_db()
    factory = session_factory()
    with factory() as s:
        try:
            user = register(
                RegisterBody(email=DEMO_EMAIL, password=DEMO_PASSWORD, name="Demo"),
                Response(),
                s,
            )
            user_id = user.id
            print(f"user: {DEMO_EMAIL} (created)")
        except Exception:
            from sqlalchemy import select

            from komaverse.db_models import UserRow

            user_id = s.execute(
                select(UserRow).where(UserRow.email == DEMO_EMAIL)
            ).scalar_one().id
            print(f"user: {DEMO_EMAIL} (existing)")
    with factory() as s:
        from komaverse.db_models import UserRow

        user = s.get(UserRow, user_id)
        project = create_project(ProjectCreate(name="Demo"), s, user)
        print(f"project: {project.id} ({project.name})")
    with factory() as s:
        from komaverse.db_models import UserRow

        user = s.get(UserRow, user_id)
        page = upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(synthetic_manga_page()),
                filename="demo.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
        print(f"page: {page.id} {page.width}x{page.height}")
    with factory() as s:
        from komaverse.db_models import UserRow

        user = s.get(UserRow, user_id)
        detected = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
        print(f"panels: {len(detected.panels)} via {detected.detector_used}")
    with factory() as s:
        from komaverse.db_models import UserRow

        user = s.get(UserRow, user_id)
        scenes = list_scenes(project.id, s, user)
        for panel in detected.panels:
            add_clip(
                scenes[0].id,
                ClipCreate(
                    kind="panel", source_id=panel.id, duration=1.5, motion="zoom-in"
                ),
                s,
                user,
            )
        grant(GrantRequest(amount=100, reason="demo"), s, user)
    with factory() as s:
        from komaverse.db_models import UserRow

        user = s.get(UserRow, user_id)
        job = export_project(
            project.id,
            ExportRequest(fps=8, width=640, height=480),
            BackgroundTasks(),
            s,
            user,
        )
        run_export_job(job.id, factory)
        out = get_export_report(job.id, s, user)
        print(f"export: {out.url} ({out.clips_rendered} clips, {out.rendered_sec:.1f}s)")
    print(f"login with {DEMO_EMAIL} / {DEMO_PASSWORD} and open the Demo project.")


if __name__ == "__main__":
    main()
