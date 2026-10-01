// mappingEditor.js — editable mapping table with ONE ROW PER DATASET COLUMN.
// Rows that a draft/file already maps are pre-filled; every other column is a
// blank row the user can map by typing a target field. Columns left blank are
// kept as storage-only extras (never modeled). Only audited transforms are
// offered; a value lookup is written as map({...}) in its own text box.

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
  "snapshot_end(reference_date)",
  "row_number",
];
const MAP_OPTION = "map";
const MAP_EXAMPLE = 'map({"Yes": 1, "No": 0})';

// `onChange()` (optional) fires after every edit, so the screen can show the
// required-field checklist and problems live instead of only on confirm.
export function mappingEditor(report, { coreKeys = [], onChange } = {}) {
  const notify = () => onChange && onChange();
  const columns = report.source_fingerprint.column_names;
  const mapped = report.proposed_mappings.map((m) => ({ ...m }));
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
      })),
  ];

  const datalistId = "mapping-target-fields";
  const datalist = el("datalist", { id: datalistId }, [
    ...IDENTITY_FIELDS.map((field) => el("option", { value: field })),
    ...coreKeys.map((key) => el("option", { value: `core.${key}` })),
  ]);

  const body = rows.map((row) => {
    const target = el("input", {
      type: "text",
      value: row.target_field,
      list: datalistId,
      placeholder: "leave blank = keep as extra",
      "aria-label": `Target field for ${row.source_column}`,
      oninput: (e) => {
        row.target_field = e.target.value.trim();
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

    return el("tr", { class: "plain" }, [
      el("td", { class: "code", text: row.source_column }),
      el("td", {}, [target]),
      el("td", {}, [transform, mapInput]),
      el("td", { class: "muted", text: row.notes || "—" }),
    ]);
  });

  const node = el("div", { class: "table-scroll mapping-table" }, [
    el("table", {}, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Source column" }),
          el("th", { text: "Target field" }),
          el("th", { text: "Transform" }),
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
      if (!IDENTITY_FIELDS.includes(row.target_field) && !row.target_field.startsWith("core.")) {
        issues.push(
          `${row.source_column} → "${row.target_field}": use an identity field or core.<key> (blank keeps it as an extra)`,
        );
      }
    }
    return issues;
  };

  const toReport = () => {
    const proposed = chosen().map((row) => ({
      source_column: row.source_column,
      target_field: row.target_field,
      confidence: typeof row.confidence === "number" ? row.confidence : 1,
      transformation: row.transformation || "identity",
      notes: row.notes ?? null,
    }));
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

  // Which required identity fields currently have a column mapped to them.
  const requiredStatus = () => {
    const targets = new Set(chosen().map((row) => row.target_field));
    return IDENTITY_FIELDS.map((field) => ({ field, ok: targets.has(field) }));
  };

  return { node, toReport, problems, requiredStatus };
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
