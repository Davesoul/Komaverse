"""Local media storage (dev). S3-compatible backend lands in M4."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import BinaryIO

from komaverse.config import settings
from komaverse.models import uuid_short


def save_upload(project_id: str, filename: str, data: bytes) -> tuple[str, str]:
    """Store upload bytes; return (relative_path, public_url)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename or "upload")[-80:]
    rel = Path("projects") / project_id / f"{uuid_short()}_{safe}"
    dest = settings.media_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel.as_posix(), f"/media/{rel.as_posix()}"


class MediaTraversalError(ValueError):
    """Raised when a media path escapes the media directory."""


def resolve(rel_path: str) -> Path:
    """Resolve a media-relative path, rejecting directory traversal."""
    base = settings.media_dir.resolve()
    target = (settings.media_dir / rel_path).resolve()
    if not target.is_relative_to(base):
        raise MediaTraversalError(f"path escapes media dir: {rel_path}")
    return target


class UploadTooLargeError(ValueError):
    pass


class QuotaExceededError(ValueError):
    pass


def read_upload_capped(
    file_obj: BinaryIO, cap_bytes: int, chunk: int = 1024 * 1024
) -> bytes:
    """Read an upload stream, failing fast once it exceeds the cap.

    A single .read() would buffer an unbounded body in RAM before the
    size check runs; chunking bounds memory to cap + one chunk.
    """
    buf = bytearray()
    while True:
        piece = file_obj.read(chunk)
        if not piece:
            return bytes(buf)
        buf += piece
        if len(buf) > cap_bytes:
            raise UploadTooLargeError(f"upload exceeds {cap_bytes} bytes")


def project_usage_bytes(project_id: str) -> int:
    """Disk bytes owned by a project (uploads + renders)."""
    total = 0
    for top in ("projects", "exports"):
        root = settings.media_dir / top / project_id
        if root.is_dir():
            total += sum(
                f.stat().st_size for f in root.rglob("*") if f.is_file()
            )
    return total


def check_quota(project_id: str, incoming_bytes: int) -> None:
    quota = settings.project_quota_mb * 1024 * 1024
    if project_usage_bytes(project_id) + incoming_bytes > quota:
        raise QuotaExceededError(
            f"project storage quota exceeded ({settings.project_quota_mb} MB)"
        )


def remove_file(rel_path: str) -> None:
    """Best-effort unlink of one media-relative file (delete paths)."""
    try:
        path = resolve(rel_path)
    except MediaTraversalError:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def remove_project_files(project_id: str) -> None:
    """Best-effort removal of a deleted project's media trees."""
    for top in ("projects", "exports"):
        shutil.rmtree(
            settings.media_dir / top / project_id, ignore_errors=True
        )
