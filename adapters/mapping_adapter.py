from __future__ import annotations

import ast
import math
import re
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from adapters._table import coerce_string, iter_rows, rows_to_records
from adapters.base import BaseAdapter
from adapters.util import parse_date, status_to_event, to_float, to_int
from config.models import MappingConfig

_IDENTITY_FIELDS = {"customer_id", "observation_start", "observation_end", "event_observed"}

_ALLOWED_TRANSFORMATIONS: tuple[str, ...] = (
    "identity",
    "str.strip()",
    "to_float",
    "to_int",
    "parse_date",
    "months_before(reference_date)",
    "months_before_midpoint(reference_date)",
    "snapshot_end(reference_date)",
    "row_number",
    "map({...})",
)

_IDENTITY = re.compile(r"^identity$")
_STRIP = re.compile(r"^(?:str\.strip\(\)|strip)$")
_TO_FLOAT = re.compile(r"^(?:to_float|float)$")
_TO_INT = re.compile(r"^(?:to_int|int)$")
_PARSE_DATE = re.compile(r"^parse_date$")
_MONTHS_BEFORE = re.compile(r"^months_before\(reference_date\)$")
_MONTHS_MIDPOINT = re.compile(r"^months_before_midpoint\(reference_date\)$")
_SNAPSHOT_END = re.compile(r"^snapshot_end\(reference_date\)$")
_ROW_NUMBER = re.compile(r"^row_number$")
_MAP = re.compile(r"^map\(.*\)$")


def is_allowed_transformation(text: str | None) -> bool:
    if not text:
        return True
    stripped = text.strip()
    if _IDENTITY.match(stripped):
        return True
    if _STRIP.match(stripped) or _TO_FLOAT.match(stripped) or _TO_INT.match(stripped):
        return True
    if _PARSE_DATE.match(stripped) or _MONTHS_BEFORE.match(stripped):
        return True
    if _MONTHS_MIDPOINT.match(stripped):
        return True
    if _SNAPSHOT_END.match(stripped) or _ROW_NUMBER.match(stripped):
        return True
    if _MAP.match(stripped):
        try:
            _parse_map(stripped)
        except ValueError:
            return False
        return True
    return False


def validate_transformation(text: str | None) -> None:
    if not is_allowed_transformation(text):
        raise ValueError(
            f"unrecognized transformation {text!r}; allowed ops: "
            + ", ".join(_ALLOWED_TRANSFORMATIONS)
        )


def compile_transformation(
    transformation: str | None, reference_date: date | None = None
) -> Callable[[Any], Any]:
    if not transformation:
        return _identity
    text = transformation.strip()
    if _IDENTITY.match(text):
        return _identity
    if _STRIP.match(text):
        return _strip
    if _TO_FLOAT.match(text):
        return to_float
    if _TO_INT.match(text):
        return to_int
    if _PARSE_DATE.match(text):
        return parse_date
    if _MONTHS_BEFORE.match(text):
        return lambda value: months_before(value, reference_date)
    if _MONTHS_MIDPOINT.match(text):
        return lambda value: months_before_midpoint(value, reference_date)
    if _SNAPSHOT_END.match(text):
        return lambda value: snapshot_end(value, reference_date)
    if _MAP.match(text):
        table = _parse_map(text)
        return lambda value: _lookup_map(table, value)
    if _ROW_NUMBER.match(text):
        raise ValueError(
            "row_number is only valid for target_field 'customer_id' — "
            "it synthesizes IDs and cannot transform a value"
        )
    raise ValueError(
        f"unrecognized transformation {text!r}; allowed ops: "
        + ", ".join(_ALLOWED_TRANSFORMATIONS)
    )


def transformation_output_kind(transformation: str | None) -> str | None:
    if not transformation:
        return "passthrough"
    text = transformation.strip()
    if _IDENTITY.match(text) or _STRIP.match(text):
        return "passthrough"
    if _TO_FLOAT.match(text) or _TO_INT.match(text) or _ROW_NUMBER.match(text):
        return "number"
    if _PARSE_DATE.match(text) or _MONTHS_BEFORE.match(text) or _SNAPSHOT_END.match(text):
        return "date"
    if _MONTHS_MIDPOINT.match(text):
        return "date"
    if _MAP.match(text):
        values = [v for v in _parse_map(text).values() if v is not None]
        if values and all(isinstance(v, str) for v in values):
            return "string"
        if values and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
        ):
            return "number"
        return None
    raise ValueError(f"unsupported mapping transformation {transformation!r}")


def apply_transformation(
    value: Any, transformation: str | None, reference_date: date | None = None
) -> Any:
    return compile_transformation(transformation, reference_date)(value)


def _identity(value: Any) -> Any:
    return value


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def months_before(value: Any, reference_date: date | None) -> date | None:
    months = to_float(value)
    if months is None or reference_date is None:
        return None
    return reference_date - timedelta(days=round(months * 30.4375))


def months_before_midpoint(value: Any, reference_date: date | None) -> date | None:
    months = to_float(value)
    if months is None or reference_date is None:
        return None
    return reference_date - timedelta(days=round((months + 0.5) * 30.4375))


def coerce_feature_value(kind: str, value: Any) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None if kind == "number" else str(value)
    if not isinstance(value, str) and pd.isna(value):
        return None
    if kind == "number":
        number = to_float(value)
        return number if number is not None and math.isfinite(number) else None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def snapshot_end(_value: Any, reference_date: date | None) -> date | None:
    return reference_date


def _parse_map(text: str) -> dict[Any, Any]:
    inner = text[4:-1].strip()
    if len(inner) > _MAX_MAP_LITERAL:
        raise ValueError(f"map() literal is too long ({len(inner)} chars)")
    try:
        parsed = ast.literal_eval(inner)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
        raise ValueError(f"map() transformation is not a valid dict literal: {text!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"map() transformation must contain a dict literal: {text!r}")
    return parsed


_MAX_MAP_LITERAL = 20_000


def _lookup_map(mapping: dict[Any, Any], value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if value in mapping:
        return mapping[value]
    normalized = str(value).strip().lower()
    for key, mapped in mapping.items():
        if str(key).strip().lower() == normalized:
            return mapped
    return None


class MappingConfigAdapter(BaseAdapter):
    confidence = 1.0
    priority = 0

    def __init__(self, config: MappingConfig) -> None:
        self._config = config
        self.name = f"mapping:{config.report.source_fingerprint.headers_hash[:12]}"
        self.version = config.mapping_version
        self.mapping_version = config.mapping_version
        self.node1_config_version = config.node1_config_version

    def recommended_node1_config(self) -> str | None:
        return self.node1_config_version

    def matches_signature(self, fingerprint: Any) -> bool:
        return fingerprint.headers_hash == self._config.report.source_fingerprint.headers_hash

    def get_mapping_config(self) -> dict:
        return {
            "adapter": self.name,
            "adapter_version": self.version,
            "mapping_version": self.mapping_version,
            "source_fingerprint": self._config.report.source_fingerprint.model_dump(),
        }

    def frame(self, raw_data: Any) -> pd.DataFrame:
        return self._frame(raw_data)

    def _frame(self, raw_data: Any) -> pd.DataFrame:
        if isinstance(raw_data, dict):
            fingerprint = self._config.report.source_fingerprint
            if fingerprint.primary_sheet is not None and fingerprint.primary_sheet in raw_data:
                return raw_data[fingerprint.primary_sheet]
            sheet_names = [s for s in fingerprint.sheet_names if s in raw_data]
            if sheet_names:
                return raw_data[sheet_names[0]]
            return next(iter(raw_data.values()))
        if isinstance(raw_data, pd.DataFrame):
            return raw_data
        raise TypeError(
            f"{self.name} expects a DataFrame or workbook, got {type(raw_data).__name__}"
        )

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        report = self._config.report
        ref_date = date.fromisoformat(reference_date)
        by_target: dict[str, Any] = {}
        for mapping in report.proposed_mappings:
            by_target[mapping.target_field] = mapping

        extra_spec = {item.suggested_key: item.source for item in report.suggested_extra_features}
        feature_kinds = {f.key: f.kind for f in self._config.approved_features}
        mapped_sources = {m.source_column for m in report.proposed_mappings} | set(
            extra_spec.values()
        )

        plan: list[tuple[str, str, Callable[[Any], Any] | None]] = []
        for target, mapping in by_target.items():
            if target == "customer_id" and (mapping.transformation or "").strip() == "row_number":
                plan.append((target, mapping.source_column, None))
            else:
                plan.append(
                    (
                        target,
                        mapping.source_column,
                        compile_transformation(mapping.transformation, ref_date),
                    )
                )

        frame = self._frame(raw_data)
        row_maps: list[dict[str, Any]] = []
        for index, values in iter_rows(frame):
            fields: dict[str, Any] = {}
            core: dict[str, Any] = {}
            features: dict[str, Any] = {}
            extra: dict[str, Any] = {}
            for target, source_column, fn in plan:
                if fn is None:
                    transformed: Any = str(index)
                else:
                    transformed = fn(values.get(source_column))
                if target in _IDENTITY_FIELDS:
                    fields[target] = transformed
                elif target.startswith("core."):
                    core[target[5:]] = _coerce_for_key(target[5:], transformed)
                elif target.startswith("feature."):
                    key = target[len("feature."):]
                    if key in feature_kinds:
                        features[key] = coerce_feature_value(feature_kinds[key], transformed)
                    else:
                        extra[key] = transformed
                else:
                    extra[target] = transformed
            for key, source in extra_spec.items():
                extra[key] = values.get(source)
            for col, value in values.items():
                if col not in mapped_sources:
                    extra[col] = value
            fields["core_features"] = core
            fields["model_features"] = features
            fields["extra_features"] = extra
            fields["original_row_id"] = str(index)
            row_maps.append(fields)

        normalized: list[dict[str, Any]] = []
        for row_map in row_maps:
            event = row_map.get("event_observed")
            if event is not None and not isinstance(event, int):
                raw_event = event
                event = status_to_event(raw_event)
                if event is None:
                    event = to_int(raw_event)
            normalized.append(
                {
                    "customer_id": coerce_string(row_map.get("customer_id")),
                    "observation_start": row_map.get("observation_start"),
                    "observation_end": row_map.get("observation_end"),
                    "event_observed": event,
                    "core_features": row_map.get("core_features", {}),
                    "extra_features": row_map.get("extra_features", {}),
                    "original_row_id": row_map.get("original_row_id"),
                }
            )
        records = rows_to_records(self, normalized, reference_date)
        if feature_kinds:
            for record, row_map in zip(records, row_maps, strict=True):
                record["model_features"] = row_map["model_features"]
        if len(records) != len(row_maps):
            raise RuntimeError(
                f"{self.name}: row accounting mismatch after transform — input rows "
                f"={len(row_maps)} output records={len(records)}; rows must never "
                "be silently dropped"
            )
        return records


def _coerce_for_key(key: str, value: Any) -> Any:
    if pd.isna(value):
        return None
    return value


def load_confirmed_mapping_adapters(config_dir: Path | None = None) -> list[MappingConfigAdapter]:
    from config.loader import config_dir as resolve_config_dir

    directory = Path(config_dir) if config_dir is not None else resolve_config_dir()
    mappings_dir = directory / "mappings"
    if not mappings_dir.is_dir():
        return []
    from config.loader import load_config

    configs = [
        (path, load_config(path, MappingConfig))
        for path in sorted(mappings_dir.glob("map_*.json"))
    ]
    superseded = {config.supersedes for _path, config in configs if config.supersedes}
    adapters: list[MappingConfigAdapter] = []
    seen: dict[str, str] = {}
    for path, config in configs:
        if config.mapping_version in superseded:
            continue
        headers_hash = config.report.source_fingerprint.headers_hash
        if headers_hash in seen:
            raise ValueError(
                f"duplicate mapping configs for headers_hash {headers_hash}: "
                f"{seen[headers_hash]} and {path.name}; remove the superseded config"
            )
        seen[headers_hash] = path.name
        adapters.append(MappingConfigAdapter(config))
    return adapters
