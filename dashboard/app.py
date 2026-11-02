"""Scorecard API for warehouse data-contract validation results."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
CONTRACTS_DIR = Path(os.getenv("CONTRACTS_DIR", "./contracts"))
DB_PATH = DATA_DIR / "quality.db"
REPORTS_DIR = DATA_DIR / "reports"

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
    return {"status": "ok", "db_exists": DB_PATH.exists(), "engine": "pandera"}


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
        return {
            "datasets": [],
            "recent_runs": [],
            "summary": {"runs": 0, "breaches": 0, "avg_pass": 0},
        }

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
            SELECT id, pass_rate, sla_pass_rate, sla_met, row_count, checks_passed, checks_total,
                   duration_ms, file_name, finished_at, report_json, report_html, quarantine_sample
            FROM runs WHERE dataset = ? ORDER BY finished_at DESC LIMIT 1
            """,
            (row["dataset"],),
        ).fetchone()
        trend_rows = conn.execute(
            """
            SELECT pass_rate, sla_met, finished_at
            FROM runs WHERE dataset = ?
            ORDER BY finished_at DESC LIMIT 24
            """,
            (row["dataset"],),
        ).fetchall()
        # chronological for sparkline
        trend = [
            {"pass_rate": round(r["pass_rate"] * 100, 1), "sla_met": bool(r["sla_met"]), "finished_at": r["finished_at"]}
            for r in reversed(trend_rows)
        ]

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

        item = {
            "dataset": row["dataset"],
            "runs": row["runs"],
            "avg_pass": round((row["avg_pass"] or 0) * 100, 1),
            "breaches": row["breaches"],
            "last_run": row["last_run"],
            "trend": trend,
            "latest": dict(latest) if latest else None,
            "latest_checks": latest_checks,
        }
        if item["latest"]:
            item["latest"]["pass_rate"] = round(item["latest"]["pass_rate"] * 100, 1)
            item["latest"]["sla_pass_rate"] = round(item["latest"]["sla_pass_rate"] * 100, 1)
            item["latest"]["sla_met"] = bool(item["latest"]["sla_met"])
        datasets.append(item)

    recent = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, dataset, file_name, pass_rate, sla_met, row_count, duration_ms, finished_at,
                   report_json, report_html, quarantine_sample
            FROM runs ORDER BY finished_at DESC LIMIT 25
            """
        )
    ]
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


def _report_file(run_id: str, kind: str) -> Path:
    conn = db()
    row = conn.execute("SELECT report_json, report_html, quarantine_sample FROM runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    name = row[kind]
    if not name:
        raise HTTPException(status_code=404, detail=f"{kind} not available")
    path = REPORTS_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="report file missing on disk")
    return path


@app.get("/api/runs/{run_id}/report.json")
def report_json(run_id: str):
    path = _report_file(run_id, "report_json")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.get("/api/runs/{run_id}/report.html")
def report_html(run_id: str):
    path = _report_file(run_id, "report_html")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}/quarantine-sample")
def quarantine_sample(run_id: str):
    path = _report_file(run_id, "quarantine_sample")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@app.get("/api/quarantine/{dataset}/latest")
def quarantine_latest(dataset: str):
    conn = db()
    row = conn.execute(
        """
        SELECT id, quarantine_sample, finished_at, pass_rate, file_name
        FROM runs
        WHERE dataset = ? AND quarantine_sample IS NOT NULL
        ORDER BY finished_at DESC LIMIT 1
        """,
        (dataset,),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="no quarantine sample for dataset")
    return {
        "run_id": row["id"],
        "file_name": row["file_name"],
        "finished_at": row["finished_at"],
        "pass_rate": round(row["pass_rate"] * 100, 1),
        "download": f"/api/runs/{row['id']}/quarantine-sample",
    }
