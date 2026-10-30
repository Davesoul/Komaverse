"""Panel detectors: heuristic (OpenCV, always available) + YOLO (local
weights, best quality) + cloud (M4 provider).

Heuristic is a compact port of the legacy decomposer cascade idea:
threshold → contours → geometric filter → IoU dedupe → reading order
→ whole-page fallback. YOLO ports the legacy YOLOPanelDetector single-shot
path (its SAHI tiling bugs left behind).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

import cv2
from PIL import Image, ImageOps, UnidentifiedImageError

from komaverse.ai.providers import (
    CloudProvider,
    MetaProvider,
    OpenRouterProvider,
    ProviderError,
)
from komaverse.config import settings

logger = logging.getLogger("komaverse.detect")


class Box(dict):
    """Panel box in pixels: {x, y, w, h}."""


class DetectorUnavailable(Exception):
    pass


class Detector(Protocol):
    name: str

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        ...


def _iou(a: Box, b: Box) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix, iy = max(0, min(ax2, bx2) - max(a["x"], b["x"])), max(
        0, min(ay2, by2) - max(a["y"], b["y"])
    )
    union = a["w"] * a["h"] + b["w"] * b["h"] - ix * iy
    return ix * iy / union if union > 0 else 0.0


def _dedupe(boxes: list[Box], iou_thresh: float = 0.5) -> list[Box]:
    boxes = sorted(boxes, key=lambda b: b["w"] * b["h"], reverse=True)
    kept: list[Box] = []
    for box in boxes:
        if all(_iou(box, k) < iou_thresh for k in kept):
            kept.append(box)
    return kept


def sort_reading_order(
    boxes: list[Box], page_width: int, direction: str = "rtl"
) -> list[Box]:
    """Manga reading order: group into columns, RTL columns, top-to-bottom rows."""
    if len(boxes) <= 1:
        return boxes
    ordered = sorted(
        boxes,
        key=lambda b: ((-b["x"], b["y"]) if direction == "rtl" else ((b["x"], b["y"]))),
    )
    columns: list[list[Box]] = []
    for box in ordered:
        for col in columns:
            if abs(box["x"] - col[0]["x"]) < page_width * 0.1:
                col.append(box)
                break
        else:
            columns.append([box])
    out: list[Box] = []
    for col in columns:
        col.sort(key=lambda b: b["y"])
        out.extend(col)
    return out


class HeuristicDetector:
    """Border/content contour detector — no model, no network."""

    name = "heuristic"

    def __init__(
        self,
        min_area_ratio: float = 0.01,
        max_area_ratio: float = 0.9,
        max_aspect: float = 20.0,
    ):
        self.min_area_ratio = min_area_ratio
        self.max_area_ratio = max_area_ratio
        self.max_aspect = max_aspect

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        gray = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise ValueError(f"cannot read image: {image}")
        h, w = gray.shape
        page_area = w * h

        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 51, 9
        )
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        boxes: list[Box] = []
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bw < 20 or bh < 20:
                continue
            area_ratio = (bw * bh) / page_area
            if not (self.min_area_ratio <= area_ratio <= self.max_area_ratio):
                continue
            aspect = max(bw, bh) / max(min(bw, bh), 1)
            if aspect > self.max_aspect:
                continue
            rect_area = max(bw * bh, 1)
            fill = cv2.contourArea(cnt) / rect_area
            approx = cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)
            if fill < 0.3 and len(approx) != 4:
                continue  # neither solid content nor a clean frame
            boxes.append(Box(x=float(x), y=float(y), w=float(bw), h=float(bh)))

        boxes = _dedupe(boxes)
        if not boxes:  # whole-page fallback
            boxes = [Box(x=0.0, y=0.0, w=float(w), h=float(h))]
        return sort_reading_order(boxes, w, direction)


def _vision_detect(provider, image: Path, direction: str = "rtl") -> list[Box]:
    """Shared vision-model path: provider boxes → validated pixel boxes."""
    try:
        raw = provider.detect_panels(image)
    except ProviderError as exc:
        raise DetectorUnavailable(str(exc)) from exc
    boxes = [
        Box(x=float(b["x"]), y=float(b["y"]), w=float(b["w"]), h=float(b["h"]))
        for b in raw
        if b.get("w", 0) > 0 and b.get("h", 0) > 0
    ]
    if not boxes:  # whole-page fallback, same contract as heuristic
        gray = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise DetectorUnavailable(f"cannot read image: {image}")
        h, w = gray.shape
        boxes = [Box(x=0.0, y=0.0, w=float(w), h=float(h))]
    else:
        gray = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
        w = gray.shape[1] if gray is not None else 1000
    # Never trust model ordering — sort server-side like heuristic.
    return sort_reading_order(boxes, w, direction)


class CloudDetector:
    """Vision-API detector — delegates to the generic cloud provider."""

    name = "cloud"

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        return _vision_detect(CloudProvider(), image, direction)


class MetaDetector:
    """Vision-API detector — Meta Muse vision (META_API_KEY)."""

    name = "meta"

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        return _vision_detect(MetaProvider(), image, direction)


class OpenRouterDetector:
    """Vision-API detector — OpenRouter vision model (OPENROUTER_API_KEY)."""

    name = "openrouter"

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        return _vision_detect(OpenRouterProvider(), image, direction)


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _model_path() -> Path:
    """YOLO weights path: KOMA_YOLO_MODEL absolute or backend-root-relative."""
    raw = Path(settings.yolo_model)
    return raw if raw.is_absolute() else _backend_root() / raw


_MODEL_CACHE: dict[str, Any] = {}


def _load_yolo(path: Path) -> Any:
    """Load (and cache) the ultralytics model. Raises DetectorUnavailable."""
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise DetectorUnavailable("ultralytics not installed") from exc
    if not path.is_file():
        raise DetectorUnavailable(f"missing weights: {path}")
    model = _MODEL_CACHE.get(str(path))
    if model is None:
        model = YOLO(str(path))
        _MODEL_CACHE[str(path)] = model
    return model


class YoloDetector:
    """Local YOLO panel detector — best quality, no network.

    Single-shot inference on the shipped panel weights; keeps the frame
    class only (class 0) so text bubbles (class 1) never become panels.
    Same contract as the other detectors: IoU dedupe, whole-page fallback,
    server-side reading order. Unavailable weights/package fall back to
    heuristic via run_detector.
    """

    name = "yolo"
    PANEL_CLASS = 0
    MIN_AREA_RATIO = 0.001

    def __init__(
        self,
        model_path: Path | None = None,
        conf: float = 0.25,
        iou: float = 0.5,
    ):
        self.model_path = model_path or _model_path()
        self.conf = conf
        self.iou = iou

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        model = _load_yolo(self.model_path)
        try:
            img = ImageOps.exif_transpose(Image.open(image)).convert("RGB")
        except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
            raise ValueError(f"cannot read image: {image}") from exc
        w, h = img.size
        results = model(img, verbose=False, conf=self.conf, iou=self.iou)
        raw = results[0].boxes
        found: list[Box] = []
        if raw is not None and len(raw):
            for b in raw:
                if int(b.cls[0]) != self.PANEL_CLASS:
                    continue
                x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
                x1 = max(0.0, min(x1, w - 1))
                y1 = max(0.0, min(y1, h - 1))
                x2 = max(0.0, min(x2, w - 1))
                y2 = max(0.0, min(y2, h - 1))
                if x2 <= x1 or y2 <= y1:
                    continue
                if (x2 - x1) * (y2 - y1) / max(w * h, 1) < self.MIN_AREA_RATIO:
                    continue
                found.append(Box(x=x1, y=y1, w=x2 - x1, h=y2 - y1))
        boxes = _dedupe(found)
        if not boxes:  # whole-page fallback, same contract as heuristic
            boxes = [Box(x=0.0, y=0.0, w=float(w), h=float(h))]
        return sort_reading_order(boxes, w, direction)


# Named YOLO weights selectable from the UI. "yolo" follows KOMA_YOLO_MODEL;
# "yolo-leoxs22" pins the small Manga109-s model (fast, finer-grained).
YOLO_LEOXS = "models/leoxs22_yolo26n_panel_detector.pt"


def _run_yolo(name: str, path: Path, image: Path, direction: str):
    try:
        return YoloDetector(model_path=path).detect(image, direction), name, False
    except DetectorUnavailable as exc:
        logger.warning("%s unavailable (%s); using heuristic", name, exc)
        return HeuristicDetector().detect(image, direction), "heuristic", True


def run_detector(
    name: str, image: Path, direction: str = "rtl"
) -> tuple[list[Box], str, bool]:
    """Run detector; yolo/vision failures fall back to heuristic. Returns (boxes, used, fell_back)."""
    if name == "yolo":
        return _run_yolo(name, _model_path(), image, direction)
    if name == "yolo-leoxs22":
        raw = Path(YOLO_LEOXS)
        path = raw if raw.is_absolute() else _backend_root() / raw
        return _run_yolo(name, path, image, direction)
    if name in ("cloud", "meta", "openrouter"):
        detector = {
            "cloud": CloudDetector,
            "meta": MetaDetector,
            "openrouter": OpenRouterDetector,
        }[name]()
        try:
            return detector.detect(image, direction), name, False
        except DetectorUnavailable as exc:
            logger.warning("%s detector unavailable (%s); using heuristic", name, exc)
            return HeuristicDetector().detect(image, direction), "heuristic", True
    if name == "heuristic":
        return HeuristicDetector().detect(image, direction), "heuristic", False
    raise ValueError(f"unknown detector: {name}")
