"""Final pre-publish consistency gate (architecture §5.29, F-3).

Any mandatory failure means **do not publish**. The gate re-checks the assembled
report against Node 4 (the source of truth): displayed decisions, counts,
ordering, separation of insufficient data, and recorded provenance.

**F-3:** required provenance must be present and non-empty, and mixed upstream
versions are never silently collapsed — both block publication.
"""

from __future__ import annotations

from schemas.node4 import Node4Output, RankedAccount
from schemas.node5 import Node5Output

_REQUIRED_METADATA = (
    "report_version",
    "node2_model_version",
    "node3_signal_version",
    "node4_ranking_version",
    "node4_threshold_version",
    "node4_critical_rules_version",
)

_VERSION_FIELDS = (
    "node2_model_version",
    "node3_signal_version",
    "node4_ranking_version",
    "node4_threshold_version",
    "node4_critical_rules_version",
)


class DoNotPublishError(RuntimeError):
    """Raised when a mandatory §5.29 consistency check fails."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__(
            "report failed final consistency checks and must not be published: "
            + "; ".join(failures[:5])
        )


def _accounts(node4: Node4Output) -> list[RankedAccount]:
    return [*node4.ranked_accounts, *node4.insufficient_data_accounts]


def _version_values(node4: Node4Output, field_name: str) -> list[str]:
    accounts = _accounts(node4)
    accessor = {
        "node2_model_version": lambda a: a.evidence_refs.node2.model_version,
        "node3_signal_version": lambda a: a.evidence_refs.node3.signal_version,
        "node4_ranking_version": lambda a: a.meta.ranking_version,
        "node4_threshold_version": lambda a: a.meta.threshold_version,
        "node4_critical_rules_version": lambda a: a.meta.critical_rules_version,
    }[field_name]
    return [value for value in (accessor(account) for account in accounts) if value]


def check_consistency(
    output: Node5Output,
    node4: Node4Output,
    *,
    require_action_rules: bool = False,
) -> list[str]:
    """Return the list of mandatory consistency failures (empty = publishable)."""
    failures: list[str] = []
    stats = node4.summary_stats
    distribution = output.report.risk_distribution
    if (
        distribution.critical,
        distribution.high,
        distribution.medium,
        distribution.low,
        distribution.insufficient_data,
    ) != (
        stats.n_critical,
        stats.n_high,
        stats.n_medium,
        stats.n_low,
        stats.n_insufficient_data,
    ):
        failures.append("risk_distribution does not match Node 4 summary stats")

    if output.report.reference_date != node4.reference_date:
        failures.append("reference_date does not match Node 4")

    for index, report in enumerate(output.report.priority_accounts):
        if index >= len(node4.ranked_accounts):
            failures.append("priority_accounts has more entries than Node 4 ranked accounts")
            break
        source = node4.ranked_accounts[index]
        if report.customer_id != source.customer_id:
            failures.append(f"priority account #{index + 1} is out of Node 4 order")
        if report.rank != source.rank:
            failures.append(f"{report.customer_id}: rank differs from Node 4")
        if report.risk_level.value != source.combined_risk_level.value:
            failures.append(f"{report.customer_id}: risk level differs from Node 4")
        if report.combined_score != source.combined_score:
            failures.append(f"{report.customer_id}: score differs from Node 4")
        if report.combined_confidence != source.combined_confidence:
            failures.append(f"{report.customer_id}: confidence differs from Node 4")

    reported_ids = {account.customer_id for account in output.report.priority_accounts}
    insufficient_ids = {
        account.customer_id for account in output.report.insufficient_data_accounts
    }
    if reported_ids & insufficient_ids:
        failures.append("a customer appears in both the main and insufficient-data lists")

    churned_ids = set(output.report.churned.customer_ids)
    if output.report.churned.n_churned != node4.summary_stats.n_churned or churned_ids != {
        account.customer_id for account in node4.churned_accounts
    }:
        failures.append("churned section does not match Node 4 churned accounts")
    if churned_ids & (reported_ids | insufficient_ids):
        failures.append("a churned customer also appears in a ranked or insufficient list")

    for report in output.report.priority_accounts:
        if report.risk_level.value == "insufficient_data":
            failures.append(
                f"{report.customer_id}: insufficient data must not appear in priority accounts"
            )

    for report in output.report.insufficient_data_accounts:
        if report.rank is not None:
            failures.append(f"{report.customer_id}: insufficient-data account must have no rank")
        if report.risk_level.value != "insufficient_data":
            failures.append(
                f"{report.customer_id}: insufficient-data account must keep its state"
            )

    for report in output.report.priority_accounts:
        if report.risk_level.value == "critical" and not any(
            reason.reason_type.value.startswith("critical_")
            for reason in report.primary_reasons
        ):
            failures.append(f"{report.customer_id}: critical account lost its critical reason")

    failures.extend(_provenance_failures(output, node4, require_action_rules))
    return failures


def _provenance_failures(
    output: Node5Output, node4: Node4Output, require_action_rules: bool
) -> list[str]:
    """F-3: required provenance must be non-empty and unambiguous."""
    failures: list[str] = []
    metadata = output.metadata
    for field_name in _REQUIRED_METADATA:
        value = getattr(metadata, field_name)
        if value is None or value == "":
            failures.append(f"metadata.{field_name} is required but empty")

    if metadata.reference_date is None:
        failures.append("metadata.reference_date is required but empty")
    if metadata.generated_at is None:
        failures.append("metadata.generated_at is required but empty")
    if require_action_rules and not metadata.action_rules_version:
        failures.append(
            "metadata.action_rules_version is required when recommendations are enabled"
        )

    for field_name in _VERSION_FIELDS:
        values = _version_values(node4, field_name)
        distinct = sorted(set(values))
        if len(distinct) > 1:
            failures.append(
                f"mixed provenance for {field_name}: {distinct}; refusing to publish"
            )
        recorded = getattr(metadata, field_name)
        if len(distinct) == 1 and recorded and recorded != distinct[0]:
            failures.append(
                f"metadata.{field_name}={recorded!r} does not match Node 4 value "
                f"{distinct[0]!r}"
            )
    return failures


def enforce_consistency(
    output: Node5Output,
    node4: Node4Output,
    *,
    require_action_rules: bool = False,
) -> None:
    """Raise :class:`DoNotPublishError` when any mandatory check fails."""
    failures = check_consistency(
        output, node4, require_action_rules=require_action_rules
    )
    if failures:
        raise DoNotPublishError(failures)
