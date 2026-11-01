"""Validate landing CSV batches against YAML data contracts; persist scorecards in SQLite."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
CONTRACTS_DIR = Path(os.getenv("CONTRACTS_DIR", "./contracts"))
POLL = float(os.getenv("POLL_INTERVAL_SEC", "3"))
DB_PATH = DATA_DIR / "quality.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
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
          duration_ms REAL NOT NULL
        )
        """
    )
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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_contract_checks(
    dataset: str,
    contract: dict,
    rows: list[dict[str, str]],
    customer_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    columns: dict = contract.get("columns") or {}

    # required / type-ish / allowed / unique / pattern / min-max
    for col, rules in columns.items():
        missing = [r for r in rows if rules.get("required") and not (r.get(col) or "").strip()]
        sample = [r.get(col) or r.get(next(iter(r), ""), "") for r in missing[:5]]
        # better sample with ids
        id_col = next(iter(columns))
        offenders = [{id_col: r.get(id_col), "field": col} for r in missing[:5]]
        checks.append(
            {
                "name": f"required:{col}",
                "passed": len(missing) == 0,
                "detail": f"{len(missing)} missing values in {col}" if missing else f"{col} complete",
                "sample": offenders,
            }
        )

        if rules.get("unique"):
            seen: dict[str, int] = {}
            for r in rows:
                v = r.get(col, "")
                if not v:
                    continue
                seen[v] = seen.get(v, 0) + 1
            dups = [k for k, n in seen.items() if n > 1]
            checks.append(
                {
                    "name": f"unique:{col}",
                    "passed": len(dups) == 0,
                    "detail": f"{len(dups)} duplicate {col} values" if dups else f"{col} unique",
                    "sample": [{col: d} for d in dups[:5]],
                }
            )

        allowed = rules.get("allowed")
        if allowed:
            bad = [r for r in rows if (r.get(col) or "") and r.get(col) not in allowed]
            checks.append(
                {
                    "name": f"allowed:{col}",
                    "passed": len(bad) == 0,
                    "detail": f"{len(bad)} rows outside allowed set for {col}" if bad else f"{col} values allowed",
                    "sample": [{id_col: r.get(id_col), col: r.get(col)} for r in bad[:5]],
                }
            )

        pattern = rules.get("pattern")
        if pattern:
            rx = re.compile(pattern)
            bad = [r for r in rows if (r.get(col) or "") and not rx.match(r.get(col, ""))]
            checks.append(
                {
                    "name": f"pattern:{col}",
                    "passed": len(bad) == 0,
                    "detail": f"{len(bad)} rows fail pattern on {col}" if bad else f"{col} matches pattern",
                    "sample": [{id_col: r.get(id_col), col: r.get(col)} for r in bad[:5]],
                }
            )

        if rules.get("type") == "float":
            bad = []
            for r in rows:
                raw = r.get(col, "")
                if not raw:
                    continue
                try:
                    val = float(raw)
                except ValueError:
                    bad.append(r)
                    continue
                if "min" in rules and val < float(rules["min"]):
                    bad.append(r)
                if "max" in rules and val > float(rules["max"]):
                    bad.append(r)
            checks.append(
                {
                    "name": f"range:{col}",
                    "passed": len(bad) == 0,
                    "detail": f"{len(bad)} rows outside numeric range for {col}" if bad else f"{col} in range",
                    "sample": [{id_col: r.get(id_col), col: r.get(col)} for r in bad[:5]],
                }
            )

        refs = rules.get("references")
        if refs and customer_ids is not None:
            bad = [r for r in rows if (r.get(col) or "") and r.get(col) not in customer_ids]
            checks.append(
                {
                    "name": f"fk:{col}",
                    "passed": len(bad) == 0,
                    "detail": f"{len(bad)} orphan {col} values" if bad else f"{col} FK ok",
                    "sample": [{id_col: r.get(id_col), col: r.get(col)} for r in bad[:5]],
                }
            )

    for exp in contract.get("expectations") or []:
        et = exp.get("type")
        if et == "row_count_between":
            n = len(rows)
            passed = exp["min"] <= n <= exp["max"]
            checks.append(
                {
                    "name": "row_count_between",
                    "passed": passed,
                    "detail": f"row_count={n} expected [{exp['min']}, {exp['max']}]",
                    "sample": [] if passed else [{"row_count": n}],
                }
            )
        elif et == "null_rate_below":
            col = exp["column"]
            nulls = sum(1 for r in rows if not (r.get(col) or "").strip())
            rate = nulls / max(len(rows), 1)
            passed = rate <= float(exp["max"])
            checks.append(
                {
                    "name": f"null_rate:{col}",
                    "passed": passed,
                    "detail": f"null_rate={rate:.2%} max={exp['max']}",
                    "sample": [],
                }
            )
        elif et == "freshness_hours":
            col = exp["column"]
            max_age = float(exp["max_age_hours"])
            now = datetime.now(timezone.utc)
            stale = []
            for r in rows:
                ts = parse_dt(r.get(col, ""))
                if ts is None:
                    stale.append(r)
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_h = (now - ts).total_seconds() / 3600.0
                if age_h > max_age or age_h < -0.1:
                    stale.append(r)
            checks.append(
                {
                    "name": f"freshness:{col}",
                    "passed": len(stale) == 0,
                    "detail": f"{len(stale)} rows older than {max_age}h or unparsable",
                    "sample": [{next(iter(columns)): r.get(next(iter(columns))), col: r.get(col)} for r in stale[:5]],
                }
            )

    return checks


def persist(conn: sqlite3.Connection, dataset: str, file_name: str, contract: dict, checks: list[dict], row_count: int, duration_ms: float) -> str:
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    pass_rate = passed / total if total else 1.0
    sla = float(contract.get("sla_pass_rate", 0.95))
    sla_met = 1 if pass_rate >= sla else 0
    conn.execute(
        """
        INSERT INTO runs (id, dataset, file_name, started_at, finished_at, row_count,
          checks_total, checks_passed, pass_rate, sla_pass_rate, sla_met, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, dataset, file_name, now, now, row_count, total, passed, pass_rate, sla, sla_met, duration_ms),
    )
    for c in checks:
        conn.execute(
            """
            INSERT INTO check_results (run_id, dataset, check_name, passed, detail, sample_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, dataset, c["name"], 1 if c["passed"] else 0, c["detail"], json.dumps(c.get("sample") or [])),
        )
    conn.commit()
    return run_id


def process_file(conn: sqlite3.Connection, contract: dict, path: Path, customer_ids: set[str] | None) -> set[str] | None:
    dataset = contract["dataset"]
    t0 = time.time()
    rows = read_csv(path)
    checks = run_contract_checks(dataset, contract, rows, customer_ids)
    duration_ms = round((time.time() - t0) * 1000, 2)
    run_id = persist(conn, dataset, path.name, contract, checks, len(rows), duration_ms)
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    pass_rate = passed / total if total else 1.0
    sla = float(contract.get("sla_pass_rate", 0.95))

    dest_root = DATA_DIR / ("processed" if pass_rate >= sla else "quarantine") / dataset
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / path.name
    path.replace(dest)

    print(
        f"[validator] {dataset} {path.name} pass={pass_rate:.0%} "
        f"sla={'MET' if pass_rate >= sla else 'BREACH'} → {dest_root.name}/ ({duration_ms}ms) run={run_id[:8]}"
    )

    if dataset == "customers":
        # ids from processed content for FK of later files in same poll cycle;
        # use rows we just read
        return {r.get("customer_id", "") for r in rows if r.get("customer_id")}
    return customer_ids


def main() -> None:
    (DATA_DIR / "landing").mkdir(parents=True, exist_ok=True)
    conn = connect()
    print(f"[validator] watching {DATA_DIR / 'landing'} against {CONTRACTS_DIR}")
    known_customers: set[str] = set()

    while True:
        contracts = load_contracts()
        # Process customers first so FK lookups work within a run
        order = ["customers", "orders", "events"]
        for dataset in order:
            contract = contracts.get(dataset)
            if not contract:
                continue
            landing = DATA_DIR / "landing" / dataset
            landing.mkdir(parents=True, exist_ok=True)
            files = sorted(landing.glob("*.csv"))
            for path in files:
                try:
                    known_customers = process_file(
                        conn,
                        contract,
                        path,
                        known_customers if dataset != "customers" else None,
                    ) or known_customers
                except Exception as exc:  # noqa: BLE001
                    print(f"[validator] error on {path}: {exc}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
