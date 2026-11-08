"""Media pipeline prerequisites: pinned ffmpeg with libx264, and a clear
XTTS lazy-load failure (no silent hang, no bare traceback to the client).
"""

import sys
from pathlib import Path

import pytest

import komaverse.ai.voice_local as voice_local
from komaverse.ai.providers import ProviderError
from komaverse.renderer import ffmpeg_info


def test_ffmpeg_bundled_with_libx264():
    info = ffmpeg_info()
    assert set(info) == {"exe", "ok", "libx264", "libass", "version"}
    assert info["ok"] is True
    assert info["libx264"] is True
    assert Path(info["exe"]).is_file()


def test_xtts_missing_package_names_the_fix(monkeypatch):
    """Without coqui-tts installed, loading XTTS must say exactly that."""
    monkeypatch.setitem(sys.modules, "TTS", None)
    monkeypatch.setattr(voice_local, "_xtts", None)
    monkeypatch.setattr(voice_local, "_xtts_ready", False)
    with pytest.raises(ProviderError, match="coqui-tts"):
        voice_local._get_xtts()
