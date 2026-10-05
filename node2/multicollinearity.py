from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from config.models import Node2Config
from node2.matrix import encoded_columns, is_raw_column


def _pairwise_correlations(
    matrix: pd.DataFrame, columns: Sequence[str]
) -> list[tuple[str, str, float]]:
    pairs: list[tuple[str, str, float]] = []
    if len(columns) < 2:
        return pairs
    data = matrix[list(columns)].astype(float)
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            a, b = columns[i], columns[j]
            corr = data[a].corr(data[b])
            if corr is not None and not np.isnan(corr) and abs(corr) > 1e-9:
                pairs.append((a, b, float(abs(corr))))
    return pairs


def _vif(matrix: pd.DataFrame, columns: Sequence[str]) -> dict[str, float]:
    vifs: dict[str, float] = {}
    if len(columns) < 2:
        return vifs
    data = matrix[list(columns)].astype(float)
    for target in columns:
        others = [col for col in columns if col != target]
        x = data[others].values
        y = data[target].values
        x_demeaned = x - x.mean(axis=0)
        y_demeaned = y - y.mean(axis=0)
        try:
            beta, *_ = np.linalg.lstsq(x_demeaned, y_demeaned, rcond=None)
            residuals = y_demeaned - x_demeaned @ beta
            rss = float(np.sum(residuals**2))
            tss = float(np.sum(y_demeaned**2))
            r_squared = 1.0 - rss / tss if tss > 0 else 0.0
            vif = 1.0 / (1.0 - r_squared) if r_squared < 1.0 else float("inf")
        except np.linalg.LinAlgError:
            vif = float("inf")
        vifs[target] = vif
    return vifs


def multicollinearity_warnings(
    matrix: pd.DataFrame,
    predictors: Sequence[str] | None = None,
    config: Node2Config | None = None,
) -> list[str]:
    warnings: list[str] = []
    if config is None:
        return warnings
    if predictors:
        columns = [col for col in encoded_columns(predictors) if col in matrix.columns]
    else:
        columns = [
            col
            for col in matrix.columns
            if col not in {"duration", "event"} and not is_raw_column(col)
        ]

    for a, b, corr in _pairwise_correlations(matrix, columns):
        if corr > config.correlation_threshold:
            warnings.append(
                f"strong pairwise correlation {corr:.2f} between {a!r} and {b!r} "
                f"(threshold {config.correlation_threshold})"
            )

    if len(columns) >= config.vif_min_predictors:
        for column, vif in sorted(_vif(matrix, columns).items()):
            if vif > config.vif_threshold:
                label = "inf" if np.isinf(vif) else f"{vif:.1f}"
                warnings.append(
                    f"high VIF {label} for {column!r} (threshold {config.vif_threshold})"
                )
    return warnings
