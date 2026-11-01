"""Run data-quality checks on landing batches and publish observability metrics."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

REQUIRED_COLS = [
    "order_id",
    "user_id",
    "product_id",
    "amount",
    "currency",
    "status",
    "channel",
    "event_time",
]
VALID_CURRENCIES = {"EUR", "USD", "GBP"}
VALID_STATUSES = {"pending", "paid", "shipped", "cancelled"}
FRESH_MAX_AGE_MIN = 180  # 3 hours
EXPECTED_ROWS_MIN = 40
EXPECTED_ROWS_MAX = 160


def wait_redis(url: str, retries: int = 60) -> redis.Redis:
    client = redis.Redis.from_url(url, decode_responses=True)
    for attempt in range(1, retries + 1):
        try:
            client.ping()
            print("[checker] redis connected")
            return client
        except Exception as exc:  # noqa: BLE001
            print(f"[checker] waiting for redis ({attempt}/{retries}): {exc}")
            time.sleep(2)
    raise RuntimeError("Redis not available")


def parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def check_schema(rows: list[dict]) -> dict:
    missing = set()
    for row in rows[:50]:
        for col in REQUIRED_COLS:
            if col not in row:
                missing.add(col)
    passed = len(missing) == 0
    return {
        "name": "schema",
        "passed": passed,
        "metric": 1.0 if passed else 0.0,
        "detail": "All required columns present" if passed else f"Missing columns: {sorted(missing)}",
        "offenders": [],
    }


def check_completeness(rows: list[dict]) -> dict:
    critical = ["user_id", "amount", "currency", "order_id"]
    nulls = 0
    total = max(len(rows) * len(critical), 1)
    offenders = []
    for row in rows:
        for col in critical:
            if row.get(col) is None or row.get(col) == "":
                nulls += 1
                if len(offenders) < 8:
                    offenders.append({"order_id": row.get("order_id"), "field": col, "issue": "null"})
    null_rate = nulls / total
    passed = null_rate <= 0.03
    return {
        "name": "completeness",
        "passed": passed,
        "metric": round(1.0 - null_rate, 4),
        "detail": f"Null rate on critical fields: {null_rate:.2%}",
        "offenders": offenders,
    }


def check_uniqueness(rows: list[dict]) -> dict:
    seen: dict[str, int] = {}
    for row in rows:
        oid = row.get("order_id")
        if oid is None:
            continue
        seen[oid] = seen.get(oid, 0) + 1
    dups = [oid for oid, n in seen.items() if n > 1]
    passed = len(dups) == 0
    return {
        "name": "uniqueness",
        "passed": passed,
        "metric": round(1.0 - (len(dups) / max(len(seen), 1)), 4),
        "detail": "order_id unique" if passed else f"{len(dups)} duplicate order_id values",
        "offenders": [{"order_id": oid, "issue": "duplicate"} for oid in dups[:8]],
    }


def check_validity(rows: list[dict]) -> dict:
    bad = 0
    offenders = []
    for row in rows:
        issues = []
        amount = row.get("amount")
        if amount is not None:
            try:
                if float(amount) <= 0:
                    issues.append("amount<=0")
            except (TypeError, ValueError):
                issues.append("amount_not_numeric")
        currency = row.get("currency")
        if currency is not None and currency not in VALID_CURRENCIES:
            issues.append("bad_currency")
        status = row.get("status")
        if status is not None and status not in VALID_STATUSES:
            issues.append("bad_status")
        if issues:
            bad += 1
            if len(offenders) < 8:
                offenders.append({"order_id": row.get("order_id"), "issue": ",".join(issues)})
    rate = bad / max(len(rows), 1)
    passed = rate <= 0.05
    return {
        "name": "validity",
        "passed": passed,
        "metric": round(1.0 - rate, 4),
        "detail": f"Invalid rows: {bad}/{len(rows)} ({rate:.1%})",
        "offenders": offenders,
    }


def check_freshness(rows: list[dict], now: datetime) -> dict:
    bad = 0
    offenders = []
    for row in rows:
        ts = parse_time(row.get("event_time"))
        if ts is None:
            bad += 1
            if len(offenders) < 8:
                offenders.append({"order_id": row.get("order_id"), "issue": "bad_timestamp"})
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = (now - ts).total_seconds() / 60.0
        if age_min < -1:
            bad += 1
            if len(offenders) < 8:
                offenders.append({"order_id": row.get("order_id"), "issue": "future_timestamp"})
        elif age_min > FRESH_MAX_AGE_MIN:
            bad += 1
            if len(offenders) < 8:
                offenders.append({"order_id": row.get("order_id"), "issue": "stale_timestamp"})
    rate = bad / max(len(rows), 1)
    passed = rate <= 0.05
    return {
        "name": "freshness",
        "passed": passed,
        "metric": round(1.0 - rate, 4),
        "detail": f"Stale/future timestamps: {bad}/{len(rows)}",
        "offenders": offenders,
    }


def check_referential(rows: list[dict], catalog: list[str]) -> dict:
    catalog_set = set(catalog or [])
    bad = 0
    offenders = []
    for row in rows:
        pid = row.get("product_id")
        if pid not in catalog_set:
            bad += 1
            if len(offenders) < 8:
                offenders.append({"order_id": row.get("order_id"), "product_id": pid, "issue": "orphan_product"})
    rate = bad / max(len(rows), 1)
    passed = rate <= 0.02
    return {
        "name": "referential",
        "passed": passed,
        "metric": round(1.0 - rate, 4),
        "detail": f"Orphan product_id rows: {bad}/{len(rows)}",
        "offenders": offenders,
    }


def check_volume(row_count: int) -> dict:
    passed = EXPECTED_ROWS_MIN <= row_count <= EXPECTED_ROWS_MAX
    return {
        "name": "volume",
        "passed": passed,
        "metric": float(row_count),
        "detail": (
            f"Batch size {row_count} within [{EXPECTED_ROWS_MIN}, {EXPECTED_ROWS_MAX}]"
            if passed
            else f"Batch size {row_count} outside [{EXPECTED_ROWS_MIN}, {EXPECTED_ROWS_MAX}]"
        ),
        "offenders": [] if passed else [{"issue": "volume_anomaly", "row_count": row_count}],
    }


def run_checks(batch: dict) -> dict:
    started = time.time()
    now = datetime.now(timezone.utc)
    rows = batch.get("rows") or []
    catalog = batch.get("product_catalog") or []

    checks = [
        check_schema(rows),
        check_completeness(rows),
        check_uniqueness(rows),
        check_validity(rows),
        check_freshness(rows, now),
        check_referential(rows, catalog),
        check_volume(len(rows)),
    ]

    passed_n = sum(1 for c in checks if c["passed"])
    failed = [c for c in checks if not c["passed"]]
    duration_ms = round((time.time() - started) * 1000, 2)

    return {
        "batch_id": batch.get("batch_id"),
        "checked_at": now.isoformat(),
        "row_count": len(rows),
        "checks_total": len(checks),
        "checks_passed": passed_n,
        "checks_failed": len(failed),
        "pass_rate_pct": round((passed_n / len(checks)) * 100, 1) if checks else 0.0,
        "duration_ms": duration_ms,
        "checks": checks,
        "failed_checks": failed,
    }


def publish(r: redis.Redis, result: dict) -> None:
    pipe = r.pipeline()
    pipe.incr("metrics:runs_total")
    pipe.incrby("metrics:rows_total", int(result["row_count"]))
    pipe.incrby("metrics:checks_failed_total", int(result["checks_failed"]))
    pipe.lpush("metrics:run_duration_ms", result["duration_ms"])
    pipe.ltrim("metrics:run_duration_ms", 0, 199)
    pipe.lpush("metrics:pass_rate_pct", result["pass_rate_pct"])
    pipe.ltrim("metrics:pass_rate_pct", 0, 199)

    summary = {
        "batch_id": result["batch_id"],
        "checked_at": result["checked_at"],
        "row_count": result["row_count"],
        "pass_rate_pct": result["pass_rate_pct"],
        "checks_passed": result["checks_passed"],
        "checks_failed": result["checks_failed"],
        "duration_ms": result["duration_ms"],
        "failed_names": [c["name"] for c in result["failed_checks"]],
    }
    pipe.lpush("feed:runs", json.dumps(summary))
    pipe.ltrim("feed:runs", 0, 39)

    for check in result["failed_checks"]:
        failure = {
            "batch_id": result["batch_id"],
            "checked_at": result["checked_at"],
            "name": check["name"],
            "detail": check["detail"],
            "offenders": check.get("offenders", [])[:5],
        }
        pipe.lpush("feed:failures", json.dumps(failure))
    pipe.ltrim("feed:failures", 0, 49)

    pipe.set("checks:latest", json.dumps(result["checks"]))
    pipe.set("run:latest", json.dumps(summary))
    pipe.execute()


def main() -> None:
    r = wait_redis(REDIS_URL)
    print("[checker] waiting for landing batches…")
    while True:
        item = r.brpop("landing:batches", timeout=5)
        if not item:
            continue
        _, payload = item
        batch = json.loads(payload)
        result = run_checks(batch)
        publish(r, result)
        print(
            f"[checker] batch {str(result['batch_id'])[:8]} "
            f"pass={result['pass_rate_pct']}% failed={result['checks_failed']} "
            f"rows={result['row_count']} {result['duration_ms']}ms"
        )


if __name__ == "__main__":
    main()
