"""Node 1 validation report + output contract (architecture §1.2, ROADMAP Task 2.8).

Status semantics:
- ``FAILED`` — batch failed (column missingness / tenure sanity) or nothing accepted.
- ``PASSED`` — every input row became a valid canonical record.
- ``PARTIAL`` — some rows accepted, some rejected.

A complete validation failure yields an empty ``canonical_dataset`` and the
pipeline stops for that batch — the system never fabricates a PASSED.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import ValidationError

from node1.validation import ValidationResult
from schemas.canonical import CanonicalRecord
from schemas.enums import ValidationStatus
from schemas.validation import Node1Output, ValidationReport


def build_report(
    records: Sequence[dict[str, Any]],
    validation: ValidationResult,
    *,
    adapter_name: str,
    mapping_version: str,
    reference_date: date,
    warnings: Sequence[str] = (),
    matched_candidates: Sequence[str] = (),
    demoted_features: dict[str, int] | None = None,
    missingness_passthrough: dict[str, int] | None = None,
) -> Node1Output:
    """Assemble the exact Node 1 output structure (§1.2)."""
    n_input_rows = len(records)
    n_accepted = len(validation.accepted)
    n_rejected = len(validation.rejected)

    if n_accepted + n_rejected != n_input_rows:
        raise RuntimeError(
            "row accounting mismatch: "
            f"n_input_rows={n_input_rows} n_accepted={n_accepted} "
            f"n_rejected={n_rejected}; "
            "every input row must be accounted for — never silently dropped"
        )

    if validation.batch_failed or n_input_rows == 0 or n_accepted == 0:
        status = ValidationStatus.FAILED
    elif n_accepted == n_input_rows:
        status = ValidationStatus.PASSED
    else:
        status = ValidationStatus.PARTIAL

    canonical: list[CanonicalRecord] = []
    if not validation.batch_failed:
        for record in validation.accepted:
            try:
                canonical.append(CanonicalRecord.model_validate(record))
            except ValidationError as exc:
                # Accepted records should already pass; never silently continue on failure.
                raise RuntimeError(
                    f"accepted record failed CanonicalRecord validation: {exc}"
                ) from exc

    demoted = dict(sorted((demoted_features or {}).items()))
    # Human-readable warnings are formatted FROM the structured counts above —
    # one source of truth, never a second count that could drift out of sync.
    demotion_warnings = [
        f"core key {key!r} demoted to extra_features in {count} record(s)"
        for key, count in demoted.items()
    ]
    passthrough = dict(sorted((missingness_passthrough or {}).items()))
    passthrough_warnings = [
        f"core feature {key!r} missing in {count} record(s); passed through as a "
        "null core within the missingness threshold (Node 2 complete-case will "
        "exclude them from the model matrix)"
        for key, count in passthrough.items()
    ]
    report = ValidationReport(
        status=status,
        n_input_rows=n_input_rows,
        n_accepted=n_accepted,
        n_rejected=n_rejected,
        errors=validation.errors,
        warnings=[*demotion_warnings, *passthrough_warnings, *warnings],
        demoted_features=demoted,
        missingness_passthrough=passthrough,
        adapter_used=adapter_name,
        matched_candidates=list(matched_candidates),
        mapping_version=mapping_version,
        reference_date=reference_date,
    )
    return Node1Output(canonical_dataset=canonical, validation_report=report)
