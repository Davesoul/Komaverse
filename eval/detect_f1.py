"""Panel-detection accuracy: heuristic detector F1 on synthetic pages.

Ground truth is exact (we draw the rects), so this measures the detector,
not the artist. SAM/cloud/YOLO variants need keys or weights and are
skipped here — see eval/README.md. Fails (exit 1) below F1 0.5.

Usage:  cd backend && PYTHONPATH=src python ../eval/detect_f1.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from PIL import Image, ImageDraw  # noqa: E402

from komaverse.detect import HeuristicDetector  # noqa: E402

IOU_MATCH = 0.5
F1_FLOOR = 0.5

CASES = [
    {"name": "single", "rects": [(40, 40, 520, 320)],},
    {"name": "three", "rects": [(40, 40, 520, 320), (40, 400, 300, 360), (360, 400, 200, 360)],},
    {"name": "tall", "rects": [(100, 30, 120, 740), (260, 30, 120, 740), (420, 30, 120, 740)],},
    {"name": "blank", "rects": [],},
]


def iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    union = aw * ah + bw * bh - ix * iy
    return ix * iy / union if union > 0 else 0.0


def draw_case(rects: list[tuple], path: Path) -> None:
    img = Image.new("L", (600, 800), 255)
    d = ImageDraw.Draw(img)
    for x, y, w, h in rects:
        d.rectangle([x, y, x + w, y + h], fill=0)
    img.save(path)


def main() -> int:
    det = HeuristicDetector()
    tp = fp = fn = 0
    per_case = []
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        for case in CASES:
            img = Path(tmp) / f"{case['name']}.png"
            draw_case(case["rects"], img)
            t1 = time.perf_counter()
            got = [(b["x"], b["y"], b["w"], b["h"]) for b in det.detect(img)]
            dt = time.perf_counter() - t1
            want = [tuple(map(float, r)) for r in case["rects"]]
            matched_gt: set[int] = set()
            case_tp = 0
            for g in got:
                best, bj = 0.0, -1
                for j, w in enumerate(want):
                    if j in matched_gt:
                        continue
                    v = iou(g, w)
                    if v > best:
                        best, bj = v, j
                if best >= IOU_MATCH:
                    matched_gt.add(bj)
                    case_tp += 1
            tp += case_tp
            fp += len(got) - case_tp
            fn += len(want) - len(matched_gt)
            per_case.append({
                "case": case["name"],
                "want": len(want),
                "got": len(got),
                "matched": case_tp,
                "seconds": round(dt, 3),
            })
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    report = {
        "detector": "heuristic",
        "iou_threshold": IOU_MATCH,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "seconds_total": round(time.perf_counter() - t0, 3),
        "cases": per_case,
    }
    print(json.dumps(report, indent=2))
    if f1 < F1_FLOOR:
        print(f"FAIL: F1 {f1:.3f} < floor {F1_FLOOR}", file=sys.stderr)
        return 1
    print(f"PASS: F1 {f1:.3f} >= floor {F1_FLOOR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
