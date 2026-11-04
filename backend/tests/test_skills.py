"""Studio craft skills: discovery, safe reading, genre grammar, wiring."""

import pytest
from conftest import create_user
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from komaverse.agent import (
    _parse_skill_name,
    run_agent_turn,
    tool_list_skills,
    tool_list_tools,
    tool_read_skill,
    tool_web_fetch,
    tool_web_search,
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


def test_agent_skill_names_resolve_in_repo(monkeypatch):
    """Every skill the agent prompt names must be readable in this checkout.

    Regression: the prompt named anime-animation-production while the
    directory was anime-animation, so read_skill failed at runtime and
    no hermetic test noticed (they use a fixture skills dir).
    """
    import komaverse.agent as agent_mod

    monkeypatch.delenv("KOMAVERSE_SKILLS_DIR", raising=False)
    from komaverse.skills import list_skills, read_skill, skills_dir

    root = skills_dir()
    if root is None:
        pytest.skip("no repo skills checkout")
    names = {s["name"] for s in list_skills()}
    prompt = agent_mod._SYSTEM
    for ref in ("anime-animation-production", "drawing-fundamentals", "masters-craft"):
        assert ref in prompt, f"agent prompt should name {ref}"
        assert ref in names, f"skill {ref} missing from {root}"
        assert len(read_skill(ref)) > 500
    masters = read_skill("masters-craft")
    assert "## Manga masters" in masters and "## Animation masters" in masters
    for name in ("Kirby", "Otomo", "Kanada", "Kon", "ligne claire"):
        assert name in masters
    assert "never reproduce protected characters" in masters.lower()
    anime = read_skill("anime-animation-production")
    assert "cel-shaded" in anime and "Shot planning" in anime
    drawing = read_skill("drawing-fundamentals")
    assert "G-pen" in drawing and "anime proportions" in drawing
    assert "Pencil owns construction" in drawing


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
        # A name absent from every skills root still rejects.
        read_skill("no-such-skill")


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


def test_list_tools_names_key_tools():
    out = tool_list_tools()
    for name in (
        "create_image",
        "add_clip",
        "list_skills",
        "read_skill",
        "web_fetch",
        "list_tools",
    ):
        assert name in out


class _FakeResp:
    def __init__(
        self,
        text,
        url="https://example.com/x",
        status=200,
        ctype="text/html",
        json_data=None,
    ):
        self.text = text
        self.url = url
        self.status_code = status
        self.headers = {"content-type": ctype}
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


DDG_HTML = (
    '<a class="result__a" href="//duckduckgo.com/l/?uddg='
    'https%3A%2F%2Fexample.com%2Fcel&amp;rut=xx">Cel shading guide</a>'
    '<td class="result-snippet">Flat colors, hard shadows.</td>'
    '<a class="result__a" href="https://example.com/b">Other page</a>'
)

WIKI_JSON = [
    "cel shading",
    ["Cel shading"],
    ["Toon shading primer."],
    ["https://en.wikipedia.org/wiki/Cel_shading"],
]

INSTANT_JSON = {
    "AbstractText": "Cel shading renders 3D as flat cartoon.",
    "AbstractURL": "https://example.com/abstract",
    "Image": "/i/cel-shading.jpg",
    "RelatedTopics": [
        {"Text": "Outline control", "FirstURL": "https://example.com/outline"}
    ],
}

PAGEIMAGES_JSON = {
    "query": {
        "pages": {
            "42": {
                "pageid": 42,
                "title": "Cel shading",
                "thumbnail": {
                    "source": "https://upload.wikimedia.org/cel-500.jpg",
                    "width": 500,
                    "height": 300,
                },
            }
        }
    }
}


def _fake_search_get(url="", params=None, **kwargs):
    url = str(url)
    if isinstance(params, dict) and params.get("prop") == "pageimages":
        return _FakeResp("", url=url, ctype="application/json", json_data=PAGEIMAGES_JSON)
    if "html.duckduckgo.com" in url:
        return _FakeResp(DDG_HTML, url=url)
    if "api.duckduckgo.com" in url:
        return _FakeResp("", url=url, ctype="application/json", json_data=INSTANT_JSON)
    if "wikipedia.org" in url:
        return _FakeResp("", url=url, ctype="application/json", json_data=WIKI_JSON)
    return _FakeResp("", url=url, status=404)


def test_web_fetch_reads_page(monkeypatch):
    import komaverse.agent as agent_mod

    html = (
        "<html><head><title>T</title></head><body>"
        "<script>var x = 1;</script><p>Hello <b>world</b></p></body></html>"
    )
    monkeypatch.setattr(agent_mod.httpx, "get", lambda *a, **k: _FakeResp(html))
    out = tool_web_fetch("https://example.com/x")
    assert "Hello world" in out
    assert "var x" not in out
    assert "from https://example.com/x" in out


def test_web_fetch_rejects_and_fails(monkeypatch):
    import komaverse.agent as agent_mod

    assert "http(s)" in tool_web_fetch("ftp://x")
    assert "http(s)" in tool_web_fetch("")
    monkeypatch.setattr(
        agent_mod.httpx, "get", lambda *a, **k: _FakeResp("", status=404)
    )
    assert "HTTP 404" in tool_web_fetch("https://example.com/missing")

    def boom(*a, **k):
        raise TimeoutError("slow")

    monkeypatch.setattr(agent_mod.httpx, "get", boom)
    assert "TimeoutError" in tool_web_fetch("https://example.com/x")
    monkeypatch.setattr(
        agent_mod.httpx, "get", lambda *a, **k: _FakeResp("x" * 5000)
    )
    assert "truncated" in tool_web_fetch("https://example.com/x", max_chars=100)


def test_web_images_wikipedia_thumbnails(monkeypatch):
    """Image search returns keyless Wikipedia thumbnails with sources."""
    import komaverse.agent as agent_mod
    from komaverse.agent import tool_web_images

    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    monkeypatch.setattr(
        agent_mod.httpx, "post", lambda *a, **k: _FakeResp("", ctype="application/json", json_data={"data": {}})
    )
    out = tool_web_images("cel shading")
    assert "https://upload.wikimedia.org/cel-500.jpg" in out
    assert "en.wikipedia.org" in out
    assert "bare https URLs" in out


def test_web_images_uses_instant_image(monkeypatch):
    """The DDG instant-answer image rides along when present."""
    import komaverse.agent as agent_mod
    from komaverse.agent import tool_web_images

    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    monkeypatch.setattr(
        agent_mod.httpx, "post", lambda *a, **k: _FakeResp("", ctype="application/json", json_data={"data": {}})
    )
    out = tool_web_images("cel shading")
    assert "https://duckduckgo.com/i/cel-shading.jpg" in out


def test_web_fetch_extracts_og_image(monkeypatch):
    """Fetched pages name their main image so the chat can show it."""
    import komaverse.agent as agent_mod

    html = (
        "<html><head>"
        '<meta property="og:image" content="https://example.com/hero.jpg">'
        "</head><body><p>Body text</p></body></html>"
    )
    monkeypatch.setattr(agent_mod.httpx, "get", lambda *a, **k: _FakeResp(html))
    out = tool_web_fetch("https://example.com/x")
    assert "Body text" in out
    assert "[image: https://example.com/hero.jpg]" in out


FANDOM_JSON = {
    "query": {
        "pages": {
            "7": {
                "title": "Roronoa Zoro",
                "thumbnail": {
                    "source": "https://static.wikia.nocookie.net/onepiece/images/zoro.png",
                },
            }
        }
    }
}


def test_web_images_uses_fandom_wiki_thumbnails(monkeypatch):
    """Character wikis (no free lead image on Wikipedia) thumbnail via
    their Fandom MediaWiki API."""
    import komaverse.agent as agent_mod
    from komaverse.agent import tool_web_images

    def fake_get(url="", params=None, **kwargs):
        if (
            isinstance(params, dict)
            and params.get("prop") == "pageimages"
            and "fandom.com" in str(url)
        ):
            return _FakeResp("", url=url, ctype="application/json", json_data=FANDOM_JSON)
        return _fake_search_get(url, params, **kwargs)

    monkeypatch.setattr(agent_mod.httpx, "get", fake_get)
    monkeypatch.setattr(
        agent_mod.httpx, "post", lambda *a, **k: _FakeResp("", ctype="application/json", json_data={"data": {}})
    )
    monkeypatch.setattr(
        agent_mod,
        "_search_duckduckgo_web",
        lambda q, limit=3: [
            ("Roronoa Zoro", "https://onepiece.fandom.com/wiki/Roronoa_Zoro", "swordsman")
        ],
    )
    out = tool_web_images("Roronoa Zoro")
    assert "static.wikia.nocookie.net" in out
    assert "fandom.com/wiki/Roronoa_Zoro" in out


ANILIST_JSON = {
    "data": {
        "Character": {
            "name": {"full": "Zoro Roronoa"},
            "image": {"large": "https://s4.anilist.co/file/anilistcdn/character/large/b62-x.png"},
            "siteUrl": "https://anilist.co/character/62",
        }
    }
}

COMMONS_JSON = {
    "query": {
        "pages": {
            "11": {
                "title": "File:Mount Fuji at dawn.jpg",
                "imageinfo": [{"thumburl": "https://thumb.wikimedia.org/fuji-500.jpg"}],
            }
        }
    }
}


def test_web_images_uses_anilist_character_art(monkeypatch):
    """Anime characters resolve to direct AniList CDN art first."""
    import komaverse.agent as agent_mod
    from komaverse.agent import tool_web_images

    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    monkeypatch.setattr(
        agent_mod.httpx,
        "post",
        lambda *a, **k: _FakeResp("", ctype="application/json", json_data=ANILIST_JSON),
    )
    out = tool_web_images("Roronoa Zoro")
    assert "https://s4.anilist.co/file/anilistcdn/character/large/b62-x.png" in out
    assert "anilist.co/character/62" in out


def test_web_images_ignores_empty_anilist_match(monkeypatch):
    """A null AniList Character (non-anime query) fails soft to other sources."""
    import komaverse.agent as agent_mod
    from komaverse.agent import _search_anilist_character

    monkeypatch.setattr(
        agent_mod.httpx,
        "post",
        lambda *a, **k: _FakeResp("", ctype="application/json", json_data={"data": {"Character": None}}),
    )
    assert _search_anilist_character("Mount Fuji") == []


def test_search_commons_images_returns_thumbs(monkeypatch):
    """Commons file search yields thumb URLs with source pages."""
    import komaverse.agent as agent_mod
    from komaverse.agent import _search_commons_images

    def fake_get(url="", params=None, **kwargs):
        assert "commons.wikimedia.org" in str(url)
        return _FakeResp("", url=url, ctype="application/json", json_data=COMMONS_JSON)

    monkeypatch.setattr(agent_mod.httpx, "get", fake_get)
    out = _search_commons_images("Mount Fuji")
    assert out[0][1] == "https://thumb.wikimedia.org/fuji-500.jpg"
    assert "commons.wikimedia.org" in out[0][2]


def test_web_images_collects_every_fandom_hit(monkeypatch):
    """All Fandom results are thumbnailed, not just the first wiki hit."""
    import komaverse.agent as agent_mod
    from komaverse.agent import tool_web_images

    def fake_get(url="", params=None, **kwargs):
        url = str(url)
        if isinstance(params, dict) and params.get("prop") == "pageimages" and "fandom.com" in url:
            wiki = url.split("https://")[-1].split(".fandom.com")[0]
            payload = {
                "query": {
                    "pages": {
                        "7": {
                            "title": "Zoro",
                            "thumbnail": {"source": f"https://static.wikia.nocookie.net/{wiki}/zoro.png"},
                        }
                    }
                }
            }
            return _FakeResp("", url=url, ctype="application/json", json_data=payload)
        return _fake_search_get(url, params, **kwargs)

    monkeypatch.setattr(agent_mod.httpx, "get", fake_get)
    monkeypatch.setattr(
        agent_mod.httpx, "post", lambda *a, **k: _FakeResp("", ctype="application/json", json_data={"data": {}})
    )
    monkeypatch.setattr(
        agent_mod,
        "_search_duckduckgo_web",
        lambda q, limit=8: [
            ("Zoro OP", "https://onepiece.fandom.com/wiki/Zoro", "swordsman"),
            ("Zoro XY", "https://otherwiki.fandom.com/wiki/Zoro", "other"),
        ],
    )
    out = tool_web_images("Zoro", 5)
    assert "https://static.wikia.nocookie.net/onepiece/zoro.png" in out
    assert "https://static.wikia.nocookie.net/otherwiki/zoro.png" in out


def test_web_fetch_retries_browser_ua_on_403(monkeypatch):
    """Bot-walled pages (Fandom wiki) load on a browser-UA retry."""
    import komaverse.agent as agent_mod

    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs.get("headers", {}).get("User-Agent", ""))
        if len(calls) == 1:
            return _FakeResp("", url=url, status=403)
        return _FakeResp("<p>Zoro the swordsman</p>", url=url)

    monkeypatch.setattr(agent_mod.httpx, "get", fake_get)
    out = tool_web_fetch("https://onepiece.fandom.com/wiki/Roronoa_Zoro")
    assert "Zoro the swordsman" in out
    assert len(calls) == 2
    assert "Mozilla" in calls[1]


def test_web_search_points_visual_subjects_to_images():
    """Text research on visual subjects must mention web_images."""
    from komaverse import agent as agent_mod

    descs = {
        f["function"]["name"]: f["function"].get("description", "")
        for f in agent_mod._TOOLS_SCHEMA
        if f.get("type") == "function"
    }
    assert "web_images" in descs["web_search"]
    assert "fandom" in descs["web_search"]


def test_web_fetch_rejects_binary(monkeypatch):
    import komaverse.agent as agent_mod

    monkeypatch.setattr(
        agent_mod.httpx,
        "get",
        lambda *a, **k: _FakeResp("PNG...", ctype="image/png"),
    )
    assert "not a text page" in tool_web_fetch("https://example.com/i.png")


def test_demo_lists_tools_and_fetches(factory, monkeypatch):
    import komaverse.agent as agent_mod

    monkeypatch.setattr(
        agent_mod.httpx, "get", lambda *a, **k: _FakeResp("<p>Docs body</p>")
    )
    with factory() as s:
        user = create_user(s)
        from komaverse.api.scans import ProjectCreate, create_project

        project = create_project(ProjectCreate(name="S"), s, user)
        out = run_agent_turn("list tools", project.id, s, user)
        assert "web_fetch" in out["reply"]
        out = run_agent_turn(
            "fetch https://example.com/docs for me", project.id, s, user
        )
        assert "Docs body" in out["reply"]


def test_demo_shows_images(factory, monkeypatch):
    """'Show me images of X' answers with renderable thumbnails."""
    import komaverse.agent as agent_mod

    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    with factory() as s:
        user = create_user(s)
        from komaverse.api.scans import ProjectCreate, create_project

        project = create_project(ProjectCreate(name="S"), s, user)
        out = run_agent_turn("show me images of cel shading", project.id, s, user)
    assert "upload.wikimedia.org" in out["reply"]


def test_web_search_prefers_web_hits(monkeypatch):
    import komaverse.agent as agent_mod

    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    out = tool_web_search("cel shading tutorial")
    assert "Cel shading guide" in out
    assert "https://example.com/cel" in out
    assert "Flat colors" in out
    assert "renders 3D as flat cartoon" in out
    assert "web_fetch" in out


def test_web_search_falls_back_to_wiki(monkeypatch):
    import komaverse.agent as agent_mod

    def empty_web(url="", params=None, **kwargs):
        if "html.duckduckgo.com" in str(url) or "api.duckduckgo.com" in str(url):
            return _FakeResp("", url=str(url))
        return _fake_search_get(url, params, **kwargs)

    monkeypatch.setattr(agent_mod.httpx, "get", empty_web)
    out = tool_web_search("cel shading")
    assert "Cel shading" in out
    assert "en.wikipedia.org" in out
    assert "needs a query" in tool_web_search("")


def test_demo_searches_web(factory, monkeypatch):
    import komaverse.agent as agent_mod

    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    with factory() as s:
        user = create_user(s)
        from komaverse.api.scans import ProjectCreate, create_project

        project = create_project(ProjectCreate(name="S"), s, user)
        out = run_agent_turn("search the web for cel shading", project.id, s, user)
        assert "Cel shading guide" in out["reply"]


def test_mcp_web_search(factory, monkeypatch):
    import komaverse.agent as agent_mod

    tools = {t["name"]: t for t in TOOLS()}
    assert "web_search" in tools
    assert tools["web_search"]["inputSchema"]["required"] == ["query"]
    monkeypatch.setattr(agent_mod.httpx, "get", _fake_search_get)
    with factory() as s:
        user = create_user(s)
        assert "Cel shading guide" in call_tool(
            "web_search", {"query": "cel shading"}, s, user
        )


def test_mcp_list_tools_and_fetch(factory, monkeypatch):
    import komaverse.agent as agent_mod

    tools = {t["name"]: t for t in TOOLS()}
    assert "list_tools" in tools and "web_fetch" in tools
    assert tools["web_fetch"]["inputSchema"]["required"] == ["url"]
    monkeypatch.setattr(
        agent_mod.httpx, "get", lambda *a, **k: _FakeResp("<p>Hi</p>")
    )
    with factory() as s:
        user = create_user(s)
        assert "web_fetch" in call_tool("list_tools", {}, s, user)
        assert "Hi" in call_tool(
            "web_fetch", {"url": "https://example.com/x"}, s, user
        )


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
