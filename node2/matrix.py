"""Feature-matrix construction shared by Node 2 components (eligibility, Cox, KM, cold-start).

Encoding is deterministic: categorical predictors are one-hot encoded with
*sorted* observed categories; numeric predictors pass through identically. Model
columns are named ``<feature>`` for numerics and ``<feature>_<category>`` for
categoricals (matching §2.12 ``feature_associations``, e.g. ``plan_tier_pro``).

Complete-case rule (§2.4 "no catastrophic missing-data problem after encoding";
§2.12 ``excluded`` state): a customer with any missing approved predictor is
excluded from the model matrix — missingness is never imputed, only surfaced.

Categorical predictors also get a ``<feature>__raw`` column (the raw category
value) used only for stratification in the PH-violation adjustment path; it is
never a model predictor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

FeatureKind = Literal["numeric", "categorical"]

# Raw-categorical column suffix (stratification only, never a predictor).
RAW_SUFFIX = "__raw"

# Documented warning thresholds (not eligibility gates).
ELEVATED_MISSINGNESS_FRACTION = 0.05
SHORT_FOLLOWUP_MEDIAN_DAYS = 90.0
EARLY_EVENT_FRACTION = 0.6


@dataclass(frozen=True)
class FeatureSpec:
    """Deterministic encoding scheme for one approved predictor (§2.11 encoding_scheme)."""

    name: str
    kind: FeatureKind
    categories: tuple[str, ...] = ()


def _is_numeric_value(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _core_get(core: Any, key: str) -> Any:
    """Read a core feature from either a dict or a Pydantic ``CoreFeatures`` model."""
    if hasattr(core, "model_dump"):
        return core.model_dump().get(key)
    if isinstance(core, dict):
        return core.get(key)
    return getattr(core, key, None)


def feature_kinds(records: Sequence[Any], predictors: Sequence[str]) -> dict[str, FeatureKind]:
    """Classify each predictor: numeric iff every *present* value is a number."""
    kinds: dict[str, FeatureKind] = {}
    for predictor in predictors:
        present = [
            _core_get(record.core_features, predictor)
            for record in records
            if _core_get(record.core_features, predictor) is not None
        ]
        if present and all(_is_numeric_value(value) for value in present):
            kinds[predictor] = "numeric"
        else:
            kinds[predictor] = "categorical"
    return kinds


def build_specs(records: Sequence[Any], predictors: Sequence[str]) -> list[FeatureSpec]:
    """Build the deterministic encoding scheme from the *fit* records.

    Categorical categories are the sorted, deduplicated observed values, so the
    column order is stable across runs and versions.
    """
    kinds = feature_kinds(records, predictors)
    specs: list[FeatureSpec] = []
    for predictor in predictors:
        kind = kinds[predictor]
        if kind == "numeric":
            specs.append(FeatureSpec(name=predictor, kind="numeric"))
        else:
            categories = sorted(
                {
                    str(value)
                    for record in records
                    if (value := _core_get(record.core_features, predictor)) is not None
                }
            )
            specs.append(
                FeatureSpec(name=predictor, kind="categorical", categories=tuple(categories))
            )
    return specs


def encode_categories(spec: FeatureSpec) -> tuple[str, ...]:
    """The one-hot columns for a categorical spec (all but the reference category).

    The reference category is the first in sorted order; hazard ratios are
    interpreted relative to it (§2.10).
    """
    return spec.categories[1:]


def encoded_columns(specs: Sequence[FeatureSpec]) -> list[str]:
    """The model-matrix column names implied by the specs."""
    columns: list[str] = []
    for spec in specs:
        if spec.kind == "numeric":
            columns.append(spec.name)
        else:
            for category in encode_categories(spec):
                columns.append(f"{spec.name}_{category}")
    return columns


def is_raw_column(name: str) -> bool:
    """True for the ``__raw`` stratification-only columns (never model predictors)."""
    return name.endswith(RAW_SUFFIX)


def encode(
    rows: Sequence[tuple[str, dict[str, Any], float, int]],
    specs: Sequence[FeatureSpec],
) -> pd.DataFrame:
    """Encode scored customers into a model matrix.

    ``rows``: ``(customer_id, core_features, tenure, event_observed)``. The
    returned frame is indexed by ``customer_id`` and has columns ``duration``,
    ``event``, plus one encoded column per spec. Categorical values unseen at fit
    time encode to all-zero rows (handled deterministically here).
    """
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
    """Predictors that have a non-None value for this customer (for cold-start)."""
    return [predictor for predictor in predictors if core.get(predictor) is not None]
