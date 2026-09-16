"""Task 5.1 — versioned Node 4 configuration (§4.2)."""

from __future__ import annotations

import pytest

from config.loader import load_node4_config
from config.models import Node4Config
from schemas.enums import FlagType, OverallSignalStrength, SignalStrength


def test_load_default_config() -> None:
    config = load_node4_config("1")
    assert config.ranking_version == "1.0"
    assert config.threshold_version == "1.0"
    assert config.critical_rules_version == "1.0"
    assert config.normalization_version == "risk_norm_v1.0"
    assert config.top_drivers_max == 5
    assert config.reference_date.isoformat() == "2026-08-15"


def test_weights_and_thresholds_exact() -> None:
    config = load_node4_config("1")
    assert config.quantitative_weight == 0.60
    assert config.qualitative_weight == 0.40
    assert config.agreement_bonus == 0.05
    assert config.risk_thresholds.medium == 0.40
    assert config.risk_thresholds.high == 0.70
    assert config.quantitative_thresholds.low == 0.20
    assert config.quantitative_thresholds.medium == 0.40
    assert config.quantitative_thresholds.high == 0.70
    assert config.confidence_weights.quantitative == 0.55
    assert config.confidence_weights.qualitative == 0.45


def test_hierarchy_weights_strength_scores_and_order() -> None:
    config = load_node4_config("1")
    assert config.hierarchy_weights[FlagType.CANCELLATION_INTENT] == 1.00
    assert config.hierarchy_weights[FlagType.RENEWAL_OR_CONTRACT_CONCERN] == 0.85
    assert config.hierarchy_weights[FlagType.COMPETITOR_MENTION] == 0.45
    assert config.hierarchy_weights[FlagType.OTHER] == 0.30
    assert config.hierarchy_weights[FlagType.POSITIVE_FEEDBACK] == 0.00
    assert config.strength_scores[SignalStrength.WEAK] == 0.33
    assert config.strength_scores[SignalStrength.MODERATE] == 0.66
    assert config.strength_scores[SignalStrength.STRONG] == 1.00
    assert config.strength_order[OverallSignalStrength.NONE] == 0
    assert config.strength_order[OverallSignalStrength.WEAK] == 1
    assert config.strength_order[OverallSignalStrength.MODERATE] == 2
    assert config.strength_order[OverallSignalStrength.STRONG] == 3


def test_all_thresholds_and_scores_within_unit_interval() -> None:
    config = load_node4_config("1")
    values = [
        config.risk_thresholds.medium,
        config.risk_thresholds.high,
        config.quantitative_thresholds.low,
        config.quantitative_thresholds.medium,
        config.quantitative_thresholds.high,
        *config.hierarchy_weights.values(),
        *config.strength_scores.values(),
    ]
    assert all(0.0 <= value <= 1.0 for value in values)


def test_weight_sum_invariant_is_enforced() -> None:
    data = load_node4_config("1").model_dump()
    data["quantitative_weight"] = 0.70
    data["qualitative_weight"] = 0.40
    with pytest.raises(ValueError):
        Node4Config.model_validate(data)


def test_positive_feedback_weight_must_be_zero() -> None:
    data = load_node4_config("1").model_dump()
    data["hierarchy_weights"][FlagType.POSITIVE_FEEDBACK] = 0.10
    with pytest.raises(ValueError):
        Node4Config.model_validate(data)


def test_unknown_config_version_fails_loudly() -> None:
    with pytest.raises(FileNotFoundError):
        load_node4_config("does-not-exist")
