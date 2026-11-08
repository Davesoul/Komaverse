"""Tauri shell least-privilege: one window, no devtools, a real CSP,
and no extra capabilities beyond the default set."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TAURI_DIR = REPO / "apps" / "studio-desktop" / "src-tauri"


def _conf() -> dict:
    return json.loads((TAURI_DIR / "tauri.conf.json").read_text())


def test_single_window_no_devtools():
    windows = _conf()["app"]["windows"]
    assert [w["label"] for w in windows] == ["main"]
    assert windows[0].get("devtools") is False


def test_csp_present_and_tight():
    csp = _conf()["app"]["security"]["csp"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    # Remote API bases are HTTPS-only; plain http stays loopback.
    assert "https:" in csp
    assert "http:" not in csp.replace("http://localhost", "").replace(
        "http://127.0.0.1", ""
    )


def test_capabilities_stay_minimal():
    caps = json.loads((TAURI_DIR / "capabilities" / "default.json").read_text())
    assert caps["permissions"] == ["core:default"]
