"""AI generation jobs + credit ledger. Auth required; jobs, ledger and
frames are scoped to the current user."""

from __future__ import annotations

import base64
import httpx
import io
import json
import logging
import random
import re
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from komaverse.ai.direction import (
    ENTITY_KINDS,
    build_final_prompt,
)
from komaverse.ai.providers import (
    ElevenLabsProvider,
    MetaProvider,
    ProviderError,
    chat_complete,
    check_openrouter_model,
    get_chat_config,
    get_provider,
    list_elevenlabs_voices,
    list_openrouter_models,
)
from komaverse.ai.voice_local import list_freetts_voices

from komaverse import storage
from komaverse.api.usage import record_usage
from komaverse.auth import ensure_owner, get_current_user
from komaverse.config import settings
from komaverse.db import get_session, session_factory
from komaverse.db_models import (
    ClonedVoiceRow,
    EntityRow,
    FrameRow,
    JobRow,
    LayerRow,
    LedgerRow,
    PageRow,
    PanelRow,
    SequenceRow,
    UserRow,
)
from komaverse.models import ClonedVoice, CreditEntry, Entity, Job
from komaverse.observability import metrics
from komaverse.renderer import (
    audio_has_stream,
    composite_frame_image,
    transcode_to_wav,
)

router = APIRouter(prefix="/api/v1")
billing_router = APIRouter(prefix="/api/v1")
logger = logging.getLogger("komaverse.ai")

COSTS = {"image": 5, "video": 25}
PROVIDERS = ("cloud", "comfyui", "fake", "local", "meta", "openrouter")


class GenerateRequest(BaseModel):
    kind: str = "image"  # image, or video (openrouter provider only)
    prompt: str = ""
    project_id: str | None = None
    provider: str = Field(default_factory=lambda: settings.ai_provider)
    model: str | None = None  # openrouter model id (catalogued); None = default
    width: int = 1024
    height: int = 1024
    seconds: float = 2.0
    size: str | None = None  # video only: WxH (1280x720...); None = provider default
    generate_audio: bool = False  # video only, always off: sound is voiceover/timeline
    attach_frame: bool = False  # gallery-first: frames only on explicit request
    seed: int | None = None  # None = random per job (stored on the job)
    negative_prompt: str = ""
    enhance: bool = False  # LLM rewrite before sending (fail-soft)
    style: str = "none"  # STYLE_PRESETS key
    genre: str = "none"  # GENRE_TAGS key (anime direction grammar)
    camera_move: str = "none"  # CAMERA_MOVES key (video only)
    entity_ids: list[str] = Field(default_factory=list)
    reference_frame_id: str | None = None  # start frame (video) / redraw (image)


class EntityCreate(BaseModel):
    kind: str = "character"  # character | field | prop
    name: str = ""
    anchor: str = ""
    frame_id: str | None = None


_REF_PX = 768  # reference snapshots are guidance, not artwork
_MAX_REFS = 3
def image_guidance(ref_pngs, start):
    """Guidance images for a still: every snap rides along (stills have no first/last frame slots), legacy reference frame first as before."""
    guidance = [png for _, png in ref_pngs]
    if start is not None and not any(role == "start" for role, _ in ref_pngs):
        guidance.insert(0, start)
    return guidance


_MAX_SEED = 2**31 - 1
_AI_SEQUENCE_NAME = "AI generations"

_VIDEO_SIZE_RE = re.compile(r"^(\d{3,4})x(\d{3,4})$")


def _parse_video_size_str(size: str | None) -> tuple[int, int] | None:
    """(w, h) from a WxH size string; None = provider default or invalid."""
    if not isinstance(size, str):
        return None
    m = _VIDEO_SIZE_RE.match(size.strip().lower())
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if 64 <= w <= 3840 and 64 <= h <= 3840:
        return (w, h)
    return None


def _ai_sequence(session: Session, project_id: str) -> SequenceRow:
    """Dedicated sequence for generated frames, so they never land in the
    user's hand-drawn sequences (orphans get adopted into Sequence 1)."""
    seqs = (
        session.execute(
            select(SequenceRow)
            .where(SequenceRow.project_id == project_id)
            .order_by(SequenceRow.index)
        )
        .scalars()
        .all()
    )
    for seq in seqs:
        if seq.name == _AI_SEQUENCE_NAME:
            return seq
    seq = SequenceRow(
        project_id=project_id, name=_AI_SEQUENCE_NAME, index=len(seqs)
    )
    session.add(seq)
    session.flush()
    return seq


def _frame_png(session: Session, project_id: str, frame_id: str) -> bytes | None:
    """Composite one draw frame to a small PNG for provider references."""
    row = session.get(FrameRow, frame_id)
    if row is None or row.project_id != project_id:
        return None
    try:
        img = composite_frame_image(row)
        img.thumbnail((_REF_PX, _REF_PX))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _as_ref_png(data: bytes) -> bytes | None:
    """Normalize arbitrary image bytes to a small guidance PNG."""
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((_REF_PX, _REF_PX))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _panel_png(session: Session, project_id: str, panel_id: str) -> bytes | None:
    """Crop one detected panel to a small PNG for provider references."""
    panel = session.get(PanelRow, panel_id)
    page = session.get(PageRow, panel.page_id) if panel else None
    if panel is None or page is None or page.project_id != project_id:
        return None
    try:
        data = storage.resolve(page.image_path).read_bytes()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        x, y = min(max(int(panel.x), 0), w), min(max(int(panel.y), 0), h)
        bw, bh = min(int(panel.w), w - x), min(int(panel.h), h - y)
        if bw < 2 or bh < 2:
            return None
        return _as_ref_png(_crop_bytes(img, x, y, bw, bh))
    except (OSError, ValueError):
        return None


def _crop_bytes(img, x: int, y: int, w: int, h: int) -> bytes:
    buf = io.BytesIO()
    img.crop((x, y, x + w, y + h)).save(buf, format="PNG")
    return buf.getvalue()


def _page_png(session: Session, project_id: str, page_id: str) -> bytes | None:
    """A whole scan page, shrunk, for provider references."""
    page = session.get(PageRow, page_id)
    if page is None or page.project_id != project_id:
        return None
    try:
        return _as_ref_png(storage.resolve(page.image_path).read_bytes())
    except OSError:
        return None


def _job_png(
    session: Session, user_id: str, project_id: str, job_id: str
) -> bytes | None:
    """A finished image generation's artifact, for variation chains."""
    row = session.get(JobRow, job_id)
    if (
        row is None
        or row.user_id != user_id
        or row.project_id != project_id
        or row.kind != "image"
        or row.status != "done"
        or not row.result_url
    ):
        return None
    try:
        rel = row.result_url.split("/media/", 1)[-1]
        return _as_ref_png(storage.resolve(rel).read_bytes())
    except OSError:
        return None


def _enhance_prompt(prompt: str) -> str:
    """One chat call to enrich a thin prompt; fail-soft to the original."""
    try:
        base_url, api_key, model = get_chat_config()
        msg = chat_complete(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Rewrite the user prompt as one dense visual paragraph for an image/video model. Keep every named subject. Reply with the rewrite only.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, list):  # multimodal content blocks
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        text = str(content).strip()
        return text or prompt
    except Exception:
        return prompt


class BalanceOut(BaseModel):
    balance: int
    entries: list[CreditEntry]


class GrantRequest(BaseModel):
    amount: int = 100
    reason: str = "dev grant"


def get_balance(session: Session, user_id: str) -> int:
    total = session.execute(
        select(func.coalesce(func.sum(LedgerRow.delta), 0)).where(
            LedgerRow.user_id == user_id
        )
    ).scalar()
    return int(total or 0)


def add_entry(
    session: Session, user_id: str, delta: int, reason: str, job_id: str | None = None
) -> LedgerRow:
    row = LedgerRow(user_id=user_id, delta=delta, reason=reason, job_id=job_id)
    session.add(row)
    return row


def run_ai_job(job_id: str, factory=None) -> None:
    """Execute one job synchronously (called in a background task)."""
    factory = factory or session_factory()
    with factory() as session:
        job = session.get(JobRow, job_id)
        if job is None:
            return
        # Expire only the cancel flag: a full refresh would discard the
        # seed/final prompt staged on this session below.
        session.expire(job, ["status"])
        if job.status == "cancelled":
            return
        job.status = "running"
        job.progress = 0.1
        session.commit()
        try:
            provider = get_provider(job.provider)
            try:
                entities = json.loads(job.entities_json or "[]")
            except ValueError:
                entities = []
            if not isinstance(entities, list):
                entities = []
            entities = [e for e in entities if isinstance(e, dict)]
            final = build_final_prompt(
                prompt=job.prompt,
                entities=entities,
                style=job.style or "none",
                genre=job.genre if job.genre is not None else "none",
                camera_move=job.camera_move or "none",
                kind=job.kind,
                negative_prompt=job.negative_prompt or "",
            )
            if job.enhance:
                final = _enhance_prompt(final)
            job.final_prompt = final
            seed_used = (
                job.seed if job.seed is not None else random.randint(0, _MAX_SEED)
            )
            job.seed = seed_used
            # Visual guidance, cheapest-first: explicit start frame, then
            # entity snapshots in order. Capped — each is a full upload.
            ref_pngs: list[tuple[str, bytes]] = []
            for ent in entities:
                if len(ref_pngs) >= _MAX_REFS:
                    break
                fid = ent.get("frame_id")
                if fid and job.project_id:
                    png = _frame_png(session, job.project_id, str(fid))
                elif ent.get("panel_id") and job.project_id:
                    png = _panel_png(session, job.project_id, str(ent["panel_id"]))
                elif ent.get("page_id") and job.project_id:
                    png = _page_png(session, job.project_id, str(ent["page_id"]))
                elif ent.get("job_id"):
                    png = _job_png(session, job.user_id, job.project_id or "", str(ent["job_id"]))
                else:
                    png = None
                if png is not None:
                    ref_pngs.append((ent.get("role") or "reference", png))
            # Roles route the video slots: an explicit start/end frame
            # opens and closes the video; untagged refs stay style/subject
            # guidance. The legacy explicit reference frame keeps its
            # start meaning when no role claims the slot.
            start = next((png for role, png in ref_pngs if role == "start"), None)
            if start is None and job.reference_frame_id and job.project_id:
                start = _frame_png(session, job.project_id, job.reference_frame_id)
            end = next((png for role, png in ref_pngs if role == "end"), None)
            rest = [png for role, png in ref_pngs if role not in ("start", "end")]
            cap = max(_MAX_REFS - (1 if start else 0) - (1 if end else 0), 0)
            rest = rest[:cap]
            if job.kind == "image":
                data = provider.generate_image(
                    final,
                    job.width,
                    job.height,
                    model=job.model,
                    seed=seed_used,
                    negative_prompt=job.negative_prompt or "",
                    reference_images=tuple(image_guidance(ref_pngs, start)),
                )
                suffix = "png"
            elif job.kind == "video":
                data = provider.generate_video(
                    final,
                    job.seconds,
                    model=job.model,
                    seed=seed_used,
                    start_frame=start,
                    end_frame=end,
                    reference_images=tuple(rest),
                    size=f"{job.width}x{job.height}" if job.width > 0 and job.height > 0 else None,
                    generate_audio=False,
                )
                suffix = "mp4"
            else:
                raise ProviderError(f"unknown job kind: {job.kind}")
        except ProviderError as exc:
            session.expire(job, ["status"])
            if job.status == "cancelled":
                return
            job.status = "failed"
            job.error = str(exc)
            # Refund only what generate() debited: with the paywall off no
            # debit happened, so an unconditional refund would mint credits.
            if settings.paywall_enabled:
                add_entry(
                    session,
                    job.user_id,
                    COSTS.get(job.kind, 0),
                    f"refund {job.id}",
                    job.id,
                )
            session.commit()
            metrics.record_job("failed")
            record_usage(
                session, job.user_id, job.kind, f"{job.provider}:{job.model or 'default'}",
                project_id=job.project_id or "",
            )
            return
        except Exception as exc:  # noqa: BLE001 — job must never hang
            session.expire(job, ["status"])
            if job.status == "cancelled":
                return
            job.status = "failed"
            job.error = f"unexpected: {exc}"
            if settings.paywall_enabled:
                add_entry(
                    session,
                    job.user_id,
                    COSTS.get(job.kind, 0),
                    f"refund {job.id}",
                    job.id,
                )
            session.commit()
            metrics.record_job("failed")
            record_usage(
                session, job.user_id, job.kind, f"{job.provider}:{job.model or 'default'}",
                project_id=job.project_id or "",
            )
            return

        # A cancel that landed mid-provider-call discards the result: the
        # job stays cancelled and nothing attaches to the library or frames.
        session.expire(job, ["status"])
        if job.status == "cancelled":
            return
        # The PNG below is the immutable job artifact (result_url); the
        # base64 layer is the editable copy. Two lifecycles, not waste:
        # deleting the frame must not break the job gallery and vice versa.
        rel = Path("ai") / f"{job.id}.{suffix}"
        dest = settings.media_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        job.result_url = f"/media/{rel.as_posix()}"
        if job.attach_frame and job.project_id and suffix == "png":
            seq = _ai_sequence(session, job.project_id)
            top = session.execute(
                select(FrameRow.index)
                .where(FrameRow.sequence_id == seq.id)
                .order_by(FrameRow.index.desc())
                .limit(1)
            ).scalars().first()
            frame = FrameRow(
                project_id=job.project_id,
                sequence_id=seq.id,
                index=(top + 1) if top is not None else 0,
                name=f"AI: {job.prompt[:40]}",
            )
            session.add(frame)
            session.flush()
            b64 = base64.b64encode(data).decode()
            session.add(
                LayerRow(
                    frame_id=frame.id,
                    name="ai",
                    layer_order=0,
                    data_url=f"data:image/png;base64,{b64}",
                )
            )
        job.status = "done"
        job.progress = 1.0
        session.commit()
        metrics.record_job("done")
        last_usage = getattr(provider, "last_usage", None) or {}
        record_usage(
            session,
            job.user_id,
            job.kind,
            f"{job.provider}:{job.model or 'default'}",
            last_usage.get("total", 0),
            COSTS.get(job.kind, 0) if settings.paywall_enabled else 0.0,
            project_id=job.project_id or "",
        )


@router.post("/ai/generate", response_model=Job, status_code=202)
def generate(
    body: GenerateRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if body.kind not in COSTS:
        raise HTTPException(400, f"unknown kind: {body.kind}")
    if body.provider not in PROVIDERS:
        raise HTTPException(400, f"unknown provider: {body.provider}")
    if body.kind == "video" and body.provider != "openrouter":
        raise HTTPException(
            400, "video generation needs provider=openrouter "
            "(meta has no video endpoint)"
        )
    if body.model and body.provider not in ("openrouter", "local"):
        raise HTTPException(400, "per-model choice is openrouter/local-only")
    if body.provider == "openrouter" and body.model:
        try:
            check_openrouter_model(body.kind, body.model)
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    if body.seed is not None and not (0 <= body.seed <= _MAX_SEED):
        raise HTTPException(400, f"seed must be 0..{_MAX_SEED}")
    if len(body.entity_ids) > 8:
        raise HTTPException(400, "at most 8 entities per generation")
    try:
        # Validates style/genre/camera ids up front (unknown ids never bill).
        build_final_prompt(
            prompt="x",
            entities=[],
            style=body.style,
            genre=body.genre,
            camera_move=body.camera_move,
            kind=body.kind,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.project_id:
        ensure_owner(session, user, body.project_id)
    snapshots: list[dict] = []
    if body.entity_ids or body.reference_frame_id:
        if not body.project_id:
            raise HTTPException(400, "entities and start frames need a project")
        seen: set[str] = set()
        for eid in body.entity_ids:
            if eid in seen:
                continue
            seen.add(eid)
            ent = session.get(EntityRow, eid)
            if (
                ent is None
                or ent.project_id != body.project_id
                or ent.user_id != user.id
            ):
                raise HTTPException(400, f"unknown entity: {eid}")
            snapshots.append(
                {
                    "id": ent.id,
                    "name": ent.name,
                    "kind": ent.kind,
                    "anchor": ent.anchor or "",
                    "frame_id": ent.frame_id,
                }
            )
        if body.reference_frame_id:
            frame = session.get(FrameRow, body.reference_frame_id)
            if frame is None or frame.project_id != body.project_id:
                raise HTTPException(
                    400, f"unknown frame: {body.reference_frame_id}"
                )
    cost = COSTS[body.kind]
    if settings.paywall_enabled and get_balance(session, user.id) < cost:
        raise HTTPException(402, f"insufficient credits (need {cost})")
    if settings.paywall_enabled:
        add_entry(session, user.id, -cost, f"{body.kind} generation")
    video_dims = _parse_video_size_str(body.size) if body.kind == "video" else None
    job = JobRow(
        user_id=user.id,
        project_id=body.project_id,
        kind=body.kind,
        prompt=body.prompt,
        provider=body.provider,
        model=body.model,
        # Video size rides width/height; (0, 0) = provider default.
        # Sound is never generated: voiceover takes + timeline audio.
        width=video_dims[0] if video_dims else (0 if body.kind == "video" else min(max(body.width, 64), 2048)),
        height=video_dims[1] if video_dims else (0 if body.kind == "video" else min(max(body.height, 64), 2048)),
        seconds=min(max(body.seconds, 0.5), 10.0),
        attach_frame=body.attach_frame,
        seed=body.seed,
        negative_prompt=body.negative_prompt,
        style=body.style,
        genre=body.genre,
        camera_move=body.camera_move,
        enhance=body.enhance,
        reference_frame_id=body.reference_frame_id,
        entities_json=json.dumps(snapshots),
    )
    session.add(job)
    session.commit()
    background.add_task(run_ai_job, job.id)
    return job.to_schema()


@router.get("/ai/jobs/{job_id}", response_model=Job)
def get_job(
    job_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(JobRow, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "job not found")
    return row.to_schema()


@router.post("/ai/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(
    job_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Cancel an image/video/export job.

    Queued jobs never run (the worker checks on pickup). A job that is
    already executing can't be preempted, but its result is discarded on
    completion and pollers treat "cancelled" as terminal. Terminal jobs
    answer 409 so callers can tell "already landed" apart from "stopped".
    """
    row = session.get(JobRow, job_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "job not found")
    if row.status not in ("queued", "running"):
        raise HTTPException(409, f"job already {row.status}")
    row.status = "cancelled"
    session.commit()
    return row.to_schema()


#: Cap for research-image imports: chat art is reference material, and
#: the downloader bounds RAM to cap + one chunk either way.
MAX_IMPORT_IMAGE_BYTES = 10 * 1024 * 1024

_IMPORT_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
}


class ImportImageBody(BaseModel):
    project_id: str = ""
    url: str = ""
    title: str = ""


@router.post("/ai/jobs/import-url", response_model=Job, status_code=201)
def import_image_from_url(
    body: ImportImageBody,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Save a web research image into the project's media library.

    Downloads the URL server-side (capped, image/* only) and records a
    finished image job (provider "web"), so the picture shows in Media
    and is referenceable as @gen:<id> like any other generation.
    """
    ensure_owner(session, user, (body.project_id or "").strip())
    cleaned = (body.url or "").strip()
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        raise HTTPException(400, "import needs an http(s) image URL")
    try:
        with httpx.stream(
            "GET",
            cleaned,
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
        ) as resp:
            if resp.status_code >= 400:
                raise HTTPException(400, f"download failed: HTTP {resp.status_code}")
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not ctype.startswith("image/"):
                raise HTTPException(415, f"not an image ({ctype or 'unknown type'})")
            buf = bytearray()
            for piece in resp.iter_bytes(1024 * 1024):
                buf += piece
                if len(buf) > MAX_IMPORT_IMAGE_BYTES:
                    raise HTTPException(413, "image too large (max 10 MB)")
            data = bytes(buf)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"download failed: {type(exc).__name__}") from exc
    if not data:
        raise HTTPException(400, "download failed: empty body")
    from urllib.parse import urlparse

    stem = urlparse(cleaned).path.rsplit("/", 1)[-1][:60] or "research-image"
    if "." not in stem:
        stem += _IMPORT_EXT.get(ctype, ".img")
    _rel, url = storage.save_upload(body.project_id.strip(), stem, data)
    title = (body.title or "").strip()[:160] or f"Research image ({urlparse(cleaned).netloc})"
    job = JobRow(
        user_id=user.id,
        project_id=body.project_id.strip(),
        kind="image",
        prompt=title,
        provider="web",
        status="done",
        progress=1.0,
        result_url=url,
    )
    session.add(job)
    session.commit()
    return job.to_schema()


@router.get("/ai/models")
def ai_models(user: UserRow = Depends(get_current_user)):
    """Curated OpenRouter model catalog + server defaults for the picker."""
    return list_openrouter_models()


class VoiceOption(BaseModel):
    id: str
    label: str
    maker: str


class VoiceCatalog(BaseModel):
    provider: str
    source: str  # live | fallback
    voices: list[VoiceOption]


@router.get("/ai/voices", response_model=VoiceCatalog)
def ai_voices(provider: str = "free-tts", user: UserRow = Depends(get_current_user), session: Session | None = Depends(get_session)):
    """TTS voice picker catalog for the Brain voice row.

    ElevenLabs voices come live from the API (needs the key);
    free voices are the vendored Kokoro catalog (source "local").
    """
    kind = (provider or "").strip().lower()
    if kind == "elevenlabs":
        try:
            voices = list_elevenlabs_voices()
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from None
        clones = (
            session.execute(
                select(ClonedVoiceRow)
                .where(ClonedVoiceRow.user_id == user.id)
                .order_by(ClonedVoiceRow.created_at.desc())
            ).scalars().all()
            if session is not None and hasattr(session, "execute")
            else []
        )
        mine = [
            {"id": r.elevenlabs_voice_id, "label": f"{r.name} · cloned", "maker": "cloned"}
            for r in clones
        ]
        return VoiceCatalog(provider=kind, source="live", voices=[*mine, *voices])
    if kind == "free-tts":
        voices, source = list_freetts_voices()
        clones = (
            session.execute(
                select(ClonedVoiceRow)
                .where(ClonedVoiceRow.user_id == user.id, ClonedVoiceRow.engine == "xtts")
                .order_by(ClonedVoiceRow.created_at.desc())
            ).scalars().all()
            if session is not None and hasattr(session, 'execute')
            else []
        )
        mine = [
            {"id": f"xtts:{r.id}", "label": f"{r.name} · cloned", "maker": "cloned"}
            for r in clones
        ]
        return VoiceCatalog(provider=kind, source=source, voices=[*mine, *voices])
    raise HTTPException(
        400, f'unknown voice provider "{provider}" (want: elevenlabs, free-tts)'
    )


MAX_CLONE_SAMPLES = 5
MAX_CLONE_BYTES = 25 * 1024 * 1024
CLONE_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm")


def _read_clone_sample(file: UploadFile) -> tuple[str, bytes]:
    name = (file.filename or "sample").strip() or "sample"
    if Path(name).suffix.lower() not in CLONE_AUDIO_EXTS:
        raise HTTPException(400, f"voice sample must be audio ({name})")
    try:
        data = storage.read_upload_capped(file.file, MAX_CLONE_BYTES)
    except storage.UploadTooLargeError:
        raise HTTPException(413, f"voice sample too large ({name})") from None
    if not data:
        raise HTTPException(400, f"voice sample is empty ({name})")
    return name, data


@router.post("/ai/voices/clone", response_model=ClonedVoice, status_code=201)
def clone_voice(
    name: str = Form(""),
    files: list[UploadFile] = File(...),
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
    engine: str = Form("elevenlabs"),
):
    """Clone a voice from speech samples.

    engine "elevenlabs": cloud Instant Voice Clone (1-5 samples,
    needs ELEVENLABS_API_KEY). engine "xtts": local XTTS v2 clone
    (exactly 1 sample, no key; needs `pip install coqui-tts`).
    Either way the clone lands in the voice picker for its engine."""
    from komaverse.ai.voice_local import wav_normalize_22k

    # engine arrives as a plain string over HTTP; direct calls that
    # omit it keep the Form() default object, which is not a string.
    kind = (engine if isinstance(engine, str) else "elevenlabs").strip().lower()
    if kind not in ("elevenlabs", "xtts"):
        raise HTTPException(400, 'clone engine must be "elevenlabs" or "xtts"')
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(400, "a name is required")
    if kind == "xtts" and len(files) != 1:
        raise HTTPException(400, "local xtts clone takes exactly 1 clear sample")
    if not files or len(files) > MAX_CLONE_SAMPLES:
        raise HTTPException(400, f"send 1–{MAX_CLONE_SAMPLES} audio samples")
    try:
        samples = [_read_clone_sample(f) for f in files]
    finally:
        for f in files:
            try:
                f.file.close()
            except Exception:
                pass
    if kind == "elevenlabs":
        try:
            voice_id = ElevenLabsProvider().clone_voice(clean, samples)
        except ProviderError as exc:
            raise HTTPException(400, f"voice cloning failed: {exc}") from None
        row = ClonedVoiceRow(
            user_id=user.id,
            name=clean[:80],
            engine="elevenlabs",
            elevenlabs_voice_id=voice_id,
            sample_count=len(samples),
        )
        session.add(row)
        session.commit()
        return row.to_schema()
    # Local XTTS v2: normalize the sample to the 22kHz reference the
    # model wants and save it under the media dir; synthesis reads it
    # back for every line (free-tts keeps the ref file the same way).
    try:
        ref_wav = wav_normalize_22k(samples[0][1])
    except ProviderError as exc:
        raise HTTPException(400, f"voice sample is unusable: {exc}") from None
    row = ClonedVoiceRow(
        user_id=user.id,
        name=clean[:80],
        engine="xtts",
        sample_count=1,
    )
    session.add(row)
    session.flush()
    voices_dir = settings.media_dir / "voices"
    voices_dir.mkdir(parents=True, exist_ok=True)
    (voices_dir / f"{row.id}.wav").write_bytes(ref_wav)
    row.ref_audio = f"voices/{row.id}.wav"
    session.commit()
    return row.to_schema()

@router.get("/ai/voices/clones", response_model=list[ClonedVoice])
def list_clones(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Your cloned voices, newest first."""
    rows = (
        session.execute(
            select(ClonedVoiceRow)
            .where(ClonedVoiceRow.user_id == user.id)
            .order_by(ClonedVoiceRow.created_at.desc())
        ).scalars().all()
    )
    return [r.to_schema() for r in rows]


@router.delete("/ai/voices/clones/{clone_id}", status_code=204)
def delete_clone(
    clone_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Remove a clone: from ElevenLabs first (cloud ones), then the record.
    Local XTTS clones also delete their saved reference audio."""
    row = session.get(ClonedVoiceRow, clone_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "clone not found")
    if (row.engine or "elevenlabs") == "xtts":
        if row.ref_audio:
            try:
                (settings.media_dir / row.ref_audio).unlink(missing_ok=True)
            except OSError:
                pass
    else:
        try:
            ElevenLabsProvider().delete_cloned_voice(row.elevenlabs_voice_id)
        except ProviderError as exc:
            raise HTTPException(400, f"elevenlabs delete failed: {exc}") from None
    session.delete(row)
    session.commit()


@router.get("/projects/{project_id}/entities", response_model=list[Entity])
def list_entities(
    project_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    ensure_owner(session, user, project_id)
    res = session.execute(
        select(EntityRow)
        .where(EntityRow.project_id == project_id, EntityRow.user_id == user.id)
        .order_by(EntityRow.id)
    )
    return [r.to_schema() for r in res.scalars().all()]


@router.post(
    "/projects/{project_id}/entities", response_model=Entity, status_code=201
)
def create_entity(
    project_id: str,
    body: EntityCreate,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    ensure_owner(session, user, project_id)
    if body.kind not in ENTITY_KINDS:
        raise HTTPException(400, f"unknown entity kind: {body.kind}")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "entity name is required")
    if body.frame_id:
        frame = session.get(FrameRow, body.frame_id)
        if frame is None or frame.project_id != project_id:
            raise HTTPException(400, f"unknown frame: {body.frame_id}")
    row = EntityRow(
        user_id=user.id,
        project_id=project_id,
        kind=body.kind,
        name=name[:80],
        anchor=body.anchor.strip(),
        frame_id=body.frame_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row.to_schema()


@router.delete("/entities/{entity_id}", status_code=204)
def delete_entity(
    entity_id: str,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    row = session.get(EntityRow, entity_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "entity not found")
    session.delete(row)
    session.commit()


@router.get("/ai/jobs", response_model=list[Job])
def list_jobs(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    res = session.execute(
        select(JobRow).where(JobRow.user_id == user.id).order_by(JobRow.id)
    )
    return [r.to_schema() for r in res.scalars().all()]


@billing_router.get("/billing/me", response_model=BalanceOut)
def my_balance(
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    res = session.execute(select(LedgerRow).where(LedgerRow.user_id == user.id))
    entries = [r.to_schema() for r in res.scalars().all()]
    return BalanceOut(balance=sum(e.delta for e in entries), entries=entries[-50:])


@billing_router.post("/billing/grant", response_model=BalanceOut)
def grant(
    body: GrantRequest,
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    if settings.is_production:
        raise HTTPException(403, "grants are disabled in production")
    if body.amount <= 0 or body.amount > 100000:
        raise HTTPException(400, "amount out of range")
    add_entry(session, user.id, body.amount, body.reason or "grant")
    session.commit()
    return my_balance(session, user)


class VoiceTranscript(BaseModel):
    text: str
    duration_sec: float = 0.0


VOICE_EXTS = {
    ".webm", ".wav", ".mp3", ".m4a", ".ogg", ".oga", ".opus",
    ".mp4", ".aac", ".flac",
}


@router.post("/ai/transcribe", response_model=VoiceTranscript)
def transcribe_voice(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    user: UserRow = Depends(get_current_user),
):
    """Speech-to-text for composer voice input via Meta's transcription model.

    Takes raw recorded audio (no project needed, nothing is stored):
    checks it decodes, forwards the bytes to Muse Voice Transcribe, and
    returns the transcript text. Needs META_API_KEY; without it the call
    fails naming the missing key.
    """
    if not (file.content_type or "").startswith("audio/"):
        raise HTTPException(400, "file must be audio")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VOICE_EXTS:
        raise HTTPException(400, f"unsupported audio type: {ext or '(none)'}")
    try:
        data = storage.read_upload_capped(
            file.file, min(settings.upload_max_mb, 32) * 1024 * 1024
        )
    except storage.UploadTooLargeError:
        raise HTTPException(413, "file too large") from None
    if not data:
        raise HTTPException(400, "empty file")
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        if not audio_has_stream(Path(tmp.name)):
            raise HTTPException(400, "cannot decode audio")
    finally:
        try:
            Path(tmp.name).unlink()
        except OSError:
            pass
    try:
        wav = transcode_to_wav(data, ext)
    except RuntimeError as exc:
        raise HTTPException(400, f"cannot prepare audio: {exc}") from None
    try:
        res = MetaProvider().transcribe_audio(wav, "voice.wav")
    except ProviderError as exc:
        raise HTTPException(400, f"transcription failed: {exc}") from None
    record_usage(
        session, user.id, "transcribe",
        settings.meta_transcribe_model, len(res.get("transcript") or "") // 4,
    )
    return VoiceTranscript(
        text=res.get("transcript") or "",
        duration_sec=(res.get("audioDurationMs") or 0) / 1000,
    )
