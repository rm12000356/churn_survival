"""Feature gate — boundary between Node 1 and Node 2 (architecture §1.8, ROADMAP Task 2.9).

New fields always land in ``extra_features`` and are stored permanently.
Promotion to ``core_features`` is a separate, explicit decision that requires:
sufficient non-missing data, meaningful variation, and either statistical
association with the event or explicit domain approval. With sparse events the
default posture is reject. Multicollinearity signals are warnings, not automatic
killers.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from config.models import Node1Config


@dataclass(frozen=True)
class PromotionVerdict:
    """Outcome of a feature-promotion evaluation (§1.8)."""

    feature: str
    recommend_promote: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def apply_feature_gate(record: dict[str, Any], approved_core_keys: Sequence[str]) -> dict[str, Any]:
    """Guarantee storage-only placement: move non-approved core keys to extra_features.

    Defensive invariant (§1.8 / §1.3): core contains only approved modeling keys;
    everything else is stored in ``extra_features`` and never auto-promoted.
    """
    approved = set(approved_core_keys)
    core = record.get("core_features", {})
    if not isinstance(core, dict):
        return record
    unexpected = {key: value for key, value in core.items() if key not in approved}
    if unexpected:
        core = {key: value for key, value in core.items() if key in approved}
        extra = dict(record.get("extra_features", {}))
        extra.update(unexpected)
        record = dict(record)
        record["core_features"] = core
        record["extra_features"] = extra
    return record


def feature_gate_records(
    records: Sequence[dict[str, Any]], approved_core_keys: Sequence[str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Apply the feature gate across a batch; return (gated_records, demotions).

    ``demotions`` maps each demoted core key -> number of records in which it was
    moved from ``core_features`` to ``extra_features`` (§1.8). This is the single
    source of truth for demotion counts: the validation report's
    ``demoted_features`` field and its derived warning strings are both built
    from it, never recomputed. A config mismatch between an adapter's ``core.*``
    mapping and the deployment's ``approved_core_keys`` is therefore visible in
    the report instead of silently rejecting records or silently hiding the
    mismatch.
    """
    approved = set(approved_core_keys)
    demotions: dict[str, int] = {}
    gated: list[dict[str, Any]] = []
    for record in records:
        core = record.get("core_features")
        if isinstance(core, dict):
            for key in core:
                if key not in approved:
                    demotions[key] = demotions.get(key, 0) + 1
        gated.append(apply_feature_gate(record, approved_core_keys))
    return gated, demotions


def evaluate_promotion(
    feature: str,
    records: Sequence[dict[str, Any]],
    config: Node1Config,
    *,
    domain_approved: bool = False,
    event_labels: Sequence[int] | None = None,
) -> PromotionVerdict:
    """Evaluate whether ``feature`` (in ``extra_features``) may be promoted to core.

    Reject by default. Promotion requires non-missingness + variation + (statistical
    association or explicit domain approval). With sparse events the posture is
    reject. Correlation with an existing core feature is a warning, not a kill.

    Every comparison pairs values **from the same record** (``event_labels`` is
    aligned with ``records``); a missing value never shifts the rows after it.
    """
    reasons: list[str] = []
    warnings: list[str] = []

    indexed = _extra_column(records, feature)
    present = [value for _index, value in indexed]
    missing_fraction = 1 - (len(present) / len(records)) if records else 1.0
    if missing_fraction > config.missingness_threshold:
        return PromotionVerdict(
            feature=feature,
            recommend_promote=False,
            reasons=(
                "missingness "
                f"{missing_fraction:.0%} exceeds threshold {config.missingness_threshold:.0%}",
            ),
        )
    reasons.append(f"non-missingness {missing_fraction:.0%} within threshold")

    unique = {value for value in present}
    if len(unique) <= 1:
        return PromotionVerdict(
            feature=feature,
            recommend_promote=False,
            reasons=(f"no meaningful variation ({len(unique)} unique value)",),
        )
    if all(_is_number(value) for value in present):
        sample_std = _std([float(v) for v in present])
        if sample_std == 0:
            return PromotionVerdict(
                feature=feature,
                recommend_promote=False,
                reasons=(f"no numeric variation (std=0, {len(unique)} unique value)",),
            )
    reasons.append(f"meaningful variation ({len(unique)} unique values)")

    n_events = _count_events(event_labels)
    if n_events is not None and n_events < config.promotion_min_events:
        return PromotionVerdict(
            feature=feature,
            recommend_promote=False,
            reasons=(
                f"sparse events ({n_events}) below promotion_min_events "
                f"({config.promotion_min_events}); default posture is reject",
            ),
        )

    if domain_approved:
        reasons.append("explicit domain approval")
    elif event_labels is not None:
        association = _association(indexed, event_labels)
        reasons.append(f"statistical association with event (mean-risk diff {association:+.3f})")
    else:
        return PromotionVerdict(
            feature=feature,
            recommend_promote=False,
            reasons=(
                "no event labels and no domain approval; promotion requires explicit approval",
            ),
        )

    if all(_is_number(value) for value in present):
        warnings.extend(
            _correlation_warnings_against(indexed, _numeric_core_columns(records, config))
        )

    return PromotionVerdict(
        feature=feature,
        recommend_promote=True,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def feature_gate_warnings(
    records: Sequence[dict[str, Any]],
    config: Node1Config,
) -> list[str]:
    """Collect non-fatal multicollinearity warnings across extra features (§1.8).

    Warnings are surfaced regardless of whether promotion is viable: a highly
    correlated extra feature is a signal worth reporting even when the event
    data are too sparse to promote anything.
    """
    # Correlations are only ever computed against approved core features; with
    # none approved there is nothing to compare against.
    if not records or not config.approved_core_keys:
        return []
    # One pass builds every extra-feature column as (record index, value), and
    # each core column is built once, not once per extra feature.
    columns: dict[str, list[tuple[int, Any]]] = {}
    for index, record in enumerate(records):
        for key, value in record.get("extra_features", {}).items():
            if not _is_missing(value):
                columns.setdefault(key, []).append((index, value))
    existing_by_core = _numeric_core_columns(records, config)
    warnings: list[str] = []
    for key in sorted(columns):
        indexed = columns[key]
        if len(indexed) >= 2 and all(_is_number(value) for _index, value in indexed):
            for warning in _correlation_warnings_against(indexed, existing_by_core):
                warnings.append(f"{key}: {warning}")
    return warnings


def _is_missing(value: Any) -> bool:
    """None, NaN, or a blank string (pandas blanks arrive as NaN, not None)."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _extra_column(records: Sequence[dict[str, Any]], feature: str) -> list[tuple[int, Any]]:
    """Present values of one extra feature, keyed by record position."""
    column: list[tuple[int, Any]] = []
    for index, record in enumerate(records):
        value = record.get("extra_features", {}).get(feature)
        if not _is_missing(value):
            column.append((index, value))
    return column


def _numeric_core_columns(
    records: Sequence[dict[str, Any]], config: Node1Config
) -> list[tuple[str, dict[int, float]]]:
    """Numeric values of each approved core feature by record position, in key order."""
    existing: list[tuple[str, dict[int, float]]] = []
    for core_key in config.approved_core_keys:
        column: dict[int, float] = {}
        for index, record in enumerate(records):
            value = record.get("core_features", {}).get(core_key)
            if _is_number(value):
                column[index] = float(value)
        existing.append((core_key, column))
    return existing


def _correlation_warnings_against(
    indexed: list[tuple[int, Any]], existing_by_core: list[tuple[str, dict[int, float]]]
) -> list[str]:
    """Warn on |r| > 0.95, correlating only records that carry both values."""
    warnings: list[str] = []
    for core_key, core_column in existing_by_core:
        pairs = [
            (float(value), core_column[index])
            for index, value in indexed
            if index in core_column
        ]
        correlation = _correlation([x for x, _ in pairs], [y for _, y in pairs])
        if correlation is not None and abs(correlation) > 0.95:
            warnings.append(
                f"high correlation ({correlation:.2f}) with existing core feature {core_key!r}"
            )
    return warnings


def _count_events(event_labels: Sequence[int] | None) -> int | None:
    if event_labels is None:
        return None
    return sum(1 for label in event_labels if label == 1)


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _association(indexed: list[tuple[int, Any]], event_labels: Sequence[int]) -> float:
    """Mean event rate where the feature is "on" minus the overall event rate.

    Each value is paired with the label of its own record.
    """
    labels = [int(label) for label in event_labels]
    if not labels:
        return 0.0
    positive = [
        labels[index]
        for index, value in indexed
        if index < len(labels) and value not in (0, "", False)
    ]
    if not positive:
        return 0.0
    return sum(positive) / len(positive) - sum(labels) / len(labels)


def _correlation(x: list[float], y: list[float]) -> float | None:
    n = min(len(x), len(y))
    if n < 2:
        return None
    x, y = x[:n], y[:n]
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=False)) / n
    std_x = _std(x)
    std_y = _std(y)
    if std_x == 0 or std_y == 0:
        return None
    return cov / (std_x * std_y)
