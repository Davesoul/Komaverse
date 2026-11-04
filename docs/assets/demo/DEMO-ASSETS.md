# Demo assets — `cleanDemo` showcase set

All files here are **real captures of the live app** (Playwright-driven
Chromium tour + local ffmpeg stitch — the standard repo-to-video playbook
used by tools like `record-demo`/aidemo, RepoClip, PageBolt: drive the app,
screenshot the tour, stitch with ffmpeg). No staged mockups, no AI-credit
spend: the `cleanDemo` project was seeded through the local API only
(`demo@komaverse.local` / `demo1234`, see `docs/DEMO_SCRIPT.md`).

Regenerate any item with the `/tmp/capture_demo.py`, `/tmp/capture5.py`,
`/tmp/seed_clean2.py`, `/tmp/export_clean2.py` scripts against a running
backend (`:8001`) + studio (`:5173`).

| File | What it shows | Use for |
|---|---|---|
| `00-intro.mp4` | Netflix-style ident intro (logo sting + Fingerstyle Impact audio) | README hero, demo video cold open |
| `01-scan.png` | Scan tab — art page, SAM panels, destination-choice popup | README, submission screenshots |
| `02-draw.png` | Draw tab — framed manga page, layers, per-frame ⧉ refs | README, submission screenshots |
| `03-render.png` | Render tab — multi-lane timeline, clip details | README, submission screenshots |
| `04-media.png` | Media tab — library, `<video>` thumbs, usage | README, submission screenshots |
| `05-agent.png` | AI widget — threads, @-refs, inline art, project_status answer | README, agent-feature proof |
| `06-timeline-export.mp4` | Exported MP4 (Ken Burns motion, native audio) played back | Demo video "export that matches preview" beat |
| `07-render-playback.mp4` | In-app timeline playback (pause state, advancing timecode) | Demo video "continuous playback, no black flash" beat |

`cleanDemo` contents behind these captures: 8 pages — the 6 real refs from
`D:\Downloads\refs` (sanji p11/p14/p15, zoro vs mister 1, naruto vs sasuke,
goku petit, SAM-detected, 37 panels total) plus the 2 original art pages;
`Opening Shot` sequence (8 frames, one per page); `Auto cut` timeline
(37 clips — the original 7 kept, 30 appended from the refs — ~74s).

## Earlier demo generations (original character Kyo Rengoku — copyright-safe)

Recovered from the first `Demo` projects (`6159c575056e`, `5bf6c79cd832`)
and mirrored into `D:\Downloads\refs\kyo-*` for reuse. The `duel at dawn`
take (`5ea842c5457d`) rendered blank and is excluded.

| File | What it is | Demo job |
|---|---|---|
| `gen-kyo-model-sheet.png` | Fighter model sheet: front/side/back, fighting stance, 6 expressions | `79ccec954aa5` |
| `gen-kyo-ref-sheet.png` | Polished character reference sheet | `9824abd78eb1` |
| `gen-kyo-mid-punch.png` | Mid-punch key visual, dojo, impact frame | `e2ccb621569e` |
| `gen-kyo-comet-fist-punch.mp4` | Punch video (drifts into Comet Fist motif abstraction) | `285b402c2d95` |
| `gen-kyo-straight-punch.mp4` | Straight-punch video from the sheet | `e14d44d29df0` |
| `gen-kyo-sakuga-fight.mp4` | Sakuga fight video | `73e640a585d2` |
| `gen-kyo-lunge-punch.mp4` | Lunge-into-punch video | `3d89cd4d78dd` |
