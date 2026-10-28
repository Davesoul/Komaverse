"""Observability primitives: structured logs, request IDs, Prometheus metrics.

All pure/testable without sockets or an event loop. The ASGI middleware in
main.py is a thin adapter over record_request().
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "komaverse_request_id", default="-"
)

START_TIME = time.time()

_recent_errors: deque = deque(maxlen=50)
_errors_lock = threading.Lock()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_ctx.get(),
        }
        for key in ("method", "path", "status", "duration_ms", "user_id", "error"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(level: str, pretty_dev: bool) -> None:
    handler = logging.StreamHandler()
    if pretty_dev:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Quiet noisy libs; keep our loggers + uvicorn errors.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


_ROUTE_PATTERNS = [
    (re.compile(r"^/api/v1/projects/[^/]+$"), "/api/v1/projects/{id}"),
    (re.compile(r"^/api/v1/projects/[^/]+/(pages|scenes|frames)$"), "/api/v1/projects/{id}/{child}"),
    (re.compile(r"^/api/v1/projects/[^/]+/export$"), "/api/v1/projects/{id}/export"),
    (re.compile(r"^/api/v1/pages/[^/]+/(detect|panels)$"), "/api/v1/pages/{id}/{action}"),
    (re.compile(r"^/api/v1/panels/[^/]+$"), "/api/v1/panels/{id}"),
    (re.compile(r"^/api/v1/scenes/[^/]+$"), "/api/v1/scenes/{id}"),
    (re.compile(r"^/api/v1/scenes/[^/]+/clips$"), "/api/v1/scenes/{id}/clips"),
    (re.compile(r"^/api/v1/clips/[^/]+$"), "/api/v1/clips/{id}"),
    (re.compile(r"^/api/v1/frames/[^/]+$"), "/api/v1/frames/{id}"),
    (re.compile(r"^/api/v1/ai/jobs/[^/]+$"), "/api/v1/ai/jobs/{id}"),
    (re.compile(r"^/media/.*"), "/media/*"),
]


def normalize_path(path: str) -> str:
    for pattern, template in _ROUTE_PATTERNS:
        if pattern.match(path):
            return template
    if path.startswith(("/api/v1/auth", "/api/v1/billing", "/api/v1/projects",
                         "/api/v1/ai/", "/api/v1/ping", "/health", "/ready",
                         "/metrics", "/robots.txt")):
        return path
    return "other"


class Metrics:
    """Thread-safe counters rendered in Prometheus exposition format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = {}
        self._latency_sum: dict[tuple[str, str], float] = {}
        self._latency_count: dict[tuple[str, str], int] = {}
        self._jobs: dict[str, int] = {}
        self._exports = 0
        self._detects: dict[str, int] = {}

    def record_request(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            key = (method, route, str(status))
            self._requests[key] = self._requests.get(key, 0) + 1
            lat = (method, route)
            self._latency_sum[lat] = self._latency_sum.get(lat, 0.0) + seconds
            self._latency_count[lat] = self._latency_count.get(lat, 0) + 1

    def record_job(self, status: str) -> None:
        with self._lock:
            self._jobs[status] = self._jobs.get(status, 0) + 1

    def record_export(self) -> None:
        with self._lock:
            self._exports += 1

    def record_detect(self, detector: str) -> None:
        with self._lock:
            self._detects[detector] = self._detects.get(detector, 0) + 1

    def render(self, version: str) -> str:
        with self._lock:
            lines = [
                "# HELP komaverse_app_info App version",
                "# TYPE komaverse_app_info gauge",
                f'komaverse_app_info{{version="{version}"}} 1',
                "# HELP komaverse_requests_total HTTP requests",
                "# TYPE komaverse_requests_total counter",
            ]
            for (method, route, status), count in sorted(self._requests.items()):
                lines.append(
                    f'komaverse_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}'
                )
            lines += [
                "# HELP komaverse_request_seconds_sum Request latency sum",
                "# TYPE komaverse_request_seconds_sum counter",
            ]
            for (method, route), total in sorted(self._latency_sum.items()):
                lines.append(
                    f'komaverse_request_seconds_sum{{method="{method}",route="{route}"}} {total:.6f}'
                )
            lines += [
                "# HELP komaverse_request_seconds_count Request latency count",
                "# TYPE komaverse_request_seconds_count counter",
            ]
            for (method, route), count in sorted(self._latency_count.items()):
                lines.append(
                    f'komaverse_request_seconds_count{{method="{method}",route="{route}"}} {count}'
                )
            lines += [
                "# HELP komaverse_ai_jobs_total AI jobs by terminal status",
                "# TYPE komaverse_ai_jobs_total counter",
            ]
            for status, count in sorted(self._jobs.items()):
                lines.append(f'komaverse_ai_jobs_total{{status="{status}"}} {count}')
            lines += [
                "# HELP komaverse_exports_total MP4 exports",
                "# TYPE komaverse_exports_total counter",
                f"komaverse_exports_total {self._exports}",
                "# HELP komaverse_detects_total Panel detections by detector used",
                "# TYPE komaverse_detects_total counter",
            ]
            for detector, count in sorted(self._detects.items()):
                lines.append(f'komaverse_detects_total{{detector="{detector}"}} {count}')
            return "\n".join(lines) + "\n"


metrics = Metrics()


def record_error(method: str, path: str, status: int, error: str = "") -> None:
    with _errors_lock:
        _recent_errors.appendleft(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "request_id": request_id_ctx.get(),
                "method": method,
                "path": path,
                "status": status,
                "error": error[:300],
            }
        )


def recent_errors() -> list[dict]:
    with _errors_lock:
        return list(_recent_errors)


def uptime_seconds() -> float:
    return time.time() - START_TIME


def check_readiness(database_url: str, media_dir: Path) -> tuple[bool, dict[str, str]]:
    """DB connect + media writability. Pure-ish: takes args, no globals."""
    checks: dict[str, str] = {}
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"fail: {exc}"
    try:
        media_dir.mkdir(parents=True, exist_ok=True)
        probe = media_dir / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        checks["media"] = "ok"
    except Exception as exc:
        checks["media"] = f"fail: {exc}"
    return all(v == "ok" for v in checks.values()), checks
