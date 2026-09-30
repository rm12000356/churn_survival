# Onboarding a new dataset / company (Node 1)

The router + adapters + canonicalization machinery is generic. A **new** company
brings its own raw columns, so it needs two configuration artifacts before Node 1
will accept it. This page is the guided walkthrough the CLI points at when it
sees an unknown shape.

## What "unknown shape" looks like

```
churn-survival node1 customer_export.csv
ERROR: no deterministic adapter matched; routing to LLM mapping-report path
       (fingerprint headers_hash=…)
```

That is the correct behavior: the system refuses rather than guess. The six-step
checklist below is printed in full by the CLI.

## Workflow (two artifacts)

### Step 1 — draft a mapping report

```
churn-survival map customer_export.csv
```

Writes `config/mappings/drafts/draft_<hash12>.json` — a `MappingReport` skeleton
with the real fingerprint pre-filled and every column listed as `unmapped_columns`.
No LLM required. Drafts live apart from confirmed configs so the deterministic
adapter loader only ever sees confirmed `map_*.json` files.

Optional: with a configured LLM provider, `churn-survival map <file> --llm`
proposes mappings; the report is still only a proposal. LLM output is rejected
(never silently accepted) when it proposes a non-audited transformation, an
invented `core.<key>`, a `row_number` outside `customer_id`, or a target that is
neither an identity field nor a `core.<union-key>`.

Report quality can be measured offline against a messy-dataset eval set:

```
uv run python scripts/eval_llm_mapping.py
```

Prints per-dataset schema-validity, whitelist-compliance, and leakage-into-core
metrics against the acceptance bar (≥90% schema-valid first try, 100%
whitelisted transforms, 0 leakage into core, 100% strict validation).

### Step 2 — fill in the mappings, then confirm

Edit the draft and move columns from `unmapped_columns` into `proposed_mappings`:

- `source_column`: exact raw column name.
- `target_field`: a canonical key — an identity field (`customer_id`,
  `observation_start`, `observation_end`, `event_observed`) or `core.<key>` where
  `<key>` is a `CoreFeatures` union member (`plan_tier`,
  `contract_length_months`, `usage_frequency`, `support_tickets_90d`, `contract`,
  `internet_service`, `monthly_charges`, `senior_citizen`). Anything else is
  rejected at confirmation time; storage-only fields belong in
  `suggested_extra_features` (never auto-fed to modeling, per hard rule 5).
- `confidence`: your confidence 0–1 (values < 0.5 are not applied).
- `transformation`: an audited op name from the strict whitelist — `identity`,
  `str.strip()`, `to_float`, `to_int`, `parse_date`,
  `months_before(reference_date)`, `snapshot_end(reference_date)`, `row_number`
  (customer_id only), or a value-map like `map({'Yes':1,'No':0})`. Any other
  string is rejected loudly at confirmation time — never silently ignored.
  New date/tenure derivations are added to `adapters/mapping_adapter.py` and
  documented in architecture §1.6 before use.

Then persist as a deterministic adapter:

```
churn-survival map config/mappings/drafts/draft_<hash12>.json --confirm
```

Writes `config/mappings/map_<timestamp>.json`. The router now treats that exact
`headers_hash` as a deterministic match (confidence 1.0, priority 0). Duplicate
fingerprints fail loudly — never silently overridden.

### Step 3 — create a deployment config

```
copy config/node1/_template.json -> config/node1/v<company>.json
```

Set `approved_core_keys` to the core features this deployment really produces and
`core_key_types` to each key's declared type (`string` | `float` | `int`).

Record the deployment config on the confirmed mapping so a **full-pipeline** run
(`churn-survival run …`, the API, or the Horizon UI) auto-resolves it instead of
defaulting to `v1`:

```
churn-survival map <draft.json> --confirm --node1-config <company>
```

This stores `node1_config_version` in `config/mappings/map_<ts>.json`. Without it,
`run` falls back to the default `v1` config and may stop with `STOPPED_VALIDATION`
if `v1`'s `approved_core_keys` do not exist in the dataset. An explicit
`--node1 <company>` (CLI) or `node1_version` (API/UI) always overrides the
auto-resolution.

### Step 4 — brand-new core feature? (only if unavoidable)

Core features are a strict union whitelist. A genuinely new core key requires a
deliberate one-line addition to `CoreFeatures` in `schemas/canonical.py` —
an explicit approval act, not an automatic consequence of onboarding.

### Step 5 — re-run

```
churn-survival node1 customer_export.csv --config <company>
```

The full pipeline no longer needs the flag once the mapping records the
deployment config (Step 3): `churn-survival run customer_export.csv` auto-selects
`v<company>` from the matched mapping.

## Real-data examples

- `config/mappings/map_*.json` — IBM Telco Churn (`headers_hash`
  `7489ef65…`): 8 mappings, 14 extra features, using `months_before` and
  `snapshot_end` for tenure-derived dates.
- `config/mappings/map_*.json` — UCI Iranian Churn (`headers_hash`
  `042b42b2…`): no customer-ID column → `row_number` op; `Subscription  Length`
  drives `months_before`/`snapshot_end`; `Frequency of use` reuses the
  deployment-union core key `usage_frequency`.
- `config/mappings/map_*.json` — Bank Customer Churn (`headers_hash`
  `dad50914…`, 10,000 rows): `CustomerId`→`customer_id`, `Tenure`→
  `months_before`/`snapshot_end`, `Exited`→`event_observed`; no core keys.
- `config/mappings/map_*.json` — Cell2Cell Telecom (`headers_hash`
  `774dc5f8…`, 71,047 rows): wide dataset, 78 columns; `CUSTOMER`→`customer_id`,
  `MONTHS`→`months_before`/`snapshot_end`, `CHURN`→`event_observed`; the other
  74 columns stored as extra features under their raw names; no core keys.
- `config/mappings/map_*.json` — Credit Card Customers (`headers_hash`
  `dd227148…`, 10,127 rows): `CLIENTNUM`→`customer_id`, `Months_on_book`→
  `months_before`/`snapshot_end`, `Attrition_Flag`→`event_observed` via a
  categorical value-map (`map({'Attrited Customer': 1, 'Existing Customer': 0})`);
  two `Naive_Bayes_Classifier_*` columns stored only as extras (rule 5); no core
  keys.
- `config/mappings/map_*.json` — `dataset6_saas_churn_messy` (synthetic,
  `headers_hash` `348c4381…`, 5,025 rows): a messy SaaS export whose `Cust ID`/
  `Signup Date`/`Cancellation Date`/`Account Status` use awkward names, mixed
  date formats (via `parse_date`), and mixed churn representations (via a
  `map({…})` value-map); `Plan`, `Contract Length (Months)`,
  `Avg Weekly Active Days`, `Support Tickets (Last 90 Days)` map to the four
  approved core keys. Decoy/noise/leakage columns (`Account Number`, `Tier`,
  `Legacy Churn Score`, `Last Login Days Ago`) are stored only as extras;
  `Internal Notes` is left unmapped. 25 deliberately-invalid rows are
  quarantined (`PARTIAL accepted=5000 rejected=25`).
- `config/node1/vtelco.json` / `config/node1/viranian.json` /
  `config/node1/vbank.json` / `config/node1/vcellular.json` /
  `config/node1/vcredit.json` / `config/node1/vdataset6.json` — the six
  deployment configs (`approved_core_keys` + `core_key_types`).