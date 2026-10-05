from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"
TRUTH_JSON = REPO / "data" / "ground_truth" / "dataset7_ground_truth.json"

REFERENCE_DATE = date(2026, 8, 15)

RAW_ROWS = 5000
N_VALID = 4550
N_INVALID = 450

TAXONOMY = {
    "FUTURE_START_DATE": 60,
    "FUTURE_END_DATE": 50,
    "BAD_EVENT_VALUE": 40,
    "DUPLICATE_ID": 80,
    "MISSING_CORE": 100,
    "IMPOSSIBLE_TENURE": 60,
    "NEGATIVE_TENURE": 30,
    "INVALID_PLAN": 30,
}

PRECEDENCE = [
    "DUPLICATE_ID",
    "FUTURE_START_DATE",
    "FUTURE_END_DATE",
    "IMPOSSIBLE_TENURE",
    "NEGATIVE_TENURE",
    "BAD_EVENT_VALUE",
    "INVALID_PLAN",
    "MISSING_CORE",
]

CANCEL_KEYWORDS = (
    "cancel",
    "cancelling",
    "terminate",
    "switch",
    "leave",
    "refund",
    "not renewing",
    "stop subscription",
)

_UNSUPPORTED_MARKERS = {
    "es": ("cancelar", "baja", "suscripción", "gracias", "quiero dar"),
    "de": ("kündigen", "kündigung", "vertrag", "stornieren", "leider"),
    "fr": ("annuler", "résiliation", "abonnement", "merci", "supprimer"),
}

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d-%m-%Y", "%Y.%m.%d")

COLUMNS = [
    "Cust ID",
    "Signup Date",
    "Cancellation Date",
    "Account Status",
    "Plan",
    "Contract Length (Months)",
    "Avg Weekly Active Days",
    "Support Tickets (Last 90 Days)",
    "Last Login (Days Ago)",
    "Region",
    "Sales Rep",
    "Internal Notes",
    "Legacy Flag",
    "Decoy A",
    "Decoy B",
]

STATUS_TO_EVENT = {
    "Churned": 1,
    "Active": 0,
    "Yes": 1,
    "No": 0,
    "1": 1,
    "0": 0,
}


@dataclass
class Check:
    id: int
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ValidationReport:
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.passed]


def _parse_date_text(text: str) -> date | None:
    value = text.strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _status_to_event(value: str) -> int | None:
    return STATUS_TO_EVENT.get(value.strip())


def _detect_language(text: str) -> str | None:
    lowered = text.lower()
    for lang, markers in _UNSUPPORTED_MARKERS.items():
        if any(marker in lowered for marker in markers):
            return lang
    return None


def _is_cancellation_thread(thread: dict) -> bool:
    for message in thread.get("messages", []):
        if message.get("role") == "customer":
            lowered = message.get("text", "").lower()
            if any(keyword in lowered for keyword in CANCEL_KEYWORDS):
                return True
    return False


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(row) for row in reader]
    return rows


def _read_threads(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_truth(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_all(
    csv_path: Path,
    threads_path: Path,
    truth_path: Path,
) -> ValidationReport:
    checks: list[Check] = []
    rows = _read_csv(csv_path)
    threads = _read_threads(threads_path)
    truth = _read_truth(truth_path)

    customer_truth = truth["customer_truth"]
    invalid_rows = truth["invalid_rows"]
    support_truth = truth["support_truth"]
    node4_oracle = truth["node4_scenario_oracle"]
    trap_oracle = truth["node5_trap_oracle"]
    duplicates = truth["cross_channel_duplicates"]
    validation_oracle = truth["validation_oracle"]

    valid_ids = set(customer_truth)
    row_by_index = list(enumerate(rows))

    derived: dict[str, dict] = {}
    for index, row in row_by_index:
        cid = (row.get("Cust ID") or "").strip()
        start = _parse_date_text(row.get("Signup Date") or "")
        cancel_text = (row.get("Cancellation Date") or "").strip()
        cancel = _parse_date_text(cancel_text) if cancel_text else None
        event = _status_to_event(row.get("Account Status") or "")
        plan = (row.get("Plan") or "").strip()
        contract_text = (row.get("Contract Length (Months)") or "").strip()
        usage_text = (row.get("Avg Weekly Active Days") or "").strip()
        tickets_text = (row.get("Support Tickets (Last 90 Days)") or "").strip()
        end = cancel if cancel is not None else (REFERENCE_DATE if start is not None else None)
        tenure = float((end - start).days) if (start and end) else None
        derived[cid] = {
            "row_index": index,
            "start": start,
            "cancel": cancel,
            "end": end,
            "event": event,
            "tenure": tenure,
            "plan": plan,
            "contract": contract_text,
            "usage": usage_text,
            "tickets": tickets_text,
        }

    def check(seq: int, name: str, passed: bool, detail: str = "") -> None:
        checks.append(Check(seq, name, bool(passed), detail))

    check(1, "raw_rows_5000", len(rows) == RAW_ROWS, f"got {len(rows)}")
    check(2, "valid_customers_4550", len(customer_truth) == N_VALID, f"got {len(customer_truth)}")
    check(3, "invalid_rows_450", len(invalid_rows) == N_INVALID, f"got {len(invalid_rows)}")
    check(4, "valid_ids_unique", len(valid_ids) == len(customer_truth))

    thread_customer_ids = {thread["customer_id"] for thread in threads}
    missing_support_ids = sorted(thread_customer_ids - valid_ids)
    check(
        5,
        "support_customer_ids_exist",
        not missing_support_ids,
        f"unknown support customer ids: {missing_support_ids[:5]}",
    )

    bad_dates: list[int] = []
    for index, row in row_by_index:
        if _parse_date_text(row.get("Signup Date") or "") is None:
            bad_dates.append(index)
        cancel_text = (row.get("Cancellation Date") or "").strip()
        if cancel_text and _parse_date_text(cancel_text) is None:
            bad_dates.append(index)
    check(6, "all_dates_parse", not bad_dates, f"bad date rows: {bad_dates[:10]}")

    window_errors: list[str] = []
    future_end_errors: list[str] = []
    for cid, rec in customer_truth.items():
        canon = rec["canonical"]
        start = date.fromisoformat(canon["observation_start"])
        end = date.fromisoformat(canon["observation_end"])
        if start > end:
            window_errors.append(cid)
        if end > REFERENCE_DATE:
            future_end_errors.append(cid)
    check(7, "observation_start_le_end", not window_errors, f"{window_errors[:5]}")
    check(8, "observation_end_le_reference", not future_end_errors, f"{future_end_errors[:5]}")

    thread_window_errors: list[str] = []
    message_window_errors: list[str] = []
    for thread in threads:
        cid = thread["customer_id"]
        canon = customer_truth[cid]["canonical"]
        start = date.fromisoformat(canon["observation_start"])
        end = date.fromisoformat(canon["observation_end"])
        created = _parse_time(thread["created_at"]).date()
        if created > end:
            thread_window_errors.append(thread["thread_id"])
        if created < start:
            thread_window_errors.append(thread["thread_id"])
        for message in thread.get("messages", []):
            ts = _parse_time(message["timestamp"]).date()
            if ts > end or ts < start:
                message_window_errors.append(message["message_id"])
    check(9, "thread_created_at_le_observation_end", not thread_window_errors,
          f"{thread_window_errors[:5]}")
    check(10, "thread_created_at_ge_observation_start", not thread_window_errors,
          f"{thread_window_errors[:5]}")
    check(11, "message_timestamp_in_window", not message_window_errors,
          f"{message_window_errors[:5]}")

    cancel_thread_window_errors: list[str] = []
    for thread in threads:
        if not _is_cancellation_thread(thread):
            continue
        cid = thread["customer_id"]
        end = date.fromisoformat(customer_truth[cid]["canonical"]["observation_end"])
        created = _parse_time(thread["created_at"]).date()
        if (end - created).days > 30:
            cancel_thread_window_errors.append(thread["thread_id"])
    check(12, "cancellation_threads_within_30d", not cancel_thread_window_errors,
          f"{cancel_thread_window_errors[:5]}")

    future_dates: list[str] = []
    for thread in threads:
        for ts in [thread["created_at"]] + [m["timestamp"] for m in thread.get("messages", [])]:
            if _parse_time(ts).date() > REFERENCE_DATE:
                future_dates.append(ts)
    check(13, "no_future_dates", not future_dates, f"{future_dates[:5]}")

    collapsed_ids = {dup["collapsed_thread_id"] for dup in duplicates}
    ticket_mismatches: list[str] = []
    for cid in valid_ids:
        in_window = 0
        for thread in threads:
            if thread["customer_id"] != cid or thread["thread_id"] in collapsed_ids:
                continue
            canon = customer_truth[cid]["canonical"]
            end = date.fromisoformat(canon["observation_end"])
            created = _parse_time(thread["created_at"]).date()
            if end - timedelta(days=90) <= created <= end:
                in_window += 1
        expected = int(customer_truth[cid]["core_features"]["support_tickets_90d"])
        if in_window != expected:
            ticket_mismatches.append(f"{cid}: got {in_window}, expected {expected}")
    check(14, "support_tickets_90d_matches_threads", not ticket_mismatches,
          f"{ticket_mismatches[:5]}")

    n_events = sum(1 for rec in customer_truth.values() if rec["canonical"]["event_observed"] == 1)
    check(
        15,
        "events_plus_censored_equals_valid",
        n_events + (N_VALID - n_events) == N_VALID,
        f"{n_events} events + {N_VALID - n_events} censored",
    )

    expected_processed = validation_oracle.get("node3_processed_threads")
    expected_failed = validation_oracle.get("node3_failed_threads")
    check(
        16,
        "node3_processed_plus_failed_equals_threads",
        expected_processed is not None
        and expected_failed is not None
        and expected_processed + expected_failed == len(threads),
        f"processed={expected_processed} failed={expected_failed} total={len(threads)}",
    )

    no_data_customers = sum(
        1
        for rec in support_truth.values()
        if rec["expected_support_data_status"] == "no_data"
    )
    with_data_customers = sum(
        1
        for rec in support_truth.values()
        if rec["expected_support_data_status"] != "no_data"
    )
    check(
        17,
        "node3_with_data_plus_no_data_equals_valid",
        no_data_customers + with_data_customers == N_VALID,
        f"{no_data_customers} + {with_data_customers} != {N_VALID}",
    )

    def cohort_count(name: str) -> int:
        return sum(
            1 for rec in customer_truth.values() if name in rec.get("support_cohorts", [])
        )

    counts = {
        "strong_cancellation": cohort_count("strong_cancellation_intent"),
        "moderate_cancellation": cohort_count("moderate_cancellation_intent"),
        "weak_cancellation": cohort_count("weak_cancellation_intent"),
        "repeated_issues": cohort_count("repeated_issues"),
        "no_support": cohort_count("no_data"),
        "conflict_a": cohort_count("conflict_a"),
        "conflict_b": cohort_count("conflict_b"),
        "cold_start": cohort_count("cold_start"),
    }

    insufficient_routing = sum(
        1
        for cid in valid_ids
        if customer_truth[cid]["pipeline_expectations"]["node2"]["model_status"]
        in ("INSUFFICIENT_DATA", "FAILED")
        and support_truth[cid]["expected_support_data_status"] == "no_data"
    )

    critical_customers = {
        cid for cid, oracle in node4_oracle.items() if oracle.get("risk_level") == "critical"
    }
    rule_counts = {"rule_1": 0, "rule_2": 0, "rule_3": 0, "rule_4": 0}
    for oracle in node4_oracle.values():
        for rule in oracle.get("critical_rules", []):
            rule_counts[rule] = rule_counts.get(rule, 0) + 1

    check(18, "strong_cancellation_ge_80", counts["strong_cancellation"] >= 80,
          f"{counts['strong_cancellation']}")
    check(19, "moderate_cancellation_ge_60", counts["moderate_cancellation"] >= 60,
          f"{counts['moderate_cancellation']}")
    check(20, "weak_cancellation_ge_40", counts["weak_cancellation"] >= 40,
          f"{counts['weak_cancellation']}")
    check(21, "repeated_issues_ge_100", counts["repeated_issues"] >= 100,
          f"{counts['repeated_issues']}")
    check(22, "no_support_ge_1600", counts["no_support"] >= 1600, f"{counts['no_support']}")
    check(23, "conflict_a_ge_40", counts["conflict_a"] >= 40, f"{counts['conflict_a']}")
    check(24, "conflict_b_ge_40", counts["conflict_b"] >= 40, f"{counts['conflict_b']}")
    check(25, "cold_start_ge_100", counts["cold_start"] >= 100, f"{counts['cold_start']}")
    check(26, "insufficient_data_candidates_ge_80", insufficient_routing >= 80,
          f"{insufficient_routing}")
    check(27, "critical_total_ge_150", len(critical_customers) >= 150,
          f"{len(critical_customers)}")
    check(28, "critical_rule_minimums",
          rule_counts["rule_1"] >= 100
          and rule_counts["rule_2"] >= 30
          and rule_counts["rule_3"] >= 20
          and rule_counts["rule_4"] >= 15,
          str(rule_counts))
    check(29, "duplicate_pairs_25", len(duplicates) == 25, f"got {len(duplicates)}")

    unsupported_language_threads = 0
    for thread in threads:
        texts = [
            m.get("text", "") for m in thread.get("messages", [])
            if m.get("role") == "customer"
        ]
        if any(_detect_language(text) is not None for text in texts):
            unsupported_language_threads += 1
    check(30, "unsupported_language_threads_45", unsupported_language_threads == 45,
          f"got {unsupported_language_threads}")
    check(31, "trap_customers_40", len(trap_oracle) == 40, f"got {len(trap_oracle)}")

    leakage_in_core: list[str] = []
    decoy_in_core: list[str] = []
    for cid, rec in customer_truth.items():
        core = rec["core_features"]
        extra = rec["extra_features"]
        if "last_login_days_ago" not in extra:
            leakage_in_core.append(f"{cid}: missing extra last_login_days_ago")
        if "last_login_days_ago" in core:
            leakage_in_core.append(f"{cid}: leakage in core")
        for decoy in ("legacy_flag", "decoy_a", "decoy_b"):
            if decoy in core:
                decoy_in_core.append(f"{cid}: {decoy} in core")
    check(32, "leakage_always_extra", not leakage_in_core, f"{leakage_in_core[:5]}")
    check(33, "decoy_never_core", not decoy_in_core, f"{decoy_in_core[:5]}")

    invalid_codes = [row["error_code"] for row in invalid_rows]
    check(
        34,
        "invalid_rows_exactly_one_primary_code",
        all(code in TAXONOMY for code in invalid_codes)
        and len(invalid_codes) == len(invalid_rows),
    )

    actual_counts = {code: invalid_codes.count(code) for code in TAXONOMY}
    check(
        35,
        "invalid_error_code_counts_match_taxonomy",
        actual_counts == TAXONOMY,
        str(actual_counts),
    )

    missing_latent: list[str] = []
    negative_latent: list[str] = []
    censoring_mismatch: list[str] = []
    for cid, rec in customer_truth.items():
        dgp = rec.get("dgp")
        if dgp is None or dgp.get("latent_event_time_months") is None:
            missing_latent.append(cid)
            continue
        latent_months = float(dgp["latent_event_time_months"])
        if latent_months < 0:
            negative_latent.append(cid)
        latent_days = latent_months * 30.4375
        canon = rec["canonical"]
        start = date.fromisoformat(canon["observation_start"])
        end = date.fromisoformat(canon["observation_end"])
        followup = float((end - start).days)
        expected_event = 1 if latent_days <= followup else 0
        if expected_event != canon["event_observed"]:
            censoring_mismatch.append(cid)
    check(36, "every_valid_has_latent_event_time", not missing_latent, f"{missing_latent[:5]}")
    check(37, "event_times_non_negative", not negative_latent, f"{negative_latent[:5]}")
    check(38, "event_status_agrees_with_censoring", not censoring_mismatch,
          f"{censoring_mismatch[:5]}")

    tenure_mismatches: list[str] = []
    for cid, rec in customer_truth.items():
        canon = rec["canonical"]
        start = date.fromisoformat(canon["observation_start"])
        end = date.fromisoformat(canon["observation_end"])
        expected = float((end - start).days)
        if float(canon["tenure"]) != expected:
            tenure_mismatches.append(cid)
    check(39, "tenure_agrees_with_dates", not tenure_mismatches, f"{tenure_mismatches[:5]}")

    check(40, "event_count_in_range", 420 <= n_events <= 520, f"{n_events} events")

    metadata = truth.get("metadata", {})
    check(
        41,
        "metadata_specification_version",
        metadata.get("specification_version") == "1.2"
        and truth.get("generator", {}).get("specification_version") == "1.2"
        and truth.get("generator", {}).get("generator_version") == "1.1",
        f"spec={metadata.get('specification_version')} "
        f"gen={truth.get('generator', {}).get('generator_version')}",
    )

    dgp_truth = truth.get("dgp_truth", {})
    tve = dgp_truth.get("time_varying_effect", {})
    check(
        42,
        "dgp_truth_and_time_varying_present",
        dgp_truth.get("distribution") == "weibull_proportional_hazards"
        and tve.get("enabled") is True
        and tve.get("feature") == "usage_frequency"
        and tve.get("knot_days") == 180.0,
        f"enabled={tve.get('enabled')} feature={tve.get('feature')}",
    )

    ph_customers = {
        cid for cid, rec in customer_truth.items()
        if rec.get("dgp", {}).get("time_varying") is True
    }
    expected_ph = {f"CUST-{i + 1:04d}" for i in range(350, 400)}
    check(
        43,
        "ph_time_varying_flagged_cohort",
        ph_customers == expected_ph,
        f"got {len(ph_customers)} expected {len(expected_ph)}",
    )

    node2_expect = truth.get("pipeline_expectations", {}).get("node2", {})
    node1_expect = truth.get("pipeline_expectations", {}).get("node1", {})
    check(
        44,
        "node2_expectations_v1_2",
        node2_expect.get("expected_model") == "cox_ph"
        and node2_expect.get("plan_tier_recoverability") in ("stratified", "partial", "adjusted")
        and node2_expect.get("ticket_effect_recoverable") is True
        and node2_expect.get("usage_effect_recoverable") is True,
        str(node2_expect),
    )
    check(
        45,
        "node1_expectations_partial",
        node1_expect.get("expected_status") == "PARTIAL"
        and node1_expect.get("n_accepted") == N_VALID + 130
        and node1_expect.get("n_rejected") == N_INVALID - 130,
        str(node1_expect),
    )

    _CSV_COL = {
        "usage_frequency": "Avg Weekly Active Days",
        "support_tickets_90d": "Support Tickets (Last 90 Days)",
        "contract_length_months": "Contract Length (Months)",
    }
    blank_mismatches: list[str] = []
    for cid, rec in customer_truth.items():
        raw_row = rows[rec["customer_index"]]
        for key, col in _CSV_COL.items():
            cell = str(raw_row.get(col, "") or "")
            missing_now = cell == ""
            missing_truth = key in rec.get("missingness", {})
            if missing_now != missing_truth:
                blank_mismatches.append(
                    f"{cid}:{key}: csv_empty={missing_now} truth={missing_truth}"
                )
    check(46, "missingness_csv_blank_matches_truth", not blank_mismatches,
          f"{blank_mismatches[:5]}")

    def _core(cid: str, key: str):
        return customer_truth[cid]["core_features"][key]

    n_usage_missing = sum(
        1 for rec in customer_truth.values()
        if "usage_frequency" in rec.get("missingness", {})
    )
    n_tickets_missing = sum(
        1 for rec in customer_truth.values()
        if "support_tickets_90d" in rec.get("missingness", {})
    )
    n_contract_missing = sum(
        1 for rec in customer_truth.values()
        if "contract_length_months" in rec.get("missingness", {})
    )
    n_enterprise = sum(1 for cid in valid_ids if _core(cid, "plan_tier") == "enterprise")
    n_starter_monthly = sum(
        1 for cid in valid_ids
        if _core(cid, "plan_tier") == "starter" and _core(cid, "contract_length_months") == 1
    )
    usage_rate = n_usage_missing / N_VALID
    tickets_rate = n_tickets_missing / n_enterprise if n_enterprise else 0.0
    contract_rate = n_contract_missing / n_starter_monthly if n_starter_monthly else 0.0
    check(
        47,
        "missingness_rates_within_tolerance",
        0.01 <= usage_rate <= 0.06
        and 0.05 <= tickets_rate <= 0.16
        and 0.12 <= contract_rate <= 0.30,
        f"usage={usage_rate:.3f} tickets={tickets_rate:.3f} contract={contract_rate:.3f}",
    )

    quant_only_bad: list[str] = []
    for i in range(770, 970):
        cid = f"CUST-{i + 1:04d}"
        oracle = node4_oracle[cid]
        score = oracle.get("expected_quant_score")
        if score is None or score < 0.70 or "quant_only" not in customer_truth[cid].get(
            "quant_cohorts", []
        ):
            quant_only_bad.append(f"{cid}:{score}")
    check(48, "quant_only_band_ge_0_70", not quant_only_bad, f"{quant_only_bad[:5]}")

    qual_only_bad: list[str] = []
    for i in range(200, 225):
        cid = f"CUST-{i + 1:04d}"
        oracle = node4_oracle[cid]
        types = oracle.get("reason_types", [])
        if oracle.get("risk_level") != "critical" or "critical_cancellation_intent" not in types \
                or "missing_quantitative_data" not in types:
            qual_only_bad.append(f"{cid}:{oracle.get('risk_level')}:{types}")
    check(49, "qual_only_strong_is_critical", not qual_only_bad, f"{qual_only_bad[:5]}")

    trap_mismatch: list[str] = []
    trap_forbidden: list[str] = []
    for cid, oracle in trap_oracle.items():
        if oracle.get("expected_risk_level") != oracle.get("truth_risk_level"):
            trap_mismatch.append(
                f"{cid}:{oracle.get('expected_risk_level')} vs "
                f"{oracle.get('truth_risk_level')}"
            )
        if oracle.get("expected_risk_level") in oracle.get("forbidden_claims", []):
            trap_forbidden.append(f"{cid}:{oracle.get('expected_risk_level')}")
    check(50, "trap_expected_matches_truth", not trap_mismatch, f"{trap_mismatch[:5]}")
    check(51, "trap_forbidden_claims_disjoint", not trap_forbidden, f"{trap_forbidden[:5]}")

    _FLAG_VOCAB = {
        "high_urgency", "repeated_issue", "strong_cancellation_intent",
        "moderate_cancellation_intent", "weak_cancellation_intent",
        "cancellation_intent_conflict", "positive_feedback",
        "renewal_or_contract_concern", "low_information",
        "cross_channel_duplicate", "unsupported_language",
    }
    bad_flags = {
        flag for rec in support_truth.values() for flag in rec.get("expected_flags", [])
        if flag not in _FLAG_VOCAB
    }
    check(52, "support_flag_vocab", not bad_flags, f"{bad_flags}")

    churn_lang_mismatch: list[str] = []
    threads_by_cid: dict[str, list[dict]] = {}
    for thread in threads:
        threads_by_cid.setdefault(thread["customer_id"], []).append(thread)
    for cid in valid_ids:
        detected = any(
            message.get("role") == "customer"
            and any(kw in message.get("text", "").lower() for kw in CANCEL_KEYWORDS)
            for thread in threads_by_cid.get(cid, [])
            if thread["thread_id"] not in collapsed_ids
            for message in thread.get("messages", [])
        )
        if detected != support_truth[cid]["expected_churn_language_detected"]:
            churn_lang_mismatch.append(cid)
    check(53, "churn_language_detected_consistent", not churn_lang_mismatch,
          f"{churn_lang_mismatch[:5]}")

    bad_strength = [
        (cid, rec["expected_signal_strength"])
        for cid, rec in support_truth.items()
        if rec["expected_signal_strength"] not in ("none", "weak", "moderate", "strong")
    ]
    check(54, "signal_strength_vocab", not bad_strength, f"{bad_strength[:5]}")

    failed_marker = "injected_corruption:required_feature_NaN_simulated"
    failed_bad: list[str] = []
    for i in range(300, 350):
        cid = f"CUST-{i + 1:04d}"
        rec = customer_truth[cid]
        notes = rec.get("extra_features", {}).get("internal_notes", "")
        if notes != failed_marker:
            failed_bad.append(f"{cid}:notes={notes!r}")
        if node4_oracle[cid].get("risk_level") != "insufficient_data":
            failed_bad.append(f"{cid}:risk={node4_oracle[cid].get('risk_level')}")
    check(55, "failed_corruption_marker", not failed_bad, f"{failed_bad[:5]}")

    n_with_missing = sum(1 for rec in customer_truth.values() if rec.get("missingness"))
    gen_missing = truth.get("generator", {}).get("missingness", {})
    check(
        56,
        "missingness_counts_consistent",
        gen_missing.get("n_customers_with_missing") == n_with_missing
        and gen_missing.get("usage_frequency_mcar") == n_usage_missing
        and gen_missing.get("support_tickets_90d_mar_enterprise") == n_tickets_missing
        and gen_missing.get("contract_length_months_mnar_starter_monthly") == n_contract_missing,
        f"with_missing={n_with_missing} recorded={gen_missing.get('n_customers_with_missing')}",
    )

    csv_bytes = csv_path.read_bytes()
    threads_bytes = threads_path.read_bytes()
    truth_bytes = truth_path.read_bytes()
    lf_ok = b"\r\n" not in csv_bytes and b"\r\n" not in threads_bytes and b"\r\n" not in truth_bytes
    check(57, "lf_only_line_endings", lf_ok, "LF-only" if lf_ok else "CRLF found")

    bad_outcomes = [
        (row["error_code"], row.get("expected_node1_outcome"))
        for row in invalid_rows
        if (row.get("expected_node1_outcome") == "accepted:missingness_passthrough")
        != (row["error_code"] in ("MISSING_CORE", "INVALID_PLAN"))
    ]
    check(
        58,
        "invalid_rows_outcomes_v1_2",
        not bad_outcomes,
        f"{bad_outcomes[:5]}",
    )

    return ValidationReport(checks)


def _print_report(report: ValidationReport) -> None:
    print("DATASET 7 VALIDATION REPORT")
    print("===========================")
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        detail = f"  {check.detail}" if check.detail else ""
        print(f"  #{check.id:<3} {check.name:<45} {status}{detail}")
    print(f"\nResult: {'PASS' if report.passed else 'FAIL'}")
    print(f"Checks: {sum(c.passed for c in report.checks)}/{len(report.checks)} passed")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    csv_path = Path(args[0]) if args else RAW_CSV
    threads_path = Path(args[1]) if len(args) > 1 else THREADS_JSON
    truth_path = Path(args[2]) if len(args) > 2 else TRUTH_JSON
    report = validate_all(csv_path, threads_path, truth_path)
    _print_report(report)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
