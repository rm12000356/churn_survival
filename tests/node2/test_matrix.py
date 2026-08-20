"""Feature-matrix construction (ROADMAP Task 3.2/§2.11 encoding)."""

from __future__ import annotations

import numpy as np

from node2.matrix import (
    FeatureSpec,
    build_specs,
    encode,
    encode_categories,
    encoded_columns,
    feature_kinds,
    is_raw_column,
    usable_predictors,
)
from tests.node2.conftest import PREDICTORS, make_record, synthetic_dataset


def test_build_specs_numeric_vs_categorical() -> None:
    specs = build_specs(synthetic_dataset(), PREDICTORS)
    by_name = {spec.name: spec for spec in specs}
    assert by_name["contract_length_months"].kind == "numeric"
    assert by_name["usage_frequency"].kind == "numeric"
    assert by_name["plan_tier"].kind == "categorical"
    assert by_name["plan_tier"].categories == ("basic", "enterprise", "pro")


def test_build_specs_mixed_values_are_categorical() -> None:
    records = [
        make_record(0, tenure=100, event=0, plan_tier="basic"),
        make_record(1, tenure=100, event=0, plan_tier=None),
    ]
    specs = build_specs(records, ["plan_tier"])
    assert specs[0].kind == "categorical"


def test_encode_categories_drops_reference() -> None:
    spec = FeatureSpec(name="plan_tier", kind="categorical", categories=("basic", "pro", "x"))
    assert encode_categories(spec) == ("pro", "x")


def test_encoded_columns_order() -> None:
    specs = [
        FeatureSpec(name="usage_frequency", kind="numeric"),
        FeatureSpec(name="plan_tier", kind="categorical", categories=("basic", "pro")),
    ]
    assert encoded_columns(specs) == ["usage_frequency", "plan_tier_pro"]


def test_is_raw_column() -> None:
    assert is_raw_column("plan_tier__raw")
    assert not is_raw_column("plan_tier_pro")
    assert not is_raw_column("duration")


def test_encode_structure() -> None:
    records = [
        make_record(1, tenure=120, event=1, plan_tier="pro", usage_frequency=30.0),
        make_record(2, tenure=90, event=0, plan_tier="basic", usage_frequency=None),
    ]
    matrix = encode(
        [
            (r.customer_id, r.core_features.model_dump(), float(r.tenure), r.event_observed)
            for r in records
        ],
        build_specs(records, PREDICTORS),
    )
    assert list(matrix.index) == ["cus_0001", "cus_0002"]
    assert matrix["duration"].tolist() == [120.0, 90.0]
    assert matrix["event"].tolist() == [1, 0]
    assert matrix.loc["cus_0001", "plan_tier_pro"] == 1.0
    assert matrix.loc["cus_0001", "plan_tier__raw"] == "pro"
    assert np.isnan(matrix.loc["cus_0002", "usage_frequency"])


def test_encode_unseen_category_is_zero_row() -> None:
    record = make_record(1, tenure=100, event=0, plan_tier="platinum")
    specs = build_specs(synthetic_dataset(), PREDICTORS)  # known category vocabulary
    matrix = encode(
        [
            (
                record.customer_id,
                record.core_features.model_dump(),
                float(record.tenure),
                record.event_observed,
            )
        ],
        specs,
    )
    assert matrix.loc["cus_0001", "plan_tier_enterprise"] == 0.0
    assert matrix.loc["cus_0001", "plan_tier_pro"] == 0.0
    assert matrix.loc["cus_0001", "plan_tier__raw"] == "platinum"


def test_usable_predictors() -> None:
    core = {"plan_tier": "pro", "contract_length_months": 12.0, "usage_frequency": None}
    assert usable_predictors(core, PREDICTORS) == ["plan_tier", "contract_length_months"]


def test_feature_kinds() -> None:
    records = [
        make_record(0, tenure=100, event=0, usage_frequency=1.0),
        make_record(1, tenure=100, event=0, usage_frequency=2.0),
    ]
    kinds = feature_kinds(records, ["usage_frequency", "plan_tier"])
    assert kinds["usage_frequency"] == "numeric"
    assert kinds["plan_tier"] == "categorical"
