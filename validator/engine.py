"""Build Pandera schemas from YAML contracts and run structured checks."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import pandera as pa
from pandera import Check, Column, DataFrameSchema
from pandera.errors import SchemaErrors


def _dtype(rules: dict) -> Any:
    t = (rules.get("type") or "string").lower()
    if t == "float":
        return "Float64"
    if t == "bool":
        return "boolean"
    if t in {"date", "datetime"}:
        return "string"
    return "string"


def prepare_frame(df: pd.DataFrame, contract: dict) -> pd.DataFrame:
    out = df.copy()
    for col, rules in (contract.get("columns") or {}).items():
        if col not in out.columns:
            out[col] = pd.NA
        if rules.get("type") == "float":
            out[col] = pd.to_numeric(out[col], errors="coerce")
        elif rules.get("type") == "bool":
            out[col] = (
                out[col]
                .astype("string")
                .str.lower()
                .map({"true": True, "false": False, "1": True, "0": False})
            )
        else:
            out[col] = out[col].astype("string").replace({"": pd.NA})
    return out


def build_column_schema(col: str, rules: dict) -> DataFrameSchema:
    checks: list[Check] = []
    if rules.get("allowed"):
        checks.append(Check.isin(rules["allowed"], error=f"allowed:{col}"))
    if rules.get("pattern"):
        pattern = rules["pattern"]
        checks.append(Check.str_matches(pattern, error=f"pattern:{col}"))
    if rules.get("type") == "float":
        if "min" in rules:
            mn = float(rules["min"])
            checks.append(Check.ge(mn, error=f"min:{col}"))
        if "max" in rules:
            mx = float(rules["max"])
            checks.append(Check.le(mx, error=f"max:{col}"))

    nullable = not bool(rules.get("required", False))
    return DataFrameSchema(
        {
            col: Column(
                _dtype(rules),
                nullable=nullable,
                unique=bool(rules.get("unique", False)),
                checks=checks,
                required=True,
                coerce=True,
            )
        },
        coerce=True,
    )


def _sample_from_failure(df: pd.DataFrame, failure_cases: pd.DataFrame, limit: int = 5) -> list[dict]:
    if failure_cases is None or failure_cases.empty:
        return []
    idx = failure_cases.get("index")
    if idx is None:
        return failure_cases.head(limit).to_dict(orient="records")
    rows = df.loc[idx.dropna().astype(int)].head(limit)
    return rows.fillna("").astype(str).to_dict(orient="records")


def run_pandera_column_checks(df: pd.DataFrame, contract: dict) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for col, rules in (contract.get("columns") or {}).items():
        schema = build_column_schema(col, rules)
        try:
            schema.validate(df[[col]], lazy=True)
            results.append(
                {
                    "name": f"pandera:{col}",
                    "passed": True,
                    "detail": f"Pandera checks passed for {col}",
                    "sample": [],
                    "engine": "pandera",
                }
            )
        except SchemaErrors as err:
            # Collapse into one failed check with failure detail
            fc = err.failure_cases
            detail_bits = []
            if fc is not None and not fc.empty and "check" in fc.columns:
                detail_bits = sorted({str(x) for x in fc["check"].dropna().tolist()})
            results.append(
                {
                    "name": f"pandera:{col}",
                    "passed": False,
                    "detail": f"Pandera failed for {col}"
                    + (f" ({', '.join(detail_bits[:4])})" if detail_bits else ""),
                    "sample": _sample_from_failure(df, fc),
                    "engine": "pandera",
                }
            )
    return results


def run_expectation_checks(
    df: pd.DataFrame,
    contract: dict,
    customer_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    id_col = next(iter(contract.get("columns") or {"id": {}}))

    # FK references from column rules
    for col, rules in (contract.get("columns") or {}).items():
        if rules.get("references") and customer_ids is not None:
            series = df[col].astype("string")
            mask = series.notna() & ~series.isin(customer_ids)
            bad = df.loc[mask]
            results.append(
                {
                    "name": f"fk:{col}",
                    "passed": bad.empty,
                    "detail": f"{len(bad)} orphan {col} values" if len(bad) else f"{col} FK ok",
                    "sample": bad.head(5).fillna("").astype(str).to_dict(orient="records"),
                    "engine": "contract",
                }
            )

    for exp in contract.get("expectations") or []:
        et = exp.get("type")
        if et == "row_count_between":
            n = len(df)
            passed = exp["min"] <= n <= exp["max"]
            results.append(
                {
                    "name": "row_count_between",
                    "passed": passed,
                    "detail": f"row_count={n} expected [{exp['min']}, {exp['max']}]",
                    "sample": [] if passed else [{"row_count": n}],
                    "engine": "contract",
                }
            )
        elif et == "null_rate_below":
            col = exp["column"]
            nulls = int(df[col].isna().sum()) if col in df.columns else len(df)
            rate = nulls / max(len(df), 1)
            passed = rate <= float(exp["max"])
            results.append(
                {
                    "name": f"null_rate:{col}",
                    "passed": passed,
                    "detail": f"null_rate={rate:.2%} max={exp['max']}",
                    "sample": [],
                    "engine": "contract",
                }
            )
        elif et == "freshness_hours":
            col = exp["column"]
            max_age = float(exp["max_age_hours"])
            now = datetime.now(timezone.utc)
            stale_idx = []
            for i, raw in df[col].items():
                if pd.isna(raw):
                    stale_idx.append(i)
                    continue
                try:
                    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                except ValueError:
                    stale_idx.append(i)
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_h = (now - ts).total_seconds() / 3600.0
                if age_h > max_age or age_h < -0.1:
                    stale_idx.append(i)
            bad = df.loc[stale_idx]
            results.append(
                {
                    "name": f"freshness:{col}",
                    "passed": bad.empty,
                    "detail": f"{len(bad)} rows older than {max_age}h or unparsable",
                    "sample": bad.head(5).fillna("").astype(str).to_dict(orient="records"),
                    "engine": "contract",
                }
            )
    return results


def validate_dataframe(
    df: pd.DataFrame,
    contract: dict,
    customer_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    prepared = prepare_frame(df, contract)
    checks = run_pandera_column_checks(prepared, contract)
    checks.extend(run_expectation_checks(prepared, contract, customer_ids))
    return checks


def validate_csv_path(
    path: str | Any,
    contract: dict,
    customer_ids: set[str] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    df = pd.read_csv(path, dtype="string")
    return df, validate_dataframe(df, contract, customer_ids)
