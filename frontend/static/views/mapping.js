// mapping.js — the human audit checkpoint. Shows the draft MappingReport with
// the audited transform names surfaced. On confirm, persists through the gate,
// then RE-TRIGGERS the run with supersedes_run_id (a newly confirmed mapping
// yields a NEW run_id; the original stopped run is never resumed in place).

import { api, errorText } from "../api.js";
import { el, clear, section } from "../components/ui.js";

const WHITELISTED_TRANSFORMS = [
  "identity",
  "str.strip()",
  "to_float",
  "to_int",
  "parse_date",
  "months_before(reference_date)",
  "snapshot_end(reference_date)",
  "row_number",
  'map({...})',
];

const IDENTITY_FIELDS = [
  "customer_id",
  "observation_start",
  "observation_end",
  "event_observed",
];

export async function renderMapping(root, ctx) {
  clear(root);
  const runId = ctx.params.id;
  ctx.setTitle("Confirm mapping");
  if (!runId) {
    root.appendChild(
      section("No run selected", [
        el("p", {
          class: "muted",
          text: "Open a run that stopped for mapping from the history.",
        }),
      ]),
    );
    return;
  }

  const host = el("div");
  root.appendChild(
    section(
      "Mapping confirmation",
      [
        el("p", {
          class: "muted",
          text:
            "No deterministic adapter matched this dataset. Review the proposed " +
            "mappings below, adjust if needed, then confirm. Confirming persists the " +
            "mapping and starts a new run.",
        }),
        host,
      ],
      "first",
    ),
  );

  let summary;
  try {
    summary = await api.getRun(runId);
  } catch (err) {
    host.appendChild(el("p", { text: errorText(err) }));
    return;
  }

  const rawPath = summary.raw_path;
  if (!rawPath) {
    host.appendChild(
      el("p", { text: "This run has no raw_path recorded; cannot draft a mapping." }),
    );
    return;
  }

  const state = {
    report: null,
    rows: [],
    useLlm: false,
  };

  const draftBtn = el("button", {
    class: "primary-btn",
    type: "button",
    text: "Load draft mapping",
  });
  const llmToggle = el("input", { type: "checkbox", id: "use-llm" });
  const tableHost = el("div");
  const confirmBtn = el("button", {
    class: "primary-btn",
    type: "button",
    text: "Confirm & run",
  });
  confirmBtn.disabled = true;

  const loadDraft = async () => {
    ctx.clearBanner();
    draftBtn.disabled = true;
    draftBtn.textContent = "Drafting…";
    try {
      const report = await api.draftMapping(rawPath, llmToggle.checked);
      state.report = report;
      state.rows = report.proposed_mappings.map((m) => ({ ...m }));
      renderTable();
      confirmBtn.disabled = false;
    } catch (err) {
      ctx.showBanner(errorText(err));
    } finally {
      draftBtn.disabled = false;
      draftBtn.textContent = "Load draft mapping";
    }
  };

  const renderTable = () => {
    clear(tableHost);
    const report = state.report;
    if (!report) return;

    const rows = state.rows.map((mapping, index) => {
      const sourceCell = el("td", { text: mapping.source_column });
      const targetCell = el("td");
      const targetInput = el("input", {
        type: "text",
        value: mapping.target_field,
        list: "target-fields",
        oninput: (e) => {
          state.rows[index].target_field = e.target.value;
        },
      });
      targetCell.appendChild(targetInput);

      const transformCell = el("td");
      const transformSelect = el("select", {
        onchange: (e) => {
          state.rows[index].transformation = e.target.value;
        },
      });
      const known = WHITELISTED_TRANSFORMS.includes(mapping.transformation);
      for (const option of WHITELISTED_TRANSFORMS) {
        transformSelect.appendChild(
          el("option", {
            value: option,
            text: option,
            selected: option === mapping.transformation,
          }),
        );
      }
      if (!known) {
        // Preserve a raw map({...}) literal verbatim if the API returned one.
        transformSelect.appendChild(
          el("option", {
            value: mapping.transformation,
            text: mapping.transformation,
            selected: true,
          }),
        );
      }
      transformCell.appendChild(transformSelect);

      const confidenceCell = el("td", {
        class: "num",
        text: Number(mapping.confidence).toFixed(2),
      });
      const notesCell = el("td", {
        class: "muted",
        text: mapping.notes || "—",
      });

      return el("tr", { class: "plain" }, [
        sourceCell,
        targetCell,
        transformCell,
        confidenceCell,
        notesCell,
      ]);
    });

    const table = el("table", {}, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Source column" }),
          el("th", { text: "Target field" }),
          el("th", { text: "Transform" }),
          el("th", { class: "num", text: "Conf." }),
          el("th", { text: "Notes" }),
        ]),
      ]),
      el("tbody", {}, rows),
    ]);

    const datalist = el("datalist", { id: "target-fields" });
    for (const field of IDENTITY_FIELDS) {
      datalist.appendChild(el("option", { value: field }));
    }
    const coreUnion = [
      "core.plan_tier",
      "core.contract_length_months",
      "core.usage_frequency",
      "core.support_tickets_90d",
      "core.monthly_charges",
    ];
    for (const field of coreUnion) {
      datalist.appendChild(el("option", { value: field }));
    }

    tableHost.appendChild(
      el("div", { class: "table-scroll" }, [table, datalist]),
    );

    const extras = report.suggested_extra_features.map(
      (e) => `${e.source} → ${e.suggested_key}`,
    );
    tableHost.appendChild(
      el("div", { class: "section" }, [
        el("h3", { class: "section-title", text: "Storage-only extras" }),
        el(
          "ul",
          { class: "plain-list" },
          extras.length
            ? extras.map((t) => el("li", { class: "code", text: t }))
            : [el("li", { class: "muted", text: "none" })],
        ),
        el("h3", { class: "section-title", text: "Unmapped columns" }),
        el(
          "ul",
          { class: "plain-list" },
          (report.unmapped_columns.length ? report.unmapped_columns : ["none"]).map(
            (c) => el("li", { class: "code", text: c }),
          ),
        ),
        report.data_quality_flags.length
          ? el("div", {}, [
              el("h3", { class: "section-title", text: "Data-quality flags" }),
              el(
                "ul",
                { class: "plain-list" },
                report.data_quality_flags.map((f) => el("li", { text: f })),
              ),
            ])
          : null,
        el("p", {
          class: "muted",
          text: `Recommended action: ${report.recommended_action}  ·  model: ${report.llm_model_used}`,
        }),
      ]),
    );
  };

  const confirm = async () => {
    ctx.clearBanner();
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Confirming…";
    // Rebuild the report with the edited rows, preserving all other fields.
    const edited = {
      ...state.report,
      proposed_mappings: state.rows.map((m) => ({
        source_column: m.source_column,
        target_field: m.target_field,
        confidence: m.confidence,
        transformation: m.transformation,
        notes: m.notes ?? null,
      })),
    };
    try {
      // Persist through the confirmation gate (auth header attached by api.js).
      await api.confirmMapping({
        report: edited,
        fingerprint: summary.pending_fingerprint || state.report.source_fingerprint,
      });
      // Re-trigger: a new mapping => a NEW run_id. Link lineage for the history.
      const result = await api.triggerRun({
        raw_path: rawPath,
        supersedes_run_id: runId,
      });
      ctx.navigateToRun(result.run_id);
    } catch (err) {
      ctx.showBanner(errorText(err));
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Confirm & run";
    }
  };

  draftBtn.addEventListener("click", loadDraft);
  confirmBtn.addEventListener("click", confirm);

  host.appendChild(
    el("div", { class: "auth-box" }, [
      draftBtn,
      el("label", { for: "use-llm", class: "muted", text: "Use LLM draft" }),
      llmToggle,
    ]),
  );
  host.appendChild(tableHost);
  host.appendChild(el("div", { class: "auth-box" }, [confirmBtn]));
}
