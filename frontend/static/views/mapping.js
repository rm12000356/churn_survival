// mapping.js — the human audit checkpoint for a run that stopped because no
// adapter matched its dataset. The user picks HOW to map it:
//   - map it themselves (one editable row per dataset column),
//   - ask the LLM for a proposal, or
//   - upload their own mapping file,
// reviews the result in the same table, optionally adds support threads, then
// confirms. Confirming persists through the gate and RE-TRIGGERS the run with
// supersedes_run_id (a newly confirmed mapping yields a NEW run_id).

import { api, errorText } from "../api.js";
import { el, clear, step, emptyState } from "../components/ui.js";
import { filePicker, loadSupportData } from "../components/filePicker.js";
import { mappingFromFile, readMappingFile } from "../components/mappingFile.js";
import { coreKeysFrom, mappingEditor } from "../components/mappingEditor.js";

// Core keys known before any deployment config approved them.
const BASE_CORE_KEYS = [
  "plan_tier",
  "contract_length_months",
  "usage_frequency",
  "support_tickets_90d",
  "monthly_charges",
];

export async function renderMapping(root, ctx) {
  clear(root);
  const runId = ctx.params.id;
  ctx.setTitle("Map dataset");
  if (!runId) {
    root.appendChild(
      emptyState(
        "No run selected",
        "Open a run that stopped for mapping from the history.",
        el("a", { href: "#/runs", text: "Go to runs" }),
      ),
    );
    return;
  }

  let summary;
  try {
    summary = await api.getRun(runId);
  } catch (err) {
    root.appendChild(emptyState("Could not load run", errorText(err)));
    return;
  }
  const rawPath = summary.raw_path;
  if (!rawPath) {
    root.appendChild(
      emptyState(
        "Cannot map this run",
        "This run has no input file recorded, so there is nothing to draft a mapping from.",
      ),
    );
    return;
  }

  let editor = null;
  let report = null;
  let confirmed = false; // the mapping is persisted; a retry only re-triggers
  let inFlight = false; // a confirm/trigger request is running
  let coreKeys = BASE_CORE_KEYS;

  const editorHost = el("div", {}, [
    el("p", { class: "muted", text: "Choose one of the options above to start." }),
  ]);
  const checklist = el("ul", { class: "checklist", "aria-label": "Required fields" });
  const problemsList = el("ul", { class: "problems", role: "status" });
  checklist.hidden = true;
  problemsList.hidden = true;

  // Live validation: the same client-side checks confirm() runs, shown as you edit.
  const updateChecks = () => {
    if (!editor) return;
    clear(checklist);
    for (const { field, ok } of editor.requiredStatus()) {
      checklist.appendChild(el("li", { class: ok ? "ok" : "", text: field }));
    }
    checklist.hidden = false;
    const problems = editor.problems().filter((p) => !p.startsWith("map a column to "));
    clear(problemsList);
    for (const p of problems) problemsList.appendChild(el("li", { text: p }));
    problemsList.hidden = !problems.length;
    const invalid = editor.problems().length > 0;
    confirmBtn.disabled = inFlight || invalid;
    confirmBtn.title = invalid ? "Map every required field first" : "";
  };
  const infoHost = el("div");
  const node1Select = el("select", { id: "node1-config" });
  node1Select.appendChild(el("option", { value: "", text: "Auto-detect (recommended)" }));
  const confirmBtn = el("button", { class: "primary-btn", type: "button", text: "Confirm & run" });
  confirmBtn.disabled = true;

  // --- 1. How to map --------------------------------------------------------
  const manualBtn = el("button", { class: "primary-btn", type: "button", text: "Map it myself" });
  const llmBtn = el("button", { class: "ghost-btn", type: "button", text: "Ask the LLM" });
  const fileInput = el("input", { type: "file", id: "mapping-file", accept: ".json" });
  const choiceButtons = [manualBtn, llmBtn];

  const showReport = (next, sourceLabel) => {
    report = next;
    editor = mappingEditor(report, { coreKeys, onChange: updateChecks });
    clear(editorHost);
    editorHost.appendChild(
      el("p", {
        class: "muted",
        text:
          `${sourceLabel}. Give each column you need a target field; columns left ` +
          "blank are kept as extras and never used by the model. Required:",
      }),
    );
    editorHost.appendChild(checklist);
    editorHost.appendChild(editor.node);
    editorHost.appendChild(problemsList);
    renderInfo();
    confirmed = false;
    confirmBtn.textContent = "Confirm & run";
    updateChecks();
  };

  const busy = async (button, label, work) => {
    ctx.clearBanner();
    const original = button.textContent;
    for (const b of choiceButtons) b.disabled = true;
    fileInput.disabled = true;
    button.textContent = label;
    try {
      await work();
    } catch (err) {
      ctx.showBanner(errorText(err));
    } finally {
      for (const b of choiceButtons) b.disabled = false;
      fileInput.disabled = false;
      button.textContent = original;
    }
  };

  manualBtn.addEventListener("click", () =>
    busy(manualBtn, "Loading columns…", async () => {
      showReport(await api.draftMapping(rawPath, false), "Manual mapping");
    }),
  );
  llmBtn.addEventListener("click", () =>
    busy(llmBtn, "Asking the LLM…", async () => {
      showReport(await api.draftMapping(rawPath, true), "LLM proposal — review every row");
    }),
  );
  fileInput.addEventListener("change", () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    busy(manualBtn, manualBtn.textContent, async () => {
      const parsed = await readMappingFile(file);
      const skeleton = await api.draftMapping(rawPath, false);
      showReport(mappingFromFile(skeleton, parsed, file.name), `Loaded from ${file.name}`);
      fileInput.value = "";
    });
  });

  const renderInfo = () => {
    clear(infoHost);
    if (!report) return;
    const extras = report.suggested_extra_features.map((e) => `${e.source} → ${e.suggested_key}`);
    if (extras.length) {
      infoHost.appendChild(el("h3", { class: "section-title", text: "Storage-only extras" }));
      infoHost.appendChild(
        el("ul", { class: "plain-list" }, extras.map((t) => el("li", { class: "code", text: t }))),
      );
    }
    if (report.data_quality_flags.length) {
      infoHost.appendChild(el("h3", { class: "section-title", text: "Data-quality flags" }));
      infoHost.appendChild(
        el("ul", { class: "plain-list" }, report.data_quality_flags.map((f) => el("li", { text: f }))),
      );
    }
    infoHost.appendChild(
      el("p", {
        class: "muted",
        text: `Recommended action: ${report.recommended_action}  ·  source: ${report.llm_model_used}`,
      }),
    );
  };

  // --- 3. Support threads (Node 3) ------------------------------------------
  const refreshFiles = async () => support.fill((await api.listRawFiles()).files);
  const support = filePicker({
    id: "support-select",
    kind: "support",
    label: "Support-threads file on the server",
    optional: true,
    noneLabel: "None — run without support threads",
    ctx,
    onUploaded: refreshFiles,
  });

  // --- 4. Confirm & run -----------------------------------------------------
  // Once the mapping is persisted, later edits could not be saved (the shape now
  // has a confirmed mapping), so the editor and the mapping choices are locked.
  const lockMapping = () => {
    editorHost.inert = true;
    for (const b of choiceButtons) b.disabled = true;
    fileInput.disabled = true;
    node1Select.disabled = true;
    if (!editorHost.parentNode.querySelector(".locked-note")) {
      editorHost.before(
        el("p", {
          class: "hint locked-note",
          text: "Mapping saved. It can no longer be edited here; only starting the run remains.",
        }),
      );
    }
  };

  const confirm = async () => {
    if (!editor || inFlight) return;
    const problems = editor.problems();
    if (problems.length) {
      ctx.showBanner(`Fix the mapping before confirming: ${problems.join("; ")}.`);
      return;
    }
    ctx.clearBanner();
    inFlight = true;
    confirmBtn.disabled = true;
    try {
      if (!confirmed) {
        confirmBtn.textContent = "Confirming…";
        try {
          await api.confirmMapping({
            report: editor.toReport(),
            raw_path: rawPath,
            node1_config_version: node1Select.value || null,
          });
        } catch (err) {
          // 409: this dataset already has a confirmed mapping (e.g. an earlier
          // attempt succeeded); the new run routes with it.
          if (!(err && err.status === 409)) throw err;
          ctx.showBanner(`${errorText(err)} Starting the run with it.`, "info");
        }
        confirmed = true;
        lockMapping();
      }
      confirmBtn.textContent = "Starting run…";
      const supportData = await loadSupportData(support.value());
      const spec = { raw_path: rawPath, supersedes_run_id: runId };
      if (supportData !== undefined) spec.support_data = supportData;
      if (node1Select.value) spec.node1_version = node1Select.value;
      const result = await api.triggerRun(spec);
      ctx.navigateToRun(result.run_id);
    } catch (err) {
      ctx.showBanner(
        confirmed ? `Mapping saved, but the run did not start: ${errorText(err)}` : errorText(err),
      );
      confirmBtn.textContent = confirmed ? "Start run" : "Confirm & run";
    } finally {
      inFlight = false;
      confirmBtn.disabled = !confirmed && editor.problems().length > 0;
    }
  };
  confirmBtn.addEventListener("click", confirm);

  root.appendChild(
    step(
      1,
      "Choose how to map this dataset",
      false,
      [
        el("p", {
          class: "hint",
          text:
            `No confirmed mapping matches ${rawPath}. Map it yourself, ask the LLM ` +
            "for a proposal, or upload a mapping file (a draft from " +
            "`churn-survival map`, or a confirmed map_*.json).",
        }),
        el("div", { class: "choice-row" }, [
          manualBtn,
          llmBtn,
          el("span", { class: "row" }, [
            el("label", { for: "mapping-file", text: "or upload a mapping file:" }),
            fileInput,
          ]),
        ]),
      ],
      "first",
    ),
  );
  root.appendChild(step(2, "Review the mapping", false, [editorHost, infoHost]));
  root.appendChild(
    step(3, "Support threads", true, [
      el("p", {
        class: "hint",
        text:
          "Support threads are not stored with the stopped run, so choose them again " +
          "if the new run should include support signals.",
      }),
      support.node,
    ]),
  );
  root.appendChild(
    step(4, "Confirm and run", false, [
      el("div", { class: "field" }, [
        el("label", {
          for: "node1-config",
          text: "Node 1 deployment config (recorded with the confirmed mapping)",
        }),
        node1Select,
      ]),
      el("div", { class: "auth-box" }, [confirmBtn]),
    ]),
  );

  try {
    const [node1] = await Promise.all([api.listNode1Configs(), refreshFiles()]);
    coreKeys = [...new Set([...BASE_CORE_KEYS, ...coreKeysFrom(node1.configs)])].sort();
    for (const config of node1.configs || []) {
      const cores = (config.approved_core_keys || []).join(", ") || "no core keys";
      node1Select.appendChild(
        el("option", { value: config.version, text: `v${config.version} — ${cores}` }),
      );
    }
  } catch (err) {
    ctx.showBanner(errorText(err));
  }
}
