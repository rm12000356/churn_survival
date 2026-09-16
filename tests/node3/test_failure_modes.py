from __future__ import annotations

import json

from config.models import Node3Config, VocabularyConfig
from node3.node import run_node3
from tests.node3.conftest import NOW, message, thread


def _run(threads, customers, config, vocab):
    return run_node3(
        customers,
        threads,
        config,
        vocabulary=vocab,
        now=NOW,
    )


def test_zero_threads_is_no_data(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    output = _run([], ["CUST-1"], node3_config, vocabulary)
    signal = output.customer_signals[0]
    assert signal.support_data_status.value == "no_data"
    assert signal.overall_signal_confidence == 0.0
    assert signal.signal_strength.value == "none"
    assert signal.summary is None
    assert output.processing_report.n_threads_processed == 0


def test_only_system_messages(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    t = thread("T1", messages=[message("m1", "auto", role="system")])
    output = _run([t], ["CUST-1"], node3_config, vocabulary)
    signal = output.customer_signals[0]
    assert signal.support_data_status.value == "limited_data"
    assert signal.signal_strength.value == "none"


def test_invalid_thread_dropped(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    output = _run([{"thread_id": "T1"}], ["CUST-1"], node3_config, vocabulary)
    assert output.processing_report.warnings
    assert output.processing_report.n_threads_processed == 0


def test_unsupported_language_is_failed(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    t = thread("T1", language="es", messages=[message("m1", "dar de baja")])
    output = _run([t], ["CUST-1"], node3_config, vocabulary)
    assert output.processing_report.n_threads_failed == 1
    assert output.processing_report.n_threads_processed == 0


def test_missing_customer_id_dropped(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    output = _run([thread("T1", customer_id="")], ["CUST-1"], node3_config, vocabulary)
    errors = output.processing_report.errors
    assert any(e["code"] == "MISSING_CUSTOMER_ID" for e in errors)


def test_cross_channel_duplicate_collapsed(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m1", "My invoice shows the wrong amount.")],
        ),
        thread(
            "T2",
            created_at="2026-08-01T10:00:00Z",
            subject="Help",
            messages=[message("m2", "My invoice shows the wrong amount!")],
        ),
    ]
    output = _run(threads, ["CUST-1"], node3_config, vocabulary)
    assert output.processing_report.n_cross_channel_duplicates_collapsed == 1
    assert output.customer_signals[0].n_threads_in_window == 1


def test_report_serializes(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    output = _run([thread("T1")], ["CUST-1"], node3_config, vocabulary)
    payload = json.loads(output.model_dump_json())
    assert set(payload) == {"customer_signals", "thread_signals", "processing_report"}
