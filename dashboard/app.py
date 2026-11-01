"""Live dashboard for the data-quality / observability pipeline."""

from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

import redis
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = FastAPI(title="Data Quality Pipeline Dashboard")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

_redis: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def health():
    try:
        get_redis().ping()
        return {"status": "ok", "redis": True}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "redis": False, "error": str(exc)}, status_code=503)


@app.get("/api/metrics")
def metrics() -> dict:
    r = get_redis()
    runs_total = int(r.get("metrics:runs_total") or 0)
    rows_total = int(r.get("metrics:rows_total") or 0)
    failed_total = int(r.get("metrics:checks_failed_total") or 0)
    durations = [float(x) for x in r.lrange("metrics:run_duration_ms", 0, 199)]
    pass_rates = [float(x) for x in r.lrange("metrics:pass_rate_pct", 0, 199)]

    avg_duration = statistics.fmean(durations) if durations else 0.0
    latest_pass = pass_rates[0] if pass_rates else 0.0
    avg_pass = statistics.fmean(pass_rates) if pass_rates else 0.0

    latest_raw = r.get("run:latest")
    latest = json.loads(latest_raw) if latest_raw else None
    checks_raw = r.get("checks:latest")
    checks = json.loads(checks_raw) if checks_raw else []

    return {
        "runs_total": runs_total,
        "rows_total": rows_total,
        "checks_failed_total": failed_total,
        "latest_pass_rate_pct": round(latest_pass, 1),
        "avg_pass_rate_pct": round(avg_pass, 1),
        "avg_duration_ms": round(avg_duration, 2),
        "latest_run": latest,
        "latest_checks": checks,
    }


@app.get("/api/feed")
def feed(limit: int = 40) -> dict:
    r = get_redis()
    limit = max(1, min(limit, 100))
    runs = [json.loads(x) for x in r.lrange("feed:runs", 0, limit - 1)]
    failures = [json.loads(x) for x in r.lrange("feed:failures", 0, min(30, limit) - 1)]
    return {"runs": runs, "failures": failures}
