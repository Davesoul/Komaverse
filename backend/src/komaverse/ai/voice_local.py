"""Local free TTS voices, vendored into the studio (no second app).

Approach learned from free-tts (the author's standalone project): lazy
engine imports, first-available auto-pick, WAV intermediates, 192k MP3
output. Differences: MP3 encoding uses the imageio-ffmpeg bundled binary
(free-tts shells PATH `ffmpeg`), WAV writing uses stdlib `wave` (no
soundfile dep), and Coqui XTTSv2 cloning is NOT vendored (GBs of weights;
asking for it fails loudly with the reason).

Voice ids are provider-shaped "engine:voice" (e.g. "kokoro:af_sarah");
a bare name means Kokoro. Engines: kokoro (pip: pykokoro), espeak-ng
(system binary), pyttsx3 (pip: pyttsx3, OS voices).
"""

from __future__ import annotations

import concurrent.futures
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from komaverse.ai.providers import ProviderError
from komaverse.config import settings

# Kokoro voice ids, verbatim from the free-tts known-good list: they are
# the named set Kokoro itself ships, so they double as our picker catalog.
FREETTS_KNOWN_VOICES = (
    "af_alloy", "af_ameliaearhart", "af_aoede", "af_bella", "af_heart",
    "af_jessica", "af_kore", "af_libritts5338", "af_nicole", "af_nova",
    "af_river", "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric",
    "am_fenrir", "am_liam", "am_libritts1272", "am_libritts6241",
    "am_michael", "am_onyx", "am_puck", "am_santa", "am_vincentprice",
    "bf_alice", "bf_emma", "bf_isabella", "bf_janegoodall", "bf_lily",
    "bm_daniel", "bm_davidattenborough", "bm_fable", "bm_george",
    "bm_lewis", "ef_dora", "em_alex", "em_santa", "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi", "if_sara", "im_nicola",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    "pf_dora", "pm_alex", "pm_santa", "zf_xiaobei", "zf_xiaoni",
    "zf_xiaoxiao", "zf_xiaoyi", "zm_yunjian", "zm_yunxi", "zm_yunxia",
    "zm_yunyang",
)

NAMED_ENGINES = ("kokoro",)
LOCAL_ENGINES = ("kokoro", "espeak-ng", "pyttsx3")


def parse_freetts_voice(value: str | None) -> tuple[str, str]:
    """Split a free voice id ("engine:voice") into its parts.

    Blank falls back to the studio default; a bare name without a colon
    is a Kokoro voice on the default engine.
    """
    raw = (value or "").strip() or settings.freetts_voice_model
    if ":" in raw:
        engine, voice = raw.split(":", 1)
        return (engine.strip() or "kokoro", voice.strip() or "af_sarah")
    return ("kokoro", raw.strip() or "af_sarah")


def _kokoro_available() -> bool:
    try:
        import pykokoro  # noqa: F401

        return True
    except Exception:
        return False


def _espeak_exe() -> str | None:
    return shutil.which("espeak-ng")


def _pyttsx3_available() -> bool:
    try:
        import pyttsx3  # noqa: F401

        return True
    except Exception:
        return False


def available_engines() -> list[tuple[str, bool]]:
    """(engine, usable-now) pairs for diagnostics and tests."""
    return [
        ("kokoro", _kokoro_available()),
        ("espeak-ng", _espeak_exe() is not None),
        ("pyttsx3", _pyttsx3_available()),
    ]


def _engine_usable(engine: str) -> bool:
    """Whether a named engine can speak right now (no installs)."""
    if engine == "kokoro":
        return _kokoro_available()
    if engine == "espeak-ng":
        return _espeak_exe() is not None
    if engine == "pyttsx3":
        return _pyttsx3_available()
    return False


def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> Path:
    """WAV -> 192k MP3 via the bundled ffmpeg (no PATH dependency)."""
    proc = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-v",
            "error",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise ProviderError(f"mp3 encode failed: {(proc.stderr or '').strip()[-200:]}")
    return mp3_path


def _write_wav_mono(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """float32 mono/stereo samples -> 16-bit PCM WAV (stdlib, no dep)."""
    data = np.asarray(audio, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=-1)
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(int(sample_rate) or 24000)
        fh.writeframes(pcm.tobytes())


def _run_kokoro(text: str, voice: str, lang: str, mp3_path: Path) -> None:
    try:
        from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig
    except Exception as exc:
        raise ProviderError(
            "kokoro voice needs `pip install pykokoro` in the backend "
            f"environment ({exc})"
        ) from exc
    pipeline = KokoroPipeline(
        PipelineConfig(voice=voice or "af_sarah", generation=GenerationConfig(lang=lang or "en"))
    )
    res = pipeline.run(text)
    wav_path = mp3_path.with_suffix(".wav")
    try:
        _write_wav_mono(wav_path, res.audio, res.sample_rate)
        _wav_to_mp3(wav_path, mp3_path)
    finally:
        wav_path.unlink(missing_ok=True)


def _run_espeak(text: str, mp3_path: Path) -> None:
    exe = _espeak_exe()
    if exe is None:
        raise ProviderError(
            "espeak-ng voice needs the espeak-ng binary "
            "(https://github.com/espeak-ng/espeak-ng/releases)"
        )
    wav_path = mp3_path.with_suffix(".wav")
    try:
        proc = subprocess.run(
            [exe, "-w", str(wav_path), text],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0 or not wav_path.is_file():
            raise ProviderError(
                f"espeak-ng failed: {(proc.stderr or '').strip()[-200:]}"
            )
        _wav_to_mp3(wav_path, mp3_path)
    finally:
        wav_path.unlink(missing_ok=True)


def _run_pyttsx3(text: str, mp3_path: Path) -> None:
    try:
        import pyttsx3
    except Exception as exc:
        raise ProviderError(
            f"system voice needs `pip install pyttsx3` ({exc})"
        ) from exc
    try:
        import pythoncom
    except Exception:
        pythoncom = None  # non-Windows drivers don't need COM
    wav_path = mp3_path.with_suffix(".wav")
    try:

        def speak() -> None:
            # SAPI demands a single-threaded apartment. Uvicorn reuses
            # worker threads, and any prior MTA init there makes SAPI
            # fail with "Cannot change thread mode" — so speak on a
            # dedicated fresh thread with our own STA init, never on the
            # caller's (possibly poisoned) thread.
            if pythoncom is not None:
                try:
                    pythoncom.CoInitialize()
                except Exception:
                    pass
            try:
                engine = pyttsx3.init()
                engine.save_to_file(text, str(wav_path))
                engine.runAndWait()
                try:
                    engine.stop()
                except Exception:
                    pass
            finally:
                if pythoncom is not None:
                    try:
                        pythoncom.CoUninitialize()
                    except Exception:
                        pass

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="kv-tts"
        ) as pool:
            future = pool.submit(speak)
            try:
                future.result(timeout=300)
            except concurrent.futures.TimeoutError as exc:
                raise ProviderError("system TTS timed out") from exc
            except ProviderError:
                raise
            except Exception as exc:
                msg = f"system voice failed: {exc}"
                if "pywintypes" in str(exc):
                    # pywin32 landed after the backend started: a running
                    # process can't see newly installed packages.
                    msg += " — restart the backend after installing voice packages"
                raise ProviderError(msg) from exc
        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            raise ProviderError("system TTS produced no audio")
        _wav_to_mp3(wav_path, mp3_path)
    finally:
        wav_path.unlink(missing_ok=True)


def _auto_engine() -> str:
    if _kokoro_available():
        return "kokoro"
    if _espeak_exe() is not None:
        return "espeak-ng"
    if _pyttsx3_available():
        return "pyttsx3"
    raise ProviderError(
        "no local voice engine: `pip install pykokoro` (best), or install "
        "the espeak-ng binary, or `pip install pyttsx3` (system voices)"
    )


def synthesize_local(
    text: str,
    *,
    engine: str | None = None,
    voice: str | None = None,
    lang: str = "en",
) -> bytes:
    """Text -> MP3 bytes from the vendored local engines (no network).

    No preference (blank engine AND voice) honors the Brain voice
    default when its engine is usable, else auto-picks the first usable
    engine. An explicit engine is honored strictly; a bare voice name
    implies Kokoro. "coqui-clone" refuses: cloning isn't vendored.
    """
    body = (text or "").strip()
    if not body:
        raise ProviderError("nothing to speak: text is empty")
    want_engine = (engine or "").strip().lower()
    want_voice = (voice or "").strip()
    if want_engine in ("", "auto") and not want_voice:
        default_engine, default_voice = parse_freetts_voice(None)
        if _engine_usable(default_engine):
            resolved_engine, resolved_voice = default_engine, default_voice
        else:
            resolved_engine = _auto_engine()
            resolved_voice = "af_sarah" if resolved_engine == "kokoro" else ""
    elif want_engine in ("", "auto"):
        resolved_engine, resolved_voice = parse_freetts_voice(want_voice)
    else:
        resolved_engine, resolved_voice = want_engine, want_voice
    if resolved_engine == "coqui-clone":
        raise ProviderError(
            "voice cloning (coqui-clone) isn't vendored into the studio — "
            "pick a Kokoro voice or a system engine"
        )
    if resolved_engine not in LOCAL_ENGINES:
        raise ProviderError(
            f'unknown voice engine "{resolved_engine}" '
            f"(want: {', '.join(LOCAL_ENGINES)}, coqui-clone)"
        )
    with tempfile.TemporaryDirectory() as tmp:
        mp3_path = Path(tmp) / "voice.mp3"
        if resolved_engine == "kokoro":
            _run_kokoro(body, resolved_voice, lang, mp3_path)
        elif resolved_engine == "espeak-ng":
            _run_espeak(body, mp3_path)
        else:
            _run_pyttsx3(body, mp3_path)
        data = mp3_path.read_bytes()
    if not data:
        raise ProviderError(f"{resolved_engine} returned empty audio")
    return data


def list_freetts_voices() -> tuple[list[dict], str]:
    """(voices, "local"): the vendored Kokoro catalog for the picker."""
    return (
        [
            {"id": f"kokoro:{name}", "label": name, "maker": "Kokoro"}
            for name in FREETTS_KNOWN_VOICES
        ],
        "local",
    )
