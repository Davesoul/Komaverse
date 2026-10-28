# Komaverse — Observability

Zero-dependency, no-vendor observability: structured logs, Prometheus metrics,
health/readiness, an error ring, and a client error funnel.

## Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | no | Liveness (`{status, version}`) |
| `GET /ready` | no | Readiness: DB connect + media writability (200/503) |
| `GET /metrics` | no | Prometheus exposition (scrape this) |
| `GET /api/v1/status` | yes | Version, uptime, entity counts, last 10 errors |
| `POST /api/v1/client-log` | no | Client error funnel (202, rate-limited) |

## Logs

- Production (`APP_ENV=production`): one JSON object per line on stdout —
  collect with Docker/CloudWatch/Loki unchanged.
- Development: pretty single-line text.
- Fields: `ts level logger msg request_id` + `method path status
  duration_ms` on request lines. Set verbosity with `LOG_LEVEL`.
- Every request gets an `X-Request-ID` (12 hex chars, echoed in the response
  header). Pass your own `X-Request-ID` header to trace across services.
- Unhandled exceptions log with `traceback` and land in the error ring.

## Metrics (`/metrics`)

- `komaverse_requests_total{method,route,status}` — route templates, never
  raw IDs (`/api/v1/projects/{id}`), so cardinality stays flat.
- `komaverse_request_seconds_{sum,count}{method,route}` — average latency =
  `sum / count` (e.g. `rate(sum[5m]) / rate(count[5m])`).
- `komaverse_ai_jobs_total{status}` — `done` / `failed`.
- `komaverse_exports_total`, `komaverse_detects_total{detector}`.
- `komaverse_app_info{version}`.

Prometheus scrape example:

```yaml
scrape_configs:
  - job_name: komaverse
    static_configs: [{ targets: ["localhost:8000"] }]
```

Useful alerts: `rate(requests_total{status=~"5.."}[5m]) > 0`,
`rate(ai_jobs_total{status="failed"}[15m]) > 0.1`, `/ready != 200`.

## Client funnel

The web app POSTs window errors, unhandled rejections, and API 5xx responses
to `/client-log` (throttled to 1 per 5s, 500-char cap). They land in the
server log tagged `client`, with the page URL attached.

## In-app status

The Home tab shows a System card (version, uptime, counts, recent 5xx with
request IDs) sourced from `/api/v1/status` — enough to triage a demo without
leaving the app.

## Deliberately out of scope

Distributed tracing (single service — request IDs suffice), external APM
(Sentry/Datadog), and log shipping sidecars. Add when there is a second
service or a production incident that needs them.
