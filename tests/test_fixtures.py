from __future__ import annotations

from pathlib import Path

import pytest

import tests.conftest as conftest
from tests.conftest import (
    make_active_customer,
    make_churned_customer,
    make_mapping_report,
    make_support_thread,
)


def test_active_customer_matches_architecture_example() -> None:
    record = make_active_customer()
    assert record["customer_id"] == "cus_8f3a2b1c"
    assert record["tenure"] == 521.0
    assert record["event_observed"] == 0
    assert record["meta"]["reference_date"] == "2026-08-15"


def test_churned_customer_matches_architecture_example() -> None:
    record = make_churned_customer()
    assert record["customer_id"] == "cus_9d2e4f7a"
    assert record["tenure"] == 472.0
    assert record["event_observed"] == 1


def test_support_thread_has_required_keys() -> None:
    thread = make_support_thread()
    for key in ("thread_id", "customer_id", "created_at", "messages"):
        assert key in thread
    roles = {m["role"] for m in thread["messages"]}
    assert roles <= {"customer", "agent", "system"}


def test_mapping_report_has_required_keys() -> None:
    report = make_mapping_report()
    for key in (
        "source_fingerprint",
        "proposed_mappings",
        "unmapped_columns",
        "suggested_extra_features",
        "data_quality_flags",
        "recommended_action",
        "llm_model_used",
        "generated_at",
    ):
        assert key in report
    assert report["source_fingerprint"]["n_sample_rows"] == 25


def test_factories_are_independent() -> None:
    a = make_active_customer()
    a["customer_id"] = "mutated"
    assert make_active_customer()["customer_id"] == "cus_8f3a2b1c"
    assert a["customer_id"] == "mutated"


def test_dataset7_corpus_is_noop_when_present() -> None:
    if not conftest.DATASET7_CORPUS.is_file():
        pytest.skip("dataset7 corpus absent in this checkout")
    conftest.require_dataset7_corpus()


def test_dataset7_corpus_skips_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest, "DATASET7_CORPUS", Path("/nonexistent/dataset7.csv"))
    monkeypatch.delenv("REQUIRE_DATASET7", raising=False)
    with pytest.raises(BaseException) as excinfo:
        conftest.require_dataset7_corpus()
    assert type(excinfo.value).__name__ == "Skipped"


def test_dataset7_corpus_fails_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conftest, "DATASET7_CORPUS", Path("/nonexistent/dataset7.csv"))
    monkeypatch.setenv("REQUIRE_DATASET7", "1")
    with pytest.raises(BaseException) as excinfo:
        conftest.require_dataset7_corpus()
    assert type(excinfo.value).__name__ == "Failed"
