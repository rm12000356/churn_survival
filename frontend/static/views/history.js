// history.js — GET /runs newest-first with a status filter (UI-only) and
// superseded_by lineage ("stopped → completed"). Timestamps are display only.

import { api, errorText } from "../api.js";
import { el, clear, section, riskBadge, relativeTime } from "../components/ui.js";

const FILTERS = [
  { key: "all", label: "All", status: "" },
  { key: "completed", label: "Completed", status: "COMPLETED" },
  { key: "attention", label: "Needs attention", status: "" },
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
  const filterHost = el("div", { class: "auth-box" });
  const listHost = el("div");
  const modelHost = el("div");

  const load = async () => {
    clear(listHost);
    listHost.appendChild(el("p", { class: "muted", text: "Loading…" }));
    try {
      const data = await api.listRuns({ limit: 100 });
      // "Needs attention" is a client-side selection of returned rows only; no
      // fields are derived.
      let runs = data.runs;
      if (activeFilter === "attention") {
        runs = runs.filter((r) => ATTENTION.has(r.execution_status));
      }
      clear(listHost);
      listHost.appendChild(runsTable(runs, ctx));
    } catch (err) {
      clear(listHost);
      listHost.appendChild(section("Could not list runs", [errorText(err)]));
    }
  };

  const renderFilters = () => {
    clear(filterHost);
    for (const filter of FILTERS) {
      const btn = el("button", {
        class: activeFilter === filter.key ? "primary-btn" : "ghost-btn",
        type: "button",
        text: filter.label,
        onclick: () => {
          activeFilter = filter.key;
          renderFilters();
          load();
        },
      });
      filterHost.appendChild(btn);
    }
  };

  const loadModels = async () => {
    try {
      const data = await api.listModels();
      clear(modelHost);
      if (!data.models.length) {
        modelHost.appendChild(el("p", { class: "muted", text: "No model artifacts." }));
        return;
      }
      modelHost.appendChild(
        el(
          "ul",
          { class: "plain-list" },
          data.models.map((v) => el("li", { class: "code", text: v })),
        ),
      );
    } catch (err) {
      clear(modelHost);
      modelHost.appendChild(el("p", { class: "muted", text: errorText(err) }));
    }
  };

  renderFilters();
  root.appendChild(
    section("Run history", [filterHost, listHost], "first"),
  );
  root.appendChild(section("Models", [modelHost]));
  await load();
  await loadModels();
}

function runsTable(runs, ctx) {
  if (!runs.length) return el("p", { class: "muted", text: "No runs." });
  const rows = runs.map((run) => {
    const cells = [
      el("td", { class: "code", text: run.run_id }),
      el("td", {}, [statusBadge(run.execution_status)]),
      el("td", { text: run.stage || "—" }),
      el("td", { text: run.raw_path || "—" }),
      el("td", { text: run.model_type || "—" }),
      el("td", { class: "num", text: fmt(run.n_ranked) }),
      el("td", { class: "num", text: fmt(run.n_insufficient) }),
      el("td", {
        class: "chain",
        text: run.superseded_by
          ? `stopped → completed (${run.superseded_by})`
          : "—",
      }),
      el("td", { class: "muted", text: relativeTime(run.created_at) }),
    ];
    return el(
      "tr",
      {
        onclick: () => {
          if (run.execution_status === "COMPLETED") {
            ctx.navigate(`#/runs/${run.run_id}/report`);
          } else if (run.execution_status === "STOPPED_NEEDS_MAPPING") {
            ctx.navigate(`#/runs/${run.run_id}/mapping`);
          } else {
            ctx.navigate(`#/runs/${run.run_id}`);
          }
        },
      },
      cells,
    );
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
        el("th", { class: "num", text: "Insuff." }),
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

function statusBadge(status) {
  const map = {
    COMPLETED: "stable",
    STOPPED_NEEDS_MAPPING: "watch",
    STOPPED_VALIDATION: "watch",
    FAILED: "critical",
    INTERRUPTED: "critical",
    RUNNING: "",
    PENDING: "",
  };
  const cls = map[status] || "";
  const level = { stable: "low", watch: "medium", critical: "critical" }[cls];
  if (level) return riskBadge(level, status.toLowerCase().replace(/_/g, " "));
  return el("span", { class: "muted", text: status.toLowerCase().replace(/_/g, " ") });
}
