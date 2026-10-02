// mappingEditor.js — editable mapping table with ONE ROW PER DATASET COLUMN.
// Rows that a draft/file already maps are pre-filled; every other column is a
// blank row the user can map by typing a target field. Columns left blank are
// kept as storage-only extras (never modeled). Only audited transforms are
// offered; a value lookup is written as map({...}) in its own text box.
//
// Model features (architecture §1.3a): any column that is not an identity or
// core field can be approved as a model feature (feature.<key>, number or
// category). The server screens every column (missingness, signal, leakage,
// proportional hazards) and the verdicts are shown next to each row; a blocked
// column cannot be approved. The LLM may PROPOSE a feature — only the
// checkbox, ticked by a person, approves it. Nothing here computes a verdict.

import { el } from "./ui.js";

export const IDENTITY_FIELDS = [
  "customer_id",
  "observation_start",
  "observation_end",
  "event_observed",
];

const TRANSFORMS = [
  "identity",
  "str.strip()",
  "to_float",
  "to_int",
  "parse_date",
  "months_before(reference_date)",
  "months_before_midpoint(reference_date)",
  "snapshot_end(reference_date)",
  "row_number",
];
const MAP_OPTION = "map";
const MAP_EXAMPLE = 'map({"Yes": 1, "No": 0})';
const FEATURE_PREFIX = "feature.";
const FEATURE_TARGET = /^feature\.[a-z][a-z0-9_]{0,47}$/;
const KINDS = ["number", "category"];

// Same rule as router.feature_screening.suggested_feature_key (display default
// only; the server's screening key wins once columns are checked).
export function suggestedKey(column) {
  let key = column
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[^0-9a-z]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!key || !/^[a-z]/.test(key)) key = key ? `f_${key}` : "feature";
  return key.slice(0, 48);
}

const pct = (value) => `${(value * 100).toFixed(1)}%`;

function screeningFacts(result) {
  const facts = [`${pct(result.missing_fraction)} missing`];
  if (result.auc !== null && result.auc !== undefined) facts.push(`AUC ${result.auc.toFixed(2)}`);
  if (result.n_levels) facts.push(`${result.n_levels} categories`);
  if (result.direction === "higher_more_churn") facts.push("higher → more churn");
  if (result.direction === "higher_less_churn") facts.push("higher → less churn");
  return facts.join(" · ");
}

const VERDICT_TEXT = { ok: "ok", warn: "check", block: "blocked" };

// `onChange()` (optional) fires after every edit, so the screen can show the
// required-field checklist and problems live instead of only on confirm.
export function mappingEditor(report, { coreKeys = [], onChange } = {}) {
  const notify = () => onChange && onChange();
  const columns = report.source_fingerprint.column_names;
  const mapped = report.proposed_mappings.map((m) => ({
    ...m,
    feature_kind: m.feature_kind ?? null,
    use: false, // a proposal is never an approval
  }));
  const referenced = new Set(mapped.map((m) => m.source_column));
  const rows = [
    ...mapped,
    ...columns
      .filter((column) => !referenced.has(column))
      .map((column) => ({
        source_column: column,
        target_field: "",
        confidence: 1,
        transformation: "identity",
        notes: null,
        feature_kind: null,
        use: false,
      })),
  ];
  const screening = new Map(); // source_column -> FeatureScreening
  let screenedAt = null; // JSON of the report the screening was computed for

  const datalistId = "mapping-target-fields";
  const datalist = el("datalist", { id: datalistId }, [
    ...IDENTITY_FIELDS.map((field) => el("option", { value: field })),
    ...coreKeys.map((key) => el("option", { value: `core.${key}` })),
  ]);

  const isFeatureRow = (row) => !row.target_field || row.target_field.startsWith(FEATURE_PREFIX);
  const refreshers = [];

  const body = rows.map((row) => {
    const target = el("input", {
      type: "text",
      value: row.target_field,
      list: datalistId,
      placeholder: "leave blank = keep as extra",
      "aria-label": `Target field for ${row.source_column}`,
      oninput: (e) => {
        row.target_field = e.target.value.trim();
        if (!isFeatureRow(row)) row.use = false;
        refresh();
        notify();
      },
    });

    const isMap = (row.transformation || "").trim().startsWith("map(");
    const mapInput = el("input", {
      type: "text",
      value: isMap ? row.transformation : MAP_EXAMPLE,
      "aria-label": `Value map for ${row.source_column}`,
      oninput: (e) => {
        row.transformation = e.target.value.trim();
        notify();
      },
    });
    mapInput.hidden = !isMap;

    const transform = el("select", {
      "aria-label": `Transform for ${row.source_column}`,
      onchange: (e) => {
        const useMap = e.target.value === MAP_OPTION;
        mapInput.hidden = !useMap;
        row.transformation = useMap ? mapInput.value.trim() : e.target.value;
        notify();
      },
    });
    const known = TRANSFORMS.includes(row.transformation);
    for (const option of TRANSFORMS) {
      transform.appendChild(
        el("option", { value: option, text: option, selected: known && option === row.transformation }),
      );
    }
    transform.appendChild(
      el("option", { value: MAP_OPTION, text: "map values…", selected: isMap }),
    );
    if (!known && !isMap) {
      // Keep an unexpected op visible so the server can reject it explicitly.
      transform.appendChild(
        el("option", { value: row.transformation, text: row.transformation, selected: true }),
      );
    }

    // --- model feature: approve checkbox + kind -------------------------------
    const use = el("input", {
      type: "checkbox",
      id: `feature-${row.source_column}`,
      "aria-label": `Use ${row.source_column} as a model feature`,
      onchange: (e) => {
        row.use = e.target.checked;
        if (row.use && !row.target_field) {
          const result = screening.get(row.source_column);
          row.target_field = `${FEATURE_PREFIX}${result ? result.key : suggestedKey(row.source_column)}`;
          target.value = row.target_field;
        }
        if (row.use && !row.feature_kind) {
          const result = screening.get(row.source_column);
          row.feature_kind = result ? result.kind : "number";
        }
        refresh();
        notify();
      },
    });
    const kind = el("select", {
      "aria-label": `Feature kind for ${row.source_column}`,
      onchange: (e) => {
        row.feature_kind = e.target.value;
        notify();
      },
    });
    for (const option of KINDS) kind.appendChild(el("option", { value: option, text: option }));
    const useLabel = el("label", { class: "inline", for: `feature-${row.source_column}` }, [
      use,
      " use",
    ]);
    const featureCell = el("td", { class: "feature-cell" }, [useLabel, kind]);

    const screenCell = el("td", { class: "screen-cell" });

    // A person's bulk choice (button click): tick this row if its verdict matches.
    row.tickIf = (verdict) => {
      const result = screening.get(row.source_column);
      if (!isFeatureRow(row) || !result || result.verdict !== verdict) return;
      row.use = true;
      if (!row.target_field) {
        row.target_field = `${FEATURE_PREFIX}${result.key}`;
        target.value = row.target_field;
      }
      if (!row.feature_kind) row.feature_kind = result.kind;
      refresh();
    };

    const refresh = () => {
      const eligible = isFeatureRow(row);
      const result = screening.get(row.source_column);
      const blocked = result && result.verdict === "block";
      if (blocked) row.use = false;
      use.checked = row.use;
      use.disabled = !eligible || blocked;
      kind.value = row.feature_kind || (result ? result.kind : "number");
      kind.disabled = !row.use;
      // Identity/core rows cannot be model features: show nothing to tick.
      useLabel.hidden = !eligible;
      kind.hidden = !eligible;

      screenCell.replaceChildren();
      if (!eligible) {
        screenCell.appendChild(el("span", { class: "muted", text: "—" }));
        return;
      }
      if (!result) {
        screenCell.appendChild(el("span", { class: "muted", text: "not checked" }));
        return;
      }
      screenCell.appendChild(
        el("span", { class: `verdict verdict-${result.verdict}`, text: VERDICT_TEXT[result.verdict] }),
      );
      screenCell.appendChild(el("div", { class: "screen-facts", text: screeningFacts(result) }));
      const reasons = [...result.block_reasons, ...result.warn_reasons];
      if (reasons.length) {
        screenCell.appendChild(
          el("ul", { class: "screen-reasons" }, reasons.map((r) => el("li", { text: r }))),
        );
      }
    };
    refreshers.push(refresh);
    refresh();

    return el("tr", { class: "plain" }, [
      el("td", { class: "code", text: row.source_column }),
      el("td", {}, [target]),
      el("td", {}, [transform, mapInput]),
      featureCell,
      screenCell,
      el("td", { class: "muted", text: row.notes || "—" }),
    ]);
  });

  const node = el("div", {
    class: "table-scroll mapping-table",
    role: "region",
    "aria-label": "Column mapping",
    tabindex: "0",
  }, [
    el("table", {}, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Source column" }),
          el("th", { text: "Target field" }),
          el("th", { text: "Transform" }),
          el("th", { text: "Model feature" }),
          el("th", { text: "Checks" }),
          el("th", { text: "Notes" }),
        ]),
      ]),
      el("tbody", {}, body),
    ]),
    datalist,
  ]);

  const chosen = () => rows.filter((row) => row.target_field);

  // Client-side checks that would otherwise only fail after confirmation.
  const problems = () => {
    const issues = [];
    const targets = chosen().map((row) => row.target_field);
    for (const field of IDENTITY_FIELDS) {
      if (!targets.includes(field)) issues.push(`map a column to ${field}`);
    }
    const seen = new Set();
    for (const target of targets) {
      if (seen.has(target)) issues.push(`${target} is mapped more than once`);
      seen.add(target);
    }
    for (const row of chosen()) {
      const t = row.target_field;
      if (t.startsWith(FEATURE_PREFIX)) {
        if (!FEATURE_TARGET.test(t)) {
          issues.push(`${row.source_column} → "${t}": feature keys are snake_case (feature.my_key)`);
        }
      } else if (!IDENTITY_FIELDS.includes(t) && !t.startsWith("core.")) {
        issues.push(
          `${row.source_column} → "${t}": use an identity field, core.<key> or feature.<key> (blank keeps it as an extra)`,
        );
      }
    }
    if (rows.some((row) => row.use) && screeningStale()) {
      issues.push("check columns again before confirming model features");
    }
    return issues;
  };

  const toReport = () => {
    const proposed = chosen().map((row) => {
      const mapping = {
        source_column: row.source_column,
        target_field: row.target_field,
        confidence: typeof row.confidence === "number" ? row.confidence : 1,
        transformation: row.transformation || "identity",
        notes: row.notes ?? null,
      };
      if (row.target_field.startsWith(FEATURE_PREFIX)) {
        const result = screening.get(row.source_column);
        mapping.feature_kind = row.feature_kind || (result ? result.kind : "number");
      }
      return mapping;
    });
    const used = new Set([
      ...proposed.map((m) => m.source_column),
      ...report.suggested_extra_features.map((e) => e.source),
    ]);
    return {
      ...report,
      proposed_mappings: proposed,
      unmapped_columns: columns.filter((column) => !used.has(column)),
    };
  };

  // What the screening depends on: every mapping except a feature row that is
  // exactly the candidate the server already screened (same key, kind and an
  // identity transform) — ticking such a row does not change any verdict.
  const screeningSignature = () =>
    JSON.stringify(
      chosen()
        .filter((row) => {
          const result = screening.get(row.source_column);
          return !(
            result &&
            row.target_field === `${FEATURE_PREFIX}${result.key}` &&
            (row.transformation || "identity") === "identity" &&
            (row.feature_kind || result.kind) === result.kind
          );
        })
        .map((row) => [row.source_column, row.target_field, row.transformation, row.feature_kind]),
    );
  const screeningStale = () => screenedAt === null || screenedAt !== screeningSignature();

  const setScreening = (results) => {
    screening.clear();
    for (const result of results) screening.set(result.source_column, result);
    screenedAt = screeningSignature();
    for (const refresh of refreshers) refresh();
    notify();
  };

  // Keys of the feature rows a person approved (ticked and not blocked).
  const approvedFeatures = () =>
    rows
      .filter((row) => row.use && row.target_field.startsWith(FEATURE_PREFIX))
      .map((row) => row.target_field.slice(FEATURE_PREFIX.length));

  // Tick every not-yet-ticked column whose screening verdict is `verdict`.
  const tickVerdict = (verdict) => {
    for (const row of rows) row.tickIf(verdict);
    notify();
  };

  // Counts of screened columns by verdict (empty before "Check columns").
  const screeningCounts = () => {
    const counts = { ok: 0, warn: 0, block: 0 };
    for (const result of screening.values()) counts[result.verdict] += 1;
    return counts;
  };

  // Which required identity fields currently have a column mapped to them.
  const requiredStatus = () => {
    const targets = new Set(chosen().map((row) => row.target_field));
    return IDENTITY_FIELDS.map((field) => ({ field, ok: targets.has(field) }));
  };

  return {
    node,
    toReport,
    problems,
    requiredStatus,
    setScreening,
    screeningStale,
    approvedFeatures,
    tickVerdict,
    screeningCounts,
  };
}

// Union of the core keys every deployment config approves (the CoreFeatures
// vocabulary grows per onboarding, so it is read from the server, not hardcoded).
export function coreKeysFrom(node1Configs) {
  const keys = new Set();
  for (const config of node1Configs || []) {
    for (const key of config.approved_core_keys || []) keys.add(key);
  }
  return [...keys].sort();
}
