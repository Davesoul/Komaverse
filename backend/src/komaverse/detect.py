"""Panel detectors: heuristic (OpenCV, always available) + cloud (M4 provider).

Heuristic is a compact port of the legacy decomposer cascade idea:
threshold → contours → geometric filter → IoU dedupe → reading order
→ whole-page fallback.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import cv2

from komaverse.ai.providers import CloudProvider, ProviderError

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


class CloudDetector:
    """Vision-API detector — delegates to the M4 cloud provider."""

    name = "cloud"

    def detect(self, image: Path, direction: str = "rtl") -> list[Box]:
        try:
            raw = CloudProvider().detect_panels(image)
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


def run_detector(
    name: str, image: Path, direction: str = "rtl"
) -> tuple[list[Box], str, bool]:
    """Run detector; cloud failures fall back to heuristic. Returns (boxes, used, fell_back)."""
    if name == "cloud":
        try:
            return CloudDetector().detect(image, direction), "cloud", False
        except DetectorUnavailable as exc:
            logger.warning("cloud detector unavailable (%s); using heuristic", exc)
            return HeuristicDetector().detect(image, direction), "heuristic", True
    if name == "heuristic":
        return HeuristicDetector().detect(image, direction), "heuristic", False
    raise ValueError(f"unknown detector: {name}")
