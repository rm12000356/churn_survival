// upload.js — start a run. Each input has its own section with its own file
// upload, so every file goes where it belongs:
//   1. Customer dataset  (Node 1, required)  -> raw_path       (CSV/Excel)
//   2. Support threads   (Node 3, optional)  -> support_data   (JSON)
//   3. Column mapping    (Node 1, optional)  -> POST /mappings/confirm, then run
//   4. Run settings      (Node 1 config override, optional LLM use) + Run
// Responses are handled, never blocked on: 202 -> status screen (which moves to
// the mapping screen on STOPPED_NEEDS_MAPPING), 4xx -> inline banner.

import { api, errorText } from "../api.js";
import { el, clear, step } from "../components/ui.js";
import { filePicker, loadSupportData } from "../components/filePicker.js";
import { mappingFromFile, readMappingFile } from "../components/mappingFile.js";

export async function renderUpload(root, ctx) {
  clear(root);

  let submitting = false;
  let mapping = null; // { name, parsed }
  let confirmedFor = ""; // dataset the loaded mapping was already confirmed for

  const status = el("p", { class: "muted", text: "Loading files…" });
  const triggerBtn = el("button", { class: "primary-btn", type: "button", text: "Run pipeline" });
  const updateRunEnabled = () => {
    triggerBtn.disabled = submitting || !dataset.value();
  };

  const refreshFiles = async () => {
    const data = await api.listRawFiles();
    dataset.fill(data.files);
    support.fill(data.files);
    updateRunEnabled();
  };

  // --- 1. Customer dataset (Node 1) -----------------------------------------
  const dataset = filePicker({
    id: "raw-select",
    kind: "dataset",
    label: "Customer dataset on the server",
    ctx,
    onChange: updateRunEnabled,
    onUploaded: refreshFiles,
  });

  // --- 2. Support threads (Node 3) ------------------------------------------
  const support = filePicker({
    id: "support-select",
    kind: "support",
    label: "Support-threads file on the server",
    optional: true,
    noneLabel: "None — run without support threads",
    ctx,
    onUploaded: refreshFiles,
  });

  // --- 3. Column mapping ----------------------------------------------------
  const mappingInput = el("input", { type: "file", id: "mapping-file", accept: ".json" });
  const mappingStatus = el("p", { class: "muted", text: "No mapping file — use the dataset's confirmed mapping if it has one." });
  const mappingClear = el("button", { class: "ghost-btn", type: "button", text: "Clear" });
  mappingClear.hidden = true;

  const setMapping = (value) => {
    mapping = value;
    confirmedFor = "";
    mappingClear.hidden = !value;
    if (!value) {
      mappingInput.value = "";
      mappingStatus.textContent =
        "No mapping file — use the dataset's confirmed mapping if it has one.";
    }
  };

  mappingInput.addEventListener("change", async () => {
    const file = mappingInput.files && mappingInput.files[0];
    if (!file) return setMapping(null);
    try {
      const parsed = await readMappingFile(file);
      setMapping({ name: file.name, parsed });
      mappingStatus.textContent = `${file.name}: ${parsed.proposed_mappings.length} column mapping(s) — confirmed for the selected dataset when you run.`;
      ctx.clearBanner();
    } catch (err) {
      setMapping(null);
      ctx.showBanner(err.message || String(err));
    }
  });
  mappingClear.addEventListener("click", () => setMapping(null));

  // Confirm the uploaded mapping for this dataset (once). A 409 means the
  // dataset already has a confirmed mapping, which the run would use instead.
  const applyMapping = async (rawPath) => {
    if (!mapping || confirmedFor === rawPath) return;
    status.textContent = `Applying mapping ${mapping.name}…`;
    const skeleton = await api.draftMapping(rawPath, false);
    const report = mappingFromFile(skeleton, mapping.parsed, mapping.name);
    try {
      await api.confirmMapping({
        report,
        raw_path: rawPath,
        node1_config_version: node1Select.value || null,
      });
    } catch (err) {
      if (err && err.status === 409) {
        throw new Error(
          `${errorText(err)}. Clear the mapping file to run with the existing mapping.`,
        );
      }
      throw err;
    }
    confirmedFor = rawPath;
  };

  // --- 4. Run settings ------------------------------------------------------
  const node1Select = el("select", { id: "node1-select" });
  const refreshConfigs = async () => {
    const node1 = await api.listNode1Configs();
    const previous = node1Select.value;
    clear(node1Select);
    node1Select.appendChild(el("option", { value: "", text: "Auto-detect (recommended)" }));
    for (const config of node1.configs || []) {
      const cores = (config.approved_core_keys || []).join(", ") || "no core keys";
      node1Select.appendChild(
        el("option", { value: config.version, text: `v${config.version} — ${cores}` }),
      );
    }
    if (previous) node1Select.value = previous;
  };

  // LLM use is opt-in per run and off by default; the toggles are enabled only
  // when the server reports a configured LLM (GET /health llm_available).
  const llmToggle = (id, label, hint) => {
    const input = el("input", { id, type: "checkbox", disabled: "" });
    const node = el("div", { class: "check-field" }, [
      input,
      el("label", { for: id }, [el("span", { text: label }), el("span", { class: "hint", text: hint })]),
    ]);
    return { input, node };
  };
  const llmNode5 = llmToggle(
    "llm-node5",
    "LLM explanation polish (Node 5)",
    "Rewrites each priority account's headline and summary. Validated against the data; " +
      "falls back to the template. Never changes a level, score, rank or confidence.",
  );
  const llmNode3 = llmToggle(
    "llm-node3",
    "LLM support-thread extraction (Node 3)",
    "Reads support threads with the LLM instead of the offline keyword extractor. " +
      "Only matters when support threads are supplied.",
  );
  const llmStatus = el("p", { class: "muted", text: "Checking LLM availability…" });
  const refreshLlm = async () => {
    try {
      const health = await api.health();
      const available = Boolean(health.llm_available);
      for (const toggle of [llmNode5, llmNode3]) toggle.input.disabled = !available;
      llmStatus.textContent = available
        ? `LLM available (${health.llm_model || "configured model"}). Off unless selected.`
        : "No LLM configured on the server (LLM_PROVIDER=none): runs are template-only.";
    } catch {
      llmStatus.textContent = "Could not check LLM availability; runs are template-only.";
    }
  };

  const onRun = async () => {
    if (submitting) return; // guard against double-submit
    const rawPath = dataset.value();
    if (!rawPath) {
      ctx.showBanner("Select or upload a customer dataset first.");
      return;
    }
    ctx.clearBanner();
    submitting = true;
    triggerBtn.disabled = true;
    triggerBtn.textContent = "Starting…";
    try {
      await applyMapping(rawPath);
      status.textContent = "";
      const supportData = await loadSupportData(support.value());
      const spec = { raw_path: rawPath };
      if (supportData !== undefined) spec.support_data = supportData;
      // Empty value means Auto-detect; only send an explicit override otherwise.
      if (node1Select.value) spec.node1_version = node1Select.value;
      if (llmNode5.input.checked) spec.llm_node5 = true;
      if (llmNode3.input.checked) spec.llm_node3 = true;
      const result = await api.triggerRun(spec);
      // Do not construct a run_id; use exactly what the API returned.
      ctx.navigateToRun(result.run_id);
    } catch (err) {
      // 4xx / auth / malformed JSON are shown inline; no silent retry.
      status.textContent = "";
      ctx.showBanner(errorText(err));
      submitting = false;
      updateRunEnabled();
      triggerBtn.textContent = "Run pipeline";
    }
  };
  triggerBtn.addEventListener("click", onRun);

  root.appendChild(
    step(1, "Customer dataset", false, [
      el("p", { class: "hint", text: "The customer table to score, as CSV or Excel." }),
      dataset.node,
    ], "first"),
  );
  root.appendChild(
    step(2, "Support threads", true, [
      el("p", {
        class: "hint",
        text: "A JSON array of support conversations. Adds qualitative signals to each account.",
      }),
      support.node,
    ]),
  );
  root.appendChild(
    step(3, "Column mapping", true, [
      el("p", {
        class: "hint",
        text:
          "Only needed when the dataset has no confirmed mapping yet. Load a mapping " +
          "JSON (a draft from `churn-survival map`, or a confirmed map_*.json); it is " +
          "confirmed for the selected dataset before the run starts. Without one, an " +
          "unknown dataset stops so you can map it on the next screen.",
      }),
      el("div", { class: "field" }, [
        el("label", { for: "mapping-file", text: "Mapping file (JSON)" }),
        el("div", { class: "auth-box" }, [mappingInput, mappingClear]),
        mappingStatus,
      ]),
    ]),
  );
  root.appendChild(
    step(4, "Run", false, [
      el("div", { class: "field" }, [
        el("label", { for: "node1-select", text: "Node 1 deployment config" }),
        node1Select,
        el("p", {
          class: "muted",
          text:
            "Auto-detect reads it from the dataset's confirmed mapping; override only to " +
            "force a specific approved-core vocabulary.",
        }),
      ]),
      el("fieldset", { class: "field" }, [
        el("legend", { text: "LLM use (optional)" }),
        llmStatus,
        llmNode5.node,
        llmNode3.node,
      ]),
      el("div", { class: "auth-box" }, [triggerBtn]),
      status,
    ]),
  );
  status.setAttribute("role", "status");

  try {
    await Promise.all([refreshFiles(), refreshConfigs(), refreshLlm()]);
    status.textContent = "";
  } catch (err) {
    status.textContent = `Could not list files: ${errorText(err)}`;
  }
}
