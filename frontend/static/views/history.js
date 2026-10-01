// history.js — GET /runs newest-first, paged, with a status filter and
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
  tableScroll,
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

const PAGE = 100;

export async function renderHistory(root, ctx) {
  clear(root);
  ctx.setTitle("Runs");

  let activeFilter = "all";
  let runs = [];
  let total = 0;
  let token = 0; // only the newest request may render (filter clicks can race)
  const filterHost = el("div", { class: "segmented", role: "group", "aria-label": "Filter runs" });
  const listHost = el("div");
  const modelHost = el("div");
  const moreBtn = el("button", { class: "ghost-btn more", type: "button", text: "Load older runs" });
  moreBtn.hidden = true;

  // "Completed" is filtered by the API; "Needs attention" spans several
  // statuses, so it selects from the returned rows. No fields are derived.
  const fetchPage = (offset) =>
    api.listRuns({
      limit: PAGE,
      offset,
      status: activeFilter === "completed" ? "COMPLETED" : "",
    });
  const visible = () =>
    activeFilter === "attention" ? runs.filter((r) => ATTENTION.has(r.execution_status)) : runs;
  const show = () => {
    clear(listHost);
    listHost.appendChild(runsTable(visible(), ctx, activeFilter));
    moreBtn.hidden = runs.length >= total;
  };

  const load = async () => {
    const mine = ++token;
    clear(listHost);
    moreBtn.hidden = true;
    listHost.appendChild(loading("Loading runs"));
    try {
      const data = await fetchPage(0);
      if (mine !== token) return;
      runs = data.runs;
      total = data.total;
      show();
    } catch (err) {
      if (mine !== token) return;
      clear(listHost);
      listHost.appendChild(emptyState("Could not list runs", errorText(err)));
    }
  };

  moreBtn.addEventListener("click", async () => {
    const mine = token;
    moreBtn.disabled = true;
    try {
      const data = await fetchPage(runs.length);
      if (mine !== token) return;
      runs = runs.concat(data.runs);
      total = data.total;
      show();
    } catch (err) {
      ctx.showBanner(errorText(err));
    } finally {
      moreBtn.disabled = false;
    }
  });

  // Filter buttons are built once and updated in place, so focus survives.
  const filterButtons = FILTERS.map((filter) => {
    const button = el("button", {
      type: "button",
      text: filter.label,
      onclick: () => {
        activeFilter = filter.key;
        renderFilters();
        load();
      },
    });
    button.dataset.key = filter.key;
    filterHost.appendChild(button);
    return button;
  });
  const renderFilters = () => {
    for (const button of filterButtons) {
      button.setAttribute("aria-pressed", button.dataset.key === activeFilter ? "true" : "false");
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
  root.appendChild(section("Run history", [filterHost, listHost, moreBtn], "first"));
  root.appendChild(section("Models", [modelHost]));
  await Promise.all([load(), loadModels()]);
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
    return actionRow({}, cells, () => {
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
  return tableScroll("Run history", table);
}

function fmt(value) {
  return value === null || value === undefined ? "—" : String(value);
}
