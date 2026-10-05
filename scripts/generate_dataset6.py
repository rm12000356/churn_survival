from __future__ import annotations

import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

SEED = 42
REFERENCE_DATE = date(2026, 8, 15)
N_VALID = 5000
TARGET_EVENTS_MIN = 400
TARGET_EVENTS_MAX = 430

REPO = Path(__file__).resolve().parents[1]
RAW_PATH = REPO / "data" / "raw" / "dataset6_saas_churn_messy.csv"
TRUTH_PATH = REPO / "data" / "ground_truth" / "dataset6_saas_churn_ground_truth.json"

COLUMNS = [
    "Cust ID",
    "Account Number",
    "Signup Date",
    "Cancellation Date",
    "Account Status",
    "Plan",
    "Tier",
    "Contract Length (Months)",
    "Avg Weekly Active Days",
    "Support Tickets (Last 90 Days)",
    "Sales Rep",
    "Region",
    "Marketing Source",
    "Industry",
    "Company Size",
    "Legacy Churn Score",
    "Last Login Days Ago",
    "Internal Notes",
]

TIERS = ["starter", "pro", "enterprise"]
CHURNED_REPS = ["Churned", "Yes", "True", "1"]
ACTIVE_REPS = ["Active", "No", "False", "0"]
SALES_REPS = ["alice@acme.io", "bob@acme.io", "carla@acme.io", "dan@acme.io", "erin@acme.io"]
REGIONS = ["US", "EU", "APAC"]
MARKETING = ["Google Ads", "LinkedIn", "Referral", "Organic", "Partner"]
INDUSTRIES = ["Technology", "Financial Services", "Healthcare", "Retail", "Other"]
COMPANY_SIZES = ["SMB", "Mid-Market", "Enterprise"]
INTERNAL_NOTES = ["VIP account", "escalated to T2", "requested SSO", "in trial", "CSM handoff"]


def _fmt_date(d: date, fmt: int) -> str:
    if fmt == 0:
        return d.strftime("%Y-%m-%d")
    if fmt == 1:
        return d.strftime("%m/%d/%Y")
    return d.strftime("%d %b %Y")


def _gen_valid(rng: np.random.Generator) -> tuple[dict[str, np.ndarray], list[date]]:
    n = N_VALID

    tier = rng.choice(TIERS, size=n, p=[0.48, 0.35, 0.17])

    contract = np.empty(n, dtype=int)
    for t in TIERS:
        mask = tier == t
        if t == "starter":
            contract[mask] = rng.choice([1, 12], size=int(mask.sum()), p=[0.70, 0.30])
        elif t == "pro":
            contract[mask] = rng.choice([1, 12, 24], size=int(mask.sum()), p=[0.25, 0.55, 0.20])
        else:
            contract[mask] = rng.choice([1, 12, 24], size=int(mask.sum()), p=[0.05, 0.30, 0.65])

    tier_mean = {"starter": 2.5, "pro": 4.0, "enterprise": 5.5}
    usage = np.array([tier_mean[t] for t in tier]) + rng.normal(0.0, 1.5, n)
    usage = np.round(np.clip(usage, 0.0, 7.0), 1)

    tickets = np.where(rng.random(n) < 0.70, 0, rng.poisson(1.3, n))
    tickets = np.clip(tickets, 0, 40).astype(int)

    age = np.round(np.clip(rng.exponential(400.0, n), 0.0, 1095.0)).astype(int)
    u = np.clip(rng.uniform(0.0, 1.0, n), 1e-12, 1.0)

    status_rep_idx = rng.integers(0, 4, n)
    date_fmt_idx = rng.integers(0, 3, n)
    sales_rep_idx = rng.integers(0, len(SALES_REPS), n)
    region_idx = rng.integers(0, len(REGIONS), n)
    marketing_idx = rng.integers(0, len(MARKETING), n)
    industry_idx = rng.integers(0, len(INDUSTRIES), n)
    industry_missing = rng.random(n) < 0.10
    company_idx = rng.integers(0, len(COMPANY_SIZES), n)
    company_missing = rng.random(n) < 0.15
    note_flag = rng.random(n) < 0.05
    note_idx = rng.integers(0, len(INTERNAL_NOTES), n)
    legacy_score = rng.integers(0, 101, n)
    last_login_churned = rng.integers(30, 121, n)
    last_login_active = rng.integers(0, 8, n)

    arrays = {
        "tier": tier,
        "contract": contract,
        "usage": usage,
        "tickets": tickets,
        "age": age,
        "u": u,
        "status_rep_idx": status_rep_idx,
        "date_fmt_idx": date_fmt_idx,
        "sales_rep_idx": sales_rep_idx,
        "region_idx": region_idx,
        "marketing_idx": marketing_idx,
        "industry_idx": industry_idx,
        "industry_missing": industry_missing,
        "company_idx": company_idx,
        "company_missing": company_missing,
        "note_flag": note_flag,
        "note_idx": note_idx,
        "legacy_score": legacy_score,
        "last_login_churned": last_login_churned,
        "last_login_active": last_login_active,
    }

    starts = [REFERENCE_DATE - timedelta(days=int(d)) for d in age]
    return arrays, starts


def _apply_forced_segments(a: dict[str, np.ndarray]) -> None:
    for i in range(0, 450):
        a["tier"][i] = "pro"
        a["contract"][i] = 12
        a["usage"][i] = 5.0
        a["tickets"][i] = 0

    for k, i in enumerate(range(450, 530)):
        a["tier"][i] = "starter"
        a["contract"][i] = 1
        a["usage"][i] = round(0.5 + 0.1 * (k % 8), 1)
        a["tickets"][i] = 3 + (k % 6)

    for k, i in enumerate(range(530, 545)):
        a["usage"][i] = round(k * 0.01, 2)

    for k, i in enumerate(range(545, 560)):
        a["usage"][i] = round(min(6.8 + k * 0.02, 7.0), 2)

    for k, i in enumerate(range(560, 570)):
        a["tickets"][i] = 15 + 2 * k

    for i in range(570, 575):
        a["age"][i] = 0


def _linear_predictor(a: dict[str, np.ndarray]) -> np.ndarray:
    lp = np.zeros(N_VALID, dtype=float)
    lp += 0.90 * (a["tier"] == "starter")
    lp += 0.35 * (a["tier"] == "pro")
    lp -= 0.04 * a["contract"].astype(float)
    lp -= 0.35 * a["usage"].astype(float)
    lp += 0.03 * a["tickets"].astype(float)
    return lp


def _pick_lambda0(exp_lp: np.ndarray, age: np.ndarray, u: np.ndarray) -> float:
    def events(lam: float) -> int:
        return int(np.sum((-np.log(u)) < lam * exp_lp * age))

    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if events(mid) >= TARGET_EVENTS_MIN:
            hi = mid
        else:
            lo = mid
    lam = hi
    while events(lam) < TARGET_EVENTS_MIN:
        lam *= 1.0001
    return lam


def _survive(
    a: dict[str, np.ndarray], starts: list[date], exp_lp: np.ndarray, lam: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    hazard = lam * exp_lp
    t = -np.log(a["u"]) / hazard
    age = a["age"].astype(float)
    event = (t < age).astype(int)
    tenure = np.where(event == 1, np.minimum(age, np.maximum(1.0, np.round(t))), age).astype(int)
    ends = [
        (starts[i] + timedelta(days=int(tenure[i]))) if event[i] == 1 else REFERENCE_DATE
        for i in range(N_VALID)
    ]
    return event, tenure, t, np.array(ends)


def _check_directions(
    a: dict[str, np.ndarray], event: np.ndarray
) -> dict[str, dict[str, float]]:
    def rate(mask: np.ndarray) -> float:
        return float(event[mask].mean()) if mask.any() else 0.0

    by_tier = {t: rate(a["tier"] == t) for t in TIERS}
    by_contract = {c: rate(a["contract"] == c) for c in (1, 12, 24)}
    usage_low = rate(a["usage"] <= 1.5)
    usage_mid = rate((a["usage"] > 1.5) & (a["usage"] <= 4.5))
    usage_high = rate(a["usage"] > 4.5)
    by_usage = {"low": usage_low, "mid": usage_mid, "high": usage_high}
    tickets_0 = rate(a["tickets"] == 0)
    tickets_1 = rate((a["tickets"] >= 1) & (a["tickets"] <= 2))
    tickets_3 = rate(a["tickets"] >= 3)
    by_tickets = {"0": tickets_0, "1-2": tickets_1, "3+": tickets_3}

    tickets_corr = float(np.corrcoef(a["tickets"].astype(float), event.astype(float))[0, 1])

    assert by_tier["starter"] > by_tier["pro"] > by_tier["enterprise"], by_tier
    assert by_contract[1] > by_contract[12] > by_contract[24], by_contract
    assert usage_low > usage_mid > usage_high, by_usage
    assert tickets_corr > 0.0, tickets_corr
    assert tickets_0 < tickets_3, by_tickets

    return {"plan_tier": by_tier, "contract_length_months": by_contract,
            "usage_frequency": by_usage, "support_tickets_90d": by_tickets,
            "support_tickets_90d_pearson_corr": tickets_corr}


def _invalid_rows() -> list[dict]:
    rows: list[dict] = []

    def base(cid: str, start: date, status: str, plan: str, contract: int,
             usage: float | None, tickets: int) -> dict:
        return {
            "customer_id": cid,
            "signup": start,
            "cancel": None,
            "status": status,
            "plan": plan,
            "contract": contract,
            "usage": usage,
            "tickets": tickets,
        }

    for k in range(12):
        rows.append(
            base(f"ACCT-MISS-{k + 1:02d}", date(2025, 1 + (k % 6), 1 + (k % 27)),
                 "Active", TIERS[k % 3], [1, 12, 24][k % 3], None, k % 3)
        )
        rows[-1]["group"] = "missing_usage_frequency"
        rows[-1]["outcome"] = "rejected:CORE_MISSING"

    for k, sd in enumerate([date(2026, 9, 1), date(2026, 9, 5), date(2026, 8, 20)]):
        rows.append(base(f"ACCT-FS-{k + 1:02d}", sd, "Active", TIERS[k], [1, 12, 24][k], 3.5, 1))
        rows[-1]["group"] = "future_start_date"
        rows[-1]["outcome"] = "rejected:WINDOW_ORDER"

    for k, cd in enumerate([date(2026, 9, 15), date(2026, 8, 30), date(2026, 10, 2)]):
        rows.append(base(f"ACCT-FE-{k + 1:02d}", date(2024, 5, 1 + k), "Churned",
                         "starter", 1, 1.2, 4))
        rows[-1]["cancel"] = cd
        rows[-1]["group"] = "future_end_date"
        rows[-1]["outcome"] = "rejected:FUTURE_LEAKAGE"

    for k, st in enumerate(["2", "pending", "", "Cancelled?"]):
        rows.append(base(f"ACCT-BEV-{k + 1:02d}", date(2025, 2, 1 + k), st,
                         "pro", 12, 4.0, 0))
        rows[-1]["group"] = "bad_event_value"
        rows[-1]["outcome"] = "rejected:EVENT_OBSERVED"

    for k, dup in enumerate(["ACCT-0001", "ACCT-0002", "ACCT-0003"]):
        rows.append(base(dup, date(2025, 3, 1 + k), "Active", "pro", 12, 4.0, 0))
        rows[-1]["group"] = "duplicate_id"
        rows[-1]["outcome"] = "rejected:UNIQUE_ID"

    return rows


def _build_invalid_extra(rng: np.random.Generator, k: int) -> dict[str, str]:
    return {
        "account_number": f"BILL-{1000 + (k % 500):04d}",
        "tier": {"starter": "S", "pro": "P", "enterprise": "E"}[TIERS[k % 3]],
        "sales_rep": SALES_REPS[k % len(SALES_REPS)],
        "region": REGIONS[k % len(REGIONS)],
        "marketing": MARKETING[k % len(MARKETING)],
        "industry": INDUSTRIES[k % len(INDUSTRIES)],
        "company_size": COMPANY_SIZES[k % len(COMPANY_SIZES)],
        "legacy_score": int(rng.integers(0, 101)),
        "last_login": int(rng.integers(0, 8)),
        "internal_notes": "",
    }


def main() -> None:
    rng = np.random.default_rng(SEED)
    arrays, starts = _gen_valid(rng)
    _apply_forced_segments(arrays)
    exp_lp = np.exp(_linear_predictor(arrays))
    lam = _pick_lambda0(exp_lp, arrays["age"].astype(float), arrays["u"])
    event, tenure, _t, ends = _survive(arrays, starts, exp_lp, lam)

    n_events = int(event.sum())
    assert TARGET_EVENTS_MIN <= n_events <= TARGET_EVENTS_MAX, (
        f"churn events {n_events} outside [{TARGET_EVENTS_MIN},{TARGET_EVENTS_MAX}]"
    )
    direction_rates = _check_directions(arrays, event)

    rows: list[list[str]] = []
    records: list[dict] = []
    for i in range(N_VALID):
        start = starts[i]
        end = ends[i]
        is_event = int(event[i]) == 1
        fmt = int(arrays["date_fmt_idx"][i])
        status = (CHURNED_REPS[int(arrays["status_rep_idx"][i])] if is_event
                  else ACTIVE_REPS[int(arrays["status_rep_idx"][i])])
        usage = float(arrays["usage"][i])
        tickets = int(arrays["tickets"][i])
        tier = str(arrays["tier"][i])
        contract = int(arrays["contract"][i])
        customer_id = f"ACCT-{i + 1:04d}"
        account_number = f"BILL-{1000 + (i % 500):04d}"
        tier_code = {"starter": "S", "pro": "P", "enterprise": "E"}[tier]

        groups: list[str] = []
        if i < 450:
            groups.append("low_variation_segment")
        if 450 <= i < 530:
            groups.append("cancellation_like")
        if 530 <= i < 545:
            groups.append("extreme_usage_low")
        if 545 <= i < 560:
            groups.append("extreme_usage_high")
        if 560 <= i < 570:
            groups.append("high_tickets")
        if 570 <= i < 575:
            groups.append("tenure_zero")
        ten = int(tenure[i])
        if 0 < ten < 14 and not is_event:
            groups.append("cold_start")
        if is_event and ten < 30:
            groups.append("early_churn")
        if is_event and ten > 900:
            groups.append("late_churn")

        rows.append([
            customer_id,
            account_number,
            _fmt_date(start, fmt),
            _fmt_date(end, fmt) if is_event else "",
            status,
            tier,
            tier_code,
            str(contract),
            f"{usage:.1f}",
            str(tickets),
            SALES_REPS[int(arrays["sales_rep_idx"][i])],
            REGIONS[int(arrays["region_idx"][i])],
            MARKETING[int(arrays["marketing_idx"][i])],
            "" if arrays["industry_missing"][i] else INDUSTRIES[int(arrays["industry_idx"][i])],
            "" if arrays["company_missing"][i] else COMPANY_SIZES[int(arrays["company_idx"][i])],
            str(int(arrays["legacy_score"][i])),
            str(int(arrays["last_login_churned"][i] if is_event
                     else arrays["last_login_active"][i])),
            INTERNAL_NOTES[int(arrays["note_idx"][i])] if arrays["note_flag"][i] else "",
        ])
        records.append({
            "raw_row_index": i,
            "customer_id": customer_id,
            "observation_start": start.isoformat(),
            "observation_end": end.isoformat(),
            "event_observed": int(event[i]),
            "plan_tier": tier,
            "contract_length_months": contract,
            "usage_frequency": usage,
            "support_tickets_90d": tickets,
            "edge_case_groups": groups,
            "expected_node1_outcome": "accepted",
        })

    invalid = _invalid_rows()
    for k, spec in enumerate(invalid):
        fmt = k % 3
        customer_id = spec["customer_id"]
        tier = spec["plan"]
        extra = _build_invalid_extra(rng, k)
        cancel = spec["cancel"]
        rows.append([
            customer_id,
            extra["account_number"],
            _fmt_date(spec["signup"], fmt),
            _fmt_date(cancel, fmt) if cancel else "",
            spec["status"],
            tier,
            {"starter": "S", "pro": "P", "enterprise": "E"}[tier],
            str(spec["contract"]),
            "" if spec["usage"] is None else f"{spec['usage']:.1f}",
            str(spec["tickets"]),
            extra["sales_rep"],
            extra["region"],
            extra["marketing"],
            extra["industry"],
            extra["company_size"],
            extra["legacy_score"],
            extra["last_login"],
            extra["internal_notes"],
        ])
        records.append({
            "raw_row_index": N_VALID + k,
            "customer_id": customer_id,
            "observation_start": spec["signup"].isoformat(),
            "observation_end": (cancel.isoformat() if cancel else REFERENCE_DATE.isoformat()),
            "event_observed": None,
            "plan_tier": tier,
            "contract_length_months": spec["contract"],
            "usage_frequency": spec["usage"],
            "support_tickets_90d": spec["tickets"],
            "edge_case_groups": [spec["group"]],
            "expected_node1_outcome": spec["outcome"],
        })

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RAW_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    truth = {
        "dataset": "dataset6_saas_churn_messy",
        "reference_date": REFERENCE_DATE.isoformat(),
        "generator": {
            "script": "scripts/generate_dataset6.py",
            "seed": SEED,
            "n_valid_customers": N_VALID,
            "n_invalid_rows": len(invalid),
            "n_raw_rows": len(rows),
            "churn_events_generated": n_events,
            "baseline_rate": lam,
        },
        "directions": {
            "plan_tier": {
                "kind": "categorical",
                "reference_category": "enterprise",
                "effect": {"starter": "higher_hazard", "pro": "no_reliable_adjusted_claim",
                           "enterprise": "baseline"},
                "coefficient": {"starter": 0.90, "pro": 0.35, "enterprise": 0.0},
                "note": "pro is confounded with contract_length_months and usage_frequency "
                        "(both hazard-lowering), so the adjusted Cox estimate for pro reverses "
                        "to HR<1; only starter's higher hazard vs enterprise is a reliable "
                        "adjusted claim.",
            },
            "contract_length_months": {
                "kind": "numeric",
                "effect": "higher_hazard_at_lower_months",
                "coefficient": -0.04,
            },
            "usage_frequency": {
                "kind": "numeric",
                "effect": "higher_hazard_at_lower_usage",
                "coefficient": -0.35,
            },
            "support_tickets_90d": {
                "kind": "numeric",
                "effect": "higher_hazard_at_more_tickets",
                "coefficient": 0.03,
            },
        },
        "observed_univariate_churn_rate": direction_rates,
        "extra_columns": {
            "Account Number": "decoy_non_unique_id",
            "Tier": "decoy_plan_code",
            "Legacy Churn Score": "noise",
            "Last Login Days Ago": "leakage",
            "Sales Rep": "irrelevant",
            "Region": "irrelevant",
            "Marketing Source": "irrelevant",
            "Industry": "irrelevant_missing_values",
            "Company Size": "irrelevant_missing_values",
            "Internal Notes": "unmapped",
        },
        "records": records,
    }
    TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRUTH_PATH.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote {RAW_PATH} ({len(rows)} data rows)")
    print(f"wrote {TRUTH_PATH}")
    print(f"churn events (valid): {n_events}  baseline lambda0={lam:.6e}")
    print("univariate churn rates:")
    for k, v in direction_rates.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
