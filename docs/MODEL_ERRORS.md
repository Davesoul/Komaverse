# Model errors & content policy: what we tested, what it means, what to do

Last verified: 2026-11-04 (live probes + public provider/report sources linked below).

## The refusal you will actually see

Generating video from a One Piece manga page (Zoro vs Mister One fight) on
`bytedance/seedance-2.0` via OpenRouter fails the job with the provider's own
words, surfaced verbatim in the chat / Media job error:

> `openrouter video failed: The request failed because the output video may
> be related to copyright restrictions. Request id: 02179…`

Komaverse now appends a hint to this class of failure (see §4): use original
characters and style descriptions instead of named third-party IP.

## 1. Error shapes by layer (tested / sourced)

| Layer | Shape | Example text |
|---|---|---|
| OpenRouter moderation (text/vision) | HTTP 403, `error.metadata.flagged_input` (≤100 chars of the flagged span) | `Your chosen model requires moderation and your input was flagged` ([errors ref](https://openrouter.ai/docs/api-reference/errors.mdx)) |
| OpenRouter image endpoint | HTTP 422 `content_policy_violation` (provider-specific, e.g. fal) | `Inappropriate content detected` (Sourceful, via community probe) |
| OpenRouter video job, prompt-side | POST rejected immediately | 4xx with moderation / policy body |
| OpenRouter video job, output-side | poll → `status: failed` | `…may be related to copyright restrictions` (Seedance, seen live) |
| Output-side silent filter | poll → `status: completed` but no media | `Video generation completed with no output (content may have been filtered)` (Veo via OpenRouter, community probe) |
| Provider unavailable / key / quota | 401 / 429 / 5xx / timeout | surfaced verbatim (`check the key`, `rate limit`, `timed out after 10 minutes`) |

Rule of thumb: **immediate 4xx = your input tripped a filter; `failed` after
polling = the output tripped one; `completed` with nothing downloadable =
silent output-side filter.** Credits are usually not consumed on immediate
rejections, but polling-then-failing jobs may bill for the attempt — check the
provider's billing page, not Komaverse.

## 2. Why manga/anime IP gets refused (researched)

- **Seedance (ByteDance) is the strictest of our catalog for IP.** It filters
  at prompt level *and* runs face detection on reference/start images: no
  recognizable real faces, no copyrighted characters. Following the MPA's
  February 2026 demands, ByteDance agreed guardrails for film/TV IP across
  Seedance/Seedream outputs (face blocking, copyrighted-character filters,
  C2PA provenance, watermarking, monitoring). A Zoro fight page trips both
  the name ("Zoro", "One Piece") and the artwork itself.
  Sources: [MPA × ByteDance MOU reporting](https://deanlee.info/essays/ai-copyright-bargain-output/),
  [Seedance copyright guide](https://morphed.app/blog/seedance-copyright),
  [Seedance 2.0 restrictions](https://newly.app/guides/seedance-2-0-content-restrictions-workarounds).
- **All commercial video models restrict recognizable real people**, with
  heightened child-safety filtering — Veo, Kling, Seedance alike
  ([frame-control policy survey](https://github.com/lbroth/videoboom/blob/HEAD/docs/research/video-models-frame-control.md)).
- **Wan (Alibaba)** ships no C2PA manifest, so provenance-gated pipelines
  exclude it — a provenance gap, not a permissiveness signal
  ([service-level exclusion](https://github.com/bitmind-ai/bitmind-subnet/pull/383)).
- **Hailuo (MiniMax)** publishes the least about IP filters; treat as unknown,
  not as lax.
- **Legal backdrop:** fan art of third-party characters is a derivative work
  of copyrighted material. Generating it — especially video, especially for
  commercial/demo use — can infringe even when a model *allows* it. A filter
  refusal is the provider protecting you (and itself).

## Per-model constraints (live OpenRouter matrix, 2026-11-04)

Source of truth at runtime: `GET /videos/models` (keyless) — Komaverse
validates duration/size against it and fails fast naming the supported
values. Frame/reference support below is verified where marked, otherwise
pass-through (provider errors surface with hints).

| Model | Durations | Sizes | Frames | Style refs | Notes |
|---|---|---|---|---|---|
| Seedance 2.0 | 4–15s | 25 sizes incl. 1280x720 | first+last | yes | strictest IP filter (see §2) |
| Seedance mini/fast | 4–15s | ≤720p sizes | first+last | yes | same family, cheaper |
| Veo 3.1 | 4/6/8s | 1280x720, 1920x1080… | first+last | yes | native audio (we keep silent) |
| Wan 2.6 | 5/10s | 1280x720, 1920x1080… | first+last | yes | no C2PA manifest |
| Wan 3.0 Prime | 2–30s | resolution-driven (no WxH list) | first+last* | yes* | *unverified — pass-through |
| Hailuo 3 Max | 5–15s | resolution-driven (480p/768p) | first *xor* last | **no** (400) | cheapest ($0.05–0.08/s); fail-fast enforced |
| HeyGen 1 | 5–30s | resolution-driven | first+last* | yes* | *unverified — pass-through |

*Sources: [OpenRouter video guide](https://openrouter.ai/docs/guides/overview/multimodal/video-generation),
[H3 Max skill](https://github.com/finpeakinc/frevana-skills/blob/HEAD/skills/minimax-h3-max/SKILL.md),
[video-model survey](https://seminal.ai/reference/categories/video-generation-models/). Policy
strictness unknown for Wan/Hailuo/HeyGen — treat as untested, not as lax.*

## 3. What to do instead (in order)

1. **Original characters.** Design your own fighter in-app (`create_image`),
   lock them with `@gen:` refs, and animate *that*. This is the reliable path
   and the one the demo video uses.
2. **Describe, don't name.** "Green-haired swordsman, shonen duel, inked
   manga style" passes prompt filters far more often than "Zoro vs Mister
   One". Note the line: style words are usually fine; names + actual manga
   artwork as reference images still trip output/image filters.
3. **Try another model.** Strictness differs per provider with no public
   guarantees. Our catalog order of *known* strictness: Seedance (strictest,
   documented) → Veo/Kling-class → Wan / Hailuo / HeyGen (unknown). A refusal
   on one model is not proof another accepts — and an acceptance is not
   permission (see legal backdrop).
4. **Image-to-video from original art.** Scan/draw your own panel, then
   animate it. Reference images of published manga pages are the single most
   refused input shape we have seen.
5. **Read the job error.** Komaverse surfaces the provider's refusal verbatim
   plus the hint in §4. A `Request id` (like the one above) is your handle if
   you appeal to the provider.

## 4. What Komaverse does about it

- Provider refusal text is never swallowed: failed jobs keep `status: failed`
  with the raw error, shown in chat, Media, and `check_job`.
- `OpenRouterProvider` appends a plain-language hint to output-side refusals
  (copyright / moderation / filtered signals) pointing back to this file.
- A `completed`-with-no-media job first tries the reconstructed content
  URL (old contract, kept); only if that is also dead does it raise
  `content may have been filtered` instead of a bare download error.
- The Render tab carries a short original-content notice next to Generate.
- Agent guidance (`create_video`/`create_image` descriptions) tells the model
  to steer users toward original characters after a policy refusal.
