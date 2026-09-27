"""Thin SQLite metadata index (ROADMAP Phase 8, architecture §8.5, D-P2).

One table, ``runs`` — the queryable run history the API serves. It is
**load-bearing**: without it a frontend would have to scan directories and parse
filenames to answer "what runs exist, with what status, for which dataset".

The index is *operational*: it carries bookkeeping timestamps and is never part
of the deterministic report/state outputs. It self-heals from disk, and legacy
rows (predating routing-inclusive ids) surface ``routing_identity_source =
unknown_pre_migration`` with null routing fields — never an empty string that
could read as valid data.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from schemas.mapping import SourceFingerprint
from schemas.run import RoutingIdentitySource, RunExecutionStatus, RunSummary

__all__ = ["RunIndex"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    execution_status TEXT NOT NULL,
    pipeline_status TEXT,
    stage TEXT,
    raw_path TEXT,
    raw_digest TEXT,
    reference_date TEXT,
    adapter_used TEXT,
    n_accepted INTEGER,
    n_rejected INTEGER,
    model_type TEXT,
    model_version TEXT,
    n_ranked INTEGER,
    n_insufficient INTEGER,
    n_reports INTEGER,
    mapping_version TEXT,
    superseded_by TEXT,
    routing_identity_source TEXT NOT NULL,
    routing_adapter TEXT,
    routing_adapter_version TEXT,
    routing_confidence REAL,
    error_code TEXT,
    warnings TEXT,
    errors TEXT,
    pending_fingerprint TEXT,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT
)
"""

_COLUMNS = (
    "run_id",
    "execution_status",
    "pipeline_status",
    "stage",
    "raw_path",
    "raw_digest",
    "reference_date",
    "adapter_used",
    "n_accepted",
    "n_rejected",
    "model_type",
    "model_version",
    "n_ranked",
    "n_insufficient",
    "n_reports",
    "mapping_version",
    "superseded_by",
    "routing_identity_source",
    "routing_adapter",
    "routing_adapter_version",
    "routing_confidence",
    "error_code",
    "warnings",
    "errors",
    "pending_fingerprint",
    "created_at",
    "started_at",
    "finished_at",
)


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class RunIndex:
    """Connection-per-operation SQLite index (WAL) over ``RunSummary`` rows."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a fresh connection, commit on success, always close."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, summary: RunSummary) -> None:
        """Insert or replace a run's metadata row."""
        row = self._to_row(summary)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        columns = ", ".join(_COLUMNS)
        with self._connection() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO runs ({columns}) VALUES ({placeholders})",
                tuple(row[column] for column in _COLUMNS),
            )

    def get(self, run_id: str) -> RunSummary | None:
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
        return self._from_row(row) if row is not None else None

    def list(
        self,
        *,
        limit: int | None = None,
        status: RunExecutionStatus | None = None,
        model_version: str | None = None,
    ) -> list[RunSummary]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("execution_status = ?")
            params.append(status.value)
        if model_version is not None:
            clauses.append("model_version = ?")
            params.append(model_version)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            f"SELECT * FROM runs {where} "
            "ORDER BY COALESCE(created_at, '') DESC, run_id ASC"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, run_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def all(self) -> list[RunSummary]:
        return self.list()

    def mark_stale_running_interrupted(self) -> int:
        """Mark every in-flight row ``INTERRUPTED`` (single-process restart)."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE runs SET execution_status = ? WHERE execution_status = ?",
                (RunExecutionStatus.INTERRUPTED.value, RunExecutionStatus.RUNNING.value),
            )
            return int(cursor.rowcount)

    def _to_row(self, summary: RunSummary) -> dict[str, Any]:
        return {
            "run_id": summary.run_id,
            "execution_status": summary.execution_status.value,
            "pipeline_status": summary.pipeline_status,
            "stage": summary.stage,
            "raw_path": summary.raw_path,
            "raw_digest": summary.raw_digest,
            "reference_date": _iso(summary.reference_date),
            "adapter_used": summary.adapter_used,
            "n_accepted": summary.n_accepted,
            "n_rejected": summary.n_rejected,
            "model_type": summary.model_type,
            "model_version": summary.model_version,
            "n_ranked": summary.n_ranked,
            "n_insufficient": summary.n_insufficient,
            "n_reports": summary.n_reports,
            "mapping_version": summary.mapping_version,
            "superseded_by": summary.superseded_by,
            "routing_identity_source": summary.routing_identity_source.value,
            "routing_adapter": summary.routing_adapter,
            "routing_adapter_version": summary.routing_adapter_version,
            "routing_confidence": summary.routing_confidence,
            "error_code": summary.error_code,
            "warnings": _dump_json(summary.warnings),
            "errors": _dump_json(summary.errors),
            "pending_fingerprint": _dump_json(
                summary.pending_fingerprint.model_dump(mode="json")
                if summary.pending_fingerprint is not None
                else None
            ),
            "created_at": _iso(summary.created_at),
            "started_at": _iso(summary.started_at),
            "finished_at": _iso(summary.finished_at),
        }

    def _from_row(self, row: sqlite3.Row) -> RunSummary:
        pending = row["pending_fingerprint"]
        return RunSummary(
            run_id=row["run_id"],
            execution_status=RunExecutionStatus(row["execution_status"]),
            pipeline_status=row["pipeline_status"],
            stage=row["stage"],
            raw_path=row["raw_path"],
            raw_digest=row["raw_digest"],
            reference_date=(
                date.fromisoformat(row["reference_date"]) if row["reference_date"] else None
            ),
            adapter_used=row["adapter_used"],
            n_accepted=row["n_accepted"],
            n_rejected=row["n_rejected"],
            model_type=row["model_type"],
            model_version=row["model_version"],
            n_ranked=row["n_ranked"],
            n_insufficient=row["n_insufficient"],
            n_reports=row["n_reports"],
            mapping_version=row["mapping_version"],
            superseded_by=row["superseded_by"],
            routing_identity_source=RoutingIdentitySource(row["routing_identity_source"]),
            routing_adapter=row["routing_adapter"],
            routing_adapter_version=row["routing_adapter_version"],
            routing_confidence=row["routing_confidence"],
            error_code=row["error_code"],
            warnings=json.loads(row["warnings"]) if row["warnings"] else [],
            errors=json.loads(row["errors"]) if row["errors"] else [],
            pending_fingerprint=(
                SourceFingerprint.model_validate(json.loads(pending)) if pending else None
            ),
            created_at=_parse_dt(row["created_at"]),
            started_at=_parse_dt(row["started_at"]),
            finished_at=_parse_dt(row["finished_at"]),
        )
