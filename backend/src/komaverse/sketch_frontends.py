"""Neural lineart frontends shared by TraceLab and the agent redraw path.

Each ``_*_sketch`` mirrors its upstream repo verbatim (net + preprocessing)
and returns ``(uint8 gray with dark lines on light paper, inverted_flag)``.
Upstream .py files are imported by path from pristine clones outside the
repo; weights resolve via ``$NAME_WEIGHTS`` env vars with local-download
fallbacks and are intentionally NOT vendored. Missing torch/weights/module
raises ValueError (the lab turns it into HTTP 400, the agent into a
tool-facing error).

``run_sketch_frontend`` is the agent entry point: lineart map at input
resolution, ready for the standard ink pipeline.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageEnhance

#: Sketch-model variants (upstream test.py ``--model``).
_A2S_MODELS = ("default", "improved")

#: Sketch-frontend comparison modes: a neural lineart map feeds the
#: standard ink pipeline (shared ordering tail) instead of the photo.
SKETCH_MODES = ("a2s", "mangaline", "anilines", "anilines-detail", "linedistiller")

#: Anime2Sketch inference box (upstream resizes inputs to 512x512).
_A2S_LOAD_SIZE = 512

#: Darkness gain applied to every neural sketch before the ink pipeline
#: (measured on a manga crop: 1.5 keeps ~8% more linework connected
#: than 1.0 without picking up tone noise; 2.4 regresses).
SKETCH_GAIN = 1.5


def _apply_sketch_gain(sk: np.ndarray, gain: float = SKETCH_GAIN) -> np.ndarray:
    """Darken sketch lines (operate on darkness, keep paper white)."""
    if gain == 1.0:
        return sk
    dark = 255.0 - sk.astype(np.float32)
    return (255.0 - np.clip(dark * gain, 0, 255)).astype(np.uint8)


def _a2s_weights_path() -> Path:
    """netG.pth location: $A2S_WEIGHTS, else the local spike download."""
    env = os.environ.get("A2S_WEIGHTS")
    if env:
        return Path(env)
    return Path(r"D:\tmp\a2s\weights\netG.pth")


def _a2s_available_models() -> list[str]:
    """Sketch models whose weight files are installed (drives the UI)."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return []
    base = _a2s_weights_path()
    return [
        m
        for m in _A2S_MODELS
        if (base if m == "default" else base.parent / "improved.bin").is_file()
    ]


_A2S_NET = None
_A2S_NET_KEY: tuple[str, str] | None = None


def _a2s_sketch(
    rgb: Image.Image, *, clahe_clip: float = -1.0, model: str = "default"
) -> tuple[np.ndarray, bool]:
    """Anime2Sketch U-Net lineart at its native 512 box.

    Returns (uint8 gray with dark lines on light paper, inverted_flag).
    The net + preprocessing mirror upstream test.py/data.py (BICUBIC
    resize to 512x512, RGB in [-1, 1]); polarity is normalized adaptively
    since the raw sketch output polarity is not contractual. ``model``
    mirrors upstream's ``--model`` flag (default U-Net vs improved
    resize+conv upsampling); ``clahe_clip`` mirrors ``--clahe_clip``
    (disabled at the -1 default). CLAHE runs on the LAB lightness
    channel — the standard equivalent of upstream's kornia tensor
    equalization, which is not vendored here. Upstream:
    https://github.com/Mukosame/Anime2Sketch
    """
    global _A2S_NET, _A2S_NET_KEY
    if model not in _A2S_MODELS:
        raise ValueError(f"unknown a2s model {model!r} (want default|improved)")
    try:
        import torch
    except ImportError as exc:
        raise ValueError("a2s mode needs torch (pip install torch)") from exc
    weights = _a2s_weights_path()
    if model == "improved":
        weights = weights.parent / "improved.bin"
    if not weights.is_file():
        hint = (
            " (improved/artifact-free weights: GoogleDrive "
            "1cf90_fPW-elGOKu5mTXT5N1dum-XY_46 — save as improved.bin "
            "beside netG.pth)"
            if model == "improved"
            else " (weights: GoogleDrive folder "
            "1Srf-WYUixK0wiUddc9y3pNKHHno5PN6R — put netG.pth next to "
            "model.py and set A2S_WEIGHTS to it)"
        )
        raise ValueError(f"Anime2Sketch weights not found at {weights}{hint}")
    model_py = next(
        (
            candidate
            for parent in (weights.parent, weights.parent.parent)
            for candidate in (parent / "model.py", parent / "weights" / "model.py")
            if candidate.is_file()
        ),
        None,
    )
    if model_py is None:
        raise ValueError(f"Anime2Sketch model.py not found near {weights}")
    key = (str(weights), model)
    if _A2S_NET is None or _A2S_NET_KEY != key:
        import importlib.util

        spec = importlib.util.spec_from_file_location("a2s_model", model_py)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        norm_layer = functools.partial(
            torch.nn.InstanceNorm2d, affine=False, track_running_stats=False
        )
        net = mod.UnetGenerator(3, 1, 8, 64, norm_layer=norm_layer, use_dropout=False)
        if model == "improved":
            # Mirror model.py create_model('improved'): swap the six
            # deconvolution upsamplers for resize+conv blocks.
            base = net.model.model[1]
            for _ in range(6):
                inc, outc = base.model[5].in_channels, base.model[5].out_channels
                base.model[5] = mod.Upsample(inc, outc)
                base = base.model[3]
        ckpt = torch.load(str(weights), map_location="cpu")
        ckpt = {k.replace("module.", ""): v for k, v in ckpt.items()}
        net.load_state_dict(ckpt)
        net.eval()
        _A2S_NET = net
        _A2S_NET_KEY = key
    small = rgb.resize((_A2S_LOAD_SIZE, _A2S_LOAD_SIZE), Image.Resampling.BICUBIC)
    if clahe_clip > 0:
        lab = cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=float(clahe_clip)).apply(lab[:, :, 0])
        small = Image.fromarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB))
    arr = np.asarray(small).astype(np.float32) / 127.5 - 1.0
    tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
    with torch.no_grad():
        out = _A2S_NET(tensor).squeeze().numpy()
    sketch = ((out + 1.0) / 2.0 * 255.0).clip(0, 255).astype(np.uint8)
    inverted = bool(sketch.mean() < 127)
    if inverted:
        sketch = 255 - sketch
    return _apply_sketch_gain(sketch), inverted


def _mangaline_weights_path() -> Path:
    """erika.pth location: $MANGALINE_WEIGHTS, else the local download."""
    env = os.environ.get("MANGALINE_WEIGHTS")
    if env:
        return Path(env)
    return Path(r"D:\tmp\mangaline\weights\erika.pth")


def _anilines_weights_path(variant: str) -> Path:
    """basic/detail .pth location: dir $ANILINES_WEIGHTS, else local download."""
    env = os.environ.get("ANILINES_WEIGHTS")
    base = Path(env) if env else Path(r"D:\tmp\anilines\weights")
    return base / f"{variant}.pth"


def _load_module_file(name: str, path: Path):
    """Import one upstream .py by path (repos stay outside the repo)."""
    import importlib.util

    if not path.is_file():
        raise ValueError(f"upstream module not found at {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_MANGALINE_NET = None
_MANGALINE_KEY: str | None = None

_ANILINES_NET = None
_ANILINES_KEY: tuple[str, str] | None = None


def _mangaline_sketch(gray: Image.Image) -> tuple[np.ndarray, bool]:
    """MangaLineExtraction structural line map at input resolution.

    Returns (uint8 gray with dark lines on light paper, inverted_flag).
    Net + preprocessing mirror model_torch.py __main__ verbatim: raw
    0-255 grayscale (no normalization), ones-padded to a multiple of 16,
    res_skip forward, clipped to 0-255, cropped to the input size. The
    weights (~165MB erika.pth) are intentionally NOT vendored into the
    repo. Upstream: https://github.com/ljsabc/MangaLineExtraction_PyTorch
    """
    try:
        import torch
    except ImportError as exc:
        raise ValueError("mangaline mode needs torch (pip install torch)") from exc
    weights = _mangaline_weights_path()
    if not weights.is_file():
        raise ValueError(
            f"MangaLine weights not found at {weights} "
            "(erika.pth: repo releases, or set $MANGALINE_WEIGHTS)"
        )
    model_py = next(
        (
            candidate
            for parent in (weights.parent, weights.parent.parent)
            for candidate in (
                parent / "model_torch.py",
                Path(r"D:\tmp\upstream-mangaline\model_torch.py"),
            )
            if candidate.is_file()
        ),
        None,
    )
    if model_py is None:
        raise ValueError(f"MangaLine model_torch.py not found near {weights}")
    global _MANGALINE_NET, _MANGALINE_KEY
    key = str(weights)
    if _MANGALINE_NET is None or _MANGALINE_KEY != key:
        mod = _load_module_file("mangaline_model", model_py)
        net = mod.res_skip()
        ckpt = torch.load(str(weights), map_location="cpu")
        net.load_state_dict(ckpt)
        net.eval()
        _MANGALINE_NET = net
        _MANGALINE_KEY = key
    arr = np.asarray(gray.convert("L"), dtype=np.float32)
    h, w = arr.shape
    rows = int(np.ceil(h / 16)) * 16
    cols = int(np.ceil(w / 16)) * 16
    patch = np.ones((1, 1, rows, cols), dtype=np.float32)
    patch[0, 0, 0:h, 0:w] = arr
    with torch.no_grad():
        out = _MANGALINE_NET(torch.from_numpy(patch)).cpu().numpy()[0, 0, :, :]
    sketch = np.clip(out[:h, :w], 0, 255).astype(np.uint8)
    inverted = bool(sketch.mean() < 127)
    if inverted:
        sketch = 255 - sketch
    return _apply_sketch_gain(sketch), inverted


def _anilines_sketch(rgb: Image.Image, *, detail_mode: bool) -> tuple[np.ndarray, bool]:
    """AniLines lineart at input resolution (basic vs detail variant).

    Returns (uint8 gray with dark lines on light paper, inverted_flag).
    Net + preprocessing mirror infer.py inference() verbatim: basic runs
    on sharpened RGB/255, detail on gray + Sobel-magnitude two channels,
    reflect-padded to a multiple of 8, no binarization, fp16 off on CPU.
    The weights (~69MB each) are intentionally NOT vendored into the
    repo. Upstream: https://github.com/IcelandicCenterArtificialIntelligence/AniLines-Anime-Line-Extractor
    """
    variant = "detail" if detail_mode else "basic"
    try:
        import torch
        import torch.nn.functional as torch_F
    except ImportError as exc:
        raise ValueError("anilines mode needs torch (pip install torch)") from exc
    weights = _anilines_weights_path(variant)
    if not weights.is_file():
        raise ValueError(
            f"AniLines {variant} weights not found at {weights} "
            "(GoogleDrive basic/detail .pth, or set $ANILINES_WEIGHTS dir)"
        )
    model_py = next(
        (
            candidate
            for parent in (weights.parent, weights.parent.parent)
            for candidate in (
                parent / "network" / "line_extractor.py",
                Path(r"D:\tmp\upstream-anilines\network\line_extractor.py"),
            )
            if candidate.is_file()
        ),
        None,
    )
    if model_py is None:
        raise ValueError(f"AniLines line_extractor.py not found near {weights}")
    global _ANILINES_NET, _ANILINES_KEY
    key = (str(weights), variant)
    if _ANILINES_NET is None or _ANILINES_KEY != key:
        mod = _load_module_file("anilines_extractor", model_py)
        net = mod.LineExtractor(2 if detail_mode else 3, 1, True)
        ckpt = torch.load(str(weights), map_location="cpu")
        net.load_state_dict(ckpt)
        for p in net.parameters():
            p.requires_grad = False
        net.eval()
        _ANILINES_NET = net
        _ANILINES_KEY = key
    if detail_mode:
        gray = cv2.cvtColor(np.asarray(rgb.convert("RGB")), cv2.COLOR_RGB2GRAY)
        sx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        sobel = 255 - cv2.normalize(
            cv2.magnitude(sx, sy), None, 0, 255, cv2.NORM_MINMAX, cv2.CV_8UC1
        )
        g = torch.from_numpy(gray).unsqueeze(0).unsqueeze(0).float() / 255.0
        s = torch.from_numpy(sobel).unsqueeze(0).unsqueeze(0).float() / 255.0
        x_in = torch.cat([g, s], dim=1)
    else:
        sharp = ImageEnhance.Sharpness(rgb.convert("RGB")).enhance(6.0)
        x_in = torch.from_numpy(np.array(sharp)).permute(2, 0, 1).unsqueeze(0).float() / 255.0
    _b, _c, h, w = x_in.shape
    x_in = torch_F.pad(x_in, (0, 8 - (w % 8), 0, 8 - (h % 8)), mode="reflect")
    with torch.no_grad():
        pred = _ANILINES_NET(x_in)[:, :, :h, :w]
    sketch = np.clip((pred[0, 0].numpy() * 255) + 0.5, 0, 255).astype(np.uint8)
    inverted = bool(sketch.mean() < 127)
    if inverted:
        sketch = 255 - sketch
    return _apply_sketch_gain(sketch), inverted


def _linedistiller_weights_path() -> Path:
    """model_200728.pth location: $LINEDISTILLER_WEIGHTS, else local download."""
    env = os.environ.get("LINEDISTILLER_WEIGHTS")
    if env:
        return Path(env)
    return Path(r"D:\tmp\linedistiller\weights\model_200728.pth")


_LINEDISTILLER_NET = None
_LINEDISTILLER_KEY: str | None = None


def _linedistiller_sketch(rgb: Image.Image) -> tuple[np.ndarray, bool]:
    """LineDistiller lineart at input resolution.

    Returns (uint8 gray with dark lines on light paper, inverted_flag).
    Net + preprocessing mirror the pytorch branch predict.py main()
    verbatim: BGR->RGB, cv2 INTER_LINEAR resize to a multiple of 16
    (upstream uses 8, which crashes the /16-deep skip-adds on some
    sizes), ToTensor + Normalize(0.5/0.5), Net forward, (res+1)/2*255
    resized back to the input size. The weights (~43MB model_200728.pth)
    are intentionally NOT vendored into the repo. Upstream (pytorch
    branch): https://github.com/hepesu/LineDistiller
    """
    try:
        import torch
    except ImportError as exc:
        raise ValueError("linedistiller mode needs torch (pip install torch)") from exc
    weights = _linedistiller_weights_path()
    if not weights.is_file():
        raise ValueError(
            f"LineDistiller weights not found at {weights} "
            "(release 2.0.0 model_200728.pth, or set $LINEDISTILLER_WEIGHTS)"
        )
    model_py = next(
        (
            candidate
            for parent in (weights.parent, weights.parent.parent)
            for candidate in (
                parent / "model.py",
                Path(r"D:\tmp\upstream-linedistiller\model.py"),
            )
            if candidate.is_file()
        ),
        None,
    )
    if model_py is None:
        raise ValueError(f"LineDistiller model.py not found near {weights}")
    global _LINEDISTILLER_NET, _LINEDISTILLER_KEY
    key = str(weights)
    if _LINEDISTILLER_NET is None or _LINEDISTILLER_KEY != key:
        mod = _load_module_file("linedistiller_model", model_py)
        net = mod.Net()
        ckpt = torch.load(str(weights), map_location="cpu")
        net.load_state_dict(ckpt)
        net.eval()
        _LINEDISTILLER_NET = net
        _LINEDISTILLER_KEY = key
    arr = np.asarray(rgb.convert("RGB"))
    h, w = arr.shape[:2]
    # Mult of 16, not upstream's mult of 8: the net halves four times, so
    # mult-of-8 inputs with an odd sixteenth crash the decoder skip-adds.
    # Mult-of-16 inputs run byte-identical to upstream.
    small = cv2.resize(arr, (max(16, w // 16 * 16), max(16, h // 16 * 16)))
    tensor = torch.from_numpy(small).permute(2, 0, 1).unsqueeze(0).float() / 255.0
    tensor = (tensor - 0.5) / 0.5
    with torch.no_grad():
        res = _LINEDISTILLER_NET(tensor)
    out = (res.squeeze(0).permute(1, 2, 0).numpy() + 1) / 2 * 255
    sketch = np.clip(cv2.resize(out, (w, h)), 0, 255).astype(np.uint8)
    if sketch.ndim == 3:
        sketch = sketch[:, :, 0]
    inverted = bool(sketch.mean() < 127)
    if inverted:
        sketch = 255 - sketch
    return _apply_sketch_gain(sketch), inverted


def run_sketch_frontend(mode: str, rgb: Image.Image) -> Image.Image:
    """Neural lineart map at input resolution (agent redraw path).

    Same frontends, same preprocessing, same weights as the lab —
    what the lab shows is what the agent draws.
    """
    if mode == "a2s":
        sk, _inverted = _a2s_sketch(rgb)
    elif mode == "mangaline":
        sk, _inverted = _mangaline_sketch(rgb.convert("L"))
    elif mode == "anilines":
        sk, _inverted = _anilines_sketch(rgb, detail_mode=False)
    elif mode == "anilines-detail":
        sk, _inverted = _anilines_sketch(rgb, detail_mode=True)
    elif mode == "linedistiller":
        sk, _inverted = _linedistiller_sketch(rgb)
    else:
        raise ValueError(f"unknown sketch frontend {mode!r} (want {'|'.join(SKETCH_MODES)})")
    return Image.fromarray(sk)
