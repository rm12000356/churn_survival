from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.models import ActionRulesConfig, Node4Config, Node5Config

NODE4 = {
    "ranking_version": "1.0",
    "threshold_version": "1.0",
    "critical_rules_version": "1.0",
    "normalization_version": "risk_norm_v1.0",
    "top_drivers_max": 5,
    "quantitative_weight": 0.60,
    "qualitative_weight": 0.40,
    "agreement_bonus": 0.05,
    "risk_thresholds": {"medium": 0.40, "high": 0.70},
    "quantitative_thresholds": {"low": 0.20, "medium": 0.40, "high": 0.70},
    "confidence_weights": {"quantitative": 0.55, "qualitative": 0.45},
    "hierarchy_weights": {
        "cancellation_intent": 1.00,
        "renewal_or_contract_concern": 0.85,
        "product_bug_or_outage": 0.70,
        "poor_support_experience": 0.70,
        "billing_complaint": 0.55,
        "feature_missing": 0.55,
        "usage_drop_related": 0.55,
        "competitor_mention": 0.45,
        "positive_feedback": 0.00,
        "other": 0.30,
    },
    "strength_scores": {"weak": 0.33, "moderate": 0.66, "strong": 1.00},
    "strength_order": {"none": 0, "weak": 1, "moderate": 2, "strong": 3},
    "reference_date": "2026-08-15",
}


def test_node4_config_round_trip() -> None:
    config = Node4Config.model_validate(NODE4)
    assert config.ranking_version == "1.0"
    assert config.normalization_version == "risk_norm_v1.0"
    assert config.top_drivers_max == 5
    assert config.hierarchy_weights["cancellation_intent"] == 1.0
    assert config.hierarchy_weights["positive_feedback"] == 0.0
    assert config.strength_order["none"] == 0


def test_positive_feedback_weight_must_be_zero() -> None:
    payload = {
        **NODE4,
        "hierarchy_weights": {**NODE4["hierarchy_weights"], "positive_feedback": 0.10},
    }
    with pytest.raises(ValidationError):
        Node4Config.model_validate(payload)


def test_quantitative_qualitative_weights_must_sum_to_one() -> None:
    payload = {**NODE4, "quantitative_weight": 0.70, "qualitative_weight": 0.40}
    with pytest.raises(ValidationError):
        Node4Config.model_validate(payload)


def test_confidence_weights_must_sum_to_one() -> None:
    payload = {
        **NODE4,
        "confidence_weights": {"quantitative": 0.60, "qualitative": 0.50},
    }
    with pytest.raises(ValidationError):
        Node4Config.model_validate(payload)


def test_unknown_hierarchy_key_rejected() -> None:
    payload = {**NODE4, "hierarchy_weights": {**NODE4["hierarchy_weights"], "we_hate_you": 1.0}}
    with pytest.raises(ValidationError):
        Node4Config.model_validate(payload)


def test_config_is_immutable() -> None:
    config = Node4Config.model_validate(NODE4)
    assert config.model_config.get("frozen") is True
    with pytest.raises(ValidationError):
        config.quantitative_weight = 0.99


def test_node5_config_defaults() -> None:
    config = Node5Config.model_validate(
        {
            "report_version": "1.0",
            "prompt_version": "prompt_v1",
            "reference_date": "2026-08-15",
            "max_accounts_in_summary": 20,
            "max_evidence_per_account": 5,
        }
    )
    assert config.include_recommendations is True
    assert config.include_evidence is True
    assert config.language == "en"


def test_action_rules_round_trip() -> None:
    config = ActionRulesConfig.model_validate(
        {
            "action_rules_version": "1.0",
            "rules": {"cancellation_intent": "Contact the account."},
        }
    )
    assert config.rules["cancellation_intent"].startswith("Contact")
