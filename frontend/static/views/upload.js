// upload.js — pick a customer dataset (CSV/Excel) and optionally a support
// threads JSON, then POST /runs. The response is handled, never blocked on:
// 202 RUNNING -> status, STOPPED_NEEDS_MAPPING -> mapping (with the returned
// fingerprint), 4xx -> inline. Nothing is retried silently.
//
// Two input kinds (both from GET /raw-files):
//   - kind "dataset"  -> Node 1 customer data (raw_path)
//   - kind "support"  -> Node 3 support threads (support_data), optional

import { api, errorText } from "../api.js";
import { el, clear, formatBytes, section } from "../components/ui.js";

export async function renderUpload(root, ctx) {
  clear(root);

  let datasets = [];
  let supportFiles = [];
  let submitting = false;

  const status = el("p", { class: "muted", text: "Loading datasets…" });
  const datasetSelect = el("select", { id: "raw-select" });
  const supportSelect = el("select", { id: "support-select" });
  const node1Select = el("select", { id: "node1-select" });
  const node1Help = el("p", {
    class: "muted",
    text:
      "Deployment config used by Node 1. Auto-detect reads it from the matched " +
      "confirmed mapping; override only to force a specific approved-core vocabulary.",
  });
  const supportHelp = el("p", {
    class: "muted",
    text: "Optional. A support-threads JSON is passed as Node 3 support_data.",
  });
  const fileInput = el("input", {
    type: "file",
    id: "file-input",
    accept: ".csv,.xlsx,.xls,.json",
  });
  const triggerBtn = el("button", {
    class: "primary-btn",
    type: "button",
    text: "Run pipeline",
  });
  const uploadBtn = el("button", {
    class: "ghost-btn",
    type: "button",
    text: "Upload file",
  });
  const helper = el("p", {
    class: "muted",
    text:
      "The server runs the file at RAW_DATA_DIR; uploads are write-gated. " +
      "JSON files are support threads (Node 3), not customer datasets.",
  });

  const currentDataset = () => datasetSelect.value;

  const updateRunEnabled = () => {
    triggerBtn.disabled = submitting || !currentDataset();
  };

  const refreshFiles = async () => {
    clear(status);
    try {
      const [data, node1] = await Promise.all([
        api.listRawFiles(),
        api.listNode1Configs(),
      ]);
      datasets = data.files.filter((f) => f.kind === "dataset");
      supportFiles = data.files.filter((f) => f.kind === "support");

      clear(datasetSelect);
      if (!datasets.length) {
        datasetSelect.appendChild(
          el("option", { value: "", text: "No customer datasets available" }),
        );
      }
      for (const file of datasets) {
        datasetSelect.appendChild(
          el("option", {
            value: file.raw_path,
            text: `${file.name}  (${formatBytes(file.size_bytes)})`,
          }),
        );
      }

      clear(supportSelect);
      supportSelect.appendChild(el("option", { value: "", text: "None" }));
      for (const file of supportFiles) {
        supportSelect.appendChild(
          el("option", {
            value: file.raw_path,
            text: `${file.name}  (${formatBytes(file.size_bytes)})`,
          }),
        );
      }

      const previousNode1 = node1Select.value;
      clear(node1Select);
      node1Select.appendChild(
        el("option", { value: "", text: "Auto-detect (recommended)" }),
      );
      for (const config of node1.configs || []) {
        const cores = (config.approved_core_keys || []).join(", ") || "no core keys";
        node1Select.appendChild(
          el("option", { value: config.version, text: `v${config.version} — ${cores}` }),
        );
      }
      if (previousNode1) node1Select.value = previousNode1;
      updateRunEnabled();
    } catch (err) {
      status.textContent = `Could not list datasets: ${errorText(err)}`;
    }
  };

  const onUpload = async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      ctx.showBanner("Choose a file to upload first.");
      return;
    }
    ctx.clearBanner();
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading…";
    try {
      const result = await api.upload(file);
      const label = result.kind === "support" ? "support threads" : "customer dataset";
      ctx.showBanner(`Uploaded ${result.filename} as ${label}.`, "info");
      await refreshFiles();
      if (result.kind === "dataset") datasetSelect.value = result.raw_path;
      else supportSelect.value = result.raw_path;
      updateRunEnabled();
    } catch (err) {
      ctx.showBanner(errorText(err));
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Upload file";
    }
  };

  // Load and validate the selected support-threads JSON. Returns an array or
  // throws. The content is untrusted input; the server validates it as
  // SupportThread. Never derived from a task decision.
  const loadSupportData = async (rawPath) => {
    if (!rawPath) return undefined;
    const name = rawPath.replace(/\\/g, "/").split("/").pop();
    const text = await api.readRawFile(name);
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new Error(`${name} is not valid JSON.`);
    }
    if (!Array.isArray(parsed)) {
      throw new Error(
        `${name} must be a JSON array of support-thread objects.`,
      );
    }
    return parsed;
  };

  const onRun = async () => {
    if (submitting) return; // guard against double-submit
    const rawPath = currentDataset();
    if (!rawPath) {
      ctx.showBanner("Select or upload a customer dataset first.");
      return;
    }
    ctx.clearBanner();
    submitting = true;
    triggerBtn.disabled = true;
    triggerBtn.textContent = "Starting…";
    try {
      const supportData = await loadSupportData(supportSelect.value);
      const spec = { raw_path: rawPath };
      if (supportData !== undefined) spec.support_data = supportData;
      // Empty value means Auto-detect; only send an explicit override otherwise.
      if (node1Select.value) spec.node1_version = node1Select.value;
      const result = await api.triggerRun(spec);
      // Do not construct a run_id; use exactly what the API returned.
      ctx.navigateToRun(result.run_id);
    } catch (err) {
      // 4xx / auth / malformed JSON are shown inline; no silent retry.
      ctx.showBanner(errorText(err));
      submitting = false;
      updateRunEnabled();
      triggerBtn.textContent = "Run pipeline";
    }
  };

  datasetSelect.addEventListener("change", updateRunEnabled);
  uploadBtn.addEventListener("click", onUpload);
  triggerBtn.addEventListener("click", onRun);

  root.appendChild(
    section(
      "New run",
      [
        el("div", { class: "field" }, [
          el("label", { for: "raw-select", text: "Customer dataset (required)" }),
          datasetSelect,
        ]),
        el("div", { class: "field" }, [
          el("label", { for: "support-select", text: "Support threads (optional)" }),
          supportSelect,
          supportHelp,
        ]),
        el("div", { class: "field" }, [
          el("label", { for: "node1-select", text: "Node 1 deployment config" }),
          node1Select,
          node1Help,
        ]),
        el("div", { class: "field" }, [
          el("label", { for: "file-input", text: "…or upload a file" }),
          el("div", { class: "auth-box" }, [fileInput, uploadBtn]),
        ]),
        helper,
        el("div", { class: "auth-box" }, [triggerBtn]),
        status,
      ],
      "first",
    ),
  );

  await refreshFiles();
}
