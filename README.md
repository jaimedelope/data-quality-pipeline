# Data Contract Scorecards

Batch **data-quality / observability** demo for a data engineering portfolio — deliberately different from the [streaming fraud pipeline](https://github.com/jaimedelope/streaming-fraud-pipeline).

| | Fraud pipeline | This project |
|--|----------------|--------------|
| Problem | Real-time anomaly scoring | Warehouse **data contracts** + SLA scorecards |
| Engine | Custom streaming rules | **Pandera** schemas generated from YAML |
| Shape | Continuous event stream | CSV **batches** landed on disk |
| Store | Kafka + Redis feeds | Files + **SQLite** history + downloadable reports |
| UI | Live streaming ops board | Contract scorecards, trends, quarantine samples |
| Port | `:8088` | `:8090` |

**Flow:** synthetic table extracts → YAML contracts → Pandera validator → pass/quarantine + JSON/HTML reports → scorecard UI

## Live demo

**[data-quality-pipeline-two.vercel.app](https://data-quality-pipeline-two.vercel.app)**

Static browser simulation (trends + report downloads). The real stack is below.

## Architecture

```text
contracts/*.yaml ──► Pandera engine ──► SQLite scorecards
        ▲                   │
        │                   ├── data/processed/
generator → landing/*.csv ──┤── data/quarantine/
                            └── data/reports/{run}.json|.html|_sample.csv
                                         │
                                         ▼
                                   Dashboard :8090
```

### Datasets & contracts

| Dataset | Owner | Examples of rules |
|---------|-------|-------------------|
| `customers` | growth-analytics | unique id, email pattern, allowed countries, row-count band |
| `orders` | commerce-analytics | amount range, status enum, FK → customers, null-rate SLA |
| `events` | product-analytics | event name enum, freshness window, uniqueness |

Failed batches go to `data/quarantine/`. Passing batches go to `data/processed/`. Every run writes a JSON + HTML report under `data/reports/`; SLA breaches also get a quarantine CSV sample.

## Quick start

```bash
docker compose up --build
```

Open [http://localhost:8090](http://localhost:8090)

- Scorecards + **pass-rate sparklines**
- Download **JSON / HTML** reports per run
- Download **quarantine sample CSV** when SLA breaches

```bash
docker compose down -v
```

## CI

GitHub Actions runs `scripts/validate_fixtures.py` on every push: Pandera validates `fixtures/customers_valid.csv` (must meet SLA) and `fixtures/customers_invalid.csv` (must breach).

```bash
pip install -r validator/requirements.txt
python scripts/validate_fixtures.py
```

## Project layout

```text
contracts/     # YAML data contracts (source of truth)
generator/     # writes landing CSVs
validator/     # Pandera engine + SQLite + reports
dashboard/     # FastAPI scorecard UI
fixtures/      # CI samples
scripts/       # validate_fixtures.py
web/           # static Vercel demo
.github/workflows/contracts.yml
```

## Why this belongs on the portfolio

Shows **analytical engineering** concerns: ownership, contracts-as-code, Pandera validation, SLAs, quarantine artifacts, historical trends, and CI — complementary to streaming fraud detection.

## Next ideas (v2)

- Airflow/Prefect schedule + warehouse write (BigQuery/Snowflake)
- Slack alert on SLA breach
- Quarantine browser with row-level repair & replay
