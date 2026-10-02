"""Deployment-declared model features (architecture §1.3a/§1.8a, Phase 11).

Covers the contract (``model_features``, ``declared_features``,
``approved_features``), deterministic screening incl. leakage tiers, the
confirm/supersede flow, Node 1 Gate 8b, and the Node 2 path end to end on a
seeded synthetic snapshot dataset.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from adapters.mapping_adapter import (
    MappingConfigAdapter,
    coerce_feature_value,
    load_confirmed_mapping_adapters,
)
from config.loader import load_feature_screening_config, load_node1_config, load_node2_config
from config.models import DeclaredFeature, MappingConfig, Node1Config
from node1.feature_gate import feature_gate_records
from node1.validation import validate_records
from router.feature_screening import screen_report, suggested_feature_key
from router.fingerprint import extract_fingerprint
from router.llm_mapper import (
    MappingAlreadyConfirmedError,
    MappingReportError,
    _default_node1_config,
    confirm_and_persist,
    demote_core_type_mismatches,
    derive_node1_config,
    find_confirmed_mapping,
    validate_mapping_report,
)
from schemas.canonical import CanonicalRecord
from schemas.mapping import ApprovedFeature, MappingReport, ProposedMapping

REF = date(2026, 8, 15)
REPO = Path(__file__).resolve().parents[2]


# --- synthetic snapshot dataset ------------------------------------------------


def _frame(n: int = 800, seed: int = 7) -> pd.DataFrame:
    """Seeded snapshot export: tenure in whole months, churn flag, features.

    ``Complain`` raises the (constant) hazard 2.5x; ``Noise`` is pure noise;
    the remaining columns are planted leakage / quality problems.
    """
    rng = np.random.RandomState(seed)
    complain = rng.binomial(1, 0.3, n)
    segment = rng.choice(["Alpha", "Beta", "Gamma"], n)
    hazard = 0.03 * np.where(complain == 1, 2.5, 1.0)
    event_time = rng.exponential(1 / hazard)
    censor_time = rng.uniform(1, 36, n)
    churn = (event_time <= censor_time).astype(int)
    tenure = np.floor(np.minimum(event_time, censor_time)).astype(int)
    perfect = churn + rng.normal(0, 0.01, n)
    reason = np.where(churn == 1, "moved", None)
    mostly_blank = np.where(rng.uniform(size=n) < 0.6, np.nan, rng.normal(size=n))
    return pd.DataFrame(
        {
            "CustomerID": np.arange(1, n + 1),
            "Tenure": tenure,
            "Churn": churn,
            "Complain": complain,
            "Noise": rng.normal(size=n),
            "Segment": segment,
            "PerfectScore": perfect,
            "CancelReason": reason,
            "MostlyBlank": mostly_blank,
            "TenureDays": tenure * 30,
        }
    )


def _identity_mappings() -> list[ProposedMapping]:
    def m(source: str, target: str, transformation: str) -> ProposedMapping:
        return ProposedMapping(
            source_column=source, target_field=target, confidence=1.0,
            transformation=transformation,
        )

    return [
        m("CustomerID", "customer_id", "to_int"),
        m("Churn", "event_observed", "map({'1': 1, '0': 0})"),
        m("Tenure", "observation_start", "months_before_midpoint(reference_date)"),
        m("Tenure", "observation_end", "snapshot_end(reference_date)"),
    ]


def _feature(source: str, kind: str, key: str | None = None) -> ProposedMapping:
    return ProposedMapping(
        source_column=source,
        target_field=f"feature.{key or suggested_feature_key(source)}",
        confidence=0.8,
        transformation="identity",
        feature_kind=kind,  # type: ignore[arg-type]
    )


def _report(frame: pd.DataFrame, features: list[ProposedMapping]) -> MappingReport:
    return MappingReport(
        source_fingerprint=extract_fingerprint(frame),
        proposed_mappings=[*_identity_mappings(), *features],
        recommended_action="create_deterministic_adapter",
        llm_model_used="test",
        generated_at="2026-08-15T00:00:00Z",
    )


def _screen(frame: pd.DataFrame, report: MappingReport, **kwargs):
    base = report.model_copy(update={"proposed_mappings": _identity_mappings()})
    return {
        r.source_column: r
        for r in screen_report(
            report,
            frame,
            reference_date=REF,
            node1_config=derive_node1_config(base, load_node1_config("1")),
            screening=load_feature_screening_config(),
            **kwargs,
        )
    }


def _approve(results: dict, keys: list[str]) -> list[ApprovedFeature]:
    by_key = {r.key: r for r in results.values()}
    return [
        ApprovedFeature(
            key=key, kind=by_key[key].kind, source_column=by_key[key].source_column,
            label=by_key[key].source_column, screening=by_key[key],
        )
        for key in keys
    ]


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    (target / "node1").mkdir(parents=True)
    (target / "node1" / "v1.json").write_text(
        (REPO / "config" / "node1" / "v1.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return target


# --- contract ----------------------------------------------------------------------


def test_canonical_model_features_left_out_when_empty_and_disjoint_from_core() -> None:
    base = {
        "customer_id": "c1",
        "observation_start": "2026-01-01",
        "observation_end": "2026-02-01",
        "event_observed": 0,
        "tenure": 31.0,
        "core_features": {},
        "meta": {
            "source_adapter": "x", "mapping_version": "y",
            "ingested_at": "2026-08-15T00:00:00Z", "reference_date": "2026-08-15",
        },
    }
    record = CanonicalRecord.model_validate(base)
    assert "model_features" not in record.model_dump(mode="json")
    record = CanonicalRecord.model_validate({**base, "model_features": {"complain": 1.0}})
    assert record.model_dump(mode="json")["model_features"] == {"complain": 1.0}
    with pytest.raises(ValidationError, match="clash with core"):
        CanonicalRecord.model_validate({**base, "model_features": {"plan_tier": "pro"}})


@pytest.mark.parametrize(
    ("keys", "message"),
    [
        (["Complain"], "snake_case"),
        (["plan_tier"], "clash"),
        (["duration"], "clash"),
        (["segment", "segment_alpha"], "one-hot"),
    ],
)
def test_node1_config_rejects_unsafe_declared_keys(keys: list[str], message: str) -> None:
    base = load_node1_config("1")
    with pytest.raises(ValidationError, match=message):
        Node1Config.model_validate(
            {
                **base.model_dump(),
                "declared_features": {k: {"kind": "number", "label": k} for k in keys},
            }
        )


def test_model_predictors_core_then_sorted_declared_and_dump_unchanged() -> None:
    base = load_node1_config("1")
    assert "declared_features" not in base.model_dump(mode="json")
    config = base.model_copy(
        update={
            "declared_features": {
                "zeta": DeclaredFeature(kind="number", label="Zeta"),
                "alpha": DeclaredFeature(kind="category", label="Alpha"),
            }
        }
    )
    assert config.model_predictors == [*base.approved_core_keys, "alpha", "zeta"]


def test_mapping_config_requires_approvals_to_match_feature_targets() -> None:
    frame = _frame(200)
    report = _report(frame, [_feature("Complain", "number")])
    with pytest.raises(ValidationError, match="approved_features"):
        MappingConfig(mapping_version="m", report=report)


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("number", "3", 3.0),
        ("number", True, None),
        ("number", float("nan"), None),
        ("number", "x", None),
        ("category", 1.0, "1"),
        ("category", 2.5, "2.5"),
        ("category", "  Gold ", "Gold"),
        ("category", "", None),
        ("category", None, None),
    ],
)
def test_coerce_feature_value(kind: str, value: object, expected: object) -> None:
    assert coerce_feature_value(kind, value) == expected


# --- mapping report rules ----------------------------------------------------------------


def test_feature_targets_need_a_kind_and_cannot_be_dates() -> None:
    frame = _frame(100)
    no_kind = _feature("Complain", "number").model_copy(update={"feature_kind": None})
    with pytest.raises(MappingReportError, match="feature_kind"):
        validate_mapping_report(_report(frame, [no_kind]))
    dated = _feature("Tenure", "number", "tenure_start").model_copy(
        update={"transformation": "parse_date"}
    )
    with pytest.raises(MappingReportError, match="dates cannot"):
        validate_mapping_report(_report(frame, [dated]))
    kind_on_core = ProposedMapping(
        source_column="Noise", target_field="core.usage_frequency", confidence=1.0,
        transformation="to_float", feature_kind="number",
    )
    with pytest.raises(MappingReportError, match="only valid on feature"):
        validate_mapping_report(_report(frame, [kind_on_core]))


def test_llm_text_column_proposed_as_number_becomes_category() -> None:
    frame = _frame(100)
    report = demote_core_type_mismatches(_report(frame, [_feature("Segment", "number")]))
    (feature,) = [m for m in report.proposed_mappings if m.target_field.startswith("feature.")]
    assert feature.feature_kind == "category"
    validate_mapping_report(report)


# --- screening ------------------------------------------------------------------------------


def test_screening_tiers_on_planted_columns() -> None:
    frame = _frame()
    results = _screen(frame, _report(frame, []), node2_config=load_node2_config("1"))
    assert results["Complain"].verdict == "ok"
    assert results["Complain"].direction == "higher_more_churn"
    assert results["Noise"].verdict in {"ok", "warn"}
    assert results["Segment"].kind == "category" and results["Segment"].n_levels == 3
    assert results["PerfectScore"].verdict == "block"
    assert any("AUC" in r for r in results["PerfectScore"].block_reasons)
    assert results["CancelReason"].verdict == "block"  # populated for churners only
    assert any("presence gap" in r for r in results["CancelReason"].block_reasons)
    assert any("outcome" in r for r in results["CancelReason"].warn_reasons)  # name
    assert results["MostlyBlank"].verdict == "block"  # past the missingness threshold
    assert results["TenureDays"].verdict == "block"
    assert any("restates tenure" in r for r in results["TenureDays"].block_reasons)
    # Every evaluable row is counted once; identity columns are not candidates.
    assert {"CustomerID", "Tenure", "Churn"}.isdisjoint(results)
    assert len({r.n_evaluable for r in results.values()}) == 1


def test_screening_is_deterministic() -> None:
    frame = _frame()
    report = _report(frame, [])
    first = [r.model_dump() for r in _screen(frame, report).values()]
    second = [r.model_dump() for r in _screen(frame, report).values()]
    assert first == second


def test_pure_category_and_too_many_levels_are_blocked() -> None:
    frame = _frame()
    frame["Region"] = np.where(frame["Churn"] == 1, "Lost", frame["Segment"])
    frame["Code"] = [f"c{i}" for i in range(len(frame))]
    results = _screen(frame, _report(frame, []))
    assert results["Region"].verdict == "block"
    assert results["Code"].verdict == "block"
    assert any("categories" in r for r in results["Code"].block_reasons)


# --- confirm / supersede ------------------------------------------------------------------------


def test_confirm_keeps_approved_features_and_stores_the_rest_as_extras(config_dir: Path) -> None:
    frame = _frame()
    report = _report(frame, [_feature("Complain", "number"), _feature("Noise", "number")])
    results = _screen(frame, report)
    config = confirm_and_persist(
        report, config_dir=config_dir, confirmed_by="t",
        approved_features=_approve(results, ["complain"]),
    )
    targets = [m.target_field for m in config.report.proposed_mappings]
    assert "feature.complain" in targets and "feature.noise" not in targets
    assert {e.source for e in config.report.suggested_extra_features} == {"Noise"}
    derived = load_node1_config(config.mapping_version, config_root=config_dir)
    assert derived.declared_features == {
        "complain": DeclaredFeature(kind="number", label="Complain")
    }
    assert derived.allow_missing_core_passthrough is True


def test_confirm_refuses_blocked_and_unknown_features(config_dir: Path) -> None:
    frame = _frame()
    report = _report(frame, [_feature("PerfectScore", "number")])
    results = _screen(frame, report)
    with pytest.raises(MappingReportError, match="blocked"):
        confirm_and_persist(
            report, config_dir=config_dir, confirmed_by="t",
            approved_features=_approve(results, ["perfect_score"]),
        )
    ok_report = _report(frame, [])
    with pytest.raises(MappingReportError, match="no feature"):
        confirm_and_persist(
            ok_report, config_dir=config_dir, confirmed_by="t",
            approved_features=_approve(_screen(frame, ok_report), ["complain"]),
        )


def test_supersede_replaces_the_active_mapping_without_editing_it(config_dir: Path) -> None:
    frame = _frame()
    first = confirm_and_persist(
        _report(frame, []), config_dir=config_dir, confirmed_by="t",
        confirmed_at=pd.Timestamp("2026-08-15T00:00:00Z").to_pydatetime(),
    )
    old_text = (config_dir / "mappings" / f"{first.mapping_version}.json").read_text()
    report = _report(frame, [_feature("Complain", "number")])
    with pytest.raises(MappingAlreadyConfirmedError):
        confirm_and_persist(report, config_dir=config_dir, confirmed_by="t")
    with pytest.raises(MappingReportError, match="not the active mapping"):
        confirm_and_persist(
            report, config_dir=config_dir, confirmed_by="t", supersedes="map_nope",
            confirmed_at=pd.Timestamp("2026-08-16T00:00:00Z").to_pydatetime(),
        )
    second = confirm_and_persist(
        report, config_dir=config_dir, confirmed_by="t", supersedes=first.mapping_version,
        approved_features=_approve(_screen(frame, report), ["complain"]),
        confirmed_at=pd.Timestamp("2026-08-16T00:00:00Z").to_pydatetime(),
    )
    assert (config_dir / "mappings" / f"{first.mapping_version}.json").read_text() == old_text
    stored = json.loads((config_dir / "mappings" / f"{second.mapping_version}.json").read_text())
    assert stored["supersedes"] == first.mapping_version
    headers_hash = report.source_fingerprint.headers_hash
    assert find_confirmed_mapping(config_dir / "mappings", headers_hash) == second.mapping_version
    adapters = load_confirmed_mapping_adapters(config_dir)
    assert [a.mapping_version for a in adapters] == [second.mapping_version]


# --- Node 1 ---------------------------------------------------------------------------------------


def _records_with(features: list[dict]) -> list[dict]:
    frame = _frame(len(features))
    records = MappingConfigAdapter(
        MappingConfig(mapping_version="m", report=_report(frame, []))
    ).transform(frame, REF.isoformat())
    for record, values in zip(records, features, strict=True):
        record["model_features"] = dict(values)
    return records


def _declared_config(**declared: str) -> Node1Config:
    return load_node1_config("1").model_copy(
        update={
            "approved_core_keys": [],
            "core_key_types": {},
            "declared_features": {
                key: DeclaredFeature(kind=kind, label=key)  # type: ignore[arg-type]
                for key, kind in declared.items()
            },
        }
    )


def test_gate_8b_types_missing_and_undeclared_keys() -> None:
    config = _declared_config(complain="number", segment="category")
    rows = [{"complain": 1.0, "segment": "A"}] * 17 + [
        {"complain": "yes", "segment": "A"},
        {"complain": 1.0, "segment": 2.0},
        {"complain": None, "segment": "A"},
    ]
    result = validate_records(_records_with(rows), config=config, reference_date=REF)
    codes = sorted(e["code"] for e in result.errors)
    assert codes == ["FEATURE_MISSING", "FEATURE_TYPE", "FEATURE_TYPE"]
    assert len(result.accepted) == 17
    stray = _records_with([{"complain": 1.0, "segment": "A", "other": 1.0}])
    gated, demoted = feature_gate_records(stray, [], ["complain", "segment"])
    assert demoted == {"other": 1}
    assert gated[0]["extra_features"]["other"] == 1.0
    assert "other" not in gated[0]["model_features"]


def test_feature_missingness_passthrough_and_batch_failure() -> None:
    config = _declared_config(complain="number")
    rows = [{"complain": 1.0}] * 9 + [{"complain": None}]
    strict = validate_records(_records_with(rows), config=config, reference_date=REF)
    assert len(strict.accepted) == 9
    passthrough = config.model_copy(update={"allow_missing_core_passthrough": True})
    loose = validate_records(_records_with(rows), config=passthrough, reference_date=REF)
    assert len(loose.accepted) == 10 and loose.missingness_passthrough == {"complain": 1}
    mostly_missing = [{"complain": 1.0}] * 5 + [{"complain": None}] * 5
    failed = validate_records(_records_with(mostly_missing), config=config, reference_date=REF)
    assert failed.batch_failed
    assert any("model feature 'complain'" in e["message"] for e in failed.errors)


# --- end to end: mapping -> Node 1 -> Node 2 ----------------------------------------


def test_approved_feature_reaches_the_cox_model(config_dir: Path, tmp_path: Path) -> None:
    from node1.node import run_node1
    from node2.node import run_node2

    frame = _frame(1200)
    path = tmp_path / "snapshot.csv"
    frame.to_csv(path, index=False)
    report = _report(frame, [_feature("Complain", "number"), _feature("Segment", "category")])
    report = report.model_copy(
        update={"source_fingerprint": extract_fingerprint(pd.read_csv(path))}
    )
    results = _screen(frame, report)
    config = confirm_and_persist(
        report, config_dir=config_dir, confirmed_by="t",
        approved_features=_approve(results, ["complain", "segment"]),
    )
    node1_config = load_node1_config(config.node1_config_version, config_root=config_dir)
    output = run_node1(
        path, config=node1_config, reference_date=REF,
        adapters=load_confirmed_mapping_adapters(config_dir),
    )
    record = output.canonical_dataset[0]
    assert set(record.model_features) == {"complain", "segment"}
    assert isinstance(record.model_features["segment"], str)
    # Midpoint convention: no zero-length windows, so tenure-0 rows are kept.
    assert min(r.tenure for r in output.canonical_dataset) > 0

    node2 = run_node2(
        output.canonical_dataset, load_node2_config("1"), node1_config.model_predictors
    )
    assert node2.model_type.value == "cox_ph"
    hazard = {a.feature: a.hazard_ratio for a in node2.feature_associations or []}
    assert hazard["complain"] > 1.5
    assert {"segment_Beta", "segment_Gamma"} <= set(hazard)
    assert node2.validation_metrics["c_index"] > 0.5


def test_default_node1_config_falls_back_to_shipped_copy(tmp_path: Path) -> None:
    assert _default_node1_config(tmp_path).validation_version
