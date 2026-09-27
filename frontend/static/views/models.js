// models.js — inspect persisted Node 2 model artifacts (never scores/fits).

import { api, errorText } from "../api.js";
import { el, clear, section } from "../components/ui.js";

function cIndex(artifact) {
  const metrics = artifact.validation_metrics || {};
  const value =
    metrics.c_index !== undefined
      ? metrics.c_index
      : metrics.concordance_index;
  return value === undefined || value === null ? "—" : Number(value).toFixed(3);
}

export async function renderModels(root, ctx) {
  clear(root);
  ctx.setTitle("Models");
  const host = el("div");
  root.appendChild(section("Model artifacts", [host], "first"));

  try {
    const data = await api.listModels();
    if (!data.models.length) {
      host.appendChild(el("p", { class: "muted", text: "No model artifacts persisted." }));
      return;
    }
    const rows = [];
    for (const version of data.models) {
      let artifact = null;
      try {
        artifact = await api.getModel(version);
      } catch {
        artifact = null;
      }
      rows.push(
        el("tr", { class: "plain" }, [
          el("td", { class: "code", text: version }),
          el("td", { text: artifact ? artifact.model_type : "—" }),
          el("td", { text: artifact ? String(artifact.n_customers ?? "—") : "—" }),
          el("td", { text: artifact ? String(artifact.n_events ?? "—") : "—" }),
          el("td", {
            text: artifact ? cIndex(artifact) : "—",
          }),
        ]),
      );
    }
    const table = el("table", {}, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Version" }),
          el("th", { text: "Type" }),
          el("th", { text: "Customers" }),
          el("th", { text: "Events" }),
          el("th", { text: "C-index" }),
        ]),
      ]),
      el("tbody", {}, rows),
    ]);
    host.appendChild(el("div", { class: "table-scroll" }, [table]));
  } catch (err) {
    host.appendChild(el("p", { text: errorText(err) }));
  }
}
