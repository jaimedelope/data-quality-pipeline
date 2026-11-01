"""Write synthetic warehouse CSV batches for contracted datasets."""

from __future__ import annotations

import csv
import os
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
INTERVAL = float(os.getenv("RUN_INTERVAL_SEC", "8"))
COUNTRIES = ["ES", "PT", "FR", "DE", "IT", "GB", "US"]
STATUSES = ["paid", "refunded", "chargeback"]
EVENTS = ["view_item", "add_to_cart", "checkout", "search"]


def ensure_dirs() -> None:
    for name in ("customers", "orders", "events"):
        (DATA_DIR / "landing" / name).mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "quarantine").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "processed").mkdir(parents=True, exist_ok=True)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def make_customers(n: int, defect_rate: float) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for _ in range(n):
        row = {
            "customer_id": f"cus_{uuid.uuid4().hex[:10]}",
            "email": f"user{random.randint(1, 99999)}@example.com",
            "country": random.choice(COUNTRIES),
            "signup_date": (now - timedelta(days=random.randint(1, 800))).date().isoformat(),
            "is_active": random.choice(["true", "false"]),
        }
        if random.random() < defect_rate:
            kind = random.choice(["bad_email", "bad_country", "dup", "null_email"])
            if kind == "bad_email":
                row["email"] = "not-an-email"
            elif kind == "bad_country":
                row["country"] = "ZZ"
            elif kind == "null_email":
                row["email"] = ""
            elif kind == "dup" and rows:
                row["customer_id"] = rows[0]["customer_id"]
        rows.append(row)
    return rows


def make_orders(n: int, customer_ids: list[str], defect_rate: float) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for _ in range(n):
        row = {
            "order_id": f"ord_{uuid.uuid4().hex[:10]}",
            "customer_id": random.choice(customer_ids) if customer_ids else f"cus_{uuid.uuid4().hex[:8]}",
            "amount_eur": f"{random.uniform(5, 400):.2f}",
            "status": random.choice(STATUSES),
            "ordered_at": (now - timedelta(minutes=random.randint(1, 180))).isoformat(),
        }
        if random.random() < defect_rate:
            kind = random.choice(["orphan", "neg", "bad_status", "dup", "null_customer"])
            if kind == "orphan":
                row["customer_id"] = f"cus_missing_{random.randint(100, 999)}"
            elif kind == "neg":
                row["amount_eur"] = f"{random.uniform(-200, -1):.2f}"
            elif kind == "bad_status":
                row["status"] = "pending"
            elif kind == "null_customer":
                row["customer_id"] = ""
            elif kind == "dup" and rows:
                row["order_id"] = rows[0]["order_id"]
        rows.append(row)
    return rows


def make_events(n: int, customer_ids: list[str], defect_rate: float) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for _ in range(n):
        row = {
            "event_id": f"evt_{uuid.uuid4().hex[:10]}",
            "customer_id": random.choice(customer_ids) if customer_ids else f"cus_{uuid.uuid4().hex[:8]}",
            "event_name": random.choice(EVENTS),
            "event_ts": (now - timedelta(minutes=random.randint(0, 90))).isoformat(),
            "session_id": f"ses_{uuid.uuid4().hex[:8]}",
        }
        if random.random() < defect_rate:
            kind = random.choice(["stale", "bad_name", "dup", "null_session"])
            if kind == "stale":
                row["event_ts"] = (now - timedelta(hours=random.randint(12, 48))).isoformat()
            elif kind == "bad_name":
                row["event_name"] = "click_mystery"
            elif kind == "null_session":
                row["session_id"] = ""
            elif kind == "dup" and rows:
                row["event_id"] = rows[0]["event_id"]
        rows.append(row)
    return rows


def emit_run() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # Occasional volume shock on one dataset
    cust_n = random.randint(45, 100)
    order_n = random.randint(70, 150)
    event_n = random.randint(90, 200)
    if random.random() < 0.12:
        shock = random.choice(["customers", "orders", "events"])
        if shock == "customers":
            cust_n = random.choice([15, 200])
        elif shock == "orders":
            order_n = random.choice([20, 260])
        else:
            event_n = random.choice([30, 320])

    defect = 0.10
    customers = make_customers(cust_n, defect)
    customer_ids = [r["customer_id"] for r in customers]
    orders = make_orders(order_n, customer_ids, defect)
    events = make_events(event_n, customer_ids, defect)

    write_csv(
        DATA_DIR / "landing" / "customers" / f"customers_{stamp}.csv",
        ["customer_id", "email", "country", "signup_date", "is_active"],
        customers,
    )
    write_csv(
        DATA_DIR / "landing" / "orders" / f"orders_{stamp}.csv",
        ["order_id", "customer_id", "amount_eur", "status", "ordered_at"],
        orders,
    )
    write_csv(
        DATA_DIR / "landing" / "events" / f"events_{stamp}.csv",
        ["event_id", "customer_id", "event_name", "event_ts", "session_id"],
        events,
    )
    print(
        f"[generator] run {stamp} customers={cust_n} orders={order_n} events={event_n}"
    )


def main() -> None:
    ensure_dirs()
    print(f"[generator] writing warehouse batches every {INTERVAL}s → {DATA_DIR}")
    while True:
        emit_run()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
