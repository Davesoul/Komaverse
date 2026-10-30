"""Komaverse API entrypoint.

Security: auth on all /api/v1 (except ping), rate limits, security headers,
gzip. Observability: request IDs, structured logs, Prometheus metrics.
/health (liveness), /ready, /metrics, /robots.txt open.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

from komaverse import __version__
from komaverse.api.agent import router as agent_router
from komaverse.api.ai import billing_router, router as ai_router
from komaverse.api.animate import router as animate_router
from komaverse.api.audio import router as audio_router
from komaverse.api.auth import router as auth_router
from komaverse.api.mcp import router as mcp_router
from komaverse.api.media import router as media_router
from komaverse.api.ops import router as ops_router
from komaverse.api.scans import router as scans_router
from komaverse.api.subtitles import router as subtitles_router
from komaverse.api.usage import router as usage_router
from komaverse.config import settings
from komaverse.db import init_db
from komaverse.observability import (
    metrics,
    new_request_id,
    normalize_path,
    record_error,
    request_id_ctx,
    setup_logging,
)
from komaverse.security import limiter, security_headers

setup_logging(settings.log_level, pretty_dev=not settings.is_production)
logger = logging.getLogger("komaverse.server")
settings.media_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("komaverse %s starting (env=%s)", __version__, settings.app_env)
    yield


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        route = normalize_path(request.url.path)
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - start
            metrics.record_request(request.method, route, 500, elapsed)
            logger.exception("%s %s -> exception", request.method, request.url.path)
            record_error(request.method, request.url.path, 500, "unhandled")
            request_id_ctx.reset(token)
            raise
        elapsed = time.perf_counter() - start
        status = response.status_code
        metrics.record_request(request.method, route, status, elapsed)
        logger.info(
            "%s %s -> %s in %.1fms",
            request.method,
            request.url.path,
            status,
            elapsed * 1000,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round(elapsed * 1000, 1),
            },
        )
        if status >= 500:
            record_error(request.method, request.url.path, status)
        response.headers["X-Request-ID"] = rid
        request_id_ctx.reset(token)
        return response


_ID_ROUTE = re.compile(
    r"^(/api/v1/(?:projects|pages|panels|scenes|clips|frames|ai/jobs|audio|takes|cues|exports)/)[^/]+"
)


def rate_limit_key(path: str) -> str:
    """Collapse resource IDs so limits apply per user, not per object.

    /media shares one generous bucket; unknown paths fall back to raw.
    """
    if path.startswith("/media/"):
        return "/media/*"
    return _ID_ROUTE.sub(r"\1{id}", path)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith(("/api/", "/media/")):
            ip = request.client.host if request.client else "unknown"
            limit, window = 120, 60
            if path.startswith("/media/"):
                limit, window = 600, 60
            elif path in (
                "/api/v1/ai/generate",
                "/api/v1/agent/chat",
            ):
                limit, window = 10, 60
            elif path.endswith("/export"):
                limit, window = 5, 60
            # NB: /detect rides the default bucket — local heuristic runs are
            # free, so batch uploads can detect every page. The paid cloud
            # detector enforces its own 10/min per-user bucket in scans.py.
            retry = limiter.check(f"{ip}:{rate_limit_key(path)}", limit, window)
            if retry is not None:
                return JSONResponse(
                    {"detail": "rate limited"},
                    status_code=429,
                    headers={"Retry-After": str(int(retry) + 1)},
                )
        response = await call_next(request)
        for key, value in security_headers(settings.is_production).items():
            response.headers[key] = value
        return response


app.add_middleware(SecurityMiddleware)
app.add_middleware(ObservabilityMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nAllow: /\n"


v1 = APIRouter(prefix="/api/v1")


@v1.get("/ping")
def ping() -> dict[str, bool]:
    return {"pong": True}


app.include_router(v1)
app.include_router(auth_router)
app.include_router(scans_router)
app.include_router(subtitles_router)
app.include_router(usage_router)
app.include_router(animate_router)
app.include_router(audio_router)
app.include_router(ai_router)
app.include_router(billing_router)
app.include_router(agent_router)
app.include_router(ops_router)
app.include_router(media_router)
app.include_router(mcp_router)


# --- Optional single-port serving of the built web UI (SERVE_WEB=true) ---
def resolve_spa_file(dist: Path, path: str) -> Path:
    """Map a URL path to a file under dist, else index.html (SPA fallback)."""
    base = dist.resolve()
    if path:
        target = (dist / path.lstrip("/")).resolve()
        if target.is_file() and target.is_relative_to(base):
            return target
    return dist / "index.html"


_WEB_DIST = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "apps"
    / "studio-web"
    / "dist"
)

if settings.serve_web:
    if _WEB_DIST.is_dir():
        app.state.serve_web = True

        @app.get("/", include_in_schema=False)
        def _spa_root():
            return FileResponse(_WEB_DIST / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def _spa(path: str):
            if path.startswith(
                (
                    "api/", "media/", "docs", "redoc", "openapi.json",
                    "health", "ready", "metrics", "robots.txt",
                )
            ):
                raise HTTPException(404)
            return FileResponse(resolve_spa_file(_WEB_DIST, path))

        logger.info("serving web UI from %s", _WEB_DIST)
    else:
        logger.warning("SERVE_WEB=true but %s missing — run npm run build", _WEB_DIST)
