# Roadmap (Oct 2026)

Ranked future work, consolidated from `COMPETITIVE.md`, ADR-8/9 deferred
items, and open gaps found during verification. Shipped items move to
`DECISIONS.md` amendments; this file keeps only what's next.

## P1 — Consistency depth (highest leverage, same APIs)

1. **End frame for video** — `frame_images` already supports `last_frame`;
   expose an optional end-frame picker next to the start frame.
2. **Entity snapshots from media/scans** — entities currently anchor to
   draw frames only; allow any media item or detected panel as the visual.
3. **Upscale / inpaint passes** — second-stage jobs on a generation or frame
   (OpenRouter image edit with `input_references` + "upscale" prompt).
4. **Multi-shot director** — chain start→end frame pairs into longer
   narratives (Kling-style), one job per shot, auto-assembled on V1.

## P2 — Draw depth (match CSP/Krita where it counts)

5. **Vector layers** — resolution-independent ink alongside raster layers.
6. **Rigging + tweening** — bone rigs and interpolated motion between keys,
   so simple moves don't need redrawing.
7. **Full brush engine port** — custom brushes (spacing/jitter/pressure
   curves), deferred since M5; presets already ship.

## P3 — Scale + infra (explicitly deferred in ADR-8/9)

8. **Job queue (RQ)** — exports and AI jobs leave in-process
   BackgroundTasks for ingest + video queues.
9. **Alembic migrations (M3)** — replace the `create_all` + ALTER backfill.
10. **Orphan GC scanner** — reclaim media with no referencing row.
11. **CLIP search** — embedding search over media/generations.
12. **Agent history / approval-gated loops** — chat safety (streaming + live tool cards shipped).

## P4 — Reach + polish (MusArt lessons, cheap goodwill)

13. **Native audio in AI video** — model-side (Kling/Veo); expose when the
    account's catalog supports it.
14. **Light/dark themes, custom shortcuts, FR locale.**
15. **Headless render + artboard batch export** — CLI export, ZIP of boards.
16. **Creation timelapse export** — replay edit history to MP4/WebM.

## Deliberately out of scope

- Fighting the `:8000` ghost holder — dev lives on `:8001`; a reboot
  reclaims the port if ever wanted.
- Real-time multiplayer editing — single-creator tool by design.
- Training custom models — we route to providers, never train.
