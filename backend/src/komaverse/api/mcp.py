"""Streamable-HTTP MCP endpoint.

Same tool catalog as the stdio server (komaverse.mcp_server), served at
POST /mcp as JSON-RPC. Long-running tools never block the transport:
export_video and create_image return a job id at once (create_video
already does), and check_job polls them to completion.

Single-user local-studio auth, same as stdio: the request acts as
KOMAVERSE_MCP_USER, resolved per call. No session cookies or API keys.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse.config import settings
from komaverse.db import get_session, session_factory
from komaverse.db_models import UserRow
from komaverse.mcp_server import dispatch

router = APIRouter(tags=["mcp"])


def handle_mcp(body: dict, factory, email: str) -> tuple[int, dict | None]:
    """Pure request handling for tests: (status, payload). None payload
    means notification acknowledged (HTTP 202, empty body)."""
    if not email:
        return 401, {
            "jsonrpc": "2.0",
            "id": body.get("id") if isinstance(body, dict) else None,
            "error": {"code": -32600, "message": "MCP user is not configured"},
        }
    with factory() as session:
        row = session.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalars().first()
        if row is None:
            return 401, {
                "jsonrpc": "2.0",
                "id": body.get("id") if isinstance(body, dict) else None,
                "error": {"code": -32600, "message": "unknown MCP user"},
            }
        user_id = row.id
    try:
        response = dispatch(body, factory, user_id)
    except Exception as exc:  # noqa: BLE001 — one bad request never kills the endpoint
        req_id = body.get("id") if isinstance(body, dict) else None
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
        }
    if response is None:
        return 202, None
    return 200, response


@router.post("/mcp")
def mcp_http(body: dict, session: Session = Depends(get_session)):  # noqa: ARG001
    """MCP over streamable HTTP: JSON-RPC in, JSON-RPC out."""
    status, payload = handle_mcp(body, session_factory(), settings.mcp_user_email)
    if payload is None:
        return Response(status_code=status)
    return JSONResponse(payload, status_code=status)
