from __future__ import annotations

from config.models import Node3Config, VocabularyConfig
from node3.node import run_node3
from tests.node3.conftest import NOW, message, thread


def test_customer_meta_versions(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    output = run_node3(
        ["CUST-1"],
        [thread("T1", messages=[message("m1", "I want to cancel my subscription.")])],
        node3_config,
        vocabulary=vocabulary,
        now=NOW,
    )
    meta = output.customer_signals[0].meta
    assert meta.prompt_version == node3_config.prompt_version
    assert meta.aggregation_version == node3_config.aggregation_version
    assert meta.vocabulary_version == node3_config.vocabulary_version
    assert meta.preprocessing_version == node3_config.preprocessing_version
    assert meta.lookback_days == node3_config.lookback_days
    assert meta.reference_date == node3_config.reference_date
    assert meta.processed_at == NOW


def test_thread_meta_versions(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    output = run_node3(
        ["CUST-1"],
        [thread("T1", messages=[message("m1", "I want to cancel my subscription.")])],
        node3_config,
        vocabulary=vocabulary,
        now=NOW,
    )
    meta = output.thread_signals[0].meta
    assert meta.prompt_version == node3_config.prompt_version
    assert meta.model_version == "offline"
    assert meta.n_customer_messages == 1
    assert meta.processed_at == NOW
