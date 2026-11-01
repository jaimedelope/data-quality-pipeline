# Data Quality / Observability Pipeline

End-to-end **batch data-quality demo** for a data engineering portfolio:

**synthetic order batches → quality checker → Redis metrics → live dashboard**

Designed to be easy to run locally with Docker and easy to explain in a case study. Complements the [streaming fraud pipeline](https://github.com/jaimedelope/streaming-fraud-pipeline): that one is real-time anomaly scoring; this one is **contract checks + observability** on landing data.

## Live demo

Try the dashboard in the browser: **[data-quality-pipeline.vercel.app](https://data-quality-pipeline.vercel.app)**

That URL is a static Vercel demo: the same UI and check rules, with batches simulated in the browser (no Redis). Git pushes to `main` redeploy only `web/`. The Docker stack below is the real pipeline.

## Architecture

```text
┌────────────┐   batches    ┌──────────────┐   results     ┌──────────┐
│ Generator  │ ───────────► │    Redis     │ ◄──────────── │ Checker  │
│ (Python)   │              │  landing +   │ ─────────────► │ (Python) │
└────────────┘              │   metrics    │   consume      └────┬─────┘
                            └──────┬───────┘                     │
                                   │                             │
                                   ▼                             │
                            ┌─────────────┐                      │
                            │  Dashboard  │ ◄────────────────────┘
                            │   :8089     │
                            └─────────────┘
```

![Data quality pipeline architecture](docs/architecture.svg)

### What each piece does

| Component | Role |
|-----------|------|
| **Generator** | Emits e-commerce order batches (~100 rows) every few seconds. ~12% of rows carry intentional defects (nulls, duplicates, bad currency, future timestamps, orphan SKUs, volume spikes). |
| **Redis** | Landing queue (`landing:batches`) plus metrics and failure feeds for the UI. |
| **Checker** | Runs seven explainable DQ rules per batch. Publishes pass/fail, latency, and offender samples. |
| **Dashboard** | FastAPI UI on `:8089` with pass rates, latest check table, failure feed, and run history. |

## Quality checks (intentionally explicit)

| Check | What it enforces |
|-------|------------------|
| **schema** | Required columns present |
| **completeness** | Null rate on `user_id`, `amount`, `currency`, `order_id` ≤ 3% |
| **uniqueness** | `order_id` has no duplicates |
| **validity** | `amount > 0`, currency ∈ {EUR,USD,GBP}, status enum |
| **freshness** | `event_time` not in the future and not older than 3 hours |
| **referential** | `product_id` exists in the batch catalog |
| **volume** | Row count within [40, 160] |

Failed checks include a short detail string and sample offenders — great for walkthroughs in interviews.

## Quick start

Prerequisites: **Docker Desktop** running.

```bash
docker compose up --build
```

Open the dashboard: [http://localhost:8089](http://localhost:8089)

You should see runs within ~10–20 seconds.

Stop everything:

```bash
docker compose down -v
```

## Project layout

```text
generator/    # synthetic order batches + injected defects
checker/      # DQ rules + Redis publishers
dashboard/    # FastAPI UI (Docker)
web/          # static Vercel demo (client-side simulation)
docs/         # architecture diagram
docker-compose.yml
vercel.json
```

## Next ideas (for v2)

- Schedule with Airflow / Prefect and write results to a warehouse table
- Great Expectations or Pandera suites as the check engine
- Prometheus histograms + Grafana boards for pass-rate SLO
- Slack / PagerDuty alert when pass rate drops below a threshold
- Quarantine topic for failed rows and a repair / replay path

## Portfolio note

This repo is the **implementation**. The Vercel URL is a lightweight live UI for recruiters; GitHub + Docker is the actual quality pipeline.
