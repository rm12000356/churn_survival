from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

FeatureKind = Literal["numeric", "categorical"]

RAW_SUFFIX = "__raw"

ELEVATED_MISSINGNESS_FRACTION = 0.05
SHORT_FOLLOWUP_MEDIAN_DAYS = 90.0
EARLY_EVENT_FRACTION = 0.6


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    kind: FeatureKind
    categories: tuple[str, ...] = ()


def _is_numeric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def modeling_values(record: Any) -> dict[str, Any]:
    core = record.core_features
    values = dict(core.model_dump() if hasattr(core, "model_dump") else core)
    values.update(getattr(record, "model_features", None) or {})
    return values


def feature_kinds(records: Sequence[Any], predictors: Sequence[str]) -> dict[str, FeatureKind]:
    kinds: dict[str, FeatureKind] = {}
    rows = [modeling_values(record) for record in records]
    for predictor in predictors:
        present = [row[predictor] for row in rows if row.get(predictor) is not None]
        if present and all(_is_numeric_value(value) for value in present):
            kinds[predictor] = "numeric"
        else:
            kinds[predictor] = "categorical"
    return kinds


def build_specs(records: Sequence[Any], predictors: Sequence[str]) -> list[FeatureSpec]:
    kinds = feature_kinds(records, predictors)
    rows = [modeling_values(record) for record in records]
    specs: list[FeatureSpec] = []
    for predictor in predictors:
        kind = kinds[predictor]
        if kind == "numeric":
            specs.append(FeatureSpec(name=predictor, kind="numeric"))
        else:
            categories = sorted(
                {str(value) for row in rows if (value := row.get(predictor)) is not None}
            )
            specs.append(
                FeatureSpec(name=predictor, kind="categorical", categories=tuple(categories))
            )
    return specs


def encode_categories(spec: FeatureSpec) -> tuple[str, ...]:
    return spec.categories[1:]


def encoded_columns(specs: Sequence[FeatureSpec]) -> list[str]:
    columns: list[str] = []
    for spec in specs:
        if spec.kind == "numeric":
            columns.append(spec.name)
        else:
            for category in encode_categories(spec):
                columns.append(f"{spec.name}_{category}")
    return columns


def is_raw_column(name: str) -> bool:
    return name.endswith(RAW_SUFFIX)


def encode(
    rows: Sequence[tuple[str, dict[str, Any], float, int]],
    specs: Sequence[FeatureSpec],
) -> pd.DataFrame:
    encoded: dict[str, list[Any]] = {"duration": [], "event": []}
    for spec in specs:
        if spec.kind == "numeric":
            encoded[spec.name] = []
        else:
            encoded[f"{spec.name}{RAW_SUFFIX}"] = []
            for category in encode_categories(spec):
                encoded[f"{spec.name}_{category}"] = []
    ids: list[str] = []

    for customer_id, core, tenure, event in rows:
        ids.append(customer_id)
        encoded["duration"].append(float(tenure))
        encoded["event"].append(int(event))
        for spec in specs:
            raw = core.get(spec.name)
            if spec.kind == "numeric":
                encoded[spec.name].append(float(raw) if raw is not None else None)
            else:
                value = str(raw) if raw is not None else None
                encoded[f"{spec.name}{RAW_SUFFIX}"].append(value)
                reference = encode_categories(spec)
                for category in reference:
                    encoded[f"{spec.name}_{category}"].append(1.0 if value == category else 0.0)

    frame = pd.DataFrame(encoded, index=ids)
    frame.index.name = "customer_id"
    return frame


def usable_predictors(core: dict[str, Any], predictors: Sequence[str]) -> list[str]:
    return [predictor for predictor in predictors if core.get(predictor) is not None]
