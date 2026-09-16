from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from config.loader import load_config, load_node4_config
from config.models import Node4Config

MINIMAL_NODE4 = {
    "ranking_version": "1.0",
    "threshold_version": "1.0",
    "critical_rules_version": "1.0",
    "normalization_version": "risk_norm_v1.0",
    "quantitative_weight": 0.60,
    "qualitative_weight": 0.40,
    "agreement_bonus": 0.05,
    "risk_thresholds": {"medium": 0.40, "high": 0.70},
    "quantitative_thresholds": {"low": 0.20, "medium": 0.40, "high": 0.70},
    "confidence_weights": {"quantitative": 0.55, "qualitative": 0.45},
    "hierarchy_weights": {"cancellation_intent": 1.00, "other": 0.30},
    "strength_scores": {"weak": 0.33, "moderate": 0.66, "strong": 1.00},
    "strength_order": {"none": 0, "weak": 1, "moderate": 2, "strong": 3},
    "reference_date": "2026-08-15",
}


def test_load_json_config(tmp_path) -> None:
    path = tmp_path / "node4_v1.json"
    path.write_text(json.dumps(MINIMAL_NODE4), encoding="utf-8")
    config = load_config(path, Node4Config)
    assert config.ranking_version == "1.0"
    assert config.hierarchy_weights["cancellation_intent"] == 1.0


def test_load_yaml_config(tmp_path) -> None:
    path = tmp_path / "node4_v1.yaml"
    path.write_text(json.dumps(MINIMAL_NODE4), encoding="utf-8")
    config = load_config(path, Node4Config)
    assert config.threshold_version == "1.0"


def test_incomplete_yaml_fails_loudly(tmp_path) -> None:
    path = tmp_path / "incomplete.yaml"
    path.write_text("ranking_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path, Node4Config)


def test_unknown_version_fails_loudly(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "config.loader.get_settings", lambda: type("S", (), {"CONFIG_DIR": tmp_path})()
    )
    with pytest.raises(FileNotFoundError):
        load_node4_config("999.0")


def test_malformed_config_fails_loudly(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_config(path, Node4Config)
