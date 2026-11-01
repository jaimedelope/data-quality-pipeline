"""Scorecard API for warehouse data-contract validation results."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
CONTRACTS_DIR = Path(os.getenv("CONTRACTS_DIR", "./contracts"))
DB_PATH = DATA_DIR / "quality.db"

app = FastAPI(title="Data Contract Scorecards")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


def db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise HTTPException(status_code=503, detail="quality.db not ready yet")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/")
def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(static_dir / "favicon.svg", media_type="image/svg+xml")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "db_exists": DB_PATH.exists()}


@app.get("/api/contracts")
def contracts() -> dict:
    items = []
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        items.append(
            {
                "dataset": doc.get("dataset"),
                "owner": doc.get("owner"),
                "description": doc.get("description"),
                "sla_pass_rate": doc.get("sla_pass_rate"),
                "columns": list((doc.get("columns") or {}).keys()),
                "yaml": path.read_text(encoding="utf-8"),
            }
        )
    return {"contracts": items}


@app.get("/api/scorecards")
def scorecards() -> dict:
    try:
        conn = db()
    except HTTPException:
        return {"datasets": [], "recent_runs": [], "summary": {"runs": 0, "breaches": 0, "avg_pass": 0}}

    datasets = []
    for row in conn.execute(
        """
        SELECT dataset,
               COUNT(*) AS runs,
               AVG(pass_rate) AS avg_pass,
               SUM(CASE WHEN sla_met = 0 THEN 1 ELSE 0 END) AS breaches,
               MAX(finished_at) AS last_run
        FROM runs
        GROUP BY dataset
        ORDER BY dataset
        """
    ):
        latest = conn.execute(
            """
            SELECT id, pass_rate, sla_pass_rate, sla_met, row_count, checks_passed, checks_total, duration_ms, file_name, finished_at
            FROM runs WHERE dataset = ? ORDER BY finished_at DESC LIMIT 1
            """,
            (row["dataset"],),
        ).fetchone()
        latest_checks = []
        if latest:
            latest_checks = [
                dict(c)
                for c in conn.execute(
                    "SELECT check_name, passed, detail, sample_json FROM check_results WHERE run_id = ? ORDER BY id",
                    (latest["id"],),
                )
            ]
            for c in latest_checks:
                c["sample"] = json.loads(c.pop("sample_json") or "[]")
                c["passed"] = bool(c["passed"])

        datasets.append(
            {
                "dataset": row["dataset"],
                "runs": row["runs"],
                "avg_pass": round((row["avg_pass"] or 0) * 100, 1),
                "breaches": row["breaches"],
                "last_run": row["last_run"],
                "latest": dict(latest) if latest else None,
                "latest_checks": latest_checks,
            }
        )
        if datasets[-1]["latest"]:
            datasets[-1]["latest"]["pass_rate"] = round(datasets[-1]["latest"]["pass_rate"] * 100, 1)
            datasets[-1]["latest"]["sla_pass_rate"] = round(datasets[-1]["latest"]["sla_pass_rate"] * 100, 1)
            datasets[-1]["latest"]["sla_met"] = bool(datasets[-1]["latest"]["sla_met"])

    recent = [dict(r) for r in conn.execute(
        """
        SELECT dataset, file_name, pass_rate, sla_met, row_count, duration_ms, finished_at
        FROM runs ORDER BY finished_at DESC LIMIT 25
        """
    )]
    for r in recent:
        r["pass_rate"] = round(r["pass_rate"] * 100, 1)
        r["sla_met"] = bool(r["sla_met"])

    total_runs = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]
    breaches = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE sla_met = 0").fetchone()["n"]
    avg_pass = conn.execute("SELECT AVG(pass_rate) AS a FROM runs").fetchone()["a"] or 0
    conn.close()

    return {
        "datasets": datasets,
        "recent_runs": recent,
        "summary": {
            "runs": total_runs,
            "breaches": breaches,
            "avg_pass": round(avg_pass * 100, 1),
        },
    }
