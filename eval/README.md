# eval/

Small, honest benchmarks for the hackathon "eval + benchmarks" gap. Each
script prints a JSON report, writes nothing to the repo, and exits nonzero
when its floor is breached — so CI can run them as gates, not theatre.

## Scripts

| Script | What it measures | Pass floor |
|---|---|---|
| `detect_f1.py` | Heuristic panel detector precision/recall/F1 on 4 synthetic pages (exact ground truth) at IoU ≥ 0.5, plus per-case latency | F1 ≥ 0.5 |
| `export_latency.py` | Wall time of a minimal MP4 export (1 scene, 1 text clip, 160×120 @ 5 fps) through the real UI export path | finishes, ≤ 300 s (hang guard; expect seconds) |

## Run

```powershell
cd backend
$env:PYTHONPATH = "src"
python ../eval/detect_f1.py
python ../eval/export_latency.py
```

## Thresholds

Floors are deliberately easy: they catch regressions and hangs, not
quality debates. Tighten them only with measured local numbers — never to
make a badge look good. Record new baselines in `benchmarks/`.

## Extending

- SAM / cloud / YOLO detectors need keys or weights, so CI only covers the
  always-available heuristic path. To score a vision detector, add a case
  that skips when its env key is absent (see `test_scans.py` skip style).
- Export sizes/FPS are minimal on purpose: the check is "the pipeline
  works end-to-end and finishes", not a render farm benchmark. Profile
  real projects with `/metrics` and the export report endpoint instead.
