// filePicker.js — one input block per file role: choose a file already on the
// server (GET /raw-files, filtered by kind) or upload a new one right here.
// Each section of a screen gets its own picker, so a customer dataset (Node 1)
// and support threads (Node 3) are never uploaded through the same control.

import { api, errorText } from "../api.js";
import { el, clear, formatBytes } from "./ui.js";

const EXTENSIONS = {
  dataset: [".csv", ".xlsx", ".xls"],
  support: [".json"],
};

const KIND_LABEL = {
  dataset: "customer dataset (CSV/Excel)",
  support: "support-threads file (JSON)",
};

function extensionOf(name) {
  const dot = name.lastIndexOf(".");
  return dot === -1 ? "" : name.slice(dot).toLowerCase();
}

// options: { id, kind: "dataset"|"support", label, optional, noneLabel, ctx,
//            onChange(rawPath), onUploaded(result) }
// `label` names the server-file <select>; the upload input gets its own label.
export function filePicker({
  id,
  kind,
  label,
  optional = false,
  noneLabel = "None",
  ctx,
  onChange,
  onUploaded,
}) {
  const select = el("select", { id });
  const fileInput = el("input", {
    type: "file",
    id: `${id}-file`,
    accept: EXTENSIONS[kind].join(","),
  });
  const uploadBtn = el("button", { class: "ghost-btn", type: "button", text: "Upload" });

  const fill = (files, selected) => {
    const previous = selected ?? select.value;
    clear(select);
    const mine = files.filter((file) => file.kind === kind);
    if (optional) select.appendChild(el("option", { value: "", text: noneLabel }));
    else if (!mine.length) {
      select.appendChild(el("option", { value: "", text: `No ${KIND_LABEL[kind]} yet — upload one` }));
    }
    for (const file of mine) {
      select.appendChild(
        el("option", {
          value: file.raw_path,
          text: `${file.name}  (${formatBytes(file.size_bytes)})`,
        }),
      );
    }
    if (previous && mine.some((file) => file.raw_path === previous)) select.value = previous;
  };

  const upload = async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      ctx.showBanner(`Choose a ${KIND_LABEL[kind]} to upload first.`);
      return;
    }
    if (!EXTENSIONS[kind].includes(extensionOf(file.name))) {
      ctx.showBanner(
        `${file.name} is not a ${KIND_LABEL[kind]}; upload it in the matching section.`,
      );
      return;
    }
    ctx.clearBanner();
    uploadBtn.disabled = true;
    uploadBtn.textContent = "Uploading…";
    try {
      const result = await api.upload(file);
      fileInput.value = "";
      ctx.showBanner(`Uploaded ${result.filename}.`, "info");
      if (onUploaded) await onUploaded(result);
      select.value = result.raw_path;
      if (onChange) onChange(select.value);
    } catch (err) {
      ctx.showBanner(errorText(err));
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.textContent = "Upload";
    }
  };

  select.addEventListener("change", () => onChange && onChange(select.value));
  uploadBtn.addEventListener("click", upload);

  const node = el("div", { class: "field" }, [
    el("label", { for: id, text: label || `Choose a ${KIND_LABEL[kind]} on the server` }),
    select,
    el("div", { class: "auth-box" }, [
      el("label", { for: `${id}-file`, class: "muted" }, [
        "or upload ",
        el("span", { class: "sr-only", text: `a new ${KIND_LABEL[kind]}` }),
      ]),
      fileInput,
      uploadBtn,
    ]),
  ]);

  return {
    node,
    fill,
    value: () => select.value,
  };
}

// Load a support-threads JSON from the server and check it is an array; the
// server validates each entry as SupportThread. Returns undefined for "none".
export async function loadSupportData(rawPath) {
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
    throw new Error(`${name} must be a JSON array of support-thread objects.`);
  }
  return parsed;
}
