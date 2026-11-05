# Koma Portal billing

Money in, credits out. Users top up with a card (Stripe, from $5); the
server pays the model providers with its own keys, so users never paste
API keys. Local models (YOLO, heuristic, XTTS, Kokoro, free-tts) cost
nothing and are never gated.

## The exchange rate

$5 = 500 credits ($0.01 each), grounded 2026-11-05 against live provider
prices at ~2.5–3× cost (fees, fraud, hosting):

| Action | Credits | Provider cost | Sources |
|---|---|---|---|
| Chat turn (default models) | 1 ($0.01) | ~$0.0005–0.001 (GPT-4o-mini, Gemini Flash-Lite, Spark contributor) | [OpenRouter catalog](https://openrouter.ai/api/v1/models) |
| Image | 3 ($0.03) | $0.009–0.018 (FLUX Klein, Qwen Turbo) | [BFL/Klein pricing](https://www.cinemadrop.com/blog/flux-3), [Qwen pricing](https://www.trezalabs.com/blog/qwen-image-2-1-api-pricing-through-a-pipeline) |
| Video, per second | 25 ($0.25) | ~$0.07–0.15/s (Seedance 2.0) | [Seedance pricing](https://hermoso.ai/blog/seedance-pricing/), [CostGoat](https://costgoat.com/pricing/seedance) |
| Voiceover line | 2 ($0.02) | ~$0.01/200 chars (ElevenLabs) | [ElevenLabs API rates](https://saaspricealerts.com/guides/elevenlabs-api-pricing/) |
| Vision read / cloud detect | 1 ($0.01) | ~$0.0005 (Flash vision) | OpenRouter catalog |

What $5 buys: ~160 images, 20 video-seconds, or 500 chat turns.
Freemium: 100 free credits, granted once per calendar month on first
spend (dormant accounts accrue nothing). Premium chat models (Haiku,
Spark full) bill the flat 1 credit/turn — the margin absorbs them.

## How it works

1. **Portal** (`GET /billing/portal`) — one round trip for the whole
   Koma Portal page: balance, free-credit state, packs, costs, purchases.
2. **Checkout** (`POST /billing/checkout {pack_id}`) — creates a Stripe
   Checkout Session ($5/500, $10/1100, $25/3000) and a `pending`
   purchase row. The user pays on Stripe's hosted page (PCI stays with
   Stripe); success/cancel redirect back to `/portal?topup=...`.
3. **Webhook** (`POST /billing/webhook`) — Stripe-signed
   `checkout.session.completed` credits the ledger exactly once
   (idempotency key: `stripe_session_id`, unique). Unknown sessions log
   and 200; `checkout.session.expired` marks the purchase expired.
4. **Spend** — every cloud surface debits before acting and 402s when
   broke: `/ai/generate` (per-second video), cloud chat turns (1/turn,
   authorized up front), vision reads, cloud detection, ElevenLabs voice
   (endpoint and agent tool). Local providers skip the ledger entirely.
5. **Refunds** — failed jobs credit back `jobs.cost_credits`, the exact
   stored debit — never a recomputed estimate (per-second video would
   otherwise mint credits).

## Running it

```bash
PAYWALL_ENABLED=true
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
APP_URL=https://your-app.example.com
```

No new variables for subscriptions — same keys, same app URL.

## Subscriptions

Monthly plans grant credits every paid month (`SUBSCRIPTION_PLANS`
in `api/billing.py`): Hobby $9 → 1,200 · Studio $29 → 5,000 ·
Atelier $79 → 15,000. Commitment beats pay-as-you-go per credit; the
credit cap bounds the worst case, so full redemption still profits —
see [BUSINESS_MODEL.md](BUSINESS_MODEL.md) for the margin math.

1. **Subscribe** (`POST /billing/subscribe {plan_id}`) — 409 when a
   plan is already active. Reuses one Stripe customer per user
   (`users.stripe_customer_id`) and opens a subscription-mode
   Checkout Session (`/portal?sub=...` redirects).
2. **Signup grant** — `checkout.session.completed` with
   `metadata.kind == "subscription"` creates the `subscriptions` row
   and grants the first month. Retried deliveries hit the existing
   row and mint nothing.
3. **Renewals** — `invoice.payment_succeeded` re-grants the month's
   credits, once per invoice (`last_invoice_id` is the idempotency
   key). Unknown subscriptions and non-active rows are ignored.
4. **Cancel** (`POST /billing/subscription/cancel`) — deletes the
   Stripe subscription immediately; granted credits stay (no
   mid-month clawback). `customer.subscription.deleted` flips the
   row to `canceled`; `.updated` syncs status + period end.
5. **Portal** — `GET /billing/portal` also returns `plans` and the
   active `subscription` (plan, status, renewal date) for the
   Subscribe card.

Register these webhook events in the Stripe Dashboard alongside the
top-up ones: `invoice.payment_succeeded`,
`customer.subscription.deleted`, `customer.subscription.updated`.

Without keys, checkout/webhook answer 503 and the freemium path keeps
working. The dev grant (`POST /billing/grant`) is blocked in
production. Webhook endpoint: `<APP_URL-ish API>/api/v1/billing/webhook`
(Stripe Dashboard → Developers → Webhooks → `checkout.session.completed`,
`checkout.session.expired`).
