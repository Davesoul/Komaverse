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
