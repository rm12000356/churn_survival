"""Task 5.12 — output completeness, summary stats, CLI, boundary failures (§4.24/§4.26)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.loader import load_node4_config
from node4.node import main as node4_main
from node4.node import run_node4
from schemas.node4 import Node4Output, Node4Provenance
from tests.node4.conftest import make_flag, make_node2, make_node3, make_signal


def _all_ids(output: Node4Output) -> list[str]:
    return [a.customer_id for a in output.ranked_accounts] + [
        a.customer_id for a in output.insufficient_data_accounts
    ]


def test_every_union_customer_appears_exactly_once() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A", "B"], risk_scores=[0.9, 0.1], survival_90=[0.1, 0.9])
    node3 = make_node3([make_signal("B"), make_signal("C")])
    output = run_node4(node2, node3, config)
    ids = _all_ids(output)
    assert sorted(ids) == ["A", "B", "C"]
    assert len(ids) == len(set(ids))


def test_no_customer_in_both_lists() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A", "B"], risk_scores=[0.9, 0.1], survival_90=[0.1, 0.9])
    node3 = make_node3([make_signal("A")])
    output = run_node4(node2, node3, config)
    main = {a.customer_id for a in output.ranked_accounts}
    insufficient = {a.customer_id for a in output.insufficient_data_accounts}
    assert main.isdisjoint(insufficient)


def test_summary_stats_match_lists() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A", "B", "C"],
        risk_scores=[0.9, 0.5, 0.1],
        survival_90=[0.1, 0.5, 0.9],
    )
    node3 = make_node3(
        [
            make_signal(
                "A",
                risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
            ),
            make_signal(
                "B",
                risk_flags=[make_flag("billing_complaint", signal_strength="moderate")],
            ),
        ]
    )
    output = run_node4(node2, node3, config)
    stats = output.summary_stats
    assert stats.n_customers == len(output.ranked_accounts) + len(
        output.insufficient_data_accounts
    )
    assert stats.n_critical == sum(
        1 for a in output.ranked_accounts if a.combined_risk_level.value == "critical"
    )
    assert stats.n_high == sum(
        1 for a in output.ranked_accounts if a.combined_risk_level.value == "high"
    )
    assert stats.n_medium == sum(
        1 for a in output.ranked_accounts if a.combined_risk_level.value == "medium"
    )
    assert stats.n_low == sum(
        1 for a in output.ranked_accounts if a.combined_risk_level.value == "low"
    )
    assert stats.n_insufficient_data == len(output.insufficient_data_accounts)


def test_malformed_record_does_not_abort_run() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A", "B"],
        states=["scored", "scored"],
        risk_scores=[0.9],  # shorter than the scored subset -> B is a partial alignment
        survival_90=[0.1],
    )
    output = run_node4(node2, make_node3([]), config)
    assert set(_all_ids(output)) == {"A", "B"}
    assert any(e["code"] == "PARTIAL_ALIGNMENT" for e in output.processing_report.errors)


def test_ranks_are_sequential_in_main_list() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A", "B", "C"], risk_scores=[0.9, 0.5, 0.1], survival_90=[0.1, 0.5, 0.9]
    )
    output = run_node4(node2, make_node3([]), config)
    assert [a.rank for a in output.ranked_accounts] == list(
        range(1, len(output.ranked_accounts) + 1)
    )


def test_run_node4_rejects_empty_inputs() -> None:
    config = load_node4_config("1")
    with pytest.raises(ValueError):
        run_node4(None, None, config)


def test_run_node4_fails_loudly_on_schema_failure() -> None:
    config = load_node4_config("1")
    with pytest.raises(ValidationError):
        run_node4({"not": "a node2 output"}, None, config)  # type: ignore[arg-type]


def test_cli_writes_output(
    fresh_settings: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    node2 = make_node2(["A"], risk_scores=[0.9], survival_90=[0.1])
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    node2_path = tmp_path / "node2.json"
    node3_path = tmp_path / "node3.json"
    node2_path.write_text(json.dumps(node2.model_dump(mode="json")), encoding="utf-8")
    node3_path.write_text(json.dumps(node3.model_dump(mode="json")), encoding="utf-8")
    out_path = tmp_path / "node4.json"

    code = node4_main(
        [
            "--node2",
            str(node2_path),
            "--node3",
            str(node3_path),
            "--config",
            "1",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    assert out_path.is_file()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["summary_stats"]["n_customers"] == 1
    assert "Node 4:" in capsys.readouterr().out


def test_cli_accepts_single_upstream(fresh_settings: None, tmp_path: Path) -> None:
    node2 = make_node2(["A"], risk_scores=[0.9], survival_90=[0.1])
    node2_path = tmp_path / "node2.json"
    node2_path.write_text(json.dumps(node2.model_dump(mode="json")), encoding="utf-8")
    assert node4_main(["--node2", str(node2_path), "--config", "1"]) == 0


def test_cli_bad_args_returns_usage(fresh_settings: None) -> None:
    assert node4_main([]) == 2


def test_cli_missing_file_fails_loudly(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert node4_main(["--node2", "does-not-exist.json", "--config", "1"]) == 1
    assert "ERROR" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# F-3 — the run explicitly records the declared reference_date (§4.28)
# --------------------------------------------------------------------------- #
def test_reference_date_is_recorded() -> None:
    config = load_node4_config("1")
    output = run_node4(
        make_node2(["A"], risk_scores=[0.5], survival_90=[0.5]), make_node3([]), config
    )
    assert output.reference_date == config.reference_date
    assert output.reference_date.isoformat() == "2026-08-15"


def test_cli_output_records_reference_date(fresh_settings: None, tmp_path: Path) -> None:
    node2 = make_node2(["A"], risk_scores=[0.5], survival_90=[0.5])
    node2_path = tmp_path / "node2.json"
    node2_path.write_text(json.dumps(node2.model_dump(mode="json")), encoding="utf-8")
    out_path = tmp_path / "node4.json"
    assert (
        node4_main(["--node2", str(node2_path), "--config", "1", "--output", str(out_path)])
        == 0
    )
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["reference_date"] == "2026-08-15"


# --------------------------------------------------------------------------- #
# F-1 — a pipeline score just below a threshold must not be rounded up
# --------------------------------------------------------------------------- #
def test_low_boundary_combined_score_is_not_rounded_up() -> None:
    config = load_node4_config("1")
    # quantitative 0.666 (survival 0.334) with no support data -> raw 0.6 * 0.666
    output = run_node4(
        make_node2(["A"], risk_scores=[0.666], survival_90=[0.334]),
        make_node3([]),
        config,
    )
    account = output.ranked_accounts[0]
    assert account.combined_score == pytest.approx(0.3996)
    assert account.combined_score < 0.40
    assert account.combined_risk_level.value == "low"


# --------------------------------------------------------------------------- #
# F1 — confidence/normalization versions are recorded as provenance labels
# --------------------------------------------------------------------------- #
def test_provenance_records_confidence_and_normalization_versions() -> None:
    node2 = make_node2(["A"], risk_scores=[0.9], survival_90=[0.1])
    node3 = make_node3([make_signal("A")])
    for version in ("1", "3", "4"):
        config = load_node4_config(version)
        output = run_node4(node2, node3, config)
        assert output.provenance is not None
        assert output.provenance.confidence_version == config.confidence_version
        assert output.provenance.normalization_version == config.normalization_version


def test_provenance_old_json_without_new_fields_loads() -> None:
    provenance = Node4Provenance.model_validate(
        {
            "node2_model_version": "m",
            "node3_signal_version": "n",
            "ranking_version": "1.3",
            "threshold_version": "1.0",
            "critical_rules_version": "1.0",
        }
    )
    assert provenance.confidence_version == ""
    assert provenance.normalization_version == ""

