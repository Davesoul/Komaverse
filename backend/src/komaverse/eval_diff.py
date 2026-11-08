"""Render-vs-reference scoring: vendored SSIM + MSE, no new deps.

Gaussian-window SSIM (Wang et al. 2004: 11x11 sigma-1.5 window,
K1=0.01, K2=0.03) on top of the already-declared OpenCV + numpy, plus
plain MSE. Compares a stroke render against its reference to drive the
opt-in refine loop; deterministic.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

_SSIM_K1 = 0.01
_SSIM_K2 = 0.03
_SSIM_WIN = 11
_SSIM_SIGMA = 1.5
_SCORE_PX = 256


def _as_gray(image: Image.Image) -> np.ndarray:
    g = image.convert("L").resize((_SCORE_PX, _SCORE_PX), Image.Resampling.LANCZOS)
    return np.asarray(g).astype(np.float64)


def ssim_gray(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM of two equal-shape float64 gray arrays (0..1)."""
    if a.shape != b.shape:
        raise ValueError("ssim needs equal shapes")
    c1, c2 = (255 * _SSIM_K1) ** 2, (255 * _SSIM_K2) ** 2
    mu_a = cv2.GaussianBlur(a, (_SSIM_WIN, _SSIM_WIN), _SSIM_SIGMA)
    mu_b = cv2.GaussianBlur(b, (_SSIM_WIN, _SSIM_WIN), _SSIM_SIGMA)
    mu_a2, mu_b2, mu_ab = mu_a * mu_a, mu_b * mu_b, mu_a * mu_b
    sig_a2 = cv2.GaussianBlur(a * a, (_SSIM_WIN, _SSIM_WIN), _SSIM_SIGMA) - mu_a2
    sig_b2 = cv2.GaussianBlur(b * b, (_SSIM_WIN, _SSIM_WIN), _SSIM_SIGMA) - mu_b2
    sig_ab = cv2.GaussianBlur(a * b, (_SSIM_WIN, _SSIM_WIN), _SSIM_SIGMA) - mu_ab
    num = (2 * mu_ab + c1) * (2 * sig_ab + c2)
    den = (mu_a2 + mu_b2 + c1) * (sig_a2 + sig_b2 + c2)
    return float(np.mean(num / np.maximum(den, 1e-12)))


def mse_gray(a: np.ndarray, b: np.ndarray) -> float:
    """Mean squared error of two equal-shape float64 gray arrays."""
    if a.shape != b.shape:
        raise ValueError("mse needs equal shapes")
    return float(np.mean((a - b) ** 2))


def score_render(render: Image.Image, reference: Image.Image) -> dict:
    """{"ssim", "mse"} of a stroke render vs its reference image.

    Both are grayscaled and resampled to the same scoring box, so any
    frame/reference size pair compares deterministically (keep inputs
    at the same aspect for strict readings).
    """
    ra, rb = _as_gray(render), _as_gray(reference)
    return {"ssim": round(ssim_gray(ra, rb), 4), "mse": round(mse_gray(ra, rb), 2)}
