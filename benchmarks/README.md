# benchmarks/

Recorded results from [`eval/`](../eval/README.md). Each file is one dated
run on stated hardware — a paper trail, not a leaderboard. CI enforces the
floors; this directory keeps the numbers humans quote.

## Latest (2026-11-05, local Windows dev box)

- Detection F1 (heuristic, 4 synthetic pages, IoU ≥ 0.5): **0.933**
  (precision 0.875, recall 1.0 — the single miss is the blank page, where
  the whole-page fallback fires by design).
- Export latency (1 scene, 1 text clip, 160×128 @ 5 fps): **0.18 s**, 2.3 KB MP4.

Full report: [`baseline-2026-11-05.json`](baseline-2026-11-05.json).

## Adding a run

1. `cd backend; $env:PYTHONPATH="src"; python ../eval/detect_f1.py`
   and `python ../eval/export_latency.py`.
2. Save the two JSON outputs as `baseline-YYYY-MM-DD.json` with a
   `machine` field (CPU/RAM/OS) and a one-line note.
3. Never edit an old file — append a new dated one.
