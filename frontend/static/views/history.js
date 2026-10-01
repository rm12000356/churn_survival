// history.js — GET /runs newest-first with a status filter (UI-only) and
// superseded_by lineage ("stopped → completed"). Timestamps are display only.

import { api, errorText } from "../api.js";
import {
  el,
  clear,
  section,
  statusBadge,
  relativeTime,
  actionRow,
  emptyState,
  loading,
} from "../components/ui.js";

const FILTERS = [
  { key: "all", label: "All" },
  { key: "completed", label: "Completed" },
  { key: "attention", label: "Needs attention" },
];

const ATTENTION = new Set([
  "STOPPED_NEEDS_MAPPING",
  "STOPPED_VALIDATION",
  "FAILED",
  "INTERRUPTED",
]);

export async function renderHistory(root, ctx) {
  clear(root);
  ctx.setTitle("Runs");

  let activeFilter = "all";
  const filterHost = el("div", { class: "segmented", role: "group", "aria-label": "Filter runs" });
  const listHost = el("div");
  const modelHost = el("div");

  const load = async () => {
    clear(listHost);
    listHost.appendChild(loading("Loading runs"));
    try {
      const data = await api.listRuns({ limit: 100 });
      // Filters are a client-side selection of returned rows only; no fields
      // are derived.
      let runs = data.runs;
      if (activeFilter === "attention") {
        runs = runs.filter((r) => ATTENTION.has(r.execution_status));
      } else if (activeFilter === "completed") {
        runs = runs.filter((r) => r.execution_status === "COMPLETED");
      }
      clear(listHost);
      listHost.appendChild(runsTable(runs, ctx, activeFilter));
    } catch (err) {
      clear(listHost);
      listHost.appendChild(emptyState("Could not list runs", errorText(err)));
    }
  };

  const renderFilters = () => {
    clear(filterHost);
    for (const filter of FILTERS) {
      filterHost.appendChild(
        el("button", {
          type: "button",
          "aria-pressed": activeFilter === filter.key ? "true" : "false",
          text: filter.label,
          onclick: () => {
            activeFilter = filter.key;
            renderFilters();
            load();
          },
        }),
      );
    }
  };

  const loadModels = async () => {
    try {
      const data = await api.listModels();
      clear(modelHost);
      if (!data.models.length) {
        modelHost.appendChild(el("p", { class: "muted", text: "No model artifacts saved yet." }));
        return;
      }
      modelHost.appendChild(
        el(
          "ul",
          { class: "plain-list" },
          data.models.map((v) =>
            el("li", {}, [el("a", { class: "code", href: "#/models", text: v })]),
          ),
        ),
      );
    } catch (err) {
      clear(modelHost);
      modelHost.appendChild(el("p", { class: "muted", text: errorText(err) }));
    }
  };

  renderFilters();
  root.appendChild(section("Run history", [filterHost, listHost], "first"));
  root.appendChild(section("Models", [modelHost]));
  await load();
  await loadModels();
}

function runsTable(runs, ctx, activeFilter) {
  if (!runs.length) {
    return activeFilter === "all"
      ? emptyState(
          "No runs yet",
          "Start a run from Upload and it will appear here.",
          el("a", { href: "#/upload", text: "Start a run" }),
        )
      : el("p", { class: "muted", text: "No runs match this filter." });
  }
  const rows = runs.map((run) => {
    const cells = [
      el("td", { class: "code truncate", text: run.run_id, title: run.run_id }),
      el("td", {}, [statusBadge(run.execution_status)]),
      el("td", { text: run.stage || "—" }),
      el("td", { class: "truncate", text: run.raw_path || "—", title: run.raw_path || "" }),
      el("td", { text: run.model_type || "—" }),
      el("td", { class: "num", text: fmt(run.n_ranked) }),
      el("td", { class: "num", text: fmt(run.n_insufficient) }),
      el("td", {
        class: "chain",
        text: run.superseded_by ? `Superseded by ${run.superseded_by}` : "—",
      }),
      el("td", { class: "muted", text: relativeTime(run.created_at), title: run.created_at || "" }),
    ];
    return actionRow({ "aria-label": `Open run ${run.run_id}` }, cells, () => {
      if (run.execution_status === "COMPLETED") {
        ctx.navigate(`#/runs/${run.run_id}/report`);
      } else if (run.execution_status === "STOPPED_NEEDS_MAPPING") {
        ctx.navigate(`#/runs/${run.run_id}/mapping`);
      } else {
        ctx.navigate(`#/runs/${run.run_id}`);
      }
    });
  });

  const table = el("table", {}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", { text: "Run" }),
        el("th", { text: "Status" }),
        el("th", { text: "Stage" }),
        el("th", { text: "Input" }),
        el("th", { text: "Model" }),
        el("th", { class: "num", text: "Ranked" }),
        el("th", { class: "num", text: "Insufficient" }),
        el("th", { text: "Lineage" }),
        el("th", { text: "Started" }),
      ]),
    ]),
    el("tbody", {}, rows),
  ]);
  return el("div", { class: "table-scroll" }, [table]);
}

function fmt(value) {
  return value === null || value === undefined ? "—" : String(value);
}
