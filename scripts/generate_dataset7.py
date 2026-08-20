"""Deterministic end-to-end churn diagnostic dataset generator (dataset7).

Produces three artifacts under the repo root:

- ``data/raw/dataset7_customers_messy.csv``           — the messy raw customer
  file (15 columns, mixed date formats, mixed status representations, leakage
  and decoy extras, and 450 deliberately-invalid rows Node 1 must quarantine).
- ``data/raw/dataset7_support_threads_messy.json``    — per-customer support
  threads (email/chat/phone/twitter) with deliberate messiness: missing
  subjects/statuses, null/empty tags, misspelled channels, cross-channel
  duplicate pairs, and unsupported-language threads.
- ``data/ground_truth/dataset7_ground_truth.json``    — the per-row oracle:
  canonical records, core/extra features, support cohorts, per-customer
  Node 2/3/4 scenario oracles, DGP latents, invalid-row taxonomy, duplicate
  pairs, and the Node 3 processed/failed counts.

The generator is fully deterministic: a single master seed, no wall-clock time,
no ``datetime.now()``. Re-running it reproduces all three files byte-for-byte.
After writing it validates the outputs with ``validate_dataset7`` (the §33
invariant list, 40 checks) and exits non-zero on any failure.

Generative model (Weibull proportional hazards, locked in the spec addendum):

    lp_i = 0.85*I[starter] + 0.25*I[pro] - 0.06*contract_length_months
         - 0.18*usage_frequency + 0.10*support_tickets_90d + frailty_i

    frailty_i ~ N(0, 0.15**2)
    S(t) = exp(-(t / lambda0)^k * exp(lp_i))    with k = 1.2

``lambda0`` is locked at 36.0 months in the DGP design. Because the locked value
with the exact coefficient set yields ~1239 churn events (far above the locked
target band 420..520), the generator retries fresh covariate draws (seeds
``MASTER_SEED+1+attempt`` for attempt in 0..99) and, when none land in band,
deterministically bisects ``lambda0`` on the accepted stream (seed
``MASTER_SEED+1+100``). Both the locked and the calibrated ``lambda0`` and the
attempt id are recorded in the ground truth ``generator`` block.

``support_tickets_90d`` is the count of non-collapsed threads in the final 90
days of each customer's observation window. Threads are placed per cohort plan
so this count normally equals the planned DGP covariate; for the small set of
*event* customers whose tenure is shorter than 90 days there is no
"outside-the-90-day-window" region, so after threads are placed the generator
recomputes the true in-window count and records *that* as the core feature (the
planned value stays the DGP covariate and is kept in ``dgp.tickets_planned``).

Node 2 model status and Node 3 support data status are recorded as a per-customer
*scenario oracle* (spec addendum §5.2/§5.3) — intent cohorts that the real nodes
approximate with a single batch-level status each.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

try:
    from validate_dataset7 import validate_all
except ImportError:  # imported from tests
    from scripts.validate_dataset7 import validate_all

MASTER_SEED = 2137457950
REFERENCE_DATE = date(2026, 8, 15)

SPECIFICATION_VERSION = "1.2"
GENERATOR_VERSION = "1.1"

N_VALID = 4550
N_INVALID = 450
N_RAW = N_VALID + N_INVALID

WEIBULL_K = 1.2
LAMBDA0_LOCKED_MONTHS = 36.0
MONTH_DAYS = 30.4375

TARGET_EVENTS_MIN = 420
TARGET_EVENTS_MAX = 520

# DGP coefficients (locked — do not change).
BETA_STARTER = 0.85
BETA_PRO = 0.25
BETA_CONTRACT = -0.06
BETA_USAGE = -0.18
BETA_TICKETS = 0.10
FRAILTY_SD = 0.15

# §31 time-varying usage effect (only the PH-violation cohort, indices 350..399).
PH_LO, PH_HI = 350, 400
PH_KNOT_DAYS = 180.0
PH_USAGE_FACTOR = 0.5  # usage coefficient beyond the 180-day knot

# §11 missingness plan (deterministic injection, dedicated RNG stream so the
# survival attempt loop is untouched).
MISSINGNESS_SEED = MASTER_SEED + 7
MISSING_USAGE_RATE = 0.03   # MCAR
MISSING_TICKETS_RATE = 0.10  # MAR on enterprise
MISSING_CONTRACT_RATE = 0.20  # MNAR on starter-monthly (contract == 1)

TIERS = ["starter", "pro", "enterprise"]
REGIONS = ["US", "EU", "APAC", "LATAM"]
SALES_REPS = ["alice@acme.io", "bob@acme.io", "carla@acme.io", "dan@acme.io", "erin@acme.io"]
INTERNAL_NOTES = ["VIP account", "escalated to T2", "requested SSO", "in trial", "CSM handoff"]
DECOY_A = ["Google Ads", "LinkedIn", "Referral", "Organic", "Partner"]
DECOY_B = ["Technology", "Financial Services", "Healthcare", "Retail", "Other"]
INVALID_PLANS = ["gold", "platinum", "free", "BETA", "silver", "trial"]
BAD_STATUSES = ["2", "pending", "Cancelled?", "Unknown", "N/A", "maybe"]

CHURNED_REPS = ["Churned", "Yes", "1", "Churned", "Yes"]
ACTIVE_REPS = ["Active", "No", "0", "Active", "No"]

CHANNELS = ["email", "chat", "phone", "twitter"]
CHANNEL_P = [0.55, 0.25, 0.15, 0.05]
CHANNEL_TYPO = {"email": "emial", "chat": "chatt"}

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

# Message pools (kept free of the validator's CANCEL_KEYWORDS unless the thread
# is deliberately a cancellation thread).
GENERIC_CUSTOMER = [
    "How do I add more seats?",
    "Question about my invoice.",
    "Can you reset my password?",
    "Do you support SSO?",
    "Where do I find the API key?",
    "The new layout looks confusing.",
    "Can you update my billing email?",
]
GENERIC_AGENT = [
    "Thanks for reaching out! We will look into this.",
    "We received your message and will respond shortly.",
    "Here is how to resolve this: check Settings > Billing.",
    "Let me escalate this to our team.",
    "We appreciate your patience.",
]
CANCEL_STRONG_CUSTOMER = [
    "I want to cancel my subscription.",
    "Please cancel my account, I am leaving.",
    "Cancel my plan please. I want to terminate it.",
    "Stop my subscription, I am not renewing.",
    "I need to terminate my contract now.",
    "I am cancelling because the service is too expensive.",
]
CANCEL_MODERATE_CUSTOMER = [
    "I am thinking about cancelling, any reason to stay?",
    "Considering cancelling my plan. Is there a discount?",
    "I may cancel soon and want to check my options.",
    "Is it easy to cancel? I am undecided.",
]
CANCEL_WEAK_CUSTOMER = [
    "I am not sure I am getting value, maybe cancel.",
    "Any promo? I was about to cancel.",
    "Thinking of cancelling but want to hear what you offer.",
]
CANCEL_NEUTRAL_CUSTOMER = [
    "Please close my account, no issues, thanks for everything.",
    "Removing my account. The service was fine, thanks.",
]
POSITIVE_CUSTOMER = [
    "Love the product, great job!",
    "Amazing support, thank you so much!",
    "This tool is fantastic, highly recommend it.",
    "Very happy with the service so far.",
    "Great experience, keep it up!",
]
RENEWAL_CUSTOMER = [
    "When does my plan renew? I want to extend it.",
    "Please renew my annual plan.",
    "Can I extend my contract for another year?",
    "Questions about my renewal terms.",
]
REPEATED_CUSTOMER = [
    "The app keeps crashing.",
    "Still getting the same error, this is the third time.",
    "The dashboard is broken again.",
    "Sync failed again, please fix it.",
    "Same bug as before, very frustrating.",
]
URGENT_CUSTOMER = [
    "URGENT: the system is down!",
    "This is urgent, please fix ASAP.",
    "Emergency: our billing is broken.",
    "I am very frustrated and need immediate help.",
]
COMPLAINT_CUSTOMER = [
    "The feature I paid for is missing.",
    "Your support was slow and unhelpful.",
    "Not happy with the billing changes.",
    "This outage is unacceptable.",
]
LOW_INFO_CUSTOMER = ["thanks", "ok", "help", "?", "issue", "please fix"]
DUP_ISSUE_CUSTOMER = [
    "My invoice shows the wrong amount.",
    "The export is missing rows.",
    "I cannot access my account.",
]
UNSUPPORTED_ES = {
    "customer": [
        "Quiero dar de baja mi suscripción, gracias.",
        "Necesito ayuda con mi suscripción, gracias.",
        "Quiero dar de baja mi plan, gracias.",
    ],
    "agent": ["Gracias por contactarnos, te ayudaremos."],
}
UNSUPPORTED_DE = {
    "customer": [
        "Ich möchte meinen Vertrag kündigen.",
        "Leider habe ich ein Problem mit dem Abo.",
        "Bitte kündigen Sie meinen Vertrag, danke.",
    ],
    "agent": ["Vielen Dank, wir helfen Ihnen."],
}
UNSUPPORTED_FR = {
    "customer": [
        "Je veux annuler mon abonnement, merci.",
        "Pouvez-vous m aider à supprimer mon compte?",
    ],
    "agent": ["Merci, nous allons vous aider."],
}

REPO = Path(__file__).resolve().parents[1]
RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"
TRUTH_JSON = REPO / "data" / "ground_truth" / "dataset7_ground_truth.json"


# --------------------------------------------------------------------------- #
# Bands / cohorts
# --------------------------------------------------------------------------- #
def _bands(i: int) -> tuple[tuple[str, str], str]:
    """Return ((node2_status, node2_reason), support_data_status) for index i."""
    if i < 120:
        node2 = ("INSUFFICIENT_DATA", "cold_start")
    elif i < 200:
        node2 = ("INSUFFICIENT_DATA", "short_tenure_15_29d")
    elif i < 225:
        node2 = ("FALLBACK", "qual_only")
    elif i < 300:
        node2 = ("FALLBACK", "fallback_generic")
    elif i < 350:
        node2 = ("FAILED", "failed_zero_variance")
    elif i < 400:
        node2 = ("WARNING", "ph_violation")
    elif i < 650:
        node2 = ("WARNING", "short_tenure_30_90d")
    elif i < 750:
        node2 = ("WARNING", "warning_generic")
    else:
        node2 = ("READY", "ready")

    if i < 90 or (770 <= i < 2500):
        support = "no_data"
    elif 90 <= i < 770 or (2500 <= i < 2830):
        support = "limited_data"
    else:
        support = "sufficient_data"
    return node2, support


def _cohorts(i: int) -> list[str]:
    """Deterministic cohort labels for index i (support_cohorts)."""
    node2, support = _bands(i)
    cohorts: list[str] = []
    if node2[1] == "cold_start":
        cohorts.append("cold_start")
    if node2[1] in ("short_tenure_15_29d", "short_tenure_30_90d"):
        cohorts.append("short_tenure")
    if 770 <= i < 970:
        cohorts.append("quant_only")
    if 2500 <= i < 2800:
        cohorts.append("low_information")
    if 2830 <= i < 2880:
        cohorts.append("conflict_a")
    if 2880 <= i < 2930:
        cohorts.append("conflict_b")
        if i < 2910:
            cohorts.append("critical_rule_2")
    if 2930 <= i < 3020:
        cohorts.append("strong_cancellation_intent")
    if 200 <= i < 225:  # qual-only strong (§22 v1.2): quant null, strong intent -> critical
        cohorts.append("strong_cancellation_intent")
    if 3020 <= i < 3080:
        cohorts.append("moderate_cancellation_intent")
    if 3080 <= i < 3120:
        cohorts.append("weak_cancellation_intent")
    if 3120 <= i < 3240:
        cohorts.append("renewal_concern")
        if i < 3140:
            cohorts.append("critical_rule_3")
    if 3240 <= i < 3340:
        cohorts.append("repeated_issues")
        if i < 3255:
            cohorts.append("critical_rule_4")
    if 3340 <= i < 3420:
        cohorts.append("high_urgency")
    if 3420 <= i < 3820:
        cohorts.append("positive_sentiment")
    if 3820 <= i < 3860:
        trap = (i - 3820) // 10 + 1
        cohorts.append(f"trap_00{trap}")
        if trap == 4:
            cohorts.append("strong_cancellation_intent")
    if 3860 <= i < 3885:
        cohorts.append("cross_channel_duplicate")
    if 3885 <= i < 3915:
        cohorts.append("unsupported_language")
    if support == "no_data":
        cohorts.append("no_data")
    return cohorts


def _age_for(i: int, rng: np.random.Generator) -> int:
    """Observation-window length (days) for index i, drawn in fixed order."""
    if i < 120:
        return int(rng.integers(5, 15))
    if i < 200:
        return int(rng.integers(15, 30))
    if i < 225:
        return int(rng.integers(30, 91))
    if i < 300:
        return int(rng.integers(30, 181))
    if i < 350:
        return int(rng.integers(30, 121))
    if i < 400:
        return int(rng.integers(180, 366))
    if i < 650:
        return int(rng.integers(30, 91))
    if i < 750:
        return int(rng.integers(91, 366))
    return int(np.clip(rng.exponential(400.0), 120.0, 1095.0))


# --------------------------------------------------------------------------- #
# Covariate drawing
# --------------------------------------------------------------------------- #
def _draw_contract(rng: np.random.Generator, tier: np.ndarray) -> np.ndarray:
    n = len(tier)
    contract = np.empty(n, dtype=int)
    for t in TIERS:
        mask = tier == t
        if t == "starter":
            contract[mask] = rng.choice([1, 12], size=int(mask.sum()), p=[0.65, 0.35])
        elif t == "pro":
            contract[mask] = rng.choice([1, 12, 24], size=int(mask.sum()), p=[0.20, 0.55, 0.25])
        else:
            contract[mask] = rng.choice([1, 12, 24], size=int(mask.sum()), p=[0.05, 0.30, 0.65])
    return contract


def _draw_covariates(rng: np.random.Generator) -> dict[str, np.ndarray]:
    n = N_VALID
    tier = rng.choice(TIERS, size=n, p=[0.42, 0.33, 0.25])
    contract = _draw_contract(rng, tier)
    tier_mean = {"starter": 3.0, "pro": 4.0, "enterprise": 5.5}
    usage = np.array([tier_mean[t] for t in tier]) + rng.normal(0.0, 1.5, n)
    usage = np.round(np.clip(usage, 0.0, 7.0), 1)
    u = np.clip(rng.uniform(0.0, 1.0, n), 1e-12, 1.0)
    frailty = rng.normal(0.0, FRAILTY_SD, n)
    status_rep = rng.integers(0, 5, n)
    last_login_churned = rng.integers(30, 121, n)
    last_login_active = rng.integers(0, 8, n)
    legacy_flag = rng.random(n) < 0.20
    region_idx = rng.integers(0, len(REGIONS), n)
    sales_rep_idx = rng.integers(0, len(SALES_REPS), n)
    note_flag = rng.random(n) < 0.06
    note_idx = rng.integers(0, len(INTERNAL_NOTES), n)
    decoy_a_idx = rng.integers(0, len(DECOY_A), n)
    decoy_b_idx = rng.integers(0, len(DECOY_B), n)
    return {
        "tier": tier,
        "contract": contract,
        "usage": usage,
        "u": u,
        "frailty": frailty,
        "status_rep": status_rep,
        "last_login_churned": last_login_churned,
        "last_login_active": last_login_active,
        "legacy_flag": legacy_flag,
        "region_idx": region_idx,
        "sales_rep_idx": sales_rep_idx,
        "note_flag": note_flag,
        "note_idx": note_idx,
        "decoy_a_idx": decoy_a_idx,
        "decoy_b_idx": decoy_b_idx,
    }


def _overwrite(a: dict[str, np.ndarray], rng: np.random.Generator) -> None:
    """Deterministically overwrite per-band covariates (age, tickets, tier...)."""
    n = N_VALID
    a["age"] = np.empty(n, dtype=int)
    a["tickets"] = np.zeros(n, dtype=int)
    for i in range(n):
        a["age"][i] = _age_for(i, rng)
        if i < 90:
            a["tickets"][i] = 0
        elif i < 120:
            a["tickets"][i] = 1 + (i % 2)
        elif i < 200:
            a["tickets"][i] = 1
        elif i < 300:
            a["tickets"][i] = 1 + (i % 2)
        elif i < 350:
            a["tier"][i] = "pro"
            a["contract"][i] = 12
            a["usage"][i] = 5.0
            a["tickets"][i] = 0
        elif i < 650 or i < 770:
            a["tickets"][i] = 1 + (i % 2)
        elif i < 970:  # quant_only (770..969): READY, no threads, strong quant
            a["tier"][i] = "starter"
            a["contract"][i] = 1
            a["usage"][i] = round((i % 5) * 0.1, 1)
            a["tickets"][i] = 0
            a["frailty"][i] = 0.05 + (i % 5) * 0.02
        elif i < 2500:
            a["tickets"][i] = 0
        elif i < 2800:
            a["tier"][i] = "pro"
            a["contract"][i] = 12
            a["usage"][i] = 4.0
            a["tickets"][i] = 1
        elif i < 2830:
            a["tickets"][i] = 1 + (i % 2)
        elif i < 2880:  # conflict_a: happy on paper, churn by surprise
            a["tier"][i] = "starter"
            a["contract"][i] = 1
            a["usage"][i] = 0.0
            a["tickets"][i] = 0
            a["frailty"][i] = 0.15  # keep quant >= 0.75 with quant_only crowding the top
        elif i < 2930:  # conflict_b: says cancel, numbers say stay
            a["tier"][i] = "enterprise"
            a["contract"][i] = 24
            a["usage"][i] = 6.5
            a["tickets"][i] = 1 + (i % 2)
        elif i < 3020:  # strong cancellation intent
            a["tier"][i] = "starter"
            a["contract"][i] = 1
            a["usage"][i] = 1.5
            a["tickets"][i] = 3
        elif i < 3080:  # moderate
            a["tier"][i] = "starter"
            a["contract"][i] = 12
            a["usage"][i] = 2.5
            a["tickets"][i] = 2
        elif i < 3120:  # weak
            a["tier"][i] = "pro"
            a["contract"][i] = 12
            a["usage"][i] = 4.0
            a["tickets"][i] = 1
        elif i < 3240:
            a["tier"][i] = "pro"
            a["contract"][i] = 12
            a["usage"][i] = 4.0
            a["tickets"][i] = 1
            if i < 3140:  # critical_rule_3
                a["tier"][i] = "starter"
                a["contract"][i] = 1
                a["usage"][i] = 1.0
        elif i < 3340:
            a["tier"][i] = "starter"
            a["contract"][i] = 12
            a["usage"][i] = 2.5
            a["tickets"][i] = 3
            if i < 3255:  # critical_rule_4
                a["contract"][i] = 1
                a["usage"][i] = 1.0
                a["tickets"][i] = 4
        elif i < 3420:
            a["tier"][i] = "starter"
            a["contract"][i] = 12
            a["usage"][i] = 2.0
            a["tickets"][i] = 2
        elif i < 3820:  # positive sentiment
            a["tier"][i] = "enterprise"
            a["contract"][i] = 24
            a["usage"][i] = 6.0
            a["tickets"][i] = 0
        elif i < 3860:  # traps 001-004 (spec v1.2 truths)
            k = i - 3820
            trap = k // 10 + 1
            if trap == 1:  # usage drop, happy -> truth MEDIUM
                a["tier"][i] = "starter"
                a["contract"][i] = 12
                a["usage"][i] = 0.0
                a["tickets"][i] = 1
                a["frailty"][i] = 0.05
            elif trap == 2:  # billing complaint -> truth HIGH
                a["tier"][i] = "starter"
                a["contract"][i] = 1
                a["usage"][i] = 0.0
                a["tickets"][i] = 3
                a["frailty"][i] = 0.05
            elif trap == 3:  # conflicting threads -> truth LOW
                a["tier"][i] = "enterprise"
                a["contract"][i] = 24
                a["usage"][i] = 6.0
                a["tickets"][i] = 0
                a["frailty"][i] = 0.05
            else:  # critical cancellation -> truth CRITICAL (rule_1 via strong intent)
                a["tier"][i] = "starter"
                a["contract"][i] = 12
                a["usage"][i] = 2.0
                a["tickets"][i] = 2
                a["frailty"][i] = 0.05
        elif i < 3885 or i < 3915:  # cross-channel duplicates
            a["tier"][i] = "pro"
            a["contract"][i] = 12
            a["usage"][i] = 4.0
            a["tickets"][i] = 0
        else:  # background sufficient
            a["tickets"][i] = int(rng.integers(0, 4))


def _linear_predictor(a: dict[str, np.ndarray]) -> np.ndarray:
    lp = np.zeros(N_VALID, dtype=float)
    lp += BETA_STARTER * (a["tier"] == "starter")
    lp += BETA_PRO * (a["tier"] == "pro")
    lp += BETA_CONTRACT * a["contract"].astype(float)
    lp += BETA_USAGE * a["usage"].astype(float)
    lp += BETA_TICKETS * a["tickets"].astype(float)
    lp += a["frailty"]
    return lp


# --------------------------------------------------------------------------- #
# Survival
# --------------------------------------------------------------------------- #
def _count_events(a: dict[str, np.ndarray], lp: np.ndarray, lam_months: float) -> int:
    latent_days = _latent_days_vec(a, lp, lam_months)
    return int(np.sum(latent_days <= a["age"]))


def _bisect_lambda0(a: dict[str, np.ndarray], lp: np.ndarray) -> float:
    """Largest lambda0 (months) with event count >= TARGET_EVENTS_MIN.

    Event count decreases monotonically in lambda0, so we drive `lo` upward,
    keeping `lo` at a count >= min and `hi` at a count < min. The returned
    lambda0 is the largest calibration that still yields at least the minimum
    event count (typically lands just above TARGET_EVENTS_MIN).
    """
    lo, hi = 0.5, 400.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if _count_events(a, lp, mid) >= TARGET_EVENTS_MIN:
            lo = mid
        else:
            hi = mid
    return lo


def _latent_days_vec(
    a: dict[str, np.ndarray], lp: np.ndarray, lam_months: float
) -> np.ndarray:
    """Invert the survival model for every customer (days to latent event).

    All customers use the locked Weibull proportional-hazards model. The
    PH-violation cohort (indices ``PH_LO..PH_HI``) uses an explicit piecewise
    time-varying usage effect (§31, spec v1.2): the full usage coefficient
    applies up to the 180-day knot, and half of it beyond. The cumulative
    hazard is continuous at the knot, so survival is continuous too:

        H(t) = (t/scale)^k * exp(lp)                       for t <= knot
        H(t) = H(knot) + ((t - knot)/scale)^k * exp(lp_post)  for t > knot
        lp_post = lp - (1 - PH_USAGE_FACTOR) * BETA_USAGE * usage_frequency

    Inversion: solve H(T) = -ln(u) exactly per branch.
    """
    scale = lam_months * MONTH_DAYS
    u = np.clip(a["u"], 1e-12, 1.0)
    x = -np.log(u)
    base = scale * (x / np.exp(lp)) ** (1.0 / WEIBULL_K)
    ph_indices = np.arange(N_VALID)
    ph_mask = (ph_indices >= PH_LO) & (ph_indices < PH_HI)
    if not ph_mask.any():
        return base
    idx = ph_indices[ph_mask]
    lp_ph = lp[idx]
    usage = a["usage"][idx].astype(float)
    x_ph = x[idx]
    h_knot = (PH_KNOT_DAYS / scale) ** WEIBULL_K * np.exp(lp_ph)
    lp_post = lp_ph - (1.0 - PH_USAGE_FACTOR) * BETA_USAGE * usage
    t = np.empty_like(x_ph)
    below = x_ph <= h_knot
    t[below] = scale * (x_ph[below] / np.exp(lp_ph[below])) ** (1.0 / WEIBULL_K)
    rem = x_ph[~below] - h_knot[~below]
    t[~below] = PH_KNOT_DAYS + scale * (rem / np.exp(lp_post[~below])) ** (1.0 / WEIBULL_K)
    base[idx] = t
    return base


def _survive(
    a: dict[str, np.ndarray], lp: np.ndarray, lam_months: float
) -> tuple[np.ndarray, np.ndarray, list[date]]:
    latent_days = _latent_days_vec(a, lp, lam_months)
    age = a["age"].astype(float)
    event = (latent_days <= age).astype(int)
    tenure = np.where(
        event == 1, np.ceil(latent_days).astype(int), age.astype(int)
    )
    tenure = np.maximum(tenure, 1)
    starts = [REFERENCE_DATE - timedelta(days=int(a["age"][i])) for i in range(N_VALID)]
    ends = [
        (starts[i] + timedelta(days=int(tenure[i]))) if event[i] == 1 else REFERENCE_DATE
        for i in range(N_VALID)
    ]
    return event, tenure, ends


def _check_directions(a: dict[str, np.ndarray], event: np.ndarray) -> dict[str, dict[str, float]]:
    def rate(mask: np.ndarray) -> float:
        return float(event[mask].mean()) if mask.any() else 0.0

    by_tier = {t: rate(a["tier"] == t) for t in TIERS}
    by_contract = {c: rate(a["contract"] == c) for c in (1, 12, 24)}
    usage_low = rate(a["usage"] <= 1.5)
    usage_mid = rate((a["usage"] > 1.5) & (a["usage"] <= 4.5))
    usage_high = rate(a["usage"] > 4.5)
    by_usage = {"low": usage_low, "mid": usage_mid, "high": usage_high}
    tickets_corr = float(np.corrcoef(a["tickets"].astype(float), event.astype(float))[0, 1])
    tickets_0 = rate(a["tickets"] == 0)
    tickets_3 = rate(a["tickets"] >= 3)
    by_tickets = {"0": tickets_0, "3+": tickets_3}

    assert by_tier["starter"] > by_tier["pro"] > by_tier["enterprise"], by_tier
    assert by_contract[1] > by_contract[12] > by_contract[24], by_contract
    assert usage_low > usage_mid > usage_high, by_usage
    assert tickets_corr > 0.0, tickets_corr
    assert tickets_0 < tickets_3, by_tickets

    return {
        "plan_tier": by_tier,
        "contract_length_months": by_contract,
        "usage_frequency": by_usage,
        "support_tickets_90d": by_tickets,
        "support_tickets_90d_pearson_corr": tickets_corr,
    }


def _generate_survival() -> tuple[dict[str, np.ndarray], np.ndarray, float, int, np.ndarray,
                                 np.ndarray, list[date]]:
    """Return covariates, lp, calibrated lambda0, attempt, event, tenure, ends."""
    for attempt in range(100):
        rng = np.random.default_rng(MASTER_SEED + 1 + attempt)
        a = _draw_covariates(rng)
        _overwrite(a, rng)
        lp = _linear_predictor(a)
        if TARGET_EVENTS_MIN <= _count_events(a, lp, LAMBDA0_LOCKED_MONTHS) <= TARGET_EVENTS_MAX:
            event, tenure, ends = _survive(a, lp, LAMBDA0_LOCKED_MONTHS)
            return a, lp, LAMBDA0_LOCKED_MONTHS, attempt, event, tenure, ends

    rng = np.random.default_rng(MASTER_SEED + 1 + 100)
    a = _draw_covariates(rng)
    _overwrite(a, rng)
    lp = _linear_predictor(a)
    lam = _bisect_lambda0(a, lp)
    event, tenure, ends = _survive(a, lp, lam)
    return a, lp, lam, 100, event, tenure, ends


# --------------------------------------------------------------------------- #
# Threads
# --------------------------------------------------------------------------- #
def _msg_texts(kind: str, rng: np.random.Generator) -> tuple[list[str], list[str]]:
    lang = None
    base = kind
    if kind.startswith("unsupported_"):
        lang = kind.split("_", 1)[1]
        base = "unsupported"
    if base == "generic":
        return [rng.choice(GENERIC_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "cancel_strong":
        return ([rng.choice(CANCEL_STRONG_CUSTOMER), rng.choice(CANCEL_STRONG_CUSTOMER)],
                [rng.choice(GENERIC_AGENT), "I understand. Let me help you with the cancellation."])
    if base == "cancel_moderate":
        return ([rng.choice(CANCEL_MODERATE_CUSTOMER), rng.choice(CANCEL_MODERATE_CUSTOMER)],
                [rng.choice(GENERIC_AGENT),
                 "We would hate to see you go. Here is a retention offer."])
    if base == "cancel_weak":
        return [rng.choice(CANCEL_WEAK_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "cancel_neutral":
        return [rng.choice(CANCEL_NEUTRAL_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "positive":
        return [rng.choice(POSITIVE_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "renewal":
        return [rng.choice(RENEWAL_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "repeated":
        return ([rng.choice(REPEATED_CUSTOMER), rng.choice(REPEATED_CUSTOMER)],
                [rng.choice(GENERIC_AGENT)])
    if base == "urgent":
        return [rng.choice(URGENT_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "complaint":
        return [rng.choice(COMPLAINT_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "low_info":
        return [rng.choice(LOW_INFO_CUSTOMER)], []
    if base == "duplicate_issue":
        return [rng.choice(DUP_ISSUE_CUSTOMER)], [rng.choice(GENERIC_AGENT)]
    if base == "unsupported":
        pool = {"es": UNSUPPORTED_ES, "de": UNSUPPORTED_DE, "fr": UNSUPPORTED_FR}[lang]
        n = 2 if lang == "de" else 1
        cust = [rng.choice(pool["customer"]) for _ in range(n)]
        return cust, [rng.choice(pool["agent"])]
    raise ValueError(kind)


def _thread_spec(i: int, tickets: int) -> list[dict]:
    """Return the thread plan for index i: kind + placement per thread."""
    if i < 90:
        return []
    if i < 120:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 200:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 225:  # qual-only strong: cancel intent, no quantitative signal
        return [{"kind": "cancel_strong", "placement": "in_30d"}] * max(tickets, 1)
    if i < 300:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 350:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 650:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 770:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 970:
        return []
    if i < 2500:
        return []
    if i < 2800:
        return [
            {"kind": "low_info", "placement": "in_window"},
            {"kind": "low_info", "placement": "out_window"},
        ]
    if i < 2830:
        return [{"kind": "generic", "placement": "in_window"}] * tickets
    if i < 2880:
        return [
            {"kind": "positive", "placement": "positive"},
            {"kind": "positive", "placement": "positive"},
        ]
    if i < 2930:
        plan = [{"kind": "cancel_moderate", "placement": "in_30d"}] * tickets
        plan.append({"kind": "positive", "placement": "out_window"})
        return plan
    if i < 3020:
        return [{"kind": "cancel_strong", "placement": "in_30d"}] * tickets
    if i < 3080:
        return [{"kind": "cancel_moderate", "placement": "in_30d"}] * tickets
    if i < 3120:
        return [{"kind": "cancel_weak", "placement": "in_30d"}] * tickets
    if i < 3240:
        return [
            {"kind": "renewal", "placement": "in_window"},
            {"kind": "renewal", "placement": "out_window"},
        ]
    if i < 3340:
        plan = [{"kind": "repeated", "placement": "in_window"}] * tickets
        plan += [{"kind": "repeated", "placement": "out_window"}] * 2
        return plan
    if i < 3420:
        plan = [{"kind": "urgent", "placement": "in_window"}] * tickets
        plan.append({"kind": "urgent", "placement": "out_window"})
        return plan
    if i < 3820:
        return [
            {"kind": "positive", "placement": "out_window"},
            {"kind": "positive", "placement": "out_window"},
        ]
    if i < 3860:
        trap = (i - 3820) // 10 + 1
        if trap == 1:
            return [{"kind": "positive", "placement": "out_window"}]
        if trap == 2:
            return [{"kind": "complaint", "placement": "in_window"}]
        if trap == 3:
            return [
                {"kind": "complaint", "placement": "in_window"},
                {"kind": "positive", "placement": "out_window"},
            ]
        return [{"kind": "cancel_strong", "placement": "in_30d"}] * max(tickets, 1)
    if i < 3885:
        return [
            {"kind": "duplicate_issue", "placement": "out_window"},
            {"kind": "duplicate_issue", "placement": "out_window", "is_dup": True},
        ]
    if i < 3915:
        k = i - 3885
        if k < 10:
            return [{"kind": "unsupported_es", "placement": "out_window"}] * 2
        if k < 15:
            return [{"kind": "unsupported_de", "placement": "out_window"}] * 2
        if k < 20:
            return [{"kind": "unsupported_de", "placement": "out_window"}]
        return [{"kind": "unsupported_fr", "placement": "out_window"}]
    plan = [{"kind": "generic", "placement": "in_window"}] * tickets
    plan.append({"kind": "generic", "placement": "out_window"})
    return plan


def _sample_date(rng: np.random.Generator, lo: date, hi: date) -> date:
    if lo > hi:
        return hi
    span = (hi - lo).days
    offset = int(rng.integers(0, span + 1))
    return lo + timedelta(days=offset)


def _thread_dt(d: date, rng: np.random.Generator) -> datetime:
    hour = int(rng.integers(8, 19))
    minute = int(rng.integers(0, 60))
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=UTC)


def _interleave(customer_texts: list[str], agent_texts: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(customer_texts) or i < len(agent_texts):
        if i < len(customer_texts):
            out.append(("customer", customer_texts[i]))
        if i < len(agent_texts):
            out.append(("agent", agent_texts[i]))
        i += 1
    return out


def _clamp_to_window(ts: datetime, end: date) -> datetime:
    if ts.date() > end:
        return datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)
    return ts


def _build_threads(
    a: dict[str, np.ndarray], ends: list[date]
) -> tuple[list[dict], dict[str, list[str]], list[dict], int, dict[str, int]]:
    """Return (threads, thread_map, duplicates, n_unsupported, unsupported_by_customer)."""
    rng = np.random.default_rng(MASTER_SEED + 2)
    threads: list[dict] = []
    thread_map: dict[str, list[str]] = {}
    duplicates: list[dict] = []
    unsupported_by_customer: dict[str, int] = {}
    n_unsupported = 0
    thread_seq = 0
    msg_seq = 0

    SUBJECTS = ["Help needed", "Billing question", "Support request", "Account issue",
                "Quick question"]
    TAGS = ["billing", "technical", "account", "feature", "general"]

    for i in range(N_VALID):
        cid = f"CUST-{i + 1:04d}"
        start = REFERENCE_DATE - timedelta(days=int(a["age"][i]))
        end = ends[i]
        spec = _thread_spec(i, int(a["tickets"][i]))
        cid_thread_ids: list[str] = []
        customer_unsupported = 0
        last_email_id: str | None = None

        for item in spec:
            kind = item["kind"]
            placement = item["placement"]
            thread_seq += 1
            tid = f"THR-{thread_seq:05d}"

            if item.get("is_dup"):
                channel = "chatt"
                dup_of = last_email_id or (cid_thread_ids[-1] if cid_thread_ids else None)
            else:
                channel = str(rng.choice(CHANNELS, p=CHANNEL_P))
                if rng.random() < 0.05:
                    channel = CHANNEL_TYPO.get(channel, channel)
                dup_of = None

            if placement == "in_30d":
                created = _sample_date(rng, max(end - timedelta(days=30), start), end)
            elif placement == "in_window":
                created = _sample_date(rng, max(end - timedelta(days=90), start), end)
            elif placement == "positive":
                lo = max(start, end - timedelta(days=180))
                hi = max(lo, end - timedelta(days=120))
                created = _sample_date(rng, lo, hi)
            else:  # out_window: strictly before the 90-day ticket window
                created = _sample_date(rng, start, max(start, end - timedelta(days=91)))

            created_dt = _thread_dt(created, rng)
            customer_texts, agent_texts = _msg_texts(kind, rng)
            messages: list[dict] = []
            ts = created_dt
            for role, text in _interleave(customer_texts, agent_texts):
                msg_seq += 1
                messages.append({
                    "message_id": f"MSG-{msg_seq:05d}",
                    "role": role,
                    "timestamp": ts.isoformat(),
                    "text": text,
                })
                if role == "customer":
                    ts = ts + timedelta(hours=int(rng.integers(2, 9)),
                                        minutes=int(rng.integers(0, 60)))
                else:
                    ts = ts + timedelta(hours=int(rng.integers(1, 5)))
                ts = _clamp_to_window(ts, end)

            thread: dict = {
                "thread_id": tid,
                "customer_id": cid,
                "channel": channel,
                "created_at": created_dt.isoformat(),
                "messages": messages,
            }
            if dup_of is not None:
                thread["duplicate_of"] = dup_of
            if kind.startswith("unsupported_"):
                thread["language"] = kind.split("_", 1)[1]
                customer_unsupported += 1
                n_unsupported += 1

            if rng.random() < 0.10 or kind == "low_info":
                thread["subject"] = None
            else:
                thread["subject"] = str(rng.choice(SUBJECTS))
            if rng.random() < 0.15:
                thread["status"] = None
            else:
                thread["status"] = str(rng.choice(["open", "resolved", "pending"]))
            tag_roll = rng.random()
            if tag_roll < 0.15:
                thread["tags"] = None
            elif tag_roll < 0.25:
                thread["tags"] = []
            elif tag_roll < 0.30:
                thread["tags"] = [""]
            else:
                ntags = int(rng.integers(1, 3))
                thread["tags"] = [str(t) for t in rng.choice(TAGS, size=ntags, replace=False)]

            threads.append(thread)
            cid_thread_ids.append(tid)
            if not item.get("is_dup") and "chat" not in channel and "chatt" not in channel:
                last_email_id = tid
            if item.get("is_dup"):
                duplicates.append({
                    "customer_id": cid,
                    "collapsed_thread_id": tid,
                    "surviving_thread_id": dup_of,
                    "channel": "chat",
                    "note": "chat thread duplicates the email thread; collapse to one ticket",
                })

        thread_map[cid] = cid_thread_ids
        unsupported_by_customer[cid] = customer_unsupported

    return threads, thread_map, duplicates, n_unsupported, unsupported_by_customer


# --------------------------------------------------------------------------- #
# CSV rendering
# --------------------------------------------------------------------------- #
def _fmt_date(d: date, fmt: int) -> str:
    if fmt == 0:
        return d.strftime("%Y-%m-%d")
    if fmt == 1:
        return d.strftime("%m/%d/%Y")
    if fmt == 2:
        return d.strftime("%d-%m-%Y")
    return d.strftime("%Y.%m.%d")


# --------------------------------------------------------------------------- #
# Invalid rows
# --------------------------------------------------------------------------- #
def _invalid_rows() -> list[dict]:
    rows: list[dict] = []

    # FUTURE_START_DATE (60): signup date after the reference date.
    for k in range(60):
        rows.append({
            "error_code": "FUTURE_START_DATE",
            "customer_id": f"CUST-INV-FS-{k + 1:03d}",
            "signup": REFERENCE_DATE + timedelta(days=1 + k),
            "cancel": None,
            "status": "Active",
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": 3.0,
            "tickets": k % 3,
            "outcome": "rejected:WINDOW_ORDER",
        })

    # FUTURE_END_DATE (50): cancellation date after the reference date.
    for k in range(50):
        rows.append({
            "error_code": "FUTURE_END_DATE",
            "customer_id": f"CUST-INV-FE-{k + 1:03d}",
            "signup": date(2024, 1, 1) + timedelta(days=k),
            "cancel": REFERENCE_DATE + timedelta(days=1 + k),
            "status": "Churned",
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": 2.5,
            "tickets": k % 3,
            "outcome": "rejected:FUTURE_LEAKAGE",
        })

    # BAD_EVENT_VALUE (40): status strings that cannot map to {0, 1}.
    for k in range(40):
        rows.append({
            "error_code": "BAD_EVENT_VALUE",
            "customer_id": f"CUST-INV-BEV-{k + 1:03d}",
            "signup": date(2025, 1, 1) + timedelta(days=k),
            "cancel": None,
            "status": BAD_STATUSES[k % len(BAD_STATUSES)],
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": 3.5,
            "tickets": k % 3,
            "outcome": "rejected:EVENT_OBSERVED",
        })

    # DUPLICATE_ID (80): reuse valid customer ids -> UNIQUE_ID gate.
    for k in range(80):
        rows.append({
            "error_code": "DUPLICATE_ID",
            "customer_id": f"CUST-{k + 1:04d}",
            "signup": date(2024, 6, 1) + timedelta(days=k),
            "cancel": None,
            "status": "Active",
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": 4.0,
            "tickets": 0,
            "outcome": "rejected:UNIQUE_ID",
        })

    # MISSING_CORE (100): 40 usage (MCAR), 30 tickets (MAR enterprise),
    # 30 contract (MNAR starter-monthly).
    for k in range(40):
        rows.append({
            "error_code": "MISSING_CORE",
            "customer_id": f"CUST-INV-MCU-{k + 1:03d}",
            "signup": date(2024, 3, 1) + timedelta(days=k),
            "cancel": None,
            "status": "Active",
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": None,
            "tickets": k % 3,
            "outcome": "accepted:missingness_passthrough",
            "missing_key": "usage_frequency",
            "mechanism": "MCAR",
        })
    for k in range(30):
        rows.append({
            "error_code": "MISSING_CORE",
            "customer_id": f"CUST-INV-MCT-{k + 1:03d}",
            "signup": date(2024, 3, 1) + timedelta(days=k),
            "cancel": None,
            "status": "Active",
            "plan": "enterprise",
            "contract": [1, 12, 24][k % 3],
            "usage": 3.0,
            "tickets": None,
            "outcome": "accepted:missingness_passthrough",
            "missing_key": "support_tickets_90d",
            "mechanism": "MAR_enterprise",
        })
    for k in range(30):
        rows.append({
            "error_code": "MISSING_CORE",
            "customer_id": f"CUST-INV-MCC-{k + 1:03d}",
            "signup": date(2024, 3, 1) + timedelta(days=k),
            "cancel": None,
            "status": "Active",
            "plan": "starter",
            "contract": None,
            "usage": 2.0,
            "tickets": k % 3,
            "outcome": "accepted:missingness_passthrough",
            "missing_key": "contract_length_months",
            "mechanism": "MNAR_starter_monthly",
        })

    # IMPOSSIBLE_TENURE (60): cancellation at least 2 days before signup.
    for k in range(60):
        signup = date(2024, 4, 1) + timedelta(days=k)
        rows.append({
            "error_code": "IMPOSSIBLE_TENURE",
            "customer_id": f"CUST-INV-IT-{k + 1:03d}",
            "signup": signup,
            "cancel": signup - timedelta(days=2 + k % 30),
            "status": "Churned",
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": 2.0,
            "tickets": k % 3,
            "outcome": "rejected:WINDOW_ORDER",
        })

    # NEGATIVE_TENURE (30): cancellation exactly one day before signup.
    for k in range(30):
        signup = date(2024, 5, 1) + timedelta(days=k)
        rows.append({
            "error_code": "NEGATIVE_TENURE",
            "customer_id": f"CUST-INV-NT-{k + 1:03d}",
            "signup": signup,
            "cancel": signup - timedelta(days=1),
            "status": "Churned",
            "plan": TIERS[k % 3],
            "contract": [1, 12, 24][k % 3],
            "usage": 2.5,
            "tickets": k % 3,
            "outcome": "rejected:WINDOW_ORDER",
        })

    # INVALID_PLAN (30): no plan-vocabulary gate in Node 1, so a secondary
    # blank usage cell forces CORE_MISSING — which the v1.2 missingness
    # passthrough now accepts (blank usage is the only error).
    for k in range(30):
        rows.append({
            "error_code": "INVALID_PLAN",
            "customer_id": f"CUST-INV-IP-{k + 1:03d}",
            "signup": date(2024, 7, 1) + timedelta(days=k),
            "cancel": None,
            "status": "Active",
            "plan": INVALID_PLANS[k % len(INVALID_PLANS)],
            "contract": [1, 12, 24][k % 3],
            "usage": None,
            "tickets": k % 3,
            "outcome": "accepted:missingness_passthrough",
            "note": "Node 1 has no plan-vocabulary gate; blank usage forces "
                    "CORE_MISSING, which the v1.2 missingness passthrough accepts",
        })

    return rows


# --------------------------------------------------------------------------- #
# Oracles
# --------------------------------------------------------------------------- #
def _key_themes(i: int, cohorts: list[str]) -> list[str]:
    mapping = {
        "strong_cancellation_intent": "cancellation_intent",
        "moderate_cancellation_intent": "cancellation_intent",
        "weak_cancellation_intent": "cancellation_intent",
        "conflict_b": "cancellation_intent",
        "renewal_concern": "renewal_or_contract_concern",
        "repeated_issues": "product_bug_or_outage",
        "high_urgency": "poor_support_experience",
        "positive_sentiment": "positive_feedback",
        "conflict_a": "positive_feedback",
    }
    themes: list[str] = []
    for cohort in cohorts:
        theme = mapping.get(cohort)
        if theme and theme not in themes:
            themes.append(theme)
    return themes or ["other"]


def _node4(i: int, quant: float | None, cohorts: list[str], node2_status: str,
           support: str) -> tuple[str, str, list[str], list[str]]:
    rules: list[str] = []
    if "critical_rule_2" in cohorts:
        rules = ["rule_2"]
    elif "critical_rule_3" in cohorts:
        rules = ["rule_3"]
    elif "critical_rule_4" in cohorts:
        rules = ["rule_4"]
    elif "strong_cancellation_intent" in cohorts or "moderate_cancellation_intent" in cohorts:
        rules = ["rule_1"]
    if rules:
        reason = {
            "rule_1": "critical_cancellation_intent",
            "rule_2": "critical_cancellation_plus_significant_flag",
            "rule_3": "critical_high_quant_plus_contract_concern",
            "rule_4": "critical_repeated_high_severity_plus_high_quant",
        }[rules[0]]
        reason_types = [reason]
        if quant is None and node2_status not in ("READY", "WARNING"):
            reason_types.append("missing_quantitative_data")
        return "critical", reason, rules, reason_types
    if node2_status not in ("READY", "WARNING"):
        reason = "missing_quantitative_data" if support == "no_data" else "limited_support_data"
        return "insufficient_data", reason, [], [reason]
    if quant is None:
        return "low", "missing_quantitative_data", [], ["missing_quantitative_data"]
    if "trap_001" in cohorts:
        # usage-drop trap: vocally happy but usage collapsed -> deterministic MEDIUM.
        # Node 5 must not inflate to high/critical off the usage trend alone.
        return "medium", "usage_trend_contrast", [], ["usage_trend_contrast"]
    if quant >= 0.75:
        reason = "quantitative_qualitative_conflict" if "conflict_a" in cohorts \
            else "high_quantitative_risk"
        return "high", reason, [], [reason]
    if "repeated_issues" in cohorts:
        return "medium", "repeated_support_issue", [], ["repeated_support_issue"]
    if "high_urgency" in cohorts:
        return "medium", "high_support_urgency", [], ["high_support_urgency"]
    if "weak_cancellation_intent" in cohorts:
        reason = "strong_support_signal"
        return ("medium" if quant >= 0.5 else "low"), reason, [], [reason]
    if "conflict_b" in cohorts:
        return "low", "quantitative_qualitative_conflict", [], ["quantitative_qualitative_conflict"]
    if quant >= 0.5:
        return "medium", "moderate_quantitative_risk", [], ["moderate_quantitative_risk"]
    return "low", "quantitative_qualitative_agreement", [], ["quantitative_qualitative_agreement"]


_TRAP_NARRATIVES = {
    1: {
        "trap_kind": "usage_drop",
        "truth_risk_level": "medium",
        "forbidden_claims": ["high", "critical", "risk_due_to_usage_drop"],
        "narrative": "Customer is vocally happy (positive threads) yet their usage has "
                     "dropped sharply; deterministic risk is medium — Node 5 must not "
                     "inflate to high/critical off the usage trend alone.",
    },
    2: {
        "trap_kind": "billing_complaint",
        "truth_risk_level": "high",
        "forbidden_claims": ["critical", "cancellation_intent"],
        "narrative": "A billing complaint thread sits on top of genuinely high "
                     "quantitative risk; the truth is high, not critical — Node 5 must "
                     "not read a complaint as cancellation intent or escalate to critical.",
    },
    3: {
        "trap_kind": "conflicting_threads",
        "truth_risk_level": "low",
        "forbidden_claims": ["high", "critical"],
        "narrative": "Threads contradict each other (one complaint, one praise) but the "
                     "quantitative risk is genuinely low; Node 5 must keep the customer "
                     "at low, not inflate on the complaint thread alone.",
    },
    4: {
        "trap_kind": "critical_cancellation",
        "truth_risk_level": "critical",
        "forbidden_claims": ["low", "medium", "stays_active"],
        "narrative": "Customer explicitly demands cancellation (strong intent, "
                     "quantitative data present); the truth is critical via rule_1 — "
                     "Node 5 must not downplay it as low/medium or claim the customer "
                     "will stay active.",
    },
}


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def _missing_plan(a: dict[str, np.ndarray]) -> dict[int, dict[str, str]]:
    """§11 deterministic missingness on VALID rows (spec v1.2).

    The CSV blanks the affected cells; the ground truth keeps the true values
    (so Node 1's versioned low-missingness passthrough accepts the records with
    null cores and Node 2's complete-case rule excludes them). Mechanisms:
    - usage_frequency            MCAR (~3% of all valid customers)
    - support_tickets_90d        MAR on enterprise (~10%)
    - contract_length_months     MNAR on starter-monthly, contract == 1 (~20%)
    A dedicated RNG stream keeps the survival attempt loop untouched.
    """
    rng = np.random.default_rng(MISSINGNESS_SEED)
    plan: dict[int, dict[str, str]] = {}
    usage_missing = rng.random(N_VALID) < MISSING_USAGE_RATE
    tickets_missing = (
        (a["tier"] == "enterprise") & (rng.random(N_VALID) < MISSING_TICKETS_RATE)
    )
    contract_missing = (
        (a["tier"] == "starter")
        & (a["contract"] == 1)
        & (rng.random(N_VALID) < MISSING_CONTRACT_RATE)
    )
    for i in range(N_VALID):
        keys: dict[str, str] = {}
        if usage_missing[i]:
            keys["usage_frequency"] = "MCAR"
        if tickets_missing[i]:
            keys["support_tickets_90d"] = "MAR_enterprise"
        if contract_missing[i]:
            keys["contract_length_months"] = "MNAR_starter_monthly"
        if keys:
            plan[i] = keys
    return plan


_CANCEL_KW = (
    "cancel", "cancelling", "terminate", "switch", "leave", "refund",
    "not renewing", "stop subscription",
)
_QUANT_COHORTS = {"quant_only", "conflict_a", "conflict_b"}
_FLAG_MAP = {
    "high_urgency": "high_urgency",
    "repeated_issues": "repeated_issue",
    "strong_cancellation_intent": "strong_cancellation_intent",
    "moderate_cancellation_intent": "moderate_cancellation_intent",
    "weak_cancellation_intent": "weak_cancellation_intent",
    "conflict_b": "cancellation_intent_conflict",
    "positive_sentiment": "positive_feedback",
    "renewal_concern": "renewal_or_contract_concern",
    "low_information": "low_information",
    "cross_channel_duplicate": "cross_channel_duplicate",
    "unsupported_language": "unsupported_language",
}


def _signal_strength(cohorts: list[str]) -> str:
    if any(c in cohorts for c in ("strong_cancellation_intent", "high_urgency", "repeated_issues")):
        return "strong"
    if any(c in cohorts for c in ("moderate_cancellation_intent", "weak_cancellation_intent",
                                  "renewal_concern", "conflict_b")):
        return "moderate"
    if any(c in cohorts for c in ("positive_sentiment", "conflict_a")):
        return "weak"
    return "none"


def _support_context(
    cid: str,
    threads_by_cid: dict[str, list[dict]],
    collapsed_ids: set[str],
) -> tuple[int, str | None, bool]:
    """§29 fields derived from the actual threads: (n_messages, latest_ts, churn_lang)."""
    msgs = 0
    latest: datetime | None = None
    churn = False
    for thread in threads_by_cid.get(cid, []):
        if thread["thread_id"] in collapsed_ids:
            continue
        for message in thread.get("messages", []):
            ts = datetime.fromisoformat(message["timestamp"].replace("Z", "+00:00"))
            msgs += 1
            if latest is None or ts > latest:
                latest = ts
            if message.get("role") == "customer" and any(
                keyword in message.get("text", "").lower() for keyword in _CANCEL_KW
            ):
                churn = True
    return msgs, (latest.isoformat() if latest is not None else None), churn


def _valid_row(
    i: int,
    a: dict[str, np.ndarray],
    starts: list[date],
    ends: list[date],
    event: np.ndarray,
    tenure: np.ndarray,
    missing: dict[str, str] | None = None,
    failed_corruption: bool = False,
) -> list[str]:
    start = starts[i]
    end = ends[i]
    is_event = int(event[i]) == 1
    fmt = i % 4
    rep = int(a["status_rep"][i]) % 5
    status = CHURNED_REPS[rep] if is_event else ACTIVE_REPS[rep]
    last_login = int(a["last_login_churned"][i] if is_event else a["last_login_active"][i])
    legacy = "Y" if bool(a["legacy_flag"][i]) else "N"
    missing = missing or {}
    contract = "" if "contract_length_months" in missing else str(int(a["contract"][i]))
    usage = "" if "usage_frequency" in missing else f"{float(a['usage'][i]):.1f}"
    tickets = "" if "support_tickets_90d" in missing else str(int(a["tickets"][i]))
    internal = (
        "injected_corruption:required_feature_NaN_simulated"
        if failed_corruption
        else (INTERNAL_NOTES[int(a["note_idx"][i])] if bool(a["note_flag"][i]) else "")
    )
    return [
        f"CUST-{i + 1:04d}",
        _fmt_date(start, fmt),
        _fmt_date(end, fmt) if is_event else "",
        status,
        str(a["tier"][i]),
        contract,
        usage,
        tickets,
        str(last_login),
        REGIONS[int(a["region_idx"][i])],
        SALES_REPS[int(a["sales_rep_idx"][i])],
        internal,
        legacy,
        DECOY_A[int(a["decoy_a_idx"][i])],
        DECOY_B[int(a["decoy_b_idx"][i])],
    ]


def _invalid_row(k: int, spec: dict) -> list[str]:
    fmt = k % 4
    contract = spec["contract"]
    usage = spec["usage"]
    tickets = spec["tickets"]
    return [
        spec["customer_id"],
        _fmt_date(spec["signup"], fmt),
        _fmt_date(spec["cancel"], fmt) if spec["cancel"] else "",
        spec["status"],
        spec["plan"],
        "" if contract is None else str(contract),
        "" if usage is None else f"{usage:.1f}",
        "" if tickets is None else str(tickets),
        str(30 + (k % 60)),
        REGIONS[k % len(REGIONS)],
        SALES_REPS[k % len(SALES_REPS)],
        "",
        "N",
        DECOY_A[k % len(DECOY_A)],
        DECOY_B[k % len(DECOY_B)],
    ]


def _render_csv(rows: list[list[str]]) -> str:
    """CSV rendered to a single str with LF line endings (deterministic bytes)."""
    out = [",".join(COLUMNS)]
    out.extend(",".join(row) for row in rows)
    return "\n".join(out) + "\n"


def _write_on_pass(path: Path, payload: str) -> None:
    """Atomic staging write: render to a temp file, then os.replace on success."""
    staging = Path(os.environ.get("TEMP", ".")) / f".staging_{path.name}"
    staging.write_text(payload, encoding="utf-8", newline="\n")
    try:
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


def main(argv: list[str] | None = None) -> int:
    a, lp, lam, attempt, event, tenure, ends = _generate_survival()
    n_events = int(event.sum())
    assert TARGET_EVENTS_MIN <= n_events <= TARGET_EVENTS_MAX, n_events
    direction_rates = _check_directions(a, event)
    starts = [REFERENCE_DATE - timedelta(days=int(a["age"][i])) for i in range(N_VALID)]

    latent_days = _latent_days_vec(a, lp, lam)
    latent_months = latent_days / MONTH_DAYS

    threads, thread_map, duplicates, n_unsupported, unsupported_by_customer = (
        _build_threads(a, ends)
    )

    threads_by_cid: dict[str, list[dict]] = {}
    for thread in threads:
        threads_by_cid.setdefault(thread["customer_id"], []).append(thread)

    planned_tickets = a["tickets"].copy()
    collapsed_ids = {d["collapsed_thread_id"] for d in duplicates}
    actual_tickets: list[int] = []
    for i in range(N_VALID):
        cid = f"CUST-{i + 1:04d}"
        end = ends[i]
        count = 0
        for thread in threads:
            if thread["customer_id"] != cid or thread["thread_id"] in collapsed_ids:
                continue
            created = datetime.fromisoformat(
                thread["created_at"].replace("Z", "+00:00")
            ).date()
            if end - timedelta(days=90) <= created <= end:
                count += 1
        actual_tickets.append(count)
    a["tickets"] = np.array(actual_tickets, dtype=int)
    n_reconciled = int(np.sum(planned_tickets != a["tickets"]))

    missing_plan = _missing_plan(a)

    eligible_mask = np.array(
        [_bands(i)[0][0] in ("READY", "WARNING") for i in range(N_VALID)]
    )
    exp_lp = np.exp(lp)
    exp_eligible = exp_lp[eligible_mask]
    quant_score: dict[int, float] = {}
    for i in range(N_VALID):
        if eligible_mask[i]:
            quant_score[i] = float((exp_eligible <= exp_lp[i]).mean())

    conflict_a_quants = [quant_score[i] for i in range(2830, 2880)]
    conflict_b_quants = [quant_score[i] for i in range(2880, 2930)]
    quant_only_quants = [quant_score[i] for i in range(770, 970)]
    assert min(conflict_a_quants) >= 0.75, min(conflict_a_quants)
    assert max(conflict_b_quants) <= 0.25, max(conflict_b_quants)
    assert min(quant_only_quants) >= 0.70, min(quant_only_quants)

    # ---- valid rows + ground truth --------------------------------------- #
    rows: list[list[str]] = []
    customer_truth: dict[str, dict] = {}
    support_truth: dict[str, dict] = {}
    node2_oracle: dict[str, dict] = {}
    node3_oracle: dict[str, dict] = {}
    node4_oracle: dict[str, dict] = {}
    trap_oracle: dict[str, dict] = {}
    horizons = {
        "READY": [30, 90, 180],
        "WARNING": [30, 90],
        "FALLBACK": [],
        "INSUFFICIENT_DATA": [],
        "FAILED": [],
    }

    for i in range(N_VALID):
        cid = f"CUST-{i + 1:04d}"
        start = starts[i]
        end = ends[i]
        is_event = int(event[i]) == 1
        node2_status, node2_reason = _bands(i)[0]
        support = _bands(i)[1]
        cohorts = _cohorts(i)
        tickets = int(a["tickets"][i])
        n_threads = len(thread_map[cid])
        n_failed = unsupported_by_customer[cid]
        quant = quant_score.get(i)
        risk, reason, rules, reason_types = _node4(i, quant, cohorts, node2_status, support)
        missing = missing_plan.get(i, {})
        failed_corruption = node2_status == "FAILED"

        rows.append(
            _valid_row(
                i, a, starts, ends, event, tenure,
                missing=missing, failed_corruption=failed_corruption,
            )
        )

        internal_notes = (
            "injected_corruption:required_feature_NaN_simulated"
            if failed_corruption
            else (INTERNAL_NOTES[int(a["note_idx"][i])] if bool(a["note_flag"][i]) else "")
        )
        dgp_entry: dict = {
            "latent_event_time_months": round(float(latent_months[i]), 6),
            "latent_event_time_days": round(float(latent_days[i]), 3),
            "linear_predictor": round(float(lp[i]), 6),
            "frailty": round(float(a["frailty"][i]), 6),
            "baseline_lambda0_months": lam,
            "weibull_shape": WEIBULL_K,
            "tickets_planned": int(planned_tickets[i]),
        }
        if failed_corruption:
            dgp_entry["corruption"] = {
                "kind": "required_feature_NaN_simulated",
                "injected_at": "node2_adapter_output",
                "representation": "extra_features.internal_notes marker (Node 2 status "
                                  "is batch-level, so per-customer FAILED is oracle "
                                  "intent recorded for diagnostic assertions)",
            }
        if PH_LO <= i < PH_HI:
            dgp_entry["time_varying"] = True
            dgp_entry["time_varying_effect"] = "usage_frequency piecewise (see dgp_truth)"
        customer_truth[cid] = {
            "customer_index": i,
            "canonical": {
                "customer_id": cid,
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
                "event_observed": int(event[i]),
                "tenure": int((end - start).days),
                "reference_date": REFERENCE_DATE.isoformat(),
            },
            "core_features": {
                "plan_tier": str(a["tier"][i]),
                "contract_length_months": int(a["contract"][i]),
                "usage_frequency": float(a["usage"][i]),
                "support_tickets_90d": tickets,
            },
            "extra_features": {
                "last_login_days_ago": int(
                    a["last_login_churned"][i] if is_event else a["last_login_active"][i]
                ),
                "region": REGIONS[int(a["region_idx"][i])],
                "sales_rep": SALES_REPS[int(a["sales_rep_idx"][i])],
                "internal_notes": internal_notes,
                "legacy_flag": "Y" if bool(a["legacy_flag"][i]) else "N",
                "decoy_a": DECOY_A[int(a["decoy_a_idx"][i])],
                "decoy_b": DECOY_B[int(a["decoy_b_idx"][i])],
            },
            "support_cohorts": cohorts,
            "quant_cohorts": [c for c in cohorts if c in _QUANT_COHORTS],
            "missingness": dict(missing),
            "node4_oracle": {
                "risk_level": risk,
                "reason_type": reason,
                "reason_types": reason_types,
                "critical_rules": rules,
                "expected_quant_score": quant,
            },
            "pipeline_expectations": {
                "node2": {
                    "model_status": node2_status,
                    "model_status_reason": node2_reason,
                    "horizons": horizons[node2_status],
                },
                "node3": {
                    "support_data_status": support,
                    "n_threads": n_threads,
                    "n_failed_threads": n_failed,
                    "key_themes": _key_themes(i, cohorts),
                },
                "node4": {
                    "expected_risk_level": risk,
                    "reason_type": reason,
                    "critical_rules": rules,
                    "expected_quant_score": quant,
                },
            },
            "dgp": dgp_entry,
        }

        node2_oracle[cid] = {
            "model_status": node2_status,
            "model_status_reason": node2_reason,
            "horizons": horizons[node2_status],
        }
        node3_oracle[cid] = {
            "support_data_status": support,
            "n_threads": n_threads,
            "n_failed_threads": n_failed,
            "key_themes": _key_themes(i, cohorts),
        }
        node4_oracle[cid] = {
            "risk_level": risk,
            "reason_type": reason,
            "reason_types": reason_types,
            "critical_rules": rules,
            "expected_quant_score": quant,
        }
        n_msgs, latest_ts, churn_detected = _support_context(cid, threads_by_cid, collapsed_ids)
        flags = [flag for c in cohorts if (flag := _FLAG_MAP.get(c)) is not None]
        strength = _signal_strength(cohorts)
        confidence = "none" if n_threads == 0 else (
            "high" if n_threads >= 3 else ("medium" if n_threads >= 2 else "low")
        )
        support_truth[cid] = {
            "expected_support_data_status": support,
            "n_threads": n_threads,
            "n_processed_threads": n_threads - n_failed,
            "n_failed_threads": n_failed,
            "tickets_90d": tickets,
            "thread_ids": thread_map[cid],
            "key_themes": _key_themes(i, cohorts),
            "expected_flags": flags,
            "expected_churn_language_detected": churn_detected,
            "expected_signal_strength": strength,
            "expected_overall_signal_confidence": confidence,
            "expected_escalation_signal": (
                "high_urgency" in cohorts or "repeated_issues" in cohorts
            ),
            "n_messages_in_window": n_msgs,
            "latest_interaction_at": latest_ts,
        }

        for trap_id in (1, 2, 3, 4):
            if f"trap_00{trap_id}" in cohorts:
                meta = _TRAP_NARRATIVES[trap_id]
                trap_oracle[cid] = {
                    "trap": f"00{trap_id}",
                    "trap_kind": meta["trap_kind"],
                    "truth_risk_level": meta["truth_risk_level"],
                    "forbidden_claims": list(meta["forbidden_claims"]),
                    "narrative": meta["narrative"],
                    "expected_risk_level": risk,
                }

    # ---- invalid rows ----------------------------------------------------- #
    invalid = _invalid_rows()
    for k, spec in enumerate(invalid):
        rows.append(_invalid_row(k, spec))
    invalid_rows_truth = [
        {
            "raw_row_index": N_VALID + k,
            "customer_id": spec["customer_id"],
            "error_code": spec["error_code"],
            "expected_node1_outcome": spec["outcome"],
            "primary_invalid_condition": {
                key: (spec[key].isoformat() if isinstance(spec[key], date) else spec[key])
                for key in ("signup", "cancel", "status", "plan", "contract", "usage", "tickets")
                if key in spec
            },
        }
        for k, spec in enumerate(invalid)
    ]

    # ---- write artifacts -------------------------------------------------- #
    missingness_counts: dict[str, int] = {}
    for per_key in missing_plan.values():
        for key in per_key:
            missingness_counts[key] = missingness_counts.get(key, 0) + 1
    n_customers_with_missing = len(missing_plan)

    invalid_blank_counts = {"usage_frequency": 0, "support_tickets_90d": 0,
                            "contract_length_months": 0}
    n_rescued_invalid = 0
    for spec in invalid:
        if spec["error_code"] in ("MISSING_CORE", "INVALID_PLAN"):
            n_rescued_invalid += 1
            if spec.get("usage") is None:
                invalid_blank_counts["usage_frequency"] += 1
            if spec.get("tickets") is None:
                invalid_blank_counts["support_tickets_90d"] += 1
            if spec.get("contract") is None:
                invalid_blank_counts["contract_length_months"] += 1
    passthrough_counts = {
        key: missingness_counts.get(key, 0) + invalid_blank_counts[key]
        for key in invalid_blank_counts
    }
    n_accepted = N_VALID + n_rescued_invalid
    n_rejected = N_INVALID - n_rescued_invalid
    quant_only_band = {"min": round(min(quant_only_quants), 4),
                       "max": round(max(quant_only_quants), 4),
                       "n_ge_0_70": sum(1 for q in quant_only_quants if q >= 0.70)}
    ph_cohort_size = PH_HI - PH_LO

    csv_text = _render_csv(rows)
    threads_text = json.dumps(threads, indent=2) + "\n"

    validation_oracle = {
        "node3_processed_threads": len(threads) - n_unsupported,
        "node3_failed_threads": n_unsupported,
    }
    channel_counts: dict[str, int] = {}
    for thread in threads:
        channel_counts[str(thread["channel"])] = channel_counts.get(str(thread["channel"]), 0) + 1

    truth = {
        "dataset": "dataset7_churn_diagnostic",
        "reference_date": REFERENCE_DATE.isoformat(),
        "metadata": {
            "dataset": "dataset7_churn_diagnostic",
            "specification_version": SPECIFICATION_VERSION,
            "generator_version": GENERATOR_VERSION,
            "generator_script": "scripts/generate_dataset7.py",
            "master_seed": MASTER_SEED,
            "reference_date": REFERENCE_DATE.isoformat(),
            "n_raw_rows": len(rows),
            "n_valid_customers": N_VALID,
            "n_invalid_rows": N_INVALID,
            "n_churn_events": n_events,
            "event_generation_attempt": attempt,
            "calibrated_lambda0_months": lam,
            "artifact_purpose": "end-to-end diagnostic corpus for Nodes 1-5",
        },
        "generator": {
            "script": "scripts/generate_dataset7.py",
            "specification_version": SPECIFICATION_VERSION,
            "generator_version": GENERATOR_VERSION,
            "master_seed": MASTER_SEED,
            "n_valid_customers": N_VALID,
            "n_invalid_rows": N_INVALID,
            "n_raw_rows": len(rows),
            "event_generation_attempt": attempt,
            "lambda0_locked_months": LAMBDA0_LOCKED_MONTHS,
            "lambda0_calibrated_months": lam,
            "weibull_shape": WEIBULL_K,
            "weibull_scale_days": lam * MONTH_DAYS,
            "coefficients": {
                "plan_tier_starter": BETA_STARTER,
                "plan_tier_pro": BETA_PRO,
                "contract_length_months": BETA_CONTRACT,
                "usage_frequency": BETA_USAGE,
                "support_tickets_90d": BETA_TICKETS,
                "frailty_sd": FRAILTY_SD,
            },
            "events_generated": n_events,
            "threads_generated": len(threads),
            "messages_generated": sum(len(t["messages"]) for t in threads),
            "unsupported_language_threads": n_unsupported,
            "cross_channel_duplicate_pairs": len(duplicates),
            "tickets_reconciled_customers": n_reconciled,
            "missingness": {
                "usage_frequency_mcar": missingness_counts.get("usage_frequency", 0),
                "support_tickets_90d_mar_enterprise": missingness_counts.get(
                    "support_tickets_90d", 0
                ),
                "contract_length_months_mnar_starter_monthly": missingness_counts.get(
                    "contract_length_months", 0
                ),
                "n_customers_with_missing": n_customers_with_missing,
                "n_customers_complete": N_VALID - n_customers_with_missing,
            },
            "quant_only_band": quant_only_band,
            "ph_cohort": {
                "indices": [PH_LO, PH_HI],
                "size": ph_cohort_size,
                "knot_days": PH_KNOT_DAYS,
                "usage_coefficient_before_knot": BETA_USAGE,
                "usage_coefficient_after_knot": round(BETA_USAGE * PH_USAGE_FACTOR, 4),
            },
        },
        "dgp_truth": {
            "distribution": "weibull_proportional_hazards",
            "baseline": {
                "shape": WEIBULL_K,
                "scale_months": lam,
                "scale_days": lam * MONTH_DAYS,
                "lambda0_locked_months": LAMBDA0_LOCKED_MONTHS,
            },
            "coefficients": {
                "plan_tier_starter": BETA_STARTER,
                "plan_tier_pro": BETA_PRO,
                "contract_length_months": BETA_CONTRACT,
                "usage_frequency": BETA_USAGE,
                "support_tickets_90d": BETA_TICKETS,
            },
            "frailty_sd": FRAILTY_SD,
            "time_varying_effect": {
                "enabled": True,
                "cohort": {"indices": [PH_LO, PH_HI], "size": ph_cohort_size},
                "feature": "usage_frequency",
                "knot_days": PH_KNOT_DAYS,
                "coefficient_before_knot": BETA_USAGE,
                "coefficient_after_knot": round(BETA_USAGE * PH_USAGE_FACTOR, 4),
                "survival_model": "piecewise_weibull_cumulative_hazard",
                "formula": (
                    "H(t)=(t/scale)^k*exp(lp) for t<=180d; "
                    "H(t)=H(180d)+((t-180d)/scale)^k*exp(lp_post) for t>180d; "
                    "lp_post=lp-(1-0.5)*BETA_USAGE*usage_frequency; S(t)=exp(-H(t))"
                ),
            },
        },
        "pipeline_expectations": {
            "node1": {
                "config": "config/node1/vdataset7.json",
                "validation_version": "1.1.0",
                "expected_status": "PARTIAL",
                "n_accepted": n_accepted,
                "n_rejected": n_rejected,
                "missingness_passthrough": dict(sorted(passthrough_counts.items())),
                "note": "Approved-core blanks within the 30% threshold pass through as "
                        "null cores (config allow_missing_core_passthrough=true, "
                        "architecture §1.7 amendment). The passthrough rescues the 495 "
                        "valid customers with §11 missingness AND the 130 taxonomy rows "
                        "whose only error is a blank core (100 MISSING_CORE + 30 "
                        "INVALID_PLAN via blank usage); Node 2 complete-case excludes "
                        "all null-core records.",
            },
            "node2": {
                "expected_model": "cox_ph",
                "expected_status": "WARNING",
                "plan_tier_recoverability": "partial",
                "strata_used": None,
                "ph_severity": "none",
                "ticket_effect_recoverable": True,
                "usage_effect_recoverable": True,
                "contract_effect_recoverable": True,
                "n_customers_complete_core": N_VALID - n_customers_with_missing,
                "note": "The PH-violation check finds no serious violator in this fit "
                        "(severity=none), so no stratified refit runs and plan_tier stays "
                        "in the adjusted model: starter is recovered (HR>1) but pro is "
                        "not reliably recovered (fitted sign disagrees with the DGP "
                        "coefficient — collinearity with usage/contract persists). "
                        "contract/usage/ticket directions are recovered on the "
                        "complete-case matrix.",
            },
            "node3": {
                "expected_processed_threads": validation_oracle["node3_processed_threads"],
                "expected_failed_threads": validation_oracle["node3_failed_threads"],
            },
        },
        "directions": {
            "plan_tier": {
                "kind": "categorical",
                "reference_category": "enterprise",
                "effect": {"starter": "recovered_higher_hazard",
                           "pro": "no_reliable_adjusted_claim",
                           "enterprise": "baseline"},
                "coefficient": {"starter": BETA_STARTER, "pro": BETA_PRO, "enterprise": 0.0},
                "note": "The DGP coefficients are factual generation parameters. In the "
                        "v1.2 fit Node 2's PH-violation check is severity=none, so no "
                        "stratified refit runs and plan_tier stays in the adjusted "
                        "model: starter is recovered (fitted HR>1, matching the DGP) but "
                        "pro is NOT reliably recovered (fitted HR<1 while the DGP "
                        "coefficient is +0.25 — collinearity between pro, higher usage, "
                        "and longer contracts persists and inverts the estimate). Only "
                        "the univariate event-rate ordering (starter > pro > enterprise) "
                        "and the starter adjusted direction are asserted.",
            },
            "contract_length_months": {
                "kind": "numeric", "effect": "higher_hazard_at_lower_months",
                "coefficient": BETA_CONTRACT,
            },
            "usage_frequency": {
                "kind": "numeric", "effect": "higher_hazard_at_lower_usage",
                "coefficient": BETA_USAGE,
            },
            "support_tickets_90d": {
                "kind": "numeric", "effect": "higher_hazard_at_more_tickets",
                "coefficient": BETA_TICKETS,
            },
        },
        "observed_univariate_event_rates": direction_rates,
        "cohort_counts": {
            name: sum(1 for rec in customer_truth.values() if name in rec["support_cohorts"])
            for name in ("strong_cancellation_intent", "moderate_cancellation_intent",
                         "weak_cancellation_intent", "repeated_issues", "no_data",
                         "conflict_a", "conflict_b", "cold_start", "quant_only",
                         "low_information", "positive_sentiment", "high_urgency",
                         "unsupported_language", "cross_channel_duplicate")
        },
        "customer_truth": customer_truth,
        "invalid_rows": invalid_rows_truth,
        "support_truth": support_truth,
        "node2_scenario_oracle": node2_oracle,
        "node3_scenario_oracle": node3_oracle,
        "node4_scenario_oracle": node4_oracle,
        "node5_trap_oracle": trap_oracle,
        "cross_channel_duplicates": duplicates,
        "validation_oracle": validation_oracle,
        "threads_summary": {
            "total_threads": len(threads),
            "channels": channel_counts,
            "collapsed_thread_ids": [d["collapsed_thread_id"] for d in duplicates],
        },
    }
    truth_text = json.dumps(truth, indent=2, sort_keys=True) + "\n"

    # LF-only deterministic bytes, staged then atomically moved (write-on-pass).
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    THREADS_JSON.parent.mkdir(parents=True, exist_ok=True)
    TRUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    _write_on_pass(RAW_CSV, csv_text)
    _write_on_pass(THREADS_JSON, threads_text)
    _write_on_pass(TRUTH_JSON, truth_text)

    print("=" * 76)
    print("dataset7 generation report (spec v1.2)")
    print("=" * 76)
    print("artifacts:")
    print(f"  {RAW_CSV}     ({len(rows)} data rows)")
    print(f"  {THREADS_JSON}  ({len(threads)} threads, "
          f"{sum(len(t['messages']) for t in threads)} messages)")
    print(f"  {TRUTH_JSON}  ({len(truth)} top-level keys)")
    print("survival:")
    print(f"  churn events (valid): {n_events}   (target band 420..520)")
    print(f"  lambda0: locked={LAMBDA0_LOCKED_MONTHS} mo  calibrated={lam:.6f} mo  "
          f"attempt={attempt}")
    print(f"  PH cohort: indices {PH_LO}..{PH_HI-1} ({ph_cohort_size} customers, "
          f"knot {PH_KNOT_DAYS} d, usage coef {BETA_USAGE} -> "
          f"{BETA_USAGE * PH_USAGE_FACTOR:.4f})")
    print("missingness (§11):")
    for key, count in sorted(missingness_counts.items()):
        print(f"  {key:<28} {count} (valid)  + {invalid_blank_counts[key]} (invalid) "
              f"= {passthrough_counts[key]} passthrough")
    print(f"  customers with any missing: {n_customers_with_missing} / {N_VALID} "
          f"({n_customers_with_missing / N_VALID:.1%})")
    print(f"  node1 expected: PARTIAL accepted={n_accepted} rejected={n_rejected} "
          f"(passthrough rescued {n_rescued_invalid} taxonomy rows)")
    print(f"quant-only band (770..969): min={quant_only_band['min']:.4f} "
          f"max={quant_only_band['max']:.4f} n_ge_0.70={quant_only_band['n_ge_0_70']}")
    print(f"conflict bands: conflict_a min quant {min(conflict_a_quants):.4f} "
          f"(assert >= 0.75) | conflict_b max quant {max(conflict_b_quants):.4f} "
          f"(assert <= 0.25)")
    print(f"tickets reconciled (planned != actual): {n_reconciled}")
    print("univariate event rates:")
    for k, v in direction_rates.items():
        print(f"  {k}: {v}")

    report = validate_all(RAW_CSV, THREADS_JSON, TRUTH_JSON)
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        detail = f"  {check.detail}" if check.detail else ""
        print(f"  #{check.id:<3} {check.name:<45} {status}{detail}")
    print(f"\nResult: {'PASS' if report.passed else 'FAIL'} "
          f"({sum(c.passed for c in report.checks)}/{len(report.checks)} checks)")
    if not report.passed:
        for check in report.checks:
            if not check.passed:
                print(f"  FAILED #{check.id}: {check.name} - {check.detail}")
        print("self-check FAILED: artifacts kept for inspection (no move-back).")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
