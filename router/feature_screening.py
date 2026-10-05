from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import date
from typing import Any

import pandas as pd

from adapters.mapping_adapter import (
    MappingConfigAdapter,
    coerce_feature_value,
    compile_transformation,
)
from config.models import FeatureScreeningConfig, MappingConfig, Node1Config, Node2Config
from node1.feature_gate import evaluate_promotion, feature_gate_records
from node1.validation import validate_records
from schemas.mapping import FeatureKind, FeatureScreening, MappingReport, ProposedMapping

FEATURE_PREFIX = "feature."
_KEY_CLEAN = re.compile(r"[^0-9a-z]+")


def suggested_feature_key(source_column: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", source_column)
    key = _KEY_CLEAN.sub("_", spaced.lower()).strip("_")
    if not key or not key[0].isalpha():
        key = f"f_{key}" if key else "feature"
    return key[:48]


def screen_report(
    report: MappingReport,
    raw: Any,
    *,
    reference_date: date,
    node1_config: Node1Config,
    screening: FeatureScreeningConfig,
    keys: Sequence[str] | None = None,
    node2_config: Node2Config | None = None,
) -> list[FeatureScreening]:
    base_report = report.model_copy(
        update={
            "proposed_mappings": [
                m
                for m in report.proposed_mappings
                if not m.target_field.startswith(FEATURE_PREFIX)
            ],
            "suggested_extra_features": [],
        }
    )
    adapter = MappingConfigAdapter(MappingConfig(mapping_version="screening", report=base_report))
    records = adapter.transform(raw, reference_date.isoformat())
    records, _ = feature_gate_records(records, node1_config.approved_core_keys)
    validation = validate_records(records, config=node1_config, reference_date=reference_date)
    accepted = validation.accepted

    frame = adapter.frame(raw)
    position = {str(index): offset for offset, index in enumerate(frame.index)}
    rows = [position[str(record["meta"]["original_row_id"])] for record in accepted]
    events = [int(record["event_observed"]) for record in accepted]
    tenures = [float(record["tenure"]) for record in accepted]
    cores = [dict(record.get("core_features") or {}) for record in accepted]

    results: list[FeatureScreening] = []
    for key, mapping, kind_hint in _candidates(report):
        if keys is not None and key not in keys:
            continue
        if mapping.source_column not in frame.columns:
            continue
        convert = compile_transformation(mapping.transformation, reference_date)
        raw_values = frame[mapping.source_column].astype(object).tolist()
        transformed = [convert(raw_values[row]) for row in rows]
        kind = kind_hint or _infer_kind(transformed)
        values = [coerce_feature_value(kind, value) for value in transformed]
        results.append(
            screen_feature(
                key,
                mapping.source_column,
                kind,
                values,
                events=events,
                tenures=tenures,
                cores=cores,
                node1_config=node1_config,
                screening=screening,
                node2_config=node2_config,
            )
        )
    return results


def _candidates(report: MappingReport) -> list[tuple[str, ProposedMapping, FeatureKind | None]]:
    mapped: set[str] = set()
    taken: set[str] = set()
    candidates: dict[str, tuple[str, ProposedMapping, FeatureKind | None]] = {}
    for mapping in report.proposed_mappings:
        if mapping.target_field.startswith(FEATURE_PREFIX):
            key = mapping.target_field[len(FEATURE_PREFIX):]
            candidates[mapping.source_column] = (key, mapping, mapping.feature_kind)
            taken.add(key)
        mapped.add(mapping.source_column)
    for column in report.source_fingerprint.column_names:
        if column in mapped:
            continue
        stem = suggested_feature_key(column)
        key, suffix = stem, 2
        while key in taken:
            key, suffix = f"{stem}_{suffix}", suffix + 1
        taken.add(key)
        candidates[column] = (
            key,
            ProposedMapping(
                source_column=column,
                target_field=f"{FEATURE_PREFIX}{key}",
                confidence=1.0,
                transformation="identity",
            ),
            None,
        )
    order = {column: i for i, column in enumerate(report.source_fingerprint.column_names)}
    return [candidates[c] for c in sorted(candidates, key=lambda c: order.get(c, len(order)))]


def _infer_kind(values: Sequence[Any]) -> FeatureKind:
    present = [v for v in values if not _missing(v)]
    if present and all(_is_number(v) for v in present):
        return "number"
    return "category"


def screen_feature(
    key: str,
    source_column: str,
    kind: FeatureKind,
    values: Sequence[Any],
    *,
    events: Sequence[int],
    tenures: Sequence[float],
    cores: Sequence[dict[str, Any]],
    node1_config: Node1Config,
    screening: FeatureScreeningConfig,
    node2_config: Node2Config | None = None,
) -> FeatureScreening:
    n = len(values)
    present = [i for i, v in enumerate(values) if not _missing(v)]
    n_present = len(present)
    missing_fraction = 1 - n_present / n if n else 1.0
    block: list[str] = []
    warn: list[str] = []
    info: list[str] = []

    promotion_records = [
        {"core_features": cores[i], "extra_features": {key: values[i]}} for i in range(n)
    ]
    verdict = evaluate_promotion(
        key, promotion_records, node1_config, event_labels=list(events)
    )
    warn.extend(verdict.warnings)
    if verdict.recommend_promote:
        info.extend(verdict.reasons)
    else:
        reason = "; ".join(verdict.reasons)
        if "sparse events" in reason or "no event labels" in reason:
            warn.append(reason)
        else:
            block.append(reason)

    presence_gap = _presence_gap(values, events)
    if presence_gap >= screening.presence_gap_block:
        block.append(
            f"populated for one outcome only (presence gap {presence_gap:.2f} "
            f">= {screening.presence_gap_block:.2f})"
        )
    elif presence_gap >= screening.presence_gap_warn:
        warn.append(f"presence differs by outcome (gap {presence_gap:.2f})")

    auc: float | None = None
    tenure_corr: float | None = None
    direction: str | None = None
    n_levels: int | None = None
    present_events = [events[i] for i in present]
    if kind == "number":
        numbers = [float(values[i]) for i in present]
        raw_auc = _auc(numbers, present_events)
        if raw_auc is not None:
            auc = max(raw_auc, 1 - raw_auc)
            if raw_auc != 0.5:
                direction = "higher_more_churn" if raw_auc > 0.5 else "higher_less_churn"
        tenure_corr = _pearson(numbers, [tenures[i] for i in present])
        if tenure_corr is not None and abs(tenure_corr) >= screening.tenure_correlation_block:
            block.append(
                f"restates tenure (correlation {tenure_corr:+.2f}); tenure is already "
                "the survival time"
            )
    else:
        labels = [str(values[i]) for i in present]
        levels: dict[str, list[int]] = {}
        for label, event in zip(labels, present_events, strict=True):
            levels.setdefault(label, []).append(event)
        n_levels = len(levels)
        rates = {label: sum(ev) / len(ev) for label, ev in levels.items()}
        level_auc = _auc([rates[label] for label in labels], present_events)
        auc = None if level_auc is None else max(level_auc, 1 - level_auc)
        if n_levels > screening.max_levels_block:
            block.append(
                f"{n_levels} categories (> {screening.max_levels_block}); too many to "
                "encode — group them with map({...}) or use the column as a number"
            )
        elif n_levels > screening.max_levels_warn:
            warn.append(f"{n_levels} categories (> {screening.max_levels_warn})")
        min_rows = max(
            screening.pure_level_min_rows,
            math.ceil(screening.pure_level_min_share * n_present),
        )
        pure = sorted(
            label for label, ev in levels.items() if len(ev) >= min_rows and sum(ev) in (0, len(ev))
        )
        if pure and n_levels > 1:
            block.append(
                "category "
                + ", ".join(repr(label) for label in pure[:3])
                + f" has a 0% or 100% churn rate across >= {min_rows} rows "
                "(it separates the outcome)"
            )

    if auc is not None:
        if auc >= screening.auc_block:
            block.append(
                f"separates churn almost perfectly on its own (AUC {auc:.3f} >= "
                f"{screening.auc_block:.2f}); likely leakage"
            )
        elif auc >= screening.auc_warn:
            warn.append(f"very strong single-feature signal (AUC {auc:.3f}); check for leakage")

    ph_p_value: float | None = None
    if node2_config is not None and not block:
        ph_p_value = _ph_preview(key, kind, values, events, tenures, node2_config)
        if ph_p_value is not None and ph_p_value < node2_config.ph_p_value_serious:
            warn.append(
                f"violates proportional hazards on its own (p={ph_p_value:.1e}); with it "
                "the model may "
                + ("be stratified by it" if kind == "category" else "fall back to Kaplan-Meier")
            )

    pattern = re.compile(screening.name_pattern, flags=re.IGNORECASE)
    if pattern.search(source_column) or pattern.search(key):
        warn.append("name suggests an outcome or post-churn field")

    return FeatureScreening(
        key=key,
        source_column=source_column,
        kind=kind,
        verdict="block" if block else ("warn" if warn else "ok"),
        block_reasons=block,
        warn_reasons=warn,
        info=info,
        n_evaluable=n,
        n_present=n_present,
        missing_fraction=_round(missing_fraction),
        n_levels=n_levels,
        auc=None if auc is None else _round(auc),
        presence_gap=_round(presence_gap),
        tenure_correlation=None if tenure_corr is None else _round(tenure_corr),
        direction=direction,
        ph_p_value=None if ph_p_value is None else float(f"{ph_p_value:.6g}"),
        screening_version=screening.screening_version,
    )


def _ph_preview(
    key: str,
    kind: FeatureKind,
    values: Sequence[Any],
    events: Sequence[int],
    tenures: Sequence[float],
    node2_config: Node2Config,
) -> float | None:
    from node2.assumptions import ph_test_p_values
    from node2.cox import fit_cox
    from node2.matrix import FeatureSpec, encode

    rows = [
        (str(i), {key: values[i]}, float(tenures[i]), int(events[i]))
        for i in range(len(values))
        if not _missing(values[i]) and tenures[i] > 0
    ]
    if sum(event for *_rest, event in rows) < node2_config.min_events:
        return None
    if kind == "number":
        spec = FeatureSpec(name=key, kind="numeric")
    else:
        categories = tuple(sorted({str(row[1][key]) for row in rows}))
        if len(categories) < 2:
            return None
        spec = FeatureSpec(name=key, kind="categorical", categories=categories)
    matrix = encode(rows, [spec])
    try:
        cph = fit_cox(matrix, node2_config)
        p_values = ph_test_p_values(cph, matrix)
    except Exception:  # noqa: BLE001 - a preview that cannot be fitted says nothing
        return None
    finite = [p for p in p_values.values() if math.isfinite(p)]
    return min(finite) if finite else None


def _round(value: float) -> float:
    return round(float(value), 6)


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _presence_gap(values: Sequence[Any], events: Sequence[int]) -> float:
    churned = [not _missing(v) for v, e in zip(values, events, strict=True) if e == 1]
    stayed = [not _missing(v) for v, e in zip(values, events, strict=True) if e == 0]
    if not churned or not stayed:
        return 0.0
    return abs(sum(churned) / len(churned) - sum(stayed) / len(stayed))


def _auc(scores: Sequence[float], events: Sequence[int]) -> float | None:
    n_pos = sum(1 for e in events if e == 1)
    n_neg = len(events) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    rank_sum = sum(rank for rank, e in zip(ranks, events, strict=True) if e == 1)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def _pearson(x: Sequence[float], y: Sequence[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((a - mean_x) ** 2 for a in x)
    syy = sum((b - mean_y) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    return sxy / math.sqrt(sxx * syy)
