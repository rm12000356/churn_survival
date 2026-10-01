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
from collections.abc import Iterable, Iterator
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


#: Bump when the table changes; ``_migrate`` adds missing columns additively.
SCHEMA_VERSION = 2

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC, run_id)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (execution_status)",
)

# Bookkeeping set by the API around an execution, not by the pipeline result.
# An upsert from a result (which does not know them) must never erase them.
_OPERATIONAL = ("superseded_by", "created_at", "started_at", "finished_at")

_IN_FLIGHT = (RunExecutionStatus.PENDING.value, RunExecutionStatus.RUNNING.value)


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _serialize(column: str, value: Any) -> Any:
    """Column value as stored (enums by value, timestamps ISO, lists as JSON)."""
    if value is None:
        return None
    if column in {"warnings", "errors"}:
        return _dump_json(value)
    if column == "pending_fingerprint":
        return _dump_json(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return getattr(value, "value", value)


def _log() -> Any:
    from logging_setup import get_logger

    return get_logger(node="run_index")


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
            self._migrate(conn)
            for statement in _INDEXES:
                conn.execute(statement)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Additive migrations: add any column this version knows but the DB lacks."""
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version >= SCHEMA_VERSION:
            return
        present = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        for column in _COLUMNS:
            if column not in present:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {column}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open a fresh connection, commit on success, always close.

        ``timeout`` makes a writer wait for the WAL lock instead of failing when the
        API threadpool and the run worker write at the same time.
        """
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def upsert(self, summary: RunSummary) -> None:
        """Insert a row, or update it without erasing operational bookkeeping.

        ``created_at`` / ``started_at`` / ``finished_at`` / ``superseded_by`` keep
        their stored value when the incoming summary does not carry one (a
        summary rebuilt from a pipeline result never knows them).
        """
        row = self._to_row(summary)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        columns = ", ".join(_COLUMNS)
        updates = ", ".join(
            f"{column} = COALESCE(excluded.{column}, runs.{column})"
            if column in _OPERATIONAL
            else f"{column} = excluded.{column}"
            for column in _COLUMNS
            if column != "run_id"
        )
        with self._connection() as conn:
            conn.execute(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
                tuple(row[column] for column in _COLUMNS),
            )

    def insert_if_missing(self, summary: RunSummary) -> bool:
        """Insert a row only when none exists (self-heal never overwrites live rows)."""
        row = self._to_row(summary)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        columns = ", ".join(_COLUMNS)
        with self._connection() as conn:
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO runs ({columns}) VALUES ({placeholders})",
                tuple(row[column] for column in _COLUMNS),
            )
            return int(cursor.rowcount) == 1

    def claim(self, summary: RunSummary) -> bool:
        """Atomically mark a run in-flight; ``False`` if it already is.

        One statement, so two concurrent triggers for the same identity cannot
        both enqueue it: the insert (new run) or the conditional update (a
        FAILED/INTERRUPTED/forced run) succeeds for exactly one caller.
        """
        row = self._to_row(summary)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        columns = ", ".join(_COLUMNS)
        in_flight = ", ".join("?" for _ in _IN_FLIGHT)
        with self._connection() as conn:
            cursor = conn.execute(
                f"INSERT INTO runs ({columns}) VALUES ({placeholders}) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "execution_status = excluded.execution_status, "
                "started_at = excluded.started_at, finished_at = NULL, error_code = NULL "
                f"WHERE runs.execution_status NOT IN ({in_flight})",
                (*(row[column] for column in _COLUMNS), *_IN_FLIGHT),
            )
            return int(cursor.rowcount) == 1

    def update_fields(self, run_id: str, **fields: Any) -> bool:
        """Update named columns of one row in a single statement (no read-modify-write)."""
        unknown = set(fields) - set(_COLUMNS[1:])
        if unknown:
            raise ValueError(f"unknown run index columns: {sorted(unknown)}")
        if not fields:
            return False
        assignments = ", ".join(f"{column} = ?" for column in fields)
        values = tuple(_serialize(column, value) for column, value in fields.items())
        with self._connection() as conn:
            cursor = conn.execute(
                f"UPDATE runs SET {assignments} WHERE run_id = ?", (*values, run_id)
            )
            return int(cursor.rowcount) == 1

    def get(self, run_id: str) -> RunSummary | None:
        with self._connection() as conn:
            cursor = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
        return self._decode(row) if row is not None else None

    @staticmethod
    def _filters(
        status: RunExecutionStatus | None, model_version: str | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("execution_status = ?")
            params.append(status.value)
        if model_version is not None:
            clauses.append("model_version = ?")
            params.append(model_version)
        return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params

    def list(
        self,
        *,
        limit: int | None = None,
        status: RunExecutionStatus | None = None,
        model_version: str | None = None,
    ) -> list[RunSummary]:
        where, params = self._filters(status, model_version)
        query = f"SELECT * FROM runs {where} ORDER BY created_at DESC, run_id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        decoded = (self._decode(row) for row in rows)
        return [summary for summary in decoded if summary is not None]

    def count(
        self,
        *,
        status: RunExecutionStatus | None = None,
        model_version: str | None = None,
    ) -> int:
        where, params = self._filters(status, model_version)
        with self._connection() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM runs {where}", tuple(params)).fetchone()
        return int(row[0])

    def list_expired(
        self, statuses: Iterable[RunExecutionStatus], cutoff: datetime
    ) -> list[str]:
        """Run ids in ``statuses`` whose last bookkeeping timestamp is before ``cutoff``."""
        values = [status.value for status in statuses]
        if not values:
            return []
        marks = ", ".join("?" for _ in values)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT run_id FROM runs WHERE execution_status IN ({marks}) "
                "AND COALESCE(finished_at, started_at, created_at) < ? ORDER BY run_id",
                (*values, cutoff.isoformat()),
            ).fetchall()
        return [row["run_id"] for row in rows]

    def delete(self, run_id: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def all(self) -> list[RunSummary]:
        return self.list()

    def mark_stale_running_interrupted(self) -> int:
        """Mark every in-flight row ``INTERRUPTED`` (single-process restart)."""
        marks = ", ".join("?" for _ in _IN_FLIGHT)
        with self._connection() as conn:
            cursor = conn.execute(
                f"UPDATE runs SET execution_status = ? WHERE execution_status IN ({marks})",
                (RunExecutionStatus.INTERRUPTED.value, *_IN_FLIGHT),
            )
            return int(cursor.rowcount)

    def _decode(self, row: sqlite3.Row) -> RunSummary | None:
        """Decode a row; an undecodable row is logged and skipped, never a 500."""
        try:
            return self._from_row(row)
        except (ValueError, TypeError, KeyError) as exc:
            _log().warning(
                "run_index_row_skipped", run_id=row["run_id"], error=type(exc).__name__
            )
            return None

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
