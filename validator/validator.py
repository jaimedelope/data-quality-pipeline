"""Validate landing CSV batches with Pandera-backed contracts; persist scorecards + reports."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from engine import validate_csv_path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
CONTRACTS_DIR = Path(os.getenv("CONTRACTS_DIR", "./contracts"))
POLL = float(os.getenv("POLL_INTERVAL_SEC", "3"))
DB_PATH = DATA_DIR / "quality.db"
REPORTS_DIR = DATA_DIR / "reports"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY,
          dataset TEXT NOT NULL,
          file_name TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          row_count INTEGER NOT NULL,
          checks_total INTEGER NOT NULL,
          checks_passed INTEGER NOT NULL,
          pass_rate REAL NOT NULL,
          sla_pass_rate REAL NOT NULL,
          sla_met INTEGER NOT NULL,
          duration_ms REAL NOT NULL,
          report_json TEXT,
          report_html TEXT,
          quarantine_sample TEXT
        )
        """
    )
    # migrate older DBs without new columns
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    for col, ddl in [
        ("report_json", "TEXT"),
        ("report_html", "TEXT"),
        ("quarantine_sample", "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {ddl}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS check_results (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          dataset TEXT NOT NULL,
          check_name TEXT NOT NULL,
          passed INTEGER NOT NULL,
          detail TEXT NOT NULL,
          sample_json TEXT,
          FOREIGN KEY(run_id) REFERENCES runs(id)
        )
        """
    )
    conn.commit()
    return conn


def load_contracts() -> dict[str, dict]:
    contracts: dict[str, dict] = {}
    for path in sorted(CONTRACTS_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        contracts[doc["dataset"]] = doc
    return contracts


def render_html_report(payload: dict) -> str:
    rows = "".join(
        f"<tr><td>{c['name']}</td><td>{'PASS' if c['passed'] else 'FAIL'}</td>"
        f"<td>{c.get('engine','')}</td><td>{c['detail']}</td></tr>"
        for c in payload["checks"]
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Report {payload['run_id'][:8]}</title>
<style>
body{{font-family:IBM Plex Sans,Segoe UI,sans-serif;background:#f4f1ea;color:#1c1917;padding:24px}}
table{{border-collapse:collapse;width:100%;background:#fffcf7}}
th,td{{border:1px solid #d6d3d1;padding:8px;text-align:left;font-size:13px}}
.badge{{display:inline-block;padding:4px 10px;border-radius:4px;font-weight:600}}
.met{{background:#dcfce7;color:#166534}}.breach{{background:#fee2e2;color:#991b1b}}
</style></head><body>
<h1>Data contract report</h1>
<p>Dataset <strong>{payload['dataset']}</strong> · file <code>{payload['file_name']}</code></p>
<p>Pass rate <strong>{payload['pass_rate_pct']}%</strong>
<span class="badge {'met' if payload['sla_met'] else 'breach'}">
{'SLA met' if payload['sla_met'] else 'SLA breach'} ({payload['sla_pass_rate_pct']}%)</span></p>
<p>Engine: Pandera column schemas + contract expectations · {payload['row_count']} rows · {payload['duration_ms']} ms</p>
<table><thead><tr><th>Check</th><th>Status</th><th>Engine</th><th>Detail</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


def write_reports(
    run_id: str,
    dataset: str,
    file_name: str,
    contract: dict,
    checks: list[dict],
    row_count: int,
    duration_ms: float,
    df: pd.DataFrame,
    sla_met: bool,
    pass_rate: float,
) -> dict[str, str | None]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "dataset": dataset,
        "file_name": file_name,
        "owner": contract.get("owner"),
        "engine": "pandera+contracts",
        "row_count": row_count,
        "duration_ms": duration_ms,
        "pass_rate_pct": round(pass_rate * 100, 1),
        "sla_pass_rate_pct": round(float(contract.get("sla_pass_rate", 0.95)) * 100, 1),
        "sla_met": sla_met,
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    json_path = REPORTS_DIR / f"{run_id}.json"
    html_path = REPORTS_DIR / f"{run_id}.html"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    html_path.write_text(render_html_report(payload), encoding="utf-8")

    sample_path = None
    if not sla_met:
        sample_path = REPORTS_DIR / f"{run_id}_quarantine_sample.csv"
        # Prefer rows mentioned in failure samples; else head of file
        sample_rows: list[dict[str, Any]] = []
        for c in checks:
            if not c.get("passed"):
                sample_rows.extend(c.get("sample") or [])
        if sample_rows:
            pd.DataFrame(sample_rows).head(50).to_csv(sample_path, index=False)
        else:
            df.head(50).to_csv(sample_path, index=False)

    return {
        "report_json": str(json_path.name),
        "report_html": str(html_path.name),
        "quarantine_sample": sample_path.name if sample_path else None,
    }


def persist_run(
    conn: sqlite3.Connection,
    run_id: str,
    dataset: str,
    file_name: str,
    contract: dict,
    checks: list[dict],
    row_count: int,
    duration_ms: float,
    report_meta: dict[str, str | None],
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    pass_rate = passed / total if total else 1.0
    sla = float(contract.get("sla_pass_rate", 0.95))
    sla_met = 1 if pass_rate >= sla else 0
    conn.execute(
        """
        INSERT INTO runs (id, dataset, file_name, started_at, finished_at, row_count,
          checks_total, checks_passed, pass_rate, sla_pass_rate, sla_met, duration_ms,
          report_json, report_html, quarantine_sample)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            dataset,
            file_name,
            now,
            now,
            row_count,
            total,
            passed,
            pass_rate,
            sla,
            sla_met,
            duration_ms,
            report_meta.get("report_json"),
            report_meta.get("report_html"),
            report_meta.get("quarantine_sample"),
        ),
    )
    for c in checks:
        conn.execute(
            """
            INSERT INTO check_results (run_id, dataset, check_name, passed, detail, sample_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                dataset,
                c["name"],
                1 if c["passed"] else 0,
                c["detail"],
                json.dumps(c.get("sample") or []),
            ),
        )
    conn.commit()


def process_file(
    conn: sqlite3.Connection,
    contract: dict,
    path: Path,
    customer_ids: set[str] | None,
) -> set[str] | None:
    dataset = contract["dataset"]
    t0 = time.time()
    df, checks = validate_csv_path(path, contract, customer_ids)
    duration_ms = round((time.time() - t0) * 1000, 2)
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    pass_rate = passed / total if total else 1.0
    sla = float(contract.get("sla_pass_rate", 0.95))
    sla_met = pass_rate >= sla
    run_id = str(uuid.uuid4())

    report_meta = write_reports(
        run_id,
        dataset,
        path.name,
        contract,
        checks,
        len(df),
        duration_ms,
        df,
        sla_met,
        pass_rate,
    )
    persist_run(conn, run_id, dataset, path.name, contract, checks, len(df), duration_ms, report_meta)

    dest_root = DATA_DIR / ("processed" if sla_met else "quarantine") / dataset
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / path.name
    path.replace(dest)

    print(
        f"[validator] {dataset} {path.name} pass={pass_rate:.0%} "
        f"sla={'MET' if sla_met else 'BREACH'} engine=pandera "
        f"→ {dest_root.name}/ ({duration_ms}ms) run={run_id[:8]}"
    )

    if dataset == "customers":
        return {str(x) for x in df["customer_id"].dropna().astype(str).tolist()}
    return customer_ids


def main() -> None:
    (DATA_DIR / "landing").mkdir(parents=True, exist_ok=True)
    conn = connect()
    print(f"[validator] Pandera engine watching {DATA_DIR / 'landing'} against {CONTRACTS_DIR}")
    known_customers: set[str] = set()

    while True:
        contracts = load_contracts()
        for dataset in ["customers", "orders", "events"]:
            contract = contracts.get(dataset)
            if not contract:
                continue
            landing = DATA_DIR / "landing" / dataset
            landing.mkdir(parents=True, exist_ok=True)
            for path in sorted(landing.glob("*.csv")):
                try:
                    known_customers = (
                        process_file(
                            conn,
                            contract,
                            path,
                            known_customers if dataset != "customers" else None,
                        )
                        or known_customers
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[validator] error on {path}: {exc}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
