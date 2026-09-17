# Node 3 Multi-Source Ingestion — Architecture Addendum

**Status:** implemented
**Scope:** Node 3 only. Nodes 1, 2, 4 and 5 are unchanged.
**Authority:** this addendum extends `architecture.md` §3. Where it is silent,
`architecture.md` still wins.

## 1. Purpose

Node 3 was defined around one input: `support_data` (`architecture.md` §3.2). This
addendum generalizes its responsibility to:

> Convert unstructured customer interactions from **one or more sources**
> (support, X.com, Gmail, and future CRM/support/survey systems) into the
> standardized qualitative signals Node 4 already understands.

Node 4 must not know where evidence came from. This is achieved by normalizing
every external source into the existing `SupportThread` contract and reusing the
existing preprocessing → extraction → aggregation pipeline unchanged.

```
                    Node 3
                      │
          ┌───────────┼───────────┐
          ↓           ↓           ↓
       Support       X.com      Gmail
       source        source     source
          │           │           │
          └───────────┼───────────┘
                      ↓   ExternalMessage (source-neutral)
              identity resolution (exact, config-driven)
                      ↓
              normalization → SupportThread (source-tagged)
                      ↓
        existing preprocess → extract → aggregate
                      ↓
                  Node3Output
                      ↓
                    Node 4
```

## 2. Contracts

### 2.1 `schemas/external.py` — `ExternalMessage`

Source-neutral message. `extra="forbid"`; `metadata` is the only open dict and is
**never** fed to the signal extractor or any model.

```
source              str        # "x" | "gmail" | ...
source_type         str | None # post | mention | dm | email | ...
external_identity   str        # source-native author identity
customer_id         str | None # None until identity resolution
thread_id           str        # source-native thread/conversation id
message_id          str        # source-native message id
timestamp           datetime
text                str
role                "customer" | "agent" | "system"
author_id           str | None
conversation_id     str | None
url                 str | None
subject             str | None
metadata            dict       # source-specific extras (open)
```

### 2.2 Additive provenance on the existing Node 3 schemas

These are optional and non-breaking; existing fixtures still validate and Node 4
ignores them.

- `SupportThread.source: str | None`
- `ThreadSignals.source: str | None`
- `Evidence.source: str | None` (flows through `RiskFlag.evidence` →
  `AggregatedRiskFlag.strongest_evidence` → Node 5 evidence lookup)

The Node 3 output contract (`Node3Output` / `Node3ProcessingReport`) is **not**
changed: source statistics are reported through the existing `warnings` / `errors`
lists.

## 3. Source abstraction (`node3/sources/`)

`ExternalSource` (`base.py`) is the only interface Node 3 depends on:

```python
class ExternalSource:
    name: str
    def fetch_customer_data(customer_ids) -> list[ExternalMessage]: ...
```

Each source owns its authentication, API communication, pagination, rate-limit
handling, response parsing and source-native ids. Implementations:

| Class | Mode | Notes |
|---|---|---|
| `MockXSource` | mock | `posts.json`, `mentions.json`, `dms.json` |
| `XSource` | live | credentials validated; transport deferred |
| `MockGmailSource` | mock | `threads.json`, `messages.json` |
| `GmailSource` | live | OAuth2 config validated; transport deferred |

Public X data and DMs are separate access paths (`include_public` /
`include_dms`); DMs are off unless explicitly enabled. `registry.build_sources`
maps a versioned `Node3SourcesConfig` + environment to instances. Adding a future
source is one registry branch plus one config entry — signal extraction never
changes.

## 4. Identity resolution (`identity.py`)

`config/identity_mapping/v1.json` is an explicit, versioned, exact-match map:

```json
{ "mapping_version": "identity_v1.0",
  "mappings": { "x": {"x_user_a": "CUST-A"}, "gmail": {"cust.c@example.com": "CUST-C"} } }
```

Rules:
- **No fuzzy matching, no LLM.** Matching is explicit and deterministic.
- Per-source case rule (QA F-11): `gmail` identities are matched
  case-insensitively (`strip + casefold`); `x` identities are matched exactly
  (case-sensitive). The same rule normalizes the incoming identity and the
  mapping keys. If two mapping keys for the same source normalize to the same
  value but map to different customers, that key is **ambiguous**: the message is
  dropped with `IDENTITY_MAPPING_AMBIGUOUS` (fail closed — never guess).
- Declared `agent_identities` per source are forced to role `agent` and can never
  be attached to a customer.
- A customer message whose identity is absent from the map is **dropped** and
  reported as `UNMAPPED_EXTERNAL_IDENTITY`. False association is worse than
  missing evidence.
- Structured identity errors never persist the raw external identity; they record
  a deterministic non-reversible reference `sha256:<12 hex>` (QA F-9). `message_id`
  and `source` are retained for provenance.

## 5. Normalization (`normalize.py`)

Messages are grouped by `(source, thread_id)`:
- Thread owner = the single distinct resolved customer among customer messages.
  Zero owners → `UNRESOLVED_THREAD`; more than one → `CROSS_CUSTOMER_CONTAMINATION`.
- `created_at` = earliest message timestamp; roles preserved; subject taken from
  the first message that has one.
- IDs are namespaced `"{source}:{raw_id}"` for global uniqueness and provenance
  (recoverable via `split(":", 1)`). No ids/timestamps/quotes are fabricated.
- Channel defaults: `x → "x"`, `gmail → "email"`.
- **Collision safety (QA F-8).** Support data may legitimately contain ids that
  look namespaced (e.g. a support thread already named `x:foo`). Before merging,
  `resolve_support_external_id_collisions` detects any external thread whose
  namespaced `thread_id` or `message_id` collides with an existing support id (or
  a duplicate external `thread_id`) and drops that external thread with a
  structured `ID_COLLISION` error. The id format is unchanged (Node 5 depends on
  it); records are never overwritten and non-colliding ids are preserved exactly.

## 6. Signal extraction compatibility

External threads enter the **same** deterministic preprocessing and the same
offline/LLM thread extractor. Flags use the existing `FlagType` vocabulary; no new
taxonomy is introduced. `positive_feedback` remains non-risk-bearing everywhere.

Deterministic offline rules gained a small number of source-agnostic phrases so
representative external wording maps into the existing categories (e.g. "still not
fixed"/"unusable" → `product_bug_or_outage` strong; "other vendors"
→ `competitor_mention` moderate; "great experience"/"solved my issue" →
`positive_feedback`). The dataset-7 golden proxy is unchanged
(κ(flag_type)=0.755, κ(signal_strength)=0.954).

The extraction prompt explicitly marks the subject and message text as untrusted
data. The subject is wrapped in `<untrusted_subject>…</untrusted_subject>` and each
message in `<untrusted_message id="…">…</untrusted_message>`; **both the subject and
message content and the message-id attribute are HTML-escaped** (QA F-1/F-2), so an
arbitrary body/id can never reproduce a structural prompt delimiter. The system
instructions and extraction task are stated before the untrusted fences and the
fence tokens appear nowhere else in the prompt. Output remains
enum/schema/message-id validated, so injected instructions cannot introduce flags,
scores, discounts or ranks. The LLM's referenced `message_id` is resolved
fail-closed: unknown or ambiguous references quarantine the thread.

## 7. Duplicate evidence and recurrence

The existing §3.3 cross-channel collapse is reused unchanged and now operates
across sources because all sources feed the same thread pool: same customer,
`created_at` within ±48h, TF-IDF cosine ≥ 0.82, and subject similarity ≥ 0.75 or a
shared key issue phrase. Collapsed threads are excluded from all counts. Distinct
reports on different days remain genuine recurrence; near-identical cross-source
reposts are not double-counted.

## 8. Time handling

Source timestamps must be **timezone-aware**; `ExternalMessage` rejects naive
timestamps (no implicit source timezone is assumed) and canonicalizes every aware
timestamp to UTC at ingestion. This prevents naive/aware comparison errors and
makes equal instants across different offsets compare equal. The existing lookback
window (`reference_date - lookback_days`) applies identically; messages outside
the window are handled by the existing `preprocess` rules.

Node 3 never uses wall-clock time (QA F-3). Unless the caller supplies an explicit
`now`, every `processed_at` (thread and customer level) is derived from the
declared `Node3Config.reference_date` at midnight UTC — the same rule Node 4 uses
for `ranked_at` and Node 5 for `generated_at`. The CLI therefore requires no new
flag and two identical runs are byte-identical, including the serialized
timestamps.

## 9. Configuration and environment

Versioned config: `config/node3/sources_v1.json`

```json
{ "sources_version": "sources_v1.0",
  "identity_mapping_version": "1",
  "sources": {
    "x":     {"enabled": true, "mode": "mock", "include_public": true,
              "include_dms": true, "agent_identities": ["acme_support"]},
    "gmail": {"enabled": true, "mode": "mock", "agent_identities": ["support@acme.com"]}
  } }
```

Environment (`.env.example`, placeholders only):
`NODE3_SOURCE_MODE` (default `mock`), `NODE3_MOCK_SOURCES_DIR` (default
`mock_sources/`), `X_ENABLED`/`X_CLIENT_ID`/`X_CLIENT_SECRET`/`X_ACCESS_TOKEN`,
`GMAIL_ENABLED`/`GMAIL_CLIENT_ID`/`GMAIL_CLIENT_SECRET`/`GMAIL_REFRESH_TOKEN`.
Missing credentials when a source is enabled fail loudly at `Settings`
construction. Mock mode requires no credentials.

## 10. CLI

```
churn-survival node3 [<threads.json>] [--customers <ids>] [--config <v>]
    [--sources <x,gmail|mock>] [--source-mode mock|live]
    [--sources-config <v>] [--identity-map <v>] [--mock-dir <path>]
    [--output <out.json>]
```

Without `--sources`, behavior is unchanged (threads-file only). With `--sources`,
the universe defaults to the identity mapping's customer ids unless `--customers`
is given; a threads file may be supplied and is merged with the external threads.

**Source-selection / mode precedence (QA F-6/F-12):**

1. `enabled` is a hard gate. An explicitly requested disabled source is a
   configuration error (non-zero exit) — it is never silently reinterpreted as
   "no source".
2. `--source-mode mock|live` (explicit CLI intent) overrides the per-source `mode`
   for the explicitly named sources.
3. A per-source `mode` in the sources config overrides the global
   `NODE3_SOURCE_MODE` environment default.
4. `--sources mock` selects only **enabled** sources whose effective mode resolves
   to mock (per-source mode, else the global default). Combining it with
   `--source-mode live` is contradictory and rejected. Mode is never silently
   switched from mock to live or live to mock.

## 11. Mock fixtures

`mock_sources/x/{posts,mentions,dms}.json`, `mock_sources/gmail/{threads,messages}.json`
(committed; `data/` is gitignored). Scenario coverage:

| Customer | Source | Scenario |
|---|---|---|
| A | X post + DM | positive feedback (non-risk) |
| B | X post | product complaint → `product_bug_or_outage` strong |
| C | Gmail | evaluating alternatives → `competitor_mention` moderate + renewal concern |
| D | X post + DM | strong cancellation intent (recurrence 2) |
| E | X mention | identity not mapped → dropped with structured warning |
| F | X post + Gmail message | cross-source duplicate → collapsed (recurrence 1) |
| G | Gmail | missing thread metadata (no subject) → tolerated |

Mock mode is deterministic; re-runs are byte-identical.

## 12. Failure modes (additive to `architecture.md` §3.12)

| Situation | Handling |
|---|---|
| Source construction failure (missing dir, live creds absent, unknown source) | structured `SOURCE_INIT_FAILED` error + warning; other sources still built (QA F-5) |
| Source fetch/auth/API failure | structured `SOURCE_FETCH_FAILED` error + warning; other sources continue |
| Malformed source record (e.g. bad timestamp) | `SourceDataError` → structured `SOURCE_DATA_INVALID` error + warning; other sources continue (QA F-4) |
| Missing/unmapped external identity | message dropped, `UNMAPPED_EXTERNAL_IDENTITY` (identity recorded only as a deterministic hash), never attached |
| Ambiguous normalized identity mapping | message dropped, `IDENTITY_MAPPING_AMBIGUOUS` (fail closed) |
| Thread with no resolved customer | dropped, `UNRESOLVED_THREAD` |
| Thread resolving to multiple customers | dropped, `CROSS_CUSTOMER_CONTAMINATION` |
| External id collides with existing support id | external thread dropped, `ID_COLLISION` (QA F-8) |
| Live credentials missing | `SourceNotConfiguredError` (loud; never a workaround) |

## 13. Live integration status / dependency decision

No X/Gmail SDK dependency was added. `httpx` (already an optional `[llm]`
dependency) is the intended transport for the deferred live paths. `XSource` /
`GmailSource` validate credentials and raise `SourceNotImplementedError` from the
transport; wiring the concrete HTTP/OAuth calls (including OAuth refresh) is a
documented follow-up. Mock remains the default.

## 14. Node 4 / Node 5 compatibility

- **Node 4: unchanged.** It consumes `CustomerSupportSignals` only; provenance
  fields are ignored by every decision path. `tests/node4` and the dataset-7
  Node 1→2→3→4 E2E pass unchanged, and a new test feeds Node 3 external-source
  output into Node 4 with no modification.
- **Node 5: unchanged.** It resolves evidence by `(customer_id, message_id)` and
  works with namespaced external ids. `tests/node5` and the dataset-7 E2E pass.
  **F-13 (INFO) — intentionally deferred, do NOT fix in this task:**
  `node5/report/evidence.py` hardcodes the evidence description
  `"reported in a support interaction"` and `ReportEvidence.source` is always
  `"node3"`; the external source is only visible through the namespaced
  `thread_id`/`message_id`. This is a presentation wording gap, not a contract
  incompatibility. Surfacing the source explicitly is deferred work for a future
  separately-approved Node 5 change (it must not be bundled into Node 3 work).

## 15. Verification

- Full suite: **1035 passed, 1 live-LLM skipped**; `node3/` coverage ~96%.
- `ruff check .` clean; `mypy schemas node3` clean.
- Dataset-7 Node 1→2→3→4 and 1→2→3→4→5 E2E green; deterministic CLI re-runs are
  byte-identical.
- `scripts/eval_node3_golden.py` unchanged at the §3.10 bars
  (κ(flag_type)=0.755, κ(signal_strength)=0.954, exact-match 1.000).
- New tests cover source adapters (mock/live/malformed/empty/auth/API), identity
  resolution, normalization, multi-source aggregation, provenance, duplicate
  collapse, prompt-injection resistance, credential redaction, and Node 4/Node 5
  compatibility.

## 17. Adversarial-QA remediation (F-1 … F-12)

An independent adversarial audit of multi-source ingestion returned *PASS WITH
FINDINGS* (2 HIGH, 5 MEDIUM, 3 LOW, 1 INFO, no CRITICAL). All findings except the
deferred F-13 are remediated with regression coverage
(`tests/node3/test_multi_source_qa_remediation.py` plus the now-active defect tests
in `tests/node3/test_multi_source_adversarial_audit.py`):

| # | Severity | Finding | Fix |
|---|---|---|---|
| F-1 | HIGH | subject outside the untrusted fence | `<untrusted_subject>` fence + HTML escaping |
| F-2 | HIGH | message fence escapable; unsafe id attribute | HTML-escape payload + id; fail-closed id resolution |
| F-3 | MEDIUM | nondeterministic `processed_at` | derive from `reference_date` (Node 4/5 rule) |
| F-4 | MEDIUM | malformed payload aborts all sources | `ValidationError` → `SourceDataError` → `SOURCE_DATA_INVALID`, per-source |
| F-5 | MEDIUM | source construction failure aborts all | `build_sources_safe` per-source isolation (`SOURCE_INIT_FAILED`) |
| F-6 | MEDIUM | `_select_sources` ignores `enabled` | disabled explicitly requested → configuration error |
| F-7 | MEDIUM | naive/aware timestamps crash | reject naive; canonicalize aware to UTC |
| F-8 | DATA | support/external namespaced id collision | detect + drop colliding external thread (`ID_COLLISION`) |
| F-9 | LOW | raw unmapped identity in errors | deterministic `sha256` reference only |
| F-10 | LOW | short secrets not redacted | redact any non-empty explicit secret |
| F-11 | LOW | identity case asymmetry | documented per-source rule; ambiguous key fails closed |
| F-12 | LOW | mode-override inconsistency | explicit precedence; `mock`+`live` rejected |
| F-13 | INFO | Node 5 external-source wording | intentionally deferred (see §14) |

Node 4 and Node 5 were not modified by this remediation.

## 16. Deviations / notes

- Mock fixtures live at the repo root (`mock_sources/`) because `data/` is
  gitignored; the path is configurable via `NODE3_MOCK_SOURCES_DIR`.
- `SupportThread.channel` remains source-specific (`x`, `email`) while
  `source` records the logical source (`x`, `gmail`).
- The offline extractor is a degraded deterministic fallback; the LLM remains the
  primary, higher-precision extractor with zero authority over risk.
