"""Node sequencing tests (ROADMAP Task 7.2)."""

from __future__ import annotations

from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.state import PipelineStage, PipelineStatus


def test_all_five_nodes_run_and_outputs_are_retained(clean_csv: Path) -> None:
    result = run_pipeline(clean_csv)
    state = result.state
    assert result.status is PipelineStatus.COMPLETED
    assert state.stage is PipelineStage.DONE
    assert state.node1_output is not None
    assert state.node2_output is not None
    assert state.node3_output is not None
    assert state.node4_output is not None
    assert state.node5_output is not None
    # Run identity includes every config that can change the output (D-P1):
    # the five node configs plus the resolved action-rules set.
    node_keys = ("node1", "node2", "node3", "node4", "node5")
    assert {key: state.config_versions[key] for key in node_keys} == {
        "node1": "1",
        "node2": "1",
        "node3": "1",
        "node4": "2",  # v2 default: optional support → quantitative-only (§4.14a)
        "node5": "1",
    }
    assert state.config_versions.get("action_rules")
    assert state.run_id is not None
    assert state.raw_digest is not None
    assert state.support_digest is not None
    assert state.routing_identity is not None and state.routing_identity.matched


def test_support_data_is_processed_by_node3(clean_csv: Path) -> None:
    threads = [
        {
            "thread_id": "thr_orch_1",
            "customer_id": "cus_1001",
            "created_at": "2026-07-20T14:30:00Z",
            "closed_at": "2026-07-21T09:12:00Z",
            "channel": "email",
            "subject": "Downgrade question",
            "status": "closed",
            "tags": ["billing"],
            "messages": [
                {
                    "message_id": "msg_orch_1",
                    "timestamp": "2026-07-20T14:30:00Z",
                    "role": "customer",
                    "text": "We are considering cancelling before the renewal.",
                }
            ],
        }
    ]
    result = run_pipeline(clean_csv, support_data=threads)
    assert result.status is PipelineStatus.COMPLETED
    report = result.state.node3_output.processing_report
    assert report.n_threads_processed == 1
    assert result.state.node4_output is not None
    assert result.state.node5_output is not None


def test_no_support_data_gets_a_no_data_baseline(clean_csv: Path) -> None:
    """Node 3 still runs (no_data) so Node 5 has publishable provenance."""
    result = run_pipeline(clean_csv)
    assert result.status is PipelineStatus.COMPLETED
    node3 = result.state.node3_output
    assert node3 is not None
    assert all(not signal.has_support_data for signal in node3.customer_signals)
    assert any("no_data baseline" in warning for warning in result.state.warnings)


class _DownClient:
    """An LLM provider that is down (e.g. an expired key)."""

    model = "down-model"

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        raise ConnectionError("401 unauthorized")


def test_node3_llm_outage_is_a_run_level_warning(clean_csv: Path) -> None:
    # REVIEW N-H5: a total outage must not finish as a silent clean run.
    threads = [
        {
            "thread_id": "thr_down_1",
            "customer_id": "cus_1001",
            "created_at": "2026-07-20T14:30:00Z",
            "channel": "email",
            "subject": "Renewal",
            "status": "closed",
            "messages": [
                {
                    "message_id": "msg_down_1",
                    "timestamp": "2026-07-20T14:30:00Z",
                    "role": "customer",
                    "text": "We are considering cancelling before the renewal.",
                }
            ],
        }
    ]
    result = run_pipeline(clean_csv, support_data=threads, llm_client=_DownClient())
    assert result.status is PipelineStatus.COMPLETED
    assert any(
        w.startswith("node3:") and "failed LLM extraction" in w for w in result.state.warnings
    )


def test_llm_run_with_partial_support_coverage_publishes(clean_csv: Path) -> None:
    # Regression: customers without threads were stamped model=offline while
    # LLM-processed ones got the client's model, so Node 5 refused to publish
    # (MIXED_PROVENANCE) every LLM run where some customers had no support data.
    from tests.node3.test_llm_concurrency import ThreadSafeClient

    threads = [
        {
            "thread_id": "thr_ok_1",
            "customer_id": "cus_1001",
            "created_at": "2026-07-20T14:30:00Z",
            "channel": "email",
            "subject": "Renewal",
            "status": "closed",
            "messages": [
                {
                    "message_id": "msg_ok_1",
                    "timestamp": "2026-07-20T14:30:00Z",
                    "role": "customer",
                    "text": "We are considering cancelling before the renewal.",
                }
            ],
        }
    ]
    result = run_pipeline(clean_csv, support_data=threads, llm_client=ThreadSafeClient())
    assert result.status is PipelineStatus.COMPLETED, result.state.errors
    signals = result.state.node3_output.customer_signals
    assert {s.meta.model_version for s in signals} == {"mock-model"}
    assert any(not s.has_support_data for s in signals)  # partial coverage
    assert result.state.node5_output is not None
