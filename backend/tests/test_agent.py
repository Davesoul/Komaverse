"""Agent tests: demo-brain turns execute real tools (detect, direct,
export, generate), ownership enforced. All sync."""

import io

import pytest
from conftest import create_user
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.datastructures import Headers

from komaverse.agent import parse_motions, parse_seconds, run_agent_turn
from komaverse.api.agent import AgentChatBody, chat
from komaverse.api.scans import ProjectCreate, create_project, upload_page
from komaverse.config import settings
from komaverse.db_models import Base, UserRow
from komaverse.security import limiter


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "ai_api_key", None)  # force demo brain
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def fake_page() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 560, 360], fill=0)
    d.rectangle([40, 400, 560, 760], fill=0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def seed_project(factory, user, name="Agent"):
    with factory() as s:
        project = create_project(ProjectCreate(name=name), s, user)
    with factory() as s:
        upload_page(
            project.id,
            UploadFile(
                file=io.BytesIO(fake_page()),
                filename="p1.png",
                headers=Headers({"content-type": "image/png"}),
            ),
            s,
            user,
        )
    return project.id


def turn(factory, user, message, project_id=None, **kw):
    with factory() as s:
        return run_agent_turn(message, project_id, s, user, **kw)


def test_agent_detect_status_unknown(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "detect all panels with heuristic", pid)
    assert out["actions"] and out["actions"][0]["tool"] == "detect_all"
    assert "2 panels" in out["reply"]

    out = turn(factory, user, "status?", pid)
    assert "2 panels" in out["reply"]

    out = turn(factory, user, "blabla nonsense", pid)
    assert out["actions"] == []
    assert "I can:" in out["reply"]


def test_agent_full_loop_to_mp4(factory, user):
    pid = seed_project(factory, user)
    assert "panels" in turn(factory, user, "find panels with heuristic", pid)["reply"]

    out = turn(factory, user, "animate it", pid, clip_seconds=0.4)
    assert out["actions"][0]["tool"] == "auto_direct"
    assert "2 clips" in out["reply"]

    out = turn(factory, user, "export the video", pid, job_factory=factory)
    assert ".mp4" in out["reply"]
    url = out["reply"].rsplit(" ", 1)[-1]
    video = settings.media_dir / url.removeprefix("/media/")
    assert video.exists() and video.stat().st_size > 1000


def test_agent_export_chains_auto_direct(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "render it", pid, clip_seconds=0.3)
    # No clips and no panels: agent says what is missing instead of failing.
    assert "detect" in out["reply"].lower() or ".mp4" in out["reply"]


def test_agent_generate_fake(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "generate a moonlit robot", pid, job_factory=factory)
    assert out["actions"] and out["actions"][0]["tool"] == "create_image"
    assert ".png" in out["reply"]
    from komaverse.api.animate import list_frames

    with factory() as s:
        assert len(list_frames(pid, s, user)) == 1


def test_agent_enforces_ownership(factory, user):
    pid = seed_project(factory, user)
    with factory() as s:
        other = create_user(s, email="other@test.local")
    with factory() as s, pytest.raises(HTTPException) as exc:
        run_agent_turn("status", pid, s, other)
    assert exc.value.status_code == 404


def test_agent_without_projects(factory, user):
    out = turn(factory, user, "status")
    assert "no projects" in out["reply"].lower()
    assert out["project_id"] is None


def test_parse_seconds_and_motions():
    assert parse_seconds("cut it, 3s each") == 3.0
    assert parse_seconds("2.5 seconds per clip") == 2.5
    assert parse_seconds("no timing here") is None
    assert parse_seconds("99s") == 30.0  # clamped
    assert parse_motions("zoom in, then pan left") == ["zoom-in", "pan-left"]
    assert parse_motions("no motions here") is None


def test_agent_timeline_uses_spoken_seconds_and_motions(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    turn(factory, user, "detect panels", pid)
    out = turn(factory, user, "direct the timeline with 3s zoom-in", pid)
    assert out["actions"][0]["tool"] == "auto_direct"
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert clips and all(c.duration == 3.0 for c in clips)
    assert all(c.motion == "zoom-in" for c in clips)


def test_agent_asks_when_projects_ambiguous(factory, user):
    seed_project(factory, user, name="Alpha")
    seed_project(factory, user, name="Beta")
    out = turn(factory, user, "status")
    assert "which project" in out["reply"].lower()
    assert "Alpha" in out["reply"] and "Beta" in out["reply"]
    assert out["project_id"] is None
    out = turn(factory, user, "status of alpha")
    assert out["project_name"] == "Alpha"


def test_agent_auto_selects_only_project(factory, user):
    pid = seed_project(factory, user, name="Solo")
    out = turn(factory, user, "status")
    assert out["project_id"] == pid


def test_agent_tool_error_becomes_reply(factory, user):
    with factory() as s:
        project = create_project(ProjectCreate(name="Empty"), s, user)
    out = turn(factory, user, "animate it", project.id)  # no pages at all
    assert out["actions"][0]["tool"] == "auto_direct"
    assert "no panels" in out["reply"].lower()


def test_agent_export_rate_limited(factory, user):
    pid = seed_project(factory, user)
    for _ in range(5):
        limiter.check(f"chat-export:{user.id}", 5, 60)
    out = turn(factory, user, "export the video", pid, job_factory=factory)
    assert "rate limited" in out["reply"].lower()


def test_agent_resolves_panel_mention(factory, user):
    from komaverse.api.scans import DetectRequest, detect_panels, get_project

    pid = seed_project(factory, user)
    with factory() as s:
        page = get_project(pid, s, user).pages[0]
    with factory() as s:
        detected = detect_panels(page.id, DetectRequest(detector="heuristic"), s, user)
    assert detected.panels, "seed page must detect panels"
    ref = {"kind": "panel", "id": detected.panels[0].id}
    out = turn(factory, user, "hmm, tell me about this thing here", pid, refs=[ref])
    assert "PG 1" in out["reply"]
    assert "Panel" in out["reply"]


def test_agent_names_missing_mention(factory, user):
    pid = seed_project(factory, user)
    out = turn(
        factory, user, "hmm, tell me about this thing here", pid,
        refs=[{"kind": "frame", "id": "nope00000000"}],
    )
    assert "(not found)" in out["reply"]


def test_chat_accepts_refs(factory, user):
    pid = seed_project(factory, user)
    with factory() as s:
        reply = chat(
            AgentChatBody(
                message="hello?", project_id=pid,
                refs=[{"kind": "take", "id": "nope00000000"}],
            ),
            s,
            user,
        )
    assert "(not found)" in reply.reply


def test_agent_creates_project_by_name(factory, user):
    out = turn(factory, user, "create project Shark Tale")
    assert out["actions"] and out["actions"][0]["tool"] == "create_project"
    assert out["project_id"] is not None
    assert "Shark Tale" in out["reply"]


def test_agent_detect_with_detector_and_direction(factory, user):
    from komaverse import agent as agent_mod

    pid = seed_project(factory, user)
    with factory() as s:
        summary = agent_mod.tool_detect_all(
            s, user, pid, detector="heuristic", direction="ltr"
        )
    assert "panels" in summary


def test_agent_parse_detector_and_direction():
    from komaverse.agent import parse_detector, parse_direction

    assert parse_detector("scan everything with yolo") == "yolo"
    assert parse_detector("detect panels") == "yolo-leoxs22"
    assert parse_detector("use the leox model") == "yolo-leoxs22"
    assert parse_detector("detect with bogus-model") == "yolo-leoxs22"
    assert parse_direction("scan left to right") == "ltr"
    assert parse_direction("scan the pages") == "rtl"
    assert parse_direction("right to left, please") == "rtl"


def test_agent_scan_picks_direction(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "scan the pages left to right", pid)
    assert out["actions"] and out["actions"][0]["tool"] == "detect_all"
    assert "ltr" in out["actions"][0]["summary"]


def test_agent_scan_unknown_detector_falls_back(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "detect panels with bogus-model", pid)
    assert out["actions"] and out["actions"][0]["tool"] == "detect_all"
    assert "yolo-leoxs22" in out["actions"][0]["summary"]


def test_agent_sees_finished_job(factory, user):
    import re

    from komaverse import agent as agent_mod

    pid = seed_project(factory, user)
    out = turn(factory, user, "generate a moonlit robot", pid, job_factory=factory)
    m = re.search(r"\[job ([A-Za-z0-9_-]+)\]", out["actions"][0]["summary"])
    assert m, "image summary must carry the job id"
    with factory() as s:
        seen = agent_mod.tool_check_job(s, user, m.group(1))
    assert "done" in seen.lower()
    assert ".png" in seen


def test_agent_generate_and_add_to_timeline(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    out = turn(
        factory, user,
        "generate a moonlit robot and add it to the timeline",
        pid, job_factory=factory,
    )
    tools = [a["tool"] for a in out["actions"]]
    assert "create_image" in tools and "add_clip" in tools
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert any(c.kind == "frame" for c in clips)


def test_agent_add_panel_token_to_timeline(factory, user):
    from komaverse.api.animate import list_scenes
    from komaverse.api.scans import DetectRequest, detect_panels, get_project

    pid = seed_project(factory, user)
    with factory() as s:
        page = get_project(pid, s, user).pages[0]
    with factory() as s:
        detected = detect_panels(
            page.id, DetectRequest(detector="heuristic", confirm=True), s, user
        )
    panel_id = detected.panels[0].id
    out = turn(factory, user, f"add @panel:{panel_id} to the timeline", pid)
    assert any(a["tool"] == "add_clip" for a in out["actions"])
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert any(c.kind == "panel" and c.source_id == panel_id for c in clips)


def test_agent_add_page_to_timeline(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    out = turn(factory, user, "add page 1 to the timeline", pid)
    assert any(a["tool"] == "add_clip" for a in out["actions"])
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert any(c.kind == "media" for c in clips)


def test_agent_add_subtitles(factory, user):
    from komaverse.api.animate import list_scenes
    from komaverse.api.subtitles import get_subtitles

    pid = seed_project(factory, user)
    out = turn(factory, user, 'add subtitles: "hello" 0-2s; "world" 2-4s', pid)
    assert any(a["tool"] == "add_subtitles" for a in out["actions"])
    with factory() as s:
        scene = list_scenes(pid, s, user)[0]
    with factory() as s:
        track = get_subtitles(scene.id, s, user)
    assert track is not None and [s.text for s in track.segments] == ["hello", "world"]


def seed_take(factory, user, project_id, name="Voice", duration=5.0):
    from komaverse.db_models import AudioTakeRow

    with factory() as s:
        row = AudioTakeRow(
            project_id=project_id,
            name=name,
            file_path="takes/seed.wav",
            url="/media/seed.wav",
            mime="audio/wav",
            duration_sec=duration,
        )
        s.add(row)
        s.commit()
        return row.id


def test_agent_add_cue_from_take(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    tid = seed_take(factory, user, pid)
    out = turn(factory, user, f"put @take:{tid} at 2s on the timeline", pid)
    assert any(a["tool"] == "add_cue" for a in out["actions"])
    with factory() as s:
        cues = list_scenes(pid, s, user)[0].cues
    assert any(c.audio_id == tid and c.start_sec == 2.0 for c in cues)


def test_agent_frame_from_media_page(factory, user):
    from komaverse.api.animate import list_frames

    pid = seed_project(factory, user)
    out = turn(factory, user, "put page 1 on a new frame", pid)
    assert any(a["tool"] == "frame_from_media" for a in out["actions"])
    with factory() as s:
        frames = list_frames(pid, s, user)
    assert len(frames) == 1 and len(frames[0].layers) == 1


def seed_video_job(factory, user, project_id, prompt="a robot walking", seconds=1.0):
    import imageio_ffmpeg
    import numpy as np

    from komaverse.db_models import JobRow

    with factory() as s:
        job = JobRow(
            user_id=user.id,
            project_id=project_id,
            kind="video",
            prompt=prompt,
            provider="openrouter",
            width=320,
            height=240,
            seconds=seconds,
            attach_frame=False,
            status="done",
            progress=1.0,
        )
        s.add(job)
        s.flush()
        rel = f"ai/{job.id}.mp4"
        dest = settings.media_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        writer = imageio_ffmpeg.write_frames(
            str(dest), (320, 240), pix_fmt_in="rgb24", fps=12, codec="libx264",
        )
        writer.send(None)
        try:
            for i in range(12):
                frame = np.full((240, 320, 3), (i * 20) % 255, dtype=np.uint8)
                writer.send(frame.tobytes())
        finally:
            writer.close()
        job.result_url = f"/media/{rel}"
        s.commit()
        return job.id


def test_agent_add_video_to_timeline(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    seed_video_job(factory, user, pid)
    out = turn(factory, user, "add the video to the timeline", pid)
    assert any(a["tool"] == "add_clip" for a in out["actions"])
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert any(c.kind == "video" for c in clips)


def test_export_renders_video_clip(factory, user):
    pid = seed_project(factory, user)
    seed_video_job(factory, user, pid)
    turn(factory, user, "add the video to the timeline", pid)
    out = turn(factory, user, "export the video", pid, job_factory=factory)
    assert ".mp4" in out["reply"]
    url = out["reply"].rsplit(" ", 1)[-1]
    video = settings.media_dir / url.removeprefix("/media/")
    assert video.exists() and video.stat().st_size > 1000


def job_entities(factory, job_id):
    import json

    from komaverse.db_models import JobRow

    with factory() as s:
        return json.loads(s.get(JobRow, job_id).entities_json or "[]")


def job_id_of(summary):
    import re

    m = re.search(r"\[job ([A-Za-z0-9_-]+)\]", summary)
    assert m, "expected a job id in the summary"
    return m.group(1)


def test_agent_generate_with_panel_reference(factory, user):
    from komaverse.api.scans import DetectRequest, detect_panels, get_project

    pid = seed_project(factory, user)
    with factory() as s:
        page = get_project(pid, s, user).pages[0]
    with factory() as s:
        detected = detect_panels(
            page.id, DetectRequest(detector="heuristic", confirm=True), s, user
        )
    panel_id = detected.panels[0].id
    out = turn(
        factory, user,
        f"generate a warrior in the style of @panel:{panel_id}",
        pid, job_factory=factory,
    )
    assert out["actions"] and out["actions"][0]["tool"] == "create_image"
    snaps = job_entities(factory, job_id_of(out["actions"][0]["summary"]))
    assert any(s.get("panel_id") == panel_id for s in snaps)


def test_agent_generate_with_media_reference(factory, user):
    from komaverse.api.scans import get_project

    pid = seed_project(factory, user)
    with factory() as s:
        page_id = get_project(pid, s, user).pages[0].id
    out = turn(
        factory, user, f"redraw @media:{page_id} as night",
        pid, job_factory=factory,
    )
    snaps = job_entities(factory, job_id_of(out["actions"][0]["summary"]))
    assert any(s.get("page_id") == page_id for s in snaps)


def test_agent_generate_names_entity(factory, user):
    from komaverse.api.ai import EntityCreate, create_entity

    pid = seed_project(factory, user)
    with factory() as s:
        ent = create_entity(
            pid, EntityCreate(kind="character", name="Mika"), s, user
        )
    out = turn(
        factory, user, "generate Mika in the rain",
        pid, job_factory=factory,
    )
    snaps = job_entities(factory, job_id_of(out["actions"][0]["summary"]))
    assert any(s.get("id") == ent.id for s in snaps)


def test_agent_generate_from_finished_generation(factory, user):
    pid = seed_project(factory, user)
    first = turn(factory, user, "generate a moonlit robot", pid, job_factory=factory)
    gid = job_id_of(first["actions"][0]["summary"])
    out = turn(
        factory, user, f"generate a darker variant of @gen:{gid}",
        pid, job_factory=factory,
    )
    snaps = job_entities(factory, job_id_of(out["actions"][0]["summary"]))
    assert any(s.get("job_id") == gid for s in snaps)


def test_agent_media_mention_resolves(factory, user):
    from komaverse.api.scans import get_project

    pid = seed_project(factory, user)
    with factory() as s:
        page_id = get_project(pid, s, user).pages[0].id
    out = turn(
        factory, user, "tell me about this page", pid,
        refs=[{"kind": "media", "id": page_id}],
    )
    assert "Scan page 1" in out["reply"]


def test_agent_lists_takes(factory, user):
    pid = seed_project(factory, user)
    seed_take(factory, user, pid, name="Intro VO")
    out = turn(factory, user, "what takes do I have", pid)
    assert any(a["tool"] == "list_takes" for a in out["actions"])
    assert "Intro VO" in out["reply"]


def test_agent_video_routes_to_async_job(factory, user):
    pid = seed_project(factory, user)
    out = turn(factory, user, "generate a video of a robot", pid, job_factory=factory)
    assert out["actions"] and out["actions"][0]["tool"] == "create_video"


def test_chat_threads_clip_seconds(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    turn(factory, user, "detect panels", pid)
    with factory() as s:
        reply = chat(
            AgentChatBody(message="cut the timeline", project_id=pid, clip_seconds=4),
            s,
            user,
        )
    assert reply.actions[0].tool == "auto_direct"
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert clips and all(c.duration == 4.0 for c in clips)


def test_agent_add_media_mention_to_timeline(factory, user):
    from komaverse.api.animate import list_scenes
    from komaverse.api.scans import get_project

    pid = seed_project(factory, user)
    with factory() as s:
        page = get_project(pid, s, user).pages[0]
    out = turn(factory, user, f"add @media:{page.id} to the timeline", pid)
    assert any(a["tool"] == "add_clip" for a in out["actions"])
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert any(c.kind == "media" and c.source_id == page.id for c in clips)


def test_agent_check_video_without_id(factory, user):
    pid = seed_project(factory, user)
    jid = seed_video_job(factory, user, pid)
    out = turn(factory, user, "is my video done?", pid)
    assert any(a["tool"] == "check_job" for a in out["actions"])
    assert "done" in out["reply"].lower()


def test_agent_check_running_video(factory, user):
    from komaverse.db_models import JobRow

    pid = seed_project(factory, user)
    jid = seed_video_job(factory, user, pid)
    with factory() as s:
        s.get(JobRow, jid).status = "running"
        s.commit()
    out = turn(factory, user, "is my video done?", pid)
    assert "still running" in out["reply"].lower()


def test_agent_single_take_needs_no_disambiguation(factory, user):
    pid = seed_project(factory, user)
    seed_take(factory, user, pid)
    out = turn(factory, user, "put the take at 1s", pid)
    assert any(a["tool"] == "add_cue" for a in out["actions"])
    out = turn(factory, user, "transcribe it", pid)
    assert any(a["tool"] == "transcribe_audio" for a in out["actions"])


def test_agent_generate_and_add_it(factory, user):
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    out = turn(
        factory, user, "generate a moonlit robot and add it", pid,
        job_factory=factory,
    )
    tools = [a["tool"] for a in out["actions"]]
    assert "create_image" in tools and "add_clip" in tools
    with factory() as s:
        clips = list_scenes(pid, s, user)[0].clips
    assert any(c.kind == "frame" for c in clips)


def test_agent_frame_panel_mention(factory, user):
    from komaverse.api.scans import get_project
    from komaverse.db_models import PanelRow

    pid = seed_project(factory, user)
    with factory() as s:
        page = get_project(pid, s, user).pages[0]
    with factory() as s:
        row = PanelRow(page_id=page.id, x=10.0, y=10.0, w=60.0, h=40.0)
        s.add(row)
        s.commit()
        panel_id = row.id
    out = turn(factory, user, f"frame the panel @panel:{panel_id}", pid)
    assert any(a["tool"] == "frame_from_media" for a in out["actions"])


def seed_draw(factory, user, name="Draw"):
    from komaverse import agent as agent_mod

    with factory() as s:
        summary = agent_mod.tool_create_project(s, user, name)
    import re

    return re.search(r"\[project ([A-Za-z0-9_-]+)\]", summary).group(1)


def draw_frame_id(factory, user, pid, name):
    from komaverse import agent as agent_mod

    import re

    with factory() as s:
        summary = agent_mod.tool_create_frame(s, user, pid, name)
    return re.search(r"\[frame ([A-Za-z0-9_-]+)\]", summary).group(1)


def test_agent_draw_layers_crud(factory, user):
    from komaverse import agent as agent_mod

    pid = seed_draw(factory, user)
    fid = draw_frame_id(factory, user, pid, "F1")
    with factory() as s:
        assert "F1" in agent_mod.tool_list_frames(s, user, pid)
        assert "Added layer 'ink'" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "add", name="ink"
        )
        assert "opacity 60%" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "set", "ink", opacity=60, locked=True
        )
        assert "hidden" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "set", "ink", visible=False
        )
        assert "Duplicated 'ink'" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "duplicate", "ink"
        )
        assert "Unknown layer 'ghost'" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "remove", "ghost"
        )
        assert "opacity must be" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "set", "ink", opacity=1000
        ).lower()
        assert "unknown blend mode" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "set", "ink", blend="bogus"
        ).lower()
        assert "unknown layer op" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "frobnicate", "ink"
        ).lower()
        assert "position 0" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "move", "ink copy", position=0
        )
        assert "unlocked" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "set", "ink", locked=False
        )
        assert "Merged 'ink' down into 'ink copy'" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "merge", "ink"
        )
        assert "Cleared the pixels" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "clear", "ink copy"
        )
        assert "Removed layer 'ink copy'" in agent_mod.tool_manage_layers(
            s, user, pid, fid, "remove", "ink copy"
        )


def test_agent_draw_tone_and_media_layer(factory, user):
    from komaverse import agent as agent_mod
    from komaverse.db_models import FrameRow

    pid = seed_project(factory, user)  # seeds scan page 1
    fid = draw_frame_id(factory, user, pid, "Tone")
    with factory() as s:
        out = agent_mod.tool_tone_layer(s, user, pid, fid, 0.35)
        assert "screentone" in out and "35%" in out
        out = agent_mod.tool_layer_from_media(s, user, pid, fid, "page 1")
        assert "as layer" in out
        row = s.get(FrameRow, fid)
        by_name = {layer.name: layer for layer in row.layers}
        assert by_name["Tone 35%"].blend_mode == "multiply"
        assert by_name["Tone 35%"].data_url.startswith("data:image/png;base64,")
        media_layer = next(v for k, v in by_name.items() if k != "Tone 35%")
        assert media_layer.data_url.startswith("data:image/png;base64,")
        assert "Unknown media" in agent_mod.tool_layer_from_media(
            s, user, pid, fid, "nope"
        )


def test_agent_draw_frames_and_clips(factory, user):
    import re

    from komaverse import agent as agent_mod
    from komaverse.api.animate import list_scenes

    pid = seed_project(factory, user)
    f1 = draw_frame_id(factory, user, pid, "A")
    f2 = draw_frame_id(factory, user, pid, "B")
    with factory() as s:
        assert "Duplicated 'A'" in agent_mod.tool_manage_frames(
            s, user, pid, "duplicate", f1
        )
        assert "Renamed frame to 'Second'" in agent_mod.tool_manage_frames(
            s, user, pid, "rename", f2, name="Second"
        )
        assert "every frame exactly once" in agent_mod.tool_manage_frames(
            s, user, pid, "reorder", frame_ids=[f1]
        )
    with factory() as s:
        frames = agent_mod.tool_list_frames(s, user, pid)
        ids = re.findall(r"\[frame ([A-Za-z0-9_-]+)\]", frames)
        assert len(ids) == 3
        assert "Reordered frames" in agent_mod.tool_manage_frames(
            s, user, pid, "reorder", frame_ids=list(reversed(ids))
        )
        assert "Deleted frame 'Second'" in agent_mod.tool_manage_frames(
            s, user, pid, "delete", f2
        )
    with factory() as s:
        added = agent_mod.tool_add_clip(s, user, pid, "frame", f1, None, 2.0, "none")
        assert "[clip " in added
        cid = re.search(r"\[clip ([A-Za-z0-9_-]+)\]", added).group(1)
        assert "Timeline" in agent_mod.tool_manage_clips(s, user, pid, "list")
        updated = agent_mod.tool_manage_clips(
            s, user, pid, "set", clip=cid, duration=4.0, motion="zoom-in"
        )
        assert "4s, zoom-in" in updated
        assert "Unknown motion" in agent_mod.tool_manage_clips(
            s, user, pid, "set", clip=cid, motion="spin"
        )
        assert "frame is used by" in agent_mod.tool_manage_frames(
            s, user, pid, "delete", f1
        )
        assert "Removed clip" in agent_mod.tool_manage_clips(
            s, user, pid, "remove", clip=cid
        )
        assert "no clips" in agent_mod.tool_manage_clips(s, user, pid, "list")
        assert "Deleted frame 'A'" in agent_mod.tool_manage_frames(
            s, user, pid, "delete", f1
        )
        scenes = list_scenes(pid, s, user)
        assert all(not sc.clips for sc in scenes)


def test_agent_sequences_crud(factory, user):
    from komaverse import agent as agent_mod

    pid = seed_project(factory, user)
    f1 = draw_frame_id(factory, user, pid, "S1")
    f2 = draw_frame_id(factory, user, pid, "S2")
    with factory() as s:
        listed = agent_mod.tool_manage_sequences(s, user, pid, "list")
        assert "Sequence 1" in listed and "(2 frames)" in listed
        assert "Created sequence 'Alt'" in agent_mod.tool_manage_sequences(
            s, user, pid, "add", name="Alt"
        )
        assert "Moved 'S1' to sequence 'Alt'" in agent_mod.tool_manage_frames(
            s, user, pid, "move", f1, target="Alt"
        )
        assert "already in 'Alt'" in agent_mod.tool_manage_frames(
            s, user, pid, "move", f1, target="Alt"
        )
        assert "Move needs a target sequence" in agent_mod.tool_manage_frames(
            s, user, pid, "move", f2
        )
        assert "Unknown sequence" in agent_mod.tool_manage_frames(
            s, user, pid, "move", f2, target="Ghost"
        )
        assert "Duplicated 'Alt' as 'Alt copy'" in agent_mod.tool_manage_sequences(
            s, user, pid, "duplicate", "Alt"
        )
        assert "Renamed sequence to 'Second'" in agent_mod.tool_manage_sequences(
            s, user, pid, "rename", "Alt", name="Second"
        )
        assert "'Second' [sequence" in agent_mod.tool_manage_sequences(
            s, user, pid, "list"
        )
        # Sequence 1 still holds S2: deleting Alt copy drops its frames.
        assert "Deleted sequence 'Alt copy'" in agent_mod.tool_manage_sequences(
            s, user, pid, "delete", "Alt copy"
        )
        added = agent_mod.tool_add_clip(s, user, pid, "frame", f2, None, 2.0, "none")
        assert "[clip " in added
        assert "used by 1 clip" in agent_mod.tool_manage_sequences(
            s, user, pid, "delete", "Sequence 1"
        )
        remaining = agent_mod.tool_manage_sequences(s, user, pid, "list")
        assert "'Second'" in remaining and "Sequence 1" in remaining
        scoped = agent_mod.tool_list_frames(s, user, pid, "Second")
        assert "Frames in 'Second'" in scoped and "S1" in scoped
        assert "Unknown sequence" in agent_mod.tool_list_frames(
            s, user, pid, "Ghost"
        )


def test_agent_timelines_crud(factory, user):
    from komaverse import agent as agent_mod

    pid = seed_project(factory, user)
    f1 = draw_frame_id(factory, user, pid, "T1")
    f2 = draw_frame_id(factory, user, pid, "T2")
    with factory() as s:
        assert "No timelines yet" not in agent_mod.tool_manage_timelines(
            s, user, pid, "list"
        )
        assert "Created timeline 'Recut'" in agent_mod.tool_manage_timelines(
            s, user, pid, "add", name="Recut"
        )
        assert "Duplicated 'Recut' as 'Recut copy'" in agent_mod.tool_manage_timelines(
            s, user, pid, "duplicate", "Recut"
        )
        assert "Renamed timeline to 'Final'" in agent_mod.tool_manage_timelines(
            s, user, pid, "rename", "Recut", name="Final"
        )
        # Two timelines with clips: export must ask which one.
        assert "Final" in agent_mod.tool_manage_timelines(s, user, pid, "list")
        agent_mod.tool_add_clip(s, user, pid, "frame", f1, "Final", 2.0, "none")
        agent_mod.tool_add_clip(s, user, pid, "frame", f2, "Recut copy", 2.0, "none")
        ambiguous = agent_mod.tool_export(s, user, pid)
        assert "Which timeline?" in ambiguous
        scoped = agent_mod.tool_export(s, user, pid, factory, False, "Final")
        assert ".mp4" in scoped
        assert "Deleted timeline 'Recut copy'" in agent_mod.tool_manage_timelines(
            s, user, pid, "delete", "Recut copy"
        )
        assert "Unknown timeline" in agent_mod.tool_export(s, user, pid, scene="Ghost")
        assert "Which timeline?" in agent_mod.tool_manage_timelines(
            s, user, pid, "rename", None, name="X"
        )


def test_agent_demo_sequence_timeline_turns(factory, user):
    pid = seed_project(factory, user)
    draw_frame_id(factory, user, pid, "Demo")
    out = turn(factory, user, "create a new sequence called Chase", pid)
    assert any(a["tool"] == "manage_sequences" for a in out["actions"])
    assert "Chase" in out["reply"]
    out = turn(factory, user, "list sequences", pid)
    assert any(a["tool"] == "manage_sequences" for a in out["actions"])
    assert "Sequence 1" in out["reply"]
    out = turn(factory, user, "create a new timeline called Recut", pid)
    assert any(a["tool"] == "manage_timelines" for a in out["actions"])
    assert "Recut" in out["reply"]
    out = turn(factory, user, "duplicate the Recut timeline", pid)
    assert any(a["tool"] == "manage_timelines" for a in out["actions"])
    assert "Duplicated" in out["reply"]


def test_agent_demo_draw_turns(factory, user):
    pid = seed_project(factory, user)
    fid = draw_frame_id(factory, user, pid, "Demo")
    out = turn(factory, user, "add a tone layer at 40% to the frame", pid)
    assert any(a["tool"] == "tone_layer" for a in out["actions"])
    assert "40%" in out["reply"]
    out = turn(factory, user, "list frames", pid)
    assert any(a["tool"] == "manage_frames" for a in out["actions"])
    assert "Demo" in out["reply"]
    with factory() as s:
        from komaverse import agent as agent_mod

        agent_mod.tool_manage_layers(s, user, pid, fid, "add", name="ink")
    out = turn(factory, user, "hide the ink layer", pid)
    assert any(a["tool"] == "manage_layers" for a in out["actions"])
    assert "hidden" in out["reply"]
    out = turn(factory, user, "duplicate the demo frame", pid)
    assert any(a["tool"] == "manage_frames" for a in out["actions"])
    assert "Duplicated" in out["reply"]
