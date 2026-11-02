#!/usr/bin/env python3
"""CI helper: validate fixture CSVs against YAML contracts using the Pandera engine."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validator"))

from engine import validate_csv_path  # noqa: E402


def load_contract(name: str) -> dict:
    path = ROOT / "contracts" / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def summarize(checks: list[dict]) -> tuple[float, bool]:
    passed = sum(1 for c in checks if c["passed"])
    total = len(checks) or 1
    return passed / total, passed == total


def main() -> int:
    failures = 0

    valid_contract = load_contract("customers")
    _, valid_checks = validate_csv_path(ROOT / "fixtures" / "customers_valid.csv", valid_contract)
    rate, all_pass = summarize(valid_checks)
    sla = float(valid_contract.get("sla_pass_rate", 0.95))
    print(f"[ci] customers_valid pass_rate={rate:.1%} all_pass={all_pass}")
    if rate < sla:
        print("[ci] FAIL: expected valid fixture to meet SLA")
        failures += 1
    if not any(c["name"].startswith("pandera:") for c in valid_checks):
        print("[ci] FAIL: expected Pandera column checks to run")
        failures += 1

    _, invalid_checks = validate_csv_path(ROOT / "fixtures" / "customers_invalid.csv", valid_contract)
    inv_rate, _ = summarize(invalid_checks)
    print(f"[ci] customers_invalid pass_rate={inv_rate:.1%}")
    if inv_rate >= sla:
        print("[ci] FAIL: expected invalid fixture to breach SLA")
        failures += 1
    failed_names = [c["name"] for c in invalid_checks if not c["passed"]]
    print(f"[ci] failed checks: {failed_names}")
    if not failed_names:
        print("[ci] FAIL: invalid fixture produced no failures")
        failures += 1

    if failures:
        print(f"[ci] {failures} failure(s)")
        return 1
    print("[ci] all fixture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
