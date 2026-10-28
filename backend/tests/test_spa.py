"""SPA file resolution: real files served, routes fall back to index.html."""

from komaverse.main import resolve_spa_file


def test_resolve_spa_file(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("idx")
    (dist / "assets" / "a.js").write_text("js")

    assert resolve_spa_file(dist, "").name == "index.html"
    assert resolve_spa_file(dist, "assets/a.js").name == "a.js"
    assert resolve_spa_file(dist, "some/route").name == "index.html"
    assert resolve_spa_file(dist, "../evil").name == "index.html"
