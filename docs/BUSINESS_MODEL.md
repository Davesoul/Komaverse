# Komaverse business model — per-model margins, revenue, profit

How the Koma Portal turns model-provider costs into gross margin.
All provider prices grounded 2026-11-05 in [BILLING.md](BILLING.md);
treat every dollar below as an estimate from that grounding, not a quote.

## The one idea

1 credit = $0.01 of face value. The server buys provider output for a
fraction of that and keeps the spread. Two things protect the margin:

1. **Caps, not meters.** Every pack and plan grants a fixed credit pool.
   Full redemption is still profitable by construction (math below), so
   heavy users can never flip a tier negative.
2. **Breakage.** Unused credits expire in practice (users churn with
   balances). Industry SaaS breakage runs 15–40%; the "realistic"
   column uses 30%.

## Cost per credit, by model

| Action | Charged | Provider cost (mid) | Cost / credit | Gross margin on face |
|---|---|---|---|---|
| Chat turn (GPT-4o-mini, Gemini Flash-Lite, Spark contributor) | 1 cr ($0.01) | ~$0.00075/turn | $0.00075 | ~92% |
| Vision read / cloud detect (Flash vision) | 1 cr ($0.01) | ~$0.0005/read | $0.0005 | ~95% |
| Image (FLUX Klein, Qwen Turbo) | 3 cr ($0.03) | ~$0.0135/image | $0.0045 | ~55% |
| Video, per second (Seedance 2.0) | 25 cr ($0.25) | ~$0.11/s | $0.0044 | ~56% |
| Voiceover line (ElevenLabs, ≤200 chars) | 2 cr ($0.02) | ~$0.01/line | $0.005 | ~50% |
| Premium chat (Haiku, Spark full — flat 1 cr) | 1 cr ($0.01) | ~$0.002–0.005/turn | $0.002–0.005 | ~50–80% |
| Local (YOLO, heuristic, XTTS, Kokoro, ComfyUI) | 0 (free) | $0 (own GPU/CPU) | $0 | n/a |

Blended cost at a typical creator mix (35% chat, 30% image, 25% video,
5% voice, 5% vision, by credits):

0.35×0.00075 + 0.30×0.0045 + 0.25×0.0044 + 0.05×0.005 + 0.05×0.0005
= **≈ $0.0030 per credit** → ~70% margin on face value, before fees.

## Revenue lines

### 1. Top-ups (pay-as-you-go, from $5)

| Pack | Price | Credits | Effective $/credit |
|---|---|---|---|
| Top-up 5 | $5 | 500 | $0.0100 |
| Top-up 10 | $10 | 1,100 | $0.0091 |
| Top-up 25 | $25 | 3,000 | $0.0083 |

Bigger packs discount the credit (volume incentive); at $0.003/credit
cost, even the $25 pack holds ~64% margin before Stripe fees.

### 2. Subscriptions (monthly credits, cancel anytime)

| Plan | Price/mo | Credits/mo | Effective $/credit | Full-redemption cost | Margin floor* |
|---|---|---|---|---|---|
| Hobby | $9 | 1,200 | $0.0075 | 1200×$0.003 = $3.60 | ~54% |
| Studio | $29 | 5,000 | $0.0058 | 5000×$0.003 = $15.00 | ~44% |
| Atelier | $79 | 15,000 | $0.0053 | 15000×$0.003 = $45.00 | ~40% |

\* Margin floor = (price − Stripe fee − full-redemption cost) / price.
Stripe takes ~2.9% + $0.30 ($0.56 / $1.14 / $2.59). With 30% breakage
(unredeemed credits) the realistic margins are ~68% / ~61% / ~58%.

Why plans can only get *more* profitable: the credit cap bounds the
worst case. Break-even redemption for Hobby is ($9−$0.56)/$0.003 ≈
2,800 credits — above the 1,200 cap, so the plan profits even if the
user burns every credit on the priciest video seconds.

### 3. Freemium (acquisition, not revenue)

100 free credits/month, granted on first spend. Worst-case cost per
active freemium user: 100 × $0.003 = **$0.30/month** — the customer
acquisition cost. One $5 top-up ($4.55 after fees, ~$3.65 gross profit
at blended mix) pays for ~15 months of a freemium user.

## Worked example — 1,000 users

Assume 700 freemium, 200 top-up buyers ($10 avg → $2,000 revenue),
100 subscribers (60 Hobby / 30 Studio / 10 Atelier → $2,200 revenue).

| Line | Revenue | Provider + Stripe cost | Gross profit |
|---|---|---|---|
| Freemium (700 × $0.30) | $0 | $210 | −$210 |
| Top-ups ($2,000, 70% margin after fees) | $2,000 | ~$600 | ~$1,400 |
| Subscriptions ($2,200, ~60% realistic) | $2,200 | ~$880 | ~$1,320 |
| **Total** | **$4,200** | **~$1,690** | **~$2,510 (~60%)** |

Hosting (Render Blueprint, one container + disk) is the only fixed
cost on top — low hundreds/month — so contribution margin ≈ gross
margin until scale forces a bigger box.

## Levers (what moves the numbers)

- **Mix toward video/images** lowers margin per credit (they cost
  ~$0.0044 vs chat's $0.00075). The per-second video rounding (always
  up) is the counterweight — a 4.2s clip bills 5 seconds.
- **Premium models** are loss-leaders inside the flat 1 cr chat price;
  if Haiku/Spark-full share exceeds ~20% of turns, split them into a
  2-credit tier.
- **Fraud/chargebacks**: Stripe fee math above excludes disputes
  ($15 each). The webhook's idempotency keys (`stripe_session_id`,
  `last_invoice_id`) exist so retries never mint credits — free money
  is the margin killer, not GPU time.
- **Local models** cost hosting only: every user who renders TTS via
  Kokoro or detects via YOLO is pure-margin engagement that still
  converts to paid cloud use.

## Changing prices safely

1. Update `COSTS` / `CREDIT_PACKS` in `api/ai.py` or
   `SUBSCRIPTION_PLANS` in `api/billing.py` (one place each).
2. Failed-job refunds reuse the stored `cost_credits`, so repricing
   never mints or burns old jobs.
3. Existing subscriptions keep their granted rate until renewal —
   Stripe bills the old price object; only new checkouts see the edit.
4. Update this doc's tables + the Portal "What things cost" copy.
