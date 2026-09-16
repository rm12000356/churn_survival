from __future__ import annotations

import pytest

from config.loader import load_node3_config, load_vocabulary
from schemas.enums import FlagType


def test_load_default_config() -> None:
    config = load_node3_config("1")
    assert config.reference_date.isoformat() == "2026-08-15"
    assert config.lookback_days == 365
    assert config.lambda_default == 0.015
    assert config.lambda_persistent == 0.004
    assert config.supported_languages == ["en"]
    assert config.dedup_tfidf_threshold == 0.82
    assert config.prompt_version == "prompt_v1.0"


def test_load_dataset7_config_widens_lookback() -> None:
    assert load_node3_config("dataset7").lookback_days == 1095


def test_unknown_config_version_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        load_node3_config("does-not-exist")


def test_load_vocabulary_ranks() -> None:
    vocab = load_vocabulary()
    assert vocab.vocabulary_version == "vocab_v1.0"
    assert vocab.ranks[FlagType.CANCELLATION_INTENT] == 1
    assert vocab.ranks[FlagType.COMPETITOR_MENTION] == 5
    assert vocab.ranks[FlagType.POSITIVE_FEEDBACK] is None
    assert vocab.ranks[FlagType.OTHER] is None
    assert vocab.governance.other_review_threshold_pct == 20.0
