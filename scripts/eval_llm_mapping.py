"""Offline eval harness for the LLM mapping-report path.

Measures raw LLM mapping-report quality against the Task-6 success criteria:
- >= 90% of reports pass MappingReport schema validation on first try;
- 100% of proposed transformations are in the audited whitelist;
- 0 leakage / derived-future-looking columns proposed for core.<KEY>;
- 100% pass the strict deterministic validation (validate_mapping_report).

Runs only when an LLM provider + key are configured (see .env). Usage:

    uv run python scripts/eval_llm_mapping.py [--n-rows 25]

Prints a per-dataset table plus aggregate pass/fail. Exit code 0 when every
criterion holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from adapters.mapping_adapter import is_allowed_transformation
from router.fingerprint import extract_fingerprint
from router.llm_mapper import (
    build_mapping_prompt,
    create_llm_client,
    extract_json,
    validate_mapping_report,
)
from schemas.mapping import MappingReport

LEAKAGE_PATTERN = re.compile(
    r"(churn|attrit|exited|score|predict|risk|propensity|forecast|future|"
    r"classifier|bayes|\bcltv\b|\bltv\b|_ago|ago\b|days? since|last login)",
    re.IGNORECASE,
)

SCHEMA_VALID_MIN = 0.90
WHITELIST_MIN = 1.0
LEAKAGE_MAX = 0
STRICT_PASS_MIN = 1.0

SEED = 42


def _leakage(source_column: str) -> bool:
    return bool(LEAKAGE_PATTERN.search(source_column))


def _make_saas_snapshot() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(SEED)
    n = 60
    return pd.DataFrame(
        {
            "Account #": [f"ACC-{1000 + i}" for i in range(n)],
            "Acct Created Date": (
                pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 700, n), unit="D")
            ),
            "Plan Tier (Current)": rng.choice(["Basic", "Pro", "Enterprise"], n),
            "Sub Status": rng.choice(["Active", "Cancelled"], n, p=[0.85, 0.15]),
            "Monthly Recurring Revenue": np.round(rng.uniform(9.0, 99.0, n), 2),
            "Support Tickets Opened": rng.integers(0, 20, n),
            "Churn Probability (Next Quarter)": np.round(rng.uniform(0.0, 1.0, n), 3),
            "Predicted LTV": np.round(rng.uniform(50.0, 5000.0, n), 2),
            "Days Since Last Login": rng.integers(0, 90, n),
        }
    )


def _make_telecom() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(SEED)
    n = 60
    return pd.DataFrame(
        {
            "customerID": [f"7590-VHVEG-{i}" for i in range(n)],
            "Tenure Months": rng.integers(1, 72, n),
            "MonthlyCharges": np.round(rng.uniform(18.0, 118.0, n), 2),
            "InternetService": rng.choice(["DSL", "Fiber optic", "No"], n),
            "Contract": rng.choice(["Month-to-month", "One year", "Two year"], n),
            "Churn Label": rng.choice(["Yes", "No"], n, p=[0.27, 0.73]),
            "Churn Score (0-100)": rng.integers(0, 100, n),
            "CLTV": rng.integers(1000, 8000, n),
        }
    )


def _make_bank() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(SEED)
    n = 60
    return pd.DataFrame(
        {
            "RowNumber": range(1, n + 1),
            "CustomerId": rng.integers(15_000_000, 16_000_000, n),
            "Surname": [f"User{i}" for i in range(n)],
            "CreditScore": rng.integers(350, 850, n),
            "Geography": rng.choice(["France", "Spain", "Germany"], n),
            "Gender": rng.choice(["Female", "Male"], n),
            "Age": rng.integers(18, 92, n),
            "Tenure (Months)": rng.integers(0, 10, n),
            "Balance": np.round(rng.uniform(0.0, 250000.0, n), 2),
            "NumOfProducts": rng.integers(1, 4, n),
            "HasCrCard": rng.integers(0, 2, n),
            "IsActiveMember": rng.integers(0, 2, n),
            "EstimatedSalary": np.round(rng.uniform(0.0, 200000.0, n), 2),
            "Exited": rng.choice([0, 1], n, p=[0.8, 0.2]),
        }
    )


def _make_credit_card() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(SEED)
    n = 60
    return pd.DataFrame(
        {
            "CLIENTNUM": rng.integers(7_000_000_000, 7_100_000_000, n),
            "Attrition_Flag": rng.choice(
                ["Existing Customer", "Attrited Customer"], n, p=[0.84, 0.16]
            ),
            "Customer_Age": rng.integers(18, 92, n),
            "Months_on_book": rng.integers(6, 56, n),
            "Total_Trans_Ct": rng.integers(1, 150, n),
            "Total_Revolving_Bal": rng.integers(0, 2500, n),
            "Avg_Open_To_Buy": np.round(rng.uniform(0.0, 5000.0, n), 2),
            "Naive_Bayes_Classifier_1": np.round(rng.uniform(0.0, 1.0, n), 4),
            "Naive_Bayes_Classifier_2": np.round(rng.uniform(0.0, 1.0, n), 4),
        }
    )


def _make_iranian() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(SEED)
    n = 60
    return pd.DataFrame(
        {
            "Call  Failure": rng.integers(0, 5, n),
            "Complains": rng.integers(0, 2, n),
            "Subscription  Length": rng.integers(1, 60, n),
            "Charge  Amount": np.round(rng.uniform(0.0, 100.0, n), 2),
            "Seconds of Use": rng.integers(0, 8000, n),
            "Frequency of use": rng.integers(0, 160, n),
            "Age": rng.integers(18, 70, n),
            "Customer Value": np.round(rng.uniform(0.0, 2000.0, n), 2),
            "Churn": rng.choice([0, 1], n, p=[0.7, 0.3]),
        }
    )


def _make_mixed_dates() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(SEED)
    n = 60
    start_dates = pd.Timestamp("2022-01-01") + pd.to_timedelta(rng.integers(0, 1400, n), unit="D")
    start_str = [
        d.strftime("%Y-%m-%d") if i % 2 else d.strftime("%m/%d/%Y")
        for i, d in enumerate(start_dates)
    ]
    churned = rng.random(n) < 0.2
    churn_str = []
    for i, flag in enumerate(churned):
        if not flag:
            churn_str.append("")
        else:
            d = start_dates[i] + pd.to_timedelta(rng.integers(60, 400), unit="D")
            churn_str.append(d.strftime("%Y-%m-%d") if i % 2 else d.strftime("%d-%b-%Y"))
    return pd.DataFrame(
        {
            "Cust No": [f"C-{1000 + i}" for i in range(n)],
            "Started (Mixed Format)": start_str,
            "Left On (Mixed Format)": churn_str,
            "Acct Status": rng.choice(["active", "cancelled"], n, p=[0.8, 0.2]),
            "Tier Label": rng.choice(["silver", "gold", "platinum"], n),
            "Usage (units/mo)": np.round(rng.uniform(0.0, 500.0, n), 1),
        }
    )


def _datasets() -> list[tuple[str, pd.DataFrame]]:
    datasets: list[tuple[str, pd.DataFrame]] = [
        ("dataset6_saas (real)", pd.read_csv(Path("data/raw/dataset6_saas_churn_messy.csv"))),
        ("saas_snapshot", _make_saas_snapshot()),
        ("telecom_telco", _make_telecom()),
        ("bank_style", _make_bank()),
        ("creditcard_style", _make_credit_card()),
        ("iranian_style", _make_iranian()),
        ("mixed_date_formats", _make_mixed_dates()),
    ]
    return datasets


def _evaluate(client: Any, name: str, frame: pd.DataFrame, n_rows: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "n_columns": len(frame.columns),
        "schema_valid": False,
        "whitelist_transforms": 0,
        "total_transforms": 0,
        "leakage_in_core": [],
        "strict_valid": False,
        "error": None,
    }
    fingerprint = extract_fingerprint(frame)
    prompt = build_mapping_prompt(fingerprint, frame, n_rows=n_rows)
    try:
        raw = client.complete(prompt)
    except Exception as exc:  # noqa: BLE001 - report any transport/API failure
        result["error"] = f"LLM call failed: {exc}"
        return result

    try:
        payload = json.loads(extract_json(raw))
    except json.JSONDecodeError as exc:
        result["error"] = f"non-JSON reply: {exc}"
        return result

    try:
        report = MappingReport.model_validate(payload)
        result["schema_valid"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"schema validation failed: {exc}"
        return result

    for mapping in report.proposed_mappings:
        result["total_transforms"] += 1
        if is_allowed_transformation(mapping.transformation):
            result["whitelist_transforms"] += 1
        if mapping.target_field.startswith("core.") and _leakage(mapping.source_column):
            result["leakage_in_core"].append(
                f"{mapping.source_column!r} -> {mapping.target_field}"
            )

    try:
        validate_mapping_report(report)
        result["strict_valid"] = True
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"strict validation failed: {exc}"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-rows", type=int, default=25, help="sample rows shown to the LLM")
    args = parser.parse_args(argv)

    try:
        client = create_llm_client()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Configure LLM_PROVIDER / LLM_API_KEY / LLM_MODEL in .env to run the eval.",
            file=sys.stderr,
        )
        return 2

    print(f"model: {client.model}  provider: {client.provider}  sample_rows: {args.n_rows}")
    print()

    results = [_evaluate(client, name, frame, args.n_rows) for name, frame in _datasets()]

    header = f"{'dataset':<24}{'cols':>5}{'schema':>8}{'wl%':>6}{'leak':>6}{'strict':>8}  notes"
    print(header)
    print("-" * len(header))

    n_schema_valid = 0
    n_strict_valid = 0
    total_transforms = 0
    whitelist_transforms = 0
    leakage_total = 0

    for r in results:
        wl = (
            f"{(r['whitelist_transforms'] / r['total_transforms'] * 100):.0f}%"
            if r["total_transforms"]
            else "-"
        )
        note = r["error"] if r["error"] else ""
        if r["leakage_in_core"]:
            joined = "; ".join(r["leakage_in_core"])
            note = f"{note} {joined}" if note else joined
        print(
            f"{r['name']:<24}{r['n_columns']:>5}"
            f"{'YES' if r['schema_valid'] else 'NO':>8}{wl:>6}"
            f"{len(r['leakage_in_core']):>6}{'YES' if r['strict_valid'] else 'NO':>8}  {note}"
        )
        n_schema_valid += int(r["schema_valid"])
        n_strict_valid += int(r["strict_valid"])
        total_transforms += r["total_transforms"]
        whitelist_transforms += r["whitelist_transforms"]
        leakage_total += len(r["leakage_in_core"])

    n = len(results)
    schema_rate = n_schema_valid / n
    whitelist_rate = whitelist_transforms / total_transforms if total_transforms else 1.0
    strict_rate = n_strict_valid / n

    print()
    checks = [
        (
            "schema valid first try >= 90%",
            schema_rate >= SCHEMA_VALID_MIN,
            f"{schema_rate:.0%}",
        ),
        (
            "100% whitelisted transformations",
            whitelist_rate >= WHITELIST_MIN,
            f"{whitelist_rate:.0%}",
        ),
        ("no leakage into core_features", leakage_total <= LEAKAGE_MAX, str(leakage_total)),
        (
            "100% strict validation pass",
            strict_rate >= STRICT_PASS_MIN,
            f"{strict_rate:.0%}",
        ),
    ]
    all_pass = True
    for label, ok, value in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {value}")
        all_pass = all_pass and ok

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
