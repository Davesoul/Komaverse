"""Studio craft skills: discovery, safe reading, genre grammar, wiring."""

import pytest
from conftest import create_user
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import (
    _parse_skill_name,
    run_agent_turn,
    tool_list_skills,
    tool_read_skill,
)
from komaverse.ai.direction import GENRE_TAGS, build_final_prompt, genre_suffix
from komaverse.config import settings
from komaverse.db_models import Base
from komaverse.mcp_server import TOOLS, call_tool
from komaverse.skills import SkillError, list_skills, read_skill


@pytest.fixture
def skill_root(tmp_path):
    root = tmp_path / "skills"
    anime = root / "anime-animation-production"
    anime.mkdir(parents=True)
    (anime / "SKILL.md").write_text(
        "---\n"
        "name: anime-animation-production\n"
        "description: >-\n"
        "  Produce anime-style and stylized 2D animation.\n"
        "---\n"
        "\n"
        "# Anime\n"
        "\n"
        "## Prompting vocabulary\n"
        "\n"
        "Use cel-shaded tags.\n"
        "\n"
        "## Shot planning\n"
        "\n"
        "Hold then cut.\n",
        encoding="utf-8",
    )
    manga = root / "manga-production"
    manga.mkdir()
    (manga / "SKILL.md").write_text("# Manga\n\nMake pages.\n", encoding="utf-8")
    (root / "empty-dir").mkdir()
    (root / "not-a-skill.txt").write_text("x", encoding="utf-8")
    return root


@pytest.fixture
def factory(tmp_path, monkeypatch, skill_root):
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    monkeypatch.setattr(settings, "ai_api_key", None)  # force demo brain
    monkeypatch.setenv("KOMAVERSE_SKILLS_DIR", str(skill_root))
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def test_list_skills_names_and_descriptions(skill_root):
    found = list_skills(skill_root)
    assert [s["name"] for s in found] == [
        "anime-animation-production",
        "manga-production",
    ]
    assert found[0]["description"] == (
        "Produce anime-style and stylized 2D animation."
    )
    # No frontmatter: falls back to the first content line.
    assert found[1]["description"] == "Make pages."


def test_list_skills_missing_dir(tmp_path):
    assert list_skills(tmp_path / "nope") == []


def test_read_skill_full_and_section(skill_root):
    full = read_skill("anime-animation-production", base=skill_root)
    assert "Shot planning" in full
    assert read_skill(
        "anime-animation-production", section="prompting", base=skill_root
    ) == "Use cel-shaded tags."
    assert read_skill(
        "anime-animation-production",
        section="Shot planning",
        base=skill_root,
    ) == "Hold then cut."


def test_read_skill_rejects(skill_root):
    for bad in ("", "nope", "../x", "a/b", "UPPER", "has space", "."):
        with pytest.raises(SkillError):
            read_skill(bad, base=skill_root)
    with pytest.raises(SkillError):
        read_skill("anime-animation-production", section="nope", base=skill_root)
    with pytest.raises(SkillError):
        read_skill("anime-animation-production")  # no repo skills in tests


def test_genre_tags_cover_skill_genres():
    assert set(GENRE_TAGS) >= {
        "none",
        "shonen",
        "shojo",
        "seinen",
        "slice-of-life",
        "mecha",
        "retro-ova",
    }
    assert "cel-shaded" in genre_suffix("shonen")
    with pytest.raises(ValueError):
        genre_suffix("western")


def test_final_prompt_carries_genre_before_style():
    final = build_final_prompt(
        prompt="a duel", entities=[], style="cel-anime", genre="shonen"
    )
    assert "cel-shaded shonen anime" in final
    assert final.index("shonen") < final.index("studio-quality")
    plain = build_final_prompt(prompt="a duel", entities=[])
    assert "cel-shaded" not in plain


def test_final_prompt_rejects_unknown_genre():
    with pytest.raises(ValueError):
        build_final_prompt(prompt="x", entities=[], genre="western")


def test_agent_skill_tools(factory):
    assert "anime-animation-production" in tool_list_skills()
    assert "manga-production" in tool_list_skills()
    assert "Hold then cut" in tool_read_skill(
        "anime-animation-production", "Shot planning"
    )
    assert "unknown skill" in tool_read_skill("nope")


def test_parse_skill_name():
    assert _parse_skill_name("list skills") is None
    assert _parse_skill_name("what skills do you have") is None
    assert (
        _parse_skill_name("read skill anime-animation-production")
        == "anime-animation-production"
    )
    assert (
        _parse_skill_name("open the manga-production skill please")
        == "manga-production"
    )
    # Bare words never resolve: names are found by their dash.
    assert _parse_skill_name("read skill shonen") is None


def test_demo_brain_lists_and_reads_skills(factory):
    with factory() as s:
        user = create_user(s)
        from komaverse.api.scans import ProjectCreate, create_project

        project = create_project(ProjectCreate(name="S"), s, user)
        out = run_agent_turn("list skills", project.id, s, user)
        assert "anime-animation-production" in out["reply"]
        out = run_agent_turn(
            "read skill anime-animation-production", project.id, s, user
        )
        assert "cel-shaded tags" in out["reply"]


def test_mcp_skill_tools(factory):
    tools = {t["name"]: t for t in TOOLS()}
    assert "list_skills" in tools and "read_skill" in tools
    assert tools["read_skill"]["inputSchema"]["required"] == ["name"]
    with factory() as s:
        user = create_user(s)
        assert "anime-animation-production" in call_tool(
            "list_skills", {}, s, user
        )
        assert "cel-shaded tags" in call_tool(
            "read_skill",
            {"name": "anime-animation-production", "section": "Prompting"},
            s,
            user,
        )
        assert "unknown skill" in call_tool("read_skill", {"name": "nope"}, s, user)
