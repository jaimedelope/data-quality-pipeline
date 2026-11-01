# Data Quality / Observability Pipeline

End-to-end **batch data-quality demo** for a data engineering portfolio:

**synthetic order batches → quality checker → Redis → live dashboard**

Designed to be easy to run locally with Docker and easy to explain in a case study. Checks are custom Python (inspired by Great Expectations / Pandera), not a heavy GE install.

## Live demo

Try the dashboard in the browser: **[data-quality-pipeline.vercel.app](https://data-quality-pipeline.vercel.app)**

That URL is a static Vercel demo: the same UI and check rules, with batches simulated in the browser (no Redis). Git pushes to `master` redeploy only `web/`. The Docker stack below is the real pipeline.

## Architecture

```text
┌────────────┐   CSV/JSON    ┌──────────┐   results    ┌───────┐
│ Generator  │ ────────────► │ Landing  │ ───────────► │ Redis │
│ (Python)   │               │  volume  │              └───┬───┘
└────────────┘               └────┬─────┘                  │
                                  │                        ▼
                                  ▼                 ┌─────────────┐
                            ┌──────────┐            │  Dashboard  │
                            │ Checker  │───────────►│    :8089    │
                            │ (Pandas) │  failures  └─────────────┘
                            └──────────┘
```

![Data quality pipeline: generator, landing, checker, Redis, and dashboard](docs/architecture.svg)

### What each piece does

| Component | Role |
|-----------|------|
| **Generator** | Emits order batches every ~12s (CSV + JSON) with ~12% intentional defects: nulls, duplicates, bad amounts/currencies, future/stale timestamps, orphan product IDs, anomalous volume. |
| **Landing** | Shared volume of batch files (demo stand-in for object storage / a lake landing zone). |
| **Checker** | Runs an explicit 7-check suite; writes pass/fail + metrics + offender samples to Redis. Does **not** use the generator’s `injected_defect` label for scoring. |
| **Redis** | Live run feed, failed-check feed, latency samples, per-check counters. |
| **Dashboard** | FastAPI UI on `:8089` — pass rate, checks by type, failure feed, run latency. |

## Quality checks

| Check | What it asserts |
|-------|-----------------|
| **Schema** | Required columns present; amount parseable as numeric |
| **Completeness** | Null rate on `user_id`, `amount`, `currency` ≤ 5% |
| **Uniqueness** | No duplicate `order_id` |
| **Validity** | `amount > 0` and ≤ 10k; `currency ∈ {EUR,USD,GBP}`; status enum |
| **Freshness** | `event_time` not in the future; not older than 30 minutes |
| **Referential** | `product_id` exists in a reference catalog (40 SKUs) |
| **Volume** | Batch size within `[40, 120]` rows |

Each check returns `pass` / `fail`, a metric payload, and a small sample of offending rows. The batch score is the % of checks that passed.

## Quick start

Prerequisites: **Docker Desktop** running.

```bash
docker compose up --build
```

Open the dashboard: [http://localhost:8089](http://localhost:8089)

You should see batch runs and failed checks within ~30–60 seconds.

Stop everything:

```bash
docker compose down -v
```

## Project layout

```text
generator/     # synthetic order batches with labeled defects
checker/       # Pandas quality suite → Redis
dashboard/     # FastAPI UI (Docker)
web/           # static Vercel demo (client-side simulation)
docs/          # architecture diagram
docker-compose.yml
vercel.json
```

## Next ideas (for v2)

- Airflow / cron orchestration instead of a polling loop
- Prometheus / Grafana for check histograms and SLOs
- Formal Great Expectations or Pandera suites wired to the same landing zone
- Slack / PagerDuty alerts on repeated check failures

## Portfolio note

This repo is the **implementation**. The Vercel URL is a lightweight live UI for recruiters; GitHub + Docker is the actual quality pipeline.
