"""Agent conversation awareness: deictic refs resolve to generated artifacts.

After generating a character, "use this character to generate a video"
must attach the finished generation (@gen:) instead of leaving the
model with only the open frame. Truncated model output must say so
instead of stopping silently.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from conftest import create_user
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import komaverse.agent as agent_mod
from komaverse.api.scans import ProjectCreate, create_project
from komaverse.config import settings
from komaverse.db_models import Base, FrameRow, JobRow, UserRow


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def user(factory) -> UserRow:
    with factory() as s:
        return create_user(s)


def _project_and_frame(s, user, name="P"):
    project = create_project(ProjectCreate(name=name), s, user)
    frame = FrameRow(project_id=project.id, name="Panel 1")
    s.add(frame)
    s.commit()
    return project, frame


def _done_job(s, user, pid, prompt, age_minutes=1):
    job = JobRow(
        user_id=user.id,
        project_id=pid,
        kind="image",
        status="done",
        prompt=prompt,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )
    s.add(job)
    s.commit()
    return job


def test_deictic_attaches_latest_generation(factory, user):
    """'use this character' resolves to the just-finished generation."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        old = _done_job(s, user, project.id, "a moonlit robot", age_minutes=60)
        new = _done_job(s, user, project.id, "warrior character", age_minutes=1)
        out = agent_mod._resolve_turn(
            "use this character to generate a video",
            project.id, s, user, frame.id, refs=[],
        )
        # The turn asks for duration/framing, and the resolved reference
        # rides the question (the early return drops message/refs, so the
        # reply must name it or the next turn could not cite it back).
        assert "early" in out
        reply = out["early"]["reply"]
        assert f"@gen:{new.id}" in reply
        assert f"@gen:{old.id}" not in reply
        assert "duration" in reply.lower()
        # The open frame is NOT attached: it wasn't part of the prompt.
        assert "@frame:" not in reply
        # ...while with full params the turn proceeds with the newest gen.
        out = agent_mod._resolve_turn(
            "use this character to generate a 4 second 16:9 720p video",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" not in out
        assert f"@gen:{new.id}" in out["message"]
        assert f"@gen:{old.id}" not in out["message"]
        kinds = [r.get("kind") for r in out["refs"]]
        assert kinds == ["gen"]
        assert out["refs"][0]["id"] == new.id
        assert "@frame:" not in out["message"]
        assert "Panel 1" in out["open_frame"]
        assert new.id in out["ref_context"]


def test_typed_gen_never_drags_frame(factory, user):
    """The reported bug: @gen:79ccec954aa5 must not gain a frame ref."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        job = _done_job(s, user, project.id, "fighter character")
        out = agent_mod._resolve_turn(
            f"using this fighter from @gen:{job.id} to generate "
            "a 4 second 16:9 720p video",
            project.id, s, user, frame.id, refs=[],
        )
        assert [r.get("kind") for r in out["refs"]] == ["gen"]
        assert "@frame:" not in out["message"]


def test_frame_directed_attaches_frame(factory, user):
    """Operating on the frame attaches it — and only it."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        _done_job(s, user, project.id, "warrior character")
        out = agent_mod._resolve_turn(
            "paint on the current frame",
            project.id, s, user, frame.id, refs=[],
        )
        # Operating on the frame attaches it — no reference intent here,
        # so the generation lock stays out of this turn.
        assert [r.get("kind") for r in out["refs"]] == ["frame"]
        assert "@gen:" not in out["message"]


def test_named_frame_resolves(factory, user):
    """A frame the user names outright resolves to its token."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "use Panel 1 as reference",
            project.id, s, user, frame.id, refs=[],
        )
        assert f"@frame:{frame.id}" in out["message"]
        assert [r.get("kind") for r in out["refs"]] == ["frame"]


def test_gen_context_carries_style_lock(factory, user):
    """The generation line locks style/genre/seed for follow-ups."""
    with factory() as s:
        project, _frame = _project_and_frame(s, user)
        job = _done_job(s, user, project.id, "warrior character")
        job.style = "shonen"
        job.genre = "manga"
        job.seed = 7
        s.commit()
        ctx = agent_mod.resolve_refs([{"kind": "gen", "id": job.id}], s, user)
        assert "style: shonen" in ctx
        assert "genre: manga" in ctx
        assert "seed: 7" in ctx


def test_explicit_token_skips_generation(factory, user):
    """A typed @frame mention keeps its meaning; no gen is injected."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        _done_job(s, user, project.id, "warrior character")
        out = agent_mod._resolve_turn(
            f"paint over @frame:{frame.id} with red",
            project.id, s, user, frame.id, refs=[],
        )
        assert "@gen:" not in out["message"]
        assert [r.get("kind") for r in out["refs"]] == ["frame"]


def test_ambiguous_deictic_asks_question(factory, user):
    """Reference intent with no resolvable referent asks instead of guessing."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "use this style for the background",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "reference" in out["early"]["reply"].lower()


def test_plain_generation_proceeds(factory, user):
    """A fully specified prompt is not interrogated."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a 16:9 high quality sunset over the sea",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" not in out
        assert out["refs"] == []
        assert "@frame:" not in out["message"]
        # ...while the frame stays known through the scope line.
        assert "Panel 1" in out["open_frame"]


def test_bare_image_asks_cost_params(factory, user):
    """No format/quality in the text always asks, with 'go' defaults."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a sunset over the sea",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        reply = out["early"]["reply"].lower()
        assert "aspect ratio" in reply
        assert "quality" in reply
        assert "'go'" in out["early"]["reply"]
        assert "1:1 square" in out["early"]["reply"]


def test_wide_hint_steers_go_default(factory, user):
    """An orientation hint flavors the question and the 'go' default."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a warrior, make it wide",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "you said 'wide'" in out["early"]["reply"]
        assert "16:9 landscape" in out["early"]["reply"]


def test_bare_video_asks_duration_and_framing(factory, user):
    """No length/framing in the text asks both, with 'go' defaults."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a video of a robot walking",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        reply = out["early"]["reply"]
        assert "duration" in reply.lower()
        assert "framing" in reply.lower()
        assert "4 seconds" in reply


def test_meta_image_skips_param_gate(factory, user, monkeypatch):
    """The prompt-only Meta image model is never asked for ratios."""
    monkeypatch.setattr(settings, "image_provider", "meta")
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a sunset over the sea",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" not in out


def test_same_style_asks_without_lock(factory, user):
    """"same style" with nothing to inherit from asks which style."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a sunset in the same style",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "style" in out["early"]["reply"].lower()


def test_same_style_inherits_lock(factory, user):
    """"same style" with a locked generation proceeds silently."""
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        job = _done_job(s, user, project.id, "warrior character")
        job.style = "shonen"
        s.commit()
        out = agent_mod._resolve_turn(
            "generate a 16:9 high quality sunset in the same style",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" not in out
        assert [r.get("kind") for r in out["refs"]] == ["gen"]


def test_vague_ratio_asks_before_generating(factory, user):
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "generate a warrior, make it wide",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "aspect ratio" in out["early"]["reply"].lower()
        # A bare dimension also asks what to generate.
        out = agent_mod._resolve_turn(
            "make it wide",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "What should I generate" in out["early"]["reply"]


def test_vague_duration_asks_before_generating(factory, user):
    with factory() as s:
        project, frame = _project_and_frame(s, user)
        out = agent_mod._resolve_turn(
            "make me a short video",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "duration" in out["early"]["reply"].lower()
        # No verb, no pre-loop question (the tool gate covers it).
        out = agent_mod._resolve_turn(
            "a short video",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" not in out
        # A numbered length skips the duration question but still owes
        # framing, which the prompt-level video API cannot take as a param.
        out = agent_mod._resolve_turn(
            "generate a 5 second video of a robot",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" in out
        assert "framing" in out["early"]["reply"].lower()
        # Length plus framing plus resolution proceeds.
        out = agent_mod._resolve_turn(
            "generate a 5 second wide 16:9 720p video of a robot",
            project.id, s, user, frame.id, refs=[],
        )
        assert "early" not in out


def test_orientation_gate():
    assert agent_mod._missing_orientation("make it wide", None, None) == "wide"
    assert agent_mod._missing_orientation("wide 16:9 shot", None, None) is None
    assert agent_mod._missing_orientation("make it wide", 1280, 720) is None
    # Bare nouns are not orientation framing.
    assert agent_mod._missing_orientation("generate a portrait of a lady", None, None) is None
    assert agent_mod._missing_orientation("a town square at dusk", None, None) is None
    assert agent_mod._missing_orientation("which ratio?", None, None)


def test_duration_gate():
    assert agent_mod._missing_duration("a short video", 4.0) == "short"
    assert agent_mod._missing_duration("a 5 second clip", 4.0) is None
    assert agent_mod._missing_duration("a short video", 8.0) is None


def test_quality_gate():
    assert agent_mod.QUALITY_RE.search("high quality render")
    assert agent_mod.QUALITY_RE.search("draft version")
    assert agent_mod.QUALITY_RE.search("4k detail")
    assert agent_mod.QUALITY_RE.search("render at 1024px")
    # Bare "high" names no tier.
    assert not agent_mod.QUALITY_RE.search("a high mountain at dusk")
    assert not agent_mod.QUALITY_RE.search("generate a sunset")


def test_openrouter_aspect_buckets():
    from komaverse.ai.providers import _aspect_ratio

    assert _aspect_ratio(1280, 720) == "16:9"
    assert _aspect_ratio(720, 1280) == "9:16"
    assert _aspect_ratio(1024, 1024) == "1:1"
    assert _aspect_ratio(1024, 768) == "4:3"
    assert _aspect_ratio(768, 1024) == "3:4"
    # Off-catalog ratios render square rather than erroring.
    assert _aspect_ratio(1000, 700) == "1:1"


def test_style_gate():
    assert agent_mod._missing_style("change the style", "none", "none", None)
    assert agent_mod._missing_style("in the style of shonen", "none", "none", None) is None
    assert agent_mod._missing_style("same style", "none", "none", ("shonen", "")) is None
    assert agent_mod._missing_style("same style", "none", "none", None)
    assert agent_mod._missing_style("make it stylish", "none", "none", None)
    assert agent_mod._missing_style("make it shonen", "shonen", "none", None) is None


def test_image_generation_asks_for_ratio(factory, user, monkeypatch):
    """'make it wide' blocks before charging or creating a job."""
    from komaverse.db_models import JobRow

    class DummyThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(agent_mod.threading, "Thread", DummyThread)
    monkeypatch.setattr(settings, "paywall_enabled", False)
    with factory() as s:
        project, _frame = _project_and_frame(s, user)
        before = s.query(JobRow).count()
        out = agent_mod.tool_generate(s, user, project.id, "make it wide", None)
        assert "aspect ratio" in out
        assert s.query(JobRow).count() == before
        # A usable format proceeds instead.
        out = agent_mod.tool_generate(s, user, project.id, "wide 16:9 shot", None)
        assert "aspect ratio" not in out
        assert s.query(JobRow).count() == before + 1


def test_video_generation_asks_for_duration(factory, user):
    """'a short video' blocks before the provider check or job creation."""
    from komaverse.db_models import JobRow

    with factory() as s:
        project, _frame = _project_and_frame(s, user)
        before = s.query(JobRow).count()
        out = agent_mod.tool_create_video(s, user, project.id, "a short video")
        assert "duration" in out
        assert s.query(JobRow).count() == before
        # A fully specified request is usable — the gate stays out of the way
        # (here the unconfigured provider answers instead).
        out = agent_mod.tool_create_video(s, user, project.id, "a 5 second 16:9 720p clip")
        assert "Before generating I need" not in out


def test_placeholder_job_ids_never_polled(factory, user, monkeypatch):
    """A literal [job id] echoed by the model is not treated as a job."""
    monkeypatch.setattr(
        agent_mod, "get_chat_config", lambda: ("http://x", "k", "m")
    )
    with factory() as s:
        project, _frame = _project_and_frame(s, user)
        real = JobRow(
            id="realjob1",
            user_id=user.id,
            project_id=project.id,
            kind="image",
            status="done",
            prompt="warrior character",
        )
        s.add(real)
        s.commit()
        monkeypatch.setattr(
            agent_mod,
            "_exec_cloud_tool",
            lambda *a, **k: "Working on it [job realjob1] then [job id].",
        )

        def step(messages, base_url, api_key, model):
            return (
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "project_status", "arguments": "{}"},
                        }
                    ],
                },
                {"total": 5},
                [],
            )

        events = list(
            agent_mod._cloud_loop(step, "hi", project.id, s, user, None, history=[])
        )
        tool_ev = next(e for e in events if e["type"] == "tool_result")
        assert tool_ev["job_ids"] == ["realjob1"]


def test_chat_sends_explicit_max_tokens(monkeypatch):
    """The model gets a generous output budget instead of a silent cutoff."""
    import komaverse.ai.providers as providers_mod

    seen = {}

    class FakeRes:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(json or {})
        return FakeRes()

    monkeypatch.setattr(providers_mod.httpx, "post", fake_post)
    providers_mod.chat_complete_with_usage(
        base_url="http://x", api_key="k", model="m", messages=[{"role": "user", "content": "hi"}]
    )
    assert seen["max_tokens"] == settings.chat_max_tokens >= 1024


def test_truncation_is_surfaced(factory, user, monkeypatch):
    """A length stop explains itself instead of ending mid-answer."""
    monkeypatch.setattr(
        agent_mod, "get_chat_config", lambda: ("http://x", "k", "m")
    )
    with factory() as s:
        project, _frame = _project_and_frame(s, user)

        def step(messages, base_url, api_key, model):
            return (
                {"role": "assistant", "content": "partial text",
                 "finish_reason": "length"},
                {"total": 5},
                [],
            )

        events = list(
            agent_mod._cloud_loop(step, "hi", project.id, s, user, None, history=[])
        )
        result = next(e for e in events if e["type"] == "result")
        assert "continue" in result["reply"]
