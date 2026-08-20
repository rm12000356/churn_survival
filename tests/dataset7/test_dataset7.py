"""Dataset 7 synthetic corpus — end-to-end tests.

The corpus (spec addendum §33) is generated deterministically by
``scripts/generate_dataset7.py`` (master seed 2137457950, reference date
2026-08-15) and self-validated by ``scripts/validate_dataset7.py`` (40 checks).
These tests assert the artifacts exist and are byte-identical to the golden
files, the validator passes, Node 1 ingests the expected 4550/450 split with the
documented quarantine gates, and Node 2 recovers the intended adjusted hazard
directions (contract/usage/tickets) while stratifying on plan_tier during its
PH-violation refit.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from config.loader import load_node1_config, load_node2_config
from node1.node import run_node1
from node2.node import fit_model
from schemas.enums import ModelType, ValidationStatus
from scripts import validate_dataset7

REPO = Path(__file__).resolve().parents[2]
RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"
TRUTH_JSON = REPO / "data" / "ground_truth" / "dataset7_ground_truth.json"

GOLDEN_SHA256 = {
    RAW_CSV: "A3D2B48693FAE5BAACC2A6C7186B827E07D6463F4778EEC235DF7C74C01762D9",
    THREADS_JSON: "6284DD8C1AB27C0D9CDDE4A3193C73C3BB4E35829E0FBE600DAE171F4E8AF1C6",
    TRUTH_JSON: "FE5CF4CC6C70709F2247BE1BC2BE2520F2387424196B9CF7A91129C8676D8A99",
}

PREDICTORS = ["plan_tier", "contract_length_months", "usage_frequency", "support_tickets_90d"]


def test_artifacts_exist() -> None:
    for path in (RAW_CSV, THREADS_JSON, TRUTH_JSON):
        assert path.is_file(), path


def test_artifacts_are_byte_identical_golden() -> None:
    for path, expected in GOLDEN_SHA256.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        assert digest == expected, (
            f"{path.name} changed - re-run scripts/generate_dataset7.py and, if the "
            "change is intended, update GOLDEN_SHA256"
        )


def test_validator_passes_all_40_checks() -> None:
    report = validate_dataset7.validate_all(RAW_CSV, THREADS_JSON, TRUTH_JSON)
    assert len(report.checks) == 40
    assert report.passed
    for check in report.checks:
        assert check.passed, f"{check.id} {check.name}: {check.detail}"


def test_invalid_row_taxonomy() -> None:
    truth = json.loads(TRUTH_JSON.read_text(encoding="utf-8"))
    codes = Counter(row["error_code"] for row in truth["invalid_rows"])
    assert len(truth["invalid_rows"]) == 450
    assert codes == {
        "FUTURE_START_DATE": 60,
        "FUTURE_END_DATE": 50,
        "BAD_EVENT_VALUE": 40,
        "DUPLICATE_ID": 80,
        "MISSING_CORE": 100,
        "IMPOSSIBLE_TENURE": 60,
        "NEGATIVE_TENURE": 30,
        "INVALID_PLAN": 30,
    }


def test_scenario_oracle_and_generator_counts() -> None:
    truth = json.loads(TRUTH_JSON.read_text(encoding="utf-8"))
    gen = truth["generator"]
    assert gen["master_seed"] == 2137457950
    assert gen["n_raw_rows"] == 5000
    assert gen["events_generated"] == 511
    assert gen["threads_generated"] == 5735
    assert gen["tickets_reconciled_customers"] == 41
    counts = truth["cohort_counts"]
    assert counts["strong_cancellation_intent"] == 90
    assert counts["moderate_cancellation_intent"] == 60
    assert counts["weak_cancellation_intent"] == 40
    assert counts["repeated_issues"] == 100
    assert counts["cross_channel_duplicate"] == 25
    assert counts["unsupported_language"] == 30
    assert len(truth["cross_channel_duplicates"]) == 25


def test_node1_ingests_4550_of_5000() -> None:
    out = run_node1(RAW_CSV, config=load_node1_config("dataset7"))
    report = out.validation_report
    assert report.status in (ValidationStatus.PASSED, ValidationStatus.PARTIAL)
    assert report.n_input_rows == 5000
    assert report.n_accepted == 4550
    assert report.n_rejected == 450
    assert report.adapter_used == "mapping:e6bfd1745c54"
    codes = Counter(error["code"] for error in report.errors)
    assert codes["WINDOW_ORDER"] == 150
    assert codes["TENURE_INVALID"] == 150
    assert codes["CORE_MISSING"] == 130
    assert codes["UNIQUE_ID"] == 80
    assert codes["FUTURE_LEAKAGE"] == 50
    assert codes["EVENT_OBSERVED"] == 40


def test_node2_recovers_adjusted_directions() -> None:
    out = run_node1(RAW_CSV, config=load_node1_config("dataset7"))
    artifact = fit_model(
        out.canonical_dataset, load_node2_config("1"), PREDICTORS
    )
    assert artifact.model_type == ModelType.COX_PH
    assert artifact.metadata.n_customers == 4550
    assert artifact.metadata.n_events == 511
    assert artifact.metadata.validation_metrics["c_index"] > 0.6
    coefs = artifact.metadata.coefficients
    assert coefs["contract_length_months"] < 0
    assert coefs["usage_frequency"] < 0
    assert coefs["support_tickets_90d"] > 0
    assert artifact.metadata.assumption_check_results["strata_used"] == "plan_tier__raw"
