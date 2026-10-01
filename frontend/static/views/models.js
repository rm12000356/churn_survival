// models.js — inspect persisted Node 2 model artifacts (never scores/fits).

import { api, errorText } from "../api.js";
import { el, clear, section, emptyState, loading, sentenceCase } from "../components/ui.js";

function cIndex(artifact) {
  const metrics = artifact.validation_metrics || {};
  const value =
    metrics.c_index !== undefined
      ? metrics.c_index
      : metrics.concordance_index;
  return value === undefined || value === null ? "—" : Number(value).toFixed(3);
}

function count(value) {
  return value === undefined || value === null ? "—" : Number(value).toLocaleString();
}

export async function renderModels(root, ctx) {
  clear(root);
  ctx.setTitle("Models");
  const host = el("div", {}, [loading("Loading models")]);
  root.appendChild(section("Model artifacts", [host], "first"));

  let data;
  try {
    data = await api.listModels();
  } catch (err) {
    clear(host);
    host.appendChild(emptyState("Could not list models", errorText(err)));
    return;
  }
  if (!data.models.length) {
    clear(host);
    host.appendChild(
      emptyState(
        "No models saved yet",
        "A survival model is saved when a run finishes with artifact persistence on.",
      ),
    );
    return;
  }

  // Fetch every artifact at once; a failed one shows dashes instead of blocking the rest.
  const artifacts = await Promise.all(
    data.models.map((version) => api.getModel(version).catch(() => null)),
  );
  const rows = data.models.map((version, i) => {
    const artifact = artifacts[i];
    return el("tr", { class: "plain" }, [
      el("td", { class: "code", text: version }),
      el("td", { text: artifact ? sentenceCase(artifact.model_type) : "—" }),
      el("td", { class: "num", text: artifact ? count(artifact.n_customers) : "—" }),
      el("td", { class: "num", text: artifact ? count(artifact.n_events) : "—" }),
      el("td", { class: "num", text: artifact ? cIndex(artifact) : "—" }),
    ]);
  });
  const table = el("table", {}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", { text: "Version" }),
        el("th", { text: "Type" }),
        el("th", { class: "num", text: "Customers" }),
        el("th", { class: "num", text: "Events" }),
        el("th", { class: "num", text: "C-index" }),
      ]),
    ]),
    el("tbody", {}, rows),
  ]);
  clear(host);
  host.appendChild(el("div", { class: "table-scroll" }, [table]));
}
