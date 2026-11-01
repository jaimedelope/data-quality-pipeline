"""Emit synthetic e-commerce order batches with intentional data-quality defects."""

from __future__ import annotations

import csv
import json
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

LANDING_DIR = Path(os.getenv("LANDING_DIR", "/landing"))
BATCH_INTERVAL = float(os.getenv("BATCH_INTERVAL_SEC", "12"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "80"))
DEFECT_RATE = float(os.getenv("DEFECT_RATE", "0.12"))

CURRENCIES = ["EUR", "USD", "GBP"]
STATUSES = ["pending", "paid", "shipped", "cancelled"]
PRODUCTS = [f"SKU-{i:03d}" for i in range(1, 41)]
USERS = [f"user_{i:03d}" for i in range(1, 61)]

COLUMNS = [
    "order_id",
    "user_id",
    "product_id",
    "amount",
    "currency",
    "status",
    "event_time",
    "created_at_ms",
    "injected_defect",
]

DEFECT_TYPES = [
    "null_user",
    "null_amount",
    "null_currency",
    "duplicate_order",
    "invalid_amount",
    "invalid_currency",
    "invalid_status",
    "future_ts",
    "stale_ts",
    "orphan_product",
]


def make_good_row(order_id: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    event_time = now - timedelta(seconds=random.randint(0, 600))
    return {
        "order_id": order_id or str(uuid.uuid4()),
        "user_id": random.choice(USERS),
        "product_id": random.choice(PRODUCTS),
        "amount": round(random.uniform(5.0, 250.0), 2),
        "currency": random.choice(CURRENCIES),
        "status": random.choice(STATUSES),
        "event_time": event_time.isoformat(),
        "created_at_ms": int(time.time() * 1000),
        "injected_defect": "",
    }


def inject_defect(row: dict, defect: str, previous_ids: list[str]) -> dict:
    row = dict(row)
    row["injected_defect"] = defect
    now = datetime.now(timezone.utc)

    if defect == "null_user":
        row["user_id"] = None
    elif defect == "null_amount":
        row["amount"] = None
    elif defect == "null_currency":
        row["currency"] = None
    elif defect == "duplicate_order" and previous_ids:
        row["order_id"] = random.choice(previous_ids)
    elif defect == "invalid_amount":
        row["amount"] = round(random.choice([-25.5, 0.0, 99999.0]), 2)
    elif defect == "invalid_currency":
        row["currency"] = random.choice(["BTC", "XXX", "YEN"])
    elif defect == "invalid_status":
        row["status"] = random.choice(["refunded_oops", "UNKNOWN", ""])
    elif defect == "future_ts":
        row["event_time"] = (now + timedelta(hours=random.randint(1, 48))).isoformat()
    elif defect == "stale_ts":
        row["event_time"] = (now - timedelta(hours=random.randint(2, 72))).isoformat()
    elif defect == "orphan_product":
        row["product_id"] = f"ORPHAN-{random.randint(1000, 9999)}"
    return row


def build_batch(size: int, defect_rate: float) -> list[dict]:
    rows: list[dict] = []
    seen_ids: list[str] = []
    roll = random.random()
    if roll < 0.08:
        size = max(5, int(size * random.uniform(0.15, 0.35)))
    elif roll < 0.16:
        size = int(size * random.uniform(1.6, 2.2))

    for _ in range(size):
        row = make_good_row()
        if random.random() < defect_rate:
            defect = random.choice(DEFECT_TYPES)
            if defect == "duplicate_order" and not seen_ids:
                defect = "orphan_product"
            row = inject_defect(row, defect, seen_ids)
        seen_ids.append(row["order_id"])
        rows.append(row)
    return rows


def write_batch(rows: list[dict], batch_id: str) -> tuple[Path, Path]:
    LANDING_DIR.mkdir(parents=True, exist_ok=True)
    json_path = LANDING_DIR / f"orders_{batch_id}.json"
    csv_path = LANDING_DIR / f"orders_{batch_id}.csv"
    meta = {
        "batch_id": batch_id,
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(rows),
        "defect_count": sum(1 for r in rows if r.get("injected_defect")),
        "rows": rows,
    }
    json_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in COLUMNS})
    return json_path, csv_path


def main() -> None:
    LANDING_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f"[generator] landing={LANDING_DIR} interval={BATCH_INTERVAL}s "
        f"size≈{BATCH_SIZE} defect_rate={DEFECT_RATE}"
    )
    batch_n = 0
    while True:
        batch_n += 1
        batch_id = f"{int(time.time())}_{batch_n:04d}"
        rows = build_batch(BATCH_SIZE, DEFECT_RATE)
        json_path, csv_path = write_batch(rows, batch_id)
        defects = sum(1 for r in rows if r.get("injected_defect"))
        print(
            f"[generator] batch={batch_id} rows={len(rows)} defects={defects} "
            f"wrote={json_path.name},{csv_path.name}"
        )
        time.sleep(BATCH_INTERVAL)


if __name__ == "__main__":
    main()
