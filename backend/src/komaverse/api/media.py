"""Authenticated media file serving. Replaces the open StaticFiles mount:
every byte under /media/ requires login and project/job ownership, and
paths are traversal-guarded by storage.resolve. Filenames are uuid-prefixed
and immutable, so responses carry a long private cache lifetime."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from komaverse import storage
from komaverse.auth import ensure_owner, get_current_user
from komaverse.db import get_session
from komaverse.db_models import JobRow, UserRow

router = APIRouter()

CACHE_IMMUTABLE = "private, max-age=31536000, immutable"


def _check_owner(session: Session, user: UserRow, subpath: str) -> None:
    """Raise 404 unless the user owns the project/job behind this path."""
    parts = subpath.split("/")
    if len(parts) == 3 and parts[0] == "projects":
        ensure_owner(session, user, parts[1])
    elif len(parts) == 3 and parts[0] == "exports":
        ensure_owner(session, user, parts[1])
    elif len(parts) == 2 and parts[0] == "ai":
        stem = Path(parts[1]).stem
        job = session.get(JobRow, stem)
        if (
            job is None
            or job.user_id != user.id
            or Path(parts[1]).suffix not in (".png", ".mp4")
        ):
            raise HTTPException(404, "not found")
    else:
        raise HTTPException(404, "not found")


@router.get("/media/{subpath:path}")
def serve_media(
    subpath: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    try:
        target = storage.resolve(subpath)
    except storage.MediaTraversalError:
        raise HTTPException(404, "not found") from None
    if not target.is_file():
        raise HTTPException(404, "not found")
    _check_owner(session, user, subpath)
    return FileResponse(target, headers={"Cache-Control": CACHE_IMMUTABLE})
