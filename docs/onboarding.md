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
proposes mappings; the report is still only a proposal.

### Step 2 — fill in the mappings, then confirm

Edit the draft and move columns from `unmapped_columns` into `proposed_mappings`:

- `source_column`: exact raw column name.
- `target_field`: a canonical key, either `core.<key>` (whitelist) or `extra.<key>`
  (storage-only — never auto-fed to modeling, per hard rule 5).
- `confidence`: your confidence 0–1 (values < 0.5 are not applied).
- `transformation`: an audited op name (`identity`, `strip`, `to_float`,
  `months_before`, `snapshot_end`, or a value-map like `map({'Yes':1,'No':0})`).
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

### Step 4 — brand-new core feature? (only if unavoidable)

Core features are a strict union whitelist. A genuinely new core key requires a
deliberate one-line addition to `CoreFeatures` in `schemas/canonical.py` —
an explicit approval act, not an automatic consequence of onboarding.

### Step 5 — re-run

```
churn-survival node1 customer_export.csv --config <company>
```

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
- `config/node1/vtelco.json` / `config/node1/viranian.json` /
  `config/node1/vbank.json` / `config/node1/vcellular.json` /
  `config/node1/vcredit.json` — the five deployment configs
  (`approved_core_keys` + `core_key_types`).