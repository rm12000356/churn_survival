"""Dataset 7 synthetic corpus — end-to-end tests.

The corpus (spec addendum §33, v1.2) is generated deterministically by
``scripts/generate_dataset7.py`` (master seed 2137457950, reference date
2026-08-15) and self-validated by ``scripts/validate_dataset7.py`` (58 checks).
These tests assert the artifacts exist and are byte-identical to the golden
files, the validator passes, Node 1 ingests the expected 4680/320 split with
the documented passthrough behaviour, and Node 2 recovers the intended adjusted
hazard directions (starter/contract/usage/tickets) without a stratified refit
(PH severity none) in this fit.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import pytest

from config.loader import load_node1_config, load_node2_config
from node1.node import UnmappedFormatError, run_node1
from node2.node import fit_model
from router.fingerprint import extract_fingerprint
from router.router import _default_adapters, route
from schemas.enums import ModelType, ValidationStatus
from scripts import validate_dataset7

REPO = Path(__file__).resolve().parents[2]
RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"
TRUTH_JSON = REPO / "data" / "ground_truth" / "dataset7_ground_truth.json"
# Hand-made router fixtures are committed (data/ is gitignored; CI has no copy).
FIXTURES = REPO / "tests" / "fixtures" / "dataset7"
MODERN_CSV = FIXTURES / "dataset7_customers_modern.csv"
GERMAN_CSV = FIXTURES / "dataset7_customers_german.csv"

GOLDEN_SHA256 = {
    RAW_CSV: "BB7ADC382A13A34B47D12F537E7085877A1B1B9AD416ED8DBFBC5138D8FBF979",
    THREADS_JSON: "A4A274C7EF34024ED2D9D31C9A3F877B6088A66081BC46E211E1CC4A3FEB4DE3",
    TRUTH_JSON: "AF30A2AAB68D97DE752AB8C02D207A0942A62224311CFDB6BDBEABAE811F2011",
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


def test_validator_passes_all_58_checks() -> None:
    report = validate_dataset7.validate_all(RAW_CSV, THREADS_JSON, TRUTH_JSON)
    assert len(report.checks) == 58
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
    assert gen["events_generated"] == 420
    assert gen["threads_generated"] == 5730
    assert gen["tickets_reconciled_customers"] == 64
    counts = truth["cohort_counts"]
    assert counts["strong_cancellation_intent"] == 125
    assert counts["moderate_cancellation_intent"] == 60
    assert counts["weak_cancellation_intent"] == 40
    assert counts["repeated_issues"] == 100
    assert counts["cross_channel_duplicate"] == 25
    assert counts["unsupported_language"] == 30
    assert len(truth["cross_channel_duplicates"]) == 25


def test_v1_2_oracle_fields() -> None:
    truth = json.loads(TRUTH_JSON.read_text(encoding="utf-8"))
    assert truth["metadata"]["specification_version"] == "1.2"
    assert truth["generator"]["generator_version"] == "1.1"
    assert truth["dgp_truth"]["time_varying_effect"]["enabled"] is True
    node2 = truth["pipeline_expectations"]["node2"]
    assert node2["expected_model"] == "cox_ph"
    assert node2["plan_tier_recoverability"] == "partial"
    assert node2["strata_used"] is None
    assert node2["ph_severity"] == "none"
    assert truth["directions"]["plan_tier"]["effect"]["starter"] == "recovered_higher_hazard"
    assert truth["directions"]["plan_tier"]["effect"]["pro"] == "no_reliable_adjusted_claim"
    missingness = truth["generator"]["missingness"]
    assert missingness["n_customers_with_missing"] == 495
    assert missingness["n_customers_complete"] == 4055


def test_node1_ingests_4680_of_5000() -> None:
    out = run_node1(RAW_CSV, config=load_node1_config("dataset7"))
    report = out.validation_report
    assert report.status in (ValidationStatus.PASSED, ValidationStatus.PARTIAL)
    assert report.n_input_rows == 5000
    assert report.n_accepted == 4680
    assert report.n_rejected == 320
    assert report.adapter_used == "mapping:e6bfd1745c54"
    codes = Counter(error["code"] for error in report.errors)
    assert codes["WINDOW_ORDER"] == 150
    assert codes["TENURE_INVALID"] == 150
    assert codes["UNIQUE_ID"] == 80
    assert codes["FUTURE_LEAKAGE"] == 50
    assert codes["EVENT_OBSERVED"] == 40
    assert "CORE_MISSING" not in codes
    passthrough = report.missingness_passthrough
    assert passthrough == {
        "contract_length_months": 283,
        "support_tickets_90d": 148,
        "usage_frequency": 205,
    }


def test_router_modern_csv_matches_clean_csv() -> None:
    frame = pd.read_csv(MODERN_CSV)
    decision = route(extract_fingerprint(frame), _default_adapters())
    assert decision.matched
    assert decision.adapter.name == "clean_csv"


def test_router_german_csv_unmapped() -> None:
    frame = pd.read_csv(GERMAN_CSV)
    decision = route(extract_fingerprint(frame), _default_adapters())
    assert not decision.matched
    from adapters.mapping_adapter import load_confirmed_mapping_adapters

    fingerprint = extract_fingerprint(frame)
    stray = [
        adapter.mapping_version
        for adapter in load_confirmed_mapping_adapters()
        if adapter.matches_signature(fingerprint)
    ]
    assert not stray, (
        f"config/mappings/{stray[0]}.json maps the German negative fixture, which must "
        "stay unmapped (it was likely confirmed while trying the UI) — remove it"
    )
    with pytest.raises(UnmappedFormatError):
        run_node1(GERMAN_CSV, config=load_node1_config("dataset7"))


def test_node2_recovers_adjusted_directions() -> None:
    out = run_node1(RAW_CSV, config=load_node1_config("dataset7"))
    artifact = fit_model(
        out.canonical_dataset, load_node2_config("1"), PREDICTORS
    )
    assert artifact.model_type == ModelType.COX_PH
    assert artifact.metadata.n_customers == 4055
    assert artifact.metadata.n_events == 353
    assert artifact.metadata.validation_metrics["c_index"] > 0.6
    coefs = artifact.metadata.coefficients
    assert coefs["plan_tier_starter"] > 0
    assert coefs["contract_length_months"] < 0
    assert coefs["usage_frequency"] < 0
    assert coefs["support_tickets_90d"] > 0
    assert artifact.metadata.assumption_check_results["strata_used"] is None
