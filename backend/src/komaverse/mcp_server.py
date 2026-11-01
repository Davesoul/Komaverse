"""MCP server (stdio) for the Komaverse studio.

Exposes the same tool functions as the in-app studio agent (komaverse.agent)
over the Model Context Protocol, so MCP clients (Claude Code, Claude Desktop)
can operate the studio: projects, detection, timelines, captions, cues,
generation, exports, and job polling.

Transport: JSON-RPC 2.0, one object per line on stdin/stdout (MCP stdio).
Only the calls the agent already makes are exposed; auth scoping, credits,
and quotas apply identically because these ARE the agent tools.

Identity: single-user local server. Set KOMAVERSE_MCP_USER to the login
email of the studio user to act as. The server refuses to start without it.

Run from backend/ with the venv:
    set KOMAVERSE_MCP_USER=you@example.com
    .venv\\Scripts\\python.exe -m komaverse.mcp_server
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from komaverse import __version__
from komaverse.agent import (
    tool_add_clip,
    tool_add_cue,
    tool_add_subtitles,
    tool_auto_direct,
    tool_cancel_job,
    tool_check_job,
    tool_create_frame,
    tool_create_project,
    tool_create_video,
    tool_detect_all,
    tool_export,
    tool_frame_from_media,
    tool_generate,
    tool_layer_from_media,
    tool_list_frames,
    tool_list_takes,
    tool_manage_clips,
    tool_manage_frames,
    tool_manage_layers,
    tool_manage_sequences,
    tool_manage_timelines,
    tool_status,
    tool_tone_layer,
    tool_video_to_frames,
)
from komaverse.api.scans import list_projects
from komaverse.config import settings
from komaverse.db import session_factory
from komaverse.db_models import UserRow

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "komaverse-studio"


def _str(args: dict, key: str, default: str = "") -> str:
    value = args.get(key, default)
    return str(value) if value is not None else default


def _float(args: dict, key: str, default: float) -> float:
    try:
        return float(args.get(key, default))
    except (TypeError, ValueError):
        return default


def call_tool(
    name: str, args: dict, session: Session, user: UserRow, job_factory=None
) -> str:
    """Run one named studio tool. Unknown names raise KeyError."""
    args = args if isinstance(args, dict) else {}
    if name == "project_status":
        return tool_status(session, user, _str(args, "project_id"))
    if name == "list_projects":
        projects = list_projects(session, user)
        if not projects:
            return "No projects yet."
        return "; ".join(f"'{p.name}' [project {p.id}]" for p in projects)
    if name == "create_project":
        return tool_create_project(session, user, _str(args, "name") or "Untitled")
    if name == "create_frame":
        return tool_create_frame(
            session, user, _str(args, "project_id"), _str(args, "name"),
            args.get("sequence"),
        )
    if name == "detect_panels":
        return tool_detect_all(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "detector") or "heuristic",
            _str(args, "direction") or "rtl",
        )
    if name == "auto_direct":
        motions = args.get("motions")
        motions = list(motions) if isinstance(motions, list) else None
        return tool_auto_direct(
            session, user, _str(args, "project_id"), _float(args, "seconds", 2.0), motions
        )
    if name == "export_video":
        # Never block the transport: the job id comes back at once, and
        # check_job reports completion.
        return tool_export(
            session, user, _str(args, "project_id"), job_factory, True,
            args.get("scene"),
        )
    if name == "create_image":
        # Same non-blocking contract as exports: immediate job id, poll it.
        return tool_generate(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "prompt"),
            job_factory,
            model=args.get("model"),
            style=_str(args, "style") or "none",
            negative_prompt=_str(args, "negative_prompt"),
            reference_frame_id=args.get("reference_frame_id"),
            references=args.get("references"),
            entity_ids=args.get("entity_ids"),
            background=True,
            attach_frame=bool(args.get("attach_frame")),
        )
    if name == "create_video":
        return tool_create_video(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "prompt"),
            _float(args, "seconds", 4.0),
            args.get("model"),
            job_factory,
            args.get("references"),
            args.get("entity_ids"),
        )
    if name == "check_job":
        return tool_check_job(session, user, _str(args, "job_id"))
    if name == "add_clip":
        return tool_add_clip(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "kind") or "frame",
            _str(args, "source_id"),
            args.get("scene"),
            _float(args, "duration", 2.0),
            _str(args, "motion") or "none",
            _str(args, "transition") or "none",
            args.get("transition_duration"),
            args.get("fade_in"),
            args.get("fade_out"),
        )
    if name == "add_subtitles":
        return tool_add_subtitles(
            session, user, _str(args, "project_id"), args.get("segments"), args.get("scene")
        )
    if name == "list_takes":
        return tool_list_takes(session, user, _str(args, "project_id"))
    if name == "add_cue":
        end = args.get("end_sec")
        try:
            end = float(end) if end is not None else None
        except (TypeError, ValueError):
            end = None
        return tool_add_cue(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "take_id"),
            args.get("scene"),
            _float(args, "start_sec", 0.0),
            _float(args, "offset_sec", 0.0),
            end,
            int(args.get("lane") or 0),
            _float(args, "volume", 1.0),
        )
    if name == "frame_from_media":
        return tool_frame_from_media(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "source"),
            args.get("name"),
            args.get("sequence"),
        )
    if name == "list_frames":
        return tool_list_frames(
            session, user, _str(args, "project_id"), args.get("sequence")
        )
    if name == "manage_layers":
        return tool_manage_layers(
            session,
            user,
            _str(args, "project_id"),
            args.get("frame"),
            _str(args, "op") or "list",
            args.get("layer"),
            args.get("name"),
            args.get("opacity"),
            args.get("visible"),
            args.get("locked"),
            args.get("blend"),
            args.get("position"),
            args.get("sequence"),
            args.get("shapes"),
        )
    if name == "tone_layer":
        return tool_tone_layer(
            session,
            user,
            _str(args, "project_id"),
            args.get("frame"),
            args.get("density", 0.2),
            args.get("angle", 20.0),
            _str(args, "color") or "#111111",
            args.get("opacity", 100),
            _str(args, "blend") or "multiply",
            args.get("name"),
            args.get("sequence"),
        )
    if name == "layer_from_media":
        return tool_layer_from_media(
            session,
            user,
            _str(args, "project_id"),
            args.get("frame"),
            _str(args, "source"),
            args.get("name"),
            args.get("opacity", 100),
            _str(args, "blend") or "normal",
            args.get("sequence"),
        )
    if name == "manage_frames":
        return tool_manage_frames(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "op") or "list",
            args.get("frame"),
            args.get("name"),
            args.get("frame_ids"),
            args.get("sequence"),
            args.get("target"),
        )
    if name == "manage_sequences":
        return tool_manage_sequences(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "op") or "list",
            args.get("sequence"),
            args.get("name"),
        )
    if name == "manage_timelines":
        return tool_manage_timelines(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "op") or "list",
            args.get("scene"),
            args.get("name"),
        )
    if name == "manage_clips":
        return tool_manage_clips(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "op") or "list",
            args.get("clip"),
            args.get("scene"),
            args.get("duration"),
            args.get("motion"),
            args.get("clip_ids"),
            args.get("transition"),
            args.get("transition_duration"),
            args.get("fade_in"),
            args.get("fade_out"),
        )
    if name == "video_to_frames":
        return tool_video_to_frames(
            session,
            user,
            _str(args, "project_id"),
            _str(args, "job"),
            args.get("sequence"),
            args.get("max_frames", 12),
        )
    if name == "cancel_job":
        return tool_cancel_job(session, user, _str(args, "job"))
    raise KeyError(f"unknown tool: {name}")


def _tool_defs() -> list[dict]:
    s = {"type": "string"}
    num = {"type": "number"}
    arr = {"type": "array"}
    return [
        {
            "name": "project_status",
            "description": "Summarize one project: pages, panels, clips, scenes, frames.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s},
                "required": ["project_id"],
            },
        },
        {
            "name": "list_projects",
            "description": "List your projects with ids.",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "create_project",
            "description": "Create a new project.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": s},
            },
        },
        {
            "name": "create_frame",
            "description": "Create a blank draw frame in a project, optionally in one sequence.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "name": s, "sequence": s},
                "required": ["project_id"],
            },
        },
        {
            "name": "manage_sequences",
            "description": (
                "Animation-shot ops: list, add, rename, duplicate, delete "
                "frame sequences."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "op": s, "sequence": s, "name": s},
                "required": ["project_id", "op"],
            },
        },
        {
            "name": "manage_timelines",
            "description": (
                "Timeline ops: list, add, rename, duplicate, delete scenes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "op": s, "scene": s, "name": s},
                "required": ["project_id", "op"],
            },
        },
        {
            "name": "video_to_frames",
            "description": (
                "Explode a finished video (@gen: id) into draw frames in a "
                "sequence (max 24)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "job": s,
                    "sequence": s,
                    "max_frames": num,
                },
                "required": ["project_id", "job"],
            },
        },
        {
            "name": "cancel_job",
            "description": (
                "Stop a queued or running image/video/export job. "
                "Queued work never runs; running work is discarded."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"job": s},
                "required": ["job"],
            },
        },
        {
            "name": "detect_panels",
            "description": (
                "Detect manga panels on every page of a project. "
                "detector (default yolo-leoxs22): heuristic, yolo, "
                "yolo-leoxs22, cloud, meta, openrouter. "
                "direction: rtl (manga) or ltr."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "detector": s, "direction": s},
                "required": ["project_id"],
            },
        },
        {
            "name": "auto_direct",
            "description": "Cut all panels into timeline clips in reading order.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "seconds": num, "motions": arr},
                "required": ["project_id"],
            },
        },
        {
            "name": "export_video",
            "description": "Render one timeline to MP4 (blocks until done).",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "scene": s},
                "required": ["project_id"],
            },
        },
        {
            "name": "create_image",
            "description": (
                "Generate an image from a prompt into the media gallery. "
                "References: @-mention tokens may stay in the prompt "
                "(@frame:/@panel:/@media:/@gen: ids), or pass structured "
                "references/entity ids."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "prompt": s,
                    "model": s,
                    "style": s,
                    "negative_prompt": s,
                    "reference_frame_id": s,
                    "references": arr,
                    "entity_ids": arr,
                    "attach_frame": {"type": "boolean"},
                },
                "required": ["project_id", "prompt"],
            },
        },
        {
            "name": "create_video",
            "description": (
                "Start an async video render from a prompt. Returns a job id "
                "at once; poll it with check_job until done."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "prompt": s,
                    "seconds": num,
                    "model": s,
                    "references": arr,
                    "entity_ids": arr,
                },
                "required": ["project_id", "prompt"],
            },
        },
        {
            "name": "check_job",
            "description": "Report one job's status and result URL.",
            "inputSchema": {
                "type": "object",
                "properties": {"job_id": s},
                "required": ["job_id"],
            },
        },
        {
            "name": "add_clip",
            "description": (
                "Add one panel, frame, scan-page, or video clip to a scene "
                "timeline. Video source_id is a finished generation job id. "
                "transition blends into this clip from the previous one."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "kind": s,
                    "source_id": s,
                    "scene": s,
                    "duration": num,
                    "motion": s,
                    "transition": s,
                    "transition_duration": num,
                    "fade_in": num,
                    "fade_out": num,
                },
                "required": ["project_id", "kind", "source_id"],
            },
        },
        {
            "name": "add_subtitles",
            "description": "Add caption segments to a scene subtitle track.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "segments": arr, "scene": s},
                "required": ["project_id", "segments"],
            },
        },
        {
            "name": "list_takes",
            "description": "List the project's voice takes with ids and durations.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s},
                "required": ["project_id"],
            },
        },
        {
            "name": "add_cue",
            "description": "Place a voice take on a scene timeline at a start time.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "take_id": s,
                    "scene": s,
                    "start_sec": num,
                    "end_sec": num,
                    "lane": num,
                },
                "required": ["project_id", "take_id"],
            },
        },
        {
            "name": "frame_from_media",
            "description": (
                "Create a draw frame from a scan page, image generation, or "
                "frame. Source is an @media:/@gen:/@frame: id or 'page N'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "source": s, "name": s, "sequence": s},
                "required": ["project_id", "source"],
            },
        },
        {
            "name": "list_frames",
            "description": "List the project's draw frames with their layers, optionally one sequence.",
            "inputSchema": {
                "type": "object",
                "properties": {"project_id": s, "sequence": s},
                "required": ["project_id"],
            },
        },
        {
            "name": "manage_layers",
            "description": (
                "Draw-layer ops on one frame (frame id, @frame: mention, or name). "
                "Ops: list, add, rename, set (opacity/visible/locked/blend), "
                "move (position 0 is bottom), remove, duplicate, clear, merge down, "
                "draw (paint shapes: stroke/rect/ellipse/fill in frame pixels; "
                "omitted layer means the top layer)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "frame": s,
                    "op": s,
                    "layer": s,
                    "name": s,
                    "opacity": num,
                    "visible": {"type": "boolean"},
                    "locked": {"type": "boolean"},
                    "blend": s,
                    "position": num,
                    "sequence": s,
                    "shapes": {"type": "array"},
                },
                "required": ["project_id", "frame", "op"],
            },
        },
        {
            "name": "tone_layer",
            "description": (
                "Add a manga screentone dot layer to a draw frame "
                "(density 0..1, angle in degrees)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "frame": s,
                    "density": num,
                    "angle": num,
                    "color": s,
                    "opacity": num,
                    "blend": s,
                    "name": s,
                    "sequence": s,
                },
                "required": ["project_id", "frame"],
            },
        },
        {
            "name": "layer_from_media",
            "description": (
                "Drop a scan page, panel, generation, or frame onto a draw "
                "frame as a new layer. Source is an @media:/@gen:/@frame:/@panel: "
                "id or 'page N'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "frame": s,
                    "source": s,
                    "name": s,
                    "opacity": num,
                    "blend": s,
                    "sequence": s,
                },
                "required": ["project_id", "frame", "source"],
            },
        },
        {
            "name": "manage_frames",
            "description": (
                "Flipbook ops: list, duplicate, delete, rename, reorder, move "
                "(frame_ids lists every frame id once; target is the destination "
                "sequence for move)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "op": s,
                    "frame": s,
                    "name": s,
                    "frame_ids": arr,
                    "sequence": s,
                    "target": s,
                },
                "required": ["project_id", "op"],
            },
        },
        {
            "name": "manage_clips",
            "description": (
                "Timeline-clip ops: list, remove, set "
                "(duration/motion/transition/fades), "
                "reorder (clip_ids lists every clip id once)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": s,
                    "op": s,
                    "clip": s,
                    "scene": s,
                    "duration": num,
                    "motion": s,
                    "transition": s,
                    "transition_duration": num,
                    "fade_in": num,
                    "fade_out": num,
                    "clip_ids": arr,
                },
                "required": ["project_id", "op"],
            },
        },
    ]


def TOOLS() -> list[dict]:
    return _tool_defs()


def dispatch(request: dict, factory, user_id: str) -> dict | None:
    """Handle one JSON-RPC request. Returns the response, or None for
    notifications (no id)."""
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid JSON-RPC request"},
        }
    method = request.get("method")
    req_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method is None:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS()},
        }
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "arguments must be an object"},
            }
        try:
            with factory() as session:
                user = session.get(UserRow, user_id)
                if user is None:
                    raise RuntimeError("user not found")
                text = call_tool(name, args, session, user, factory)
        except KeyError as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": str(exc)},
            }
        except HTTPException as exc:
            text = f"error {exc.status_code}: {exc.detail}"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": True},
            }
        except Exception as exc:  # noqa: BLE001 — never hang the client
            text = f"error: {type(exc).__name__}: {exc}"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": True},
            }
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    }


def resolve_user_id(factory, email: str) -> str | None:
    """User id for the configured login email, or None."""
    with factory() as session:
        row = session.execute(
            select(UserRow).where(UserRow.email == email)
        ).scalars().first()
        return row.id if row is not None else None


def serve(factory=None) -> int:
    """Read JSON-RPC requests from stdin, write responses to stdout."""
    factory = factory or session_factory()
    email = os.environ.get("KOMAVERSE_MCP_USER", "").strip()
    if not email:
        print(
            "KOMAVERSE_MCP_USER is not set (the studio login email to act as).",
            file=sys.stderr,
        )
        return 1
    user_id = resolve_user_id(factory, email)
    if user_id is None:
        print(f"No studio user with email {email!r}.", file=sys.stderr)
        return 1
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "invalid JSON"},
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            continue
        try:
            response = dispatch(request, factory, user_id)
        except Exception as exc:  # noqa: BLE001 — one bad request never kills the server
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
