// report.js — ranked accounts + insufficient-data section + account detail.
// Every risk level/score/rank/confidence value is read verbatim from
// GET /runs/{id}/ranked-accounts; the explanation text/provenance comes from
// GET /runs/{id}/report. Nothing is recomputed or re-sorted client-side.

import { api, errorText } from "../api.js";
import {
  el,
  clear,
  section,
  riskBadge,
  provenanceTag,
  formatScore,
  formatConfidence,
  levelClass,
  horizonBand,
  meter,
  actionRow,
  emptyState,
  loading,
  sentenceCase,
  downloadCsv,
  scrollToNode,
  tableScroll,
} from "../components/ui.js";

export async function renderReport(root, ctx) {
  clear(root);
  const runId = ctx.params.id;
  ctx.setTitle("Ranked report");
  if (!runId) {
    root.appendChild(
      emptyState(
        "No run selected",
        "Open a completed run from the history.",
        el("a", { href: "#/runs", text: "Go to runs" }),
      ),
    );
    return;
  }

  const host = el("div");
  root.appendChild(host);
  host.appendChild(loading("Loading report"));
  ctx.onTeardown(closeDrawer);

  let node4;
  let report;
  try {
    [node4, report] = await Promise.all([
      api.getRankedAccounts(runId),
      api.getReport(runId).catch(() => null),
    ]);
  } catch (err) {
    clear(host);
    host.appendChild(emptyState("Report unavailable", errorText(err)));
    return;
  }
  clear(host);

  // Index the Node 5 report by customer_id for the detail drawer (headline,
  // summary, provenance, evidence). Falls back to Node 4 fields if absent.
  const reportById = new Map();
  if (report) {
    for (const account of [
      ...report.report.priority_accounts,
      ...report.report.insufficient_data_accounts,
    ]) {
      reportById.set(account.customer_id, account);
    }
  }

  const ranked = accountList(node4.ranked_accounts, reportById, { runId, name: "ranked" });
  const insufficient = accountList(node4.insufficient_data_accounts, reportById, {
    runId,
    name: "insufficient-data",
    insufficient: true,
  });

  const distribution = report ? report.report.risk_distribution : null;
  if (distribution) {
    // Legend entries filter the lists below; nothing is recounted client-side.
    const onSelect = (level) => {
      const target = level === "insufficient_data" ? insufficient : ranked;
      if (level !== "insufficient_data") ranked.setLevel(level);
      scrollToNode(target.node);
    };
    // The static report needs the API key header, which a plain link cannot
    // send, so it is fetched with the key and opened as a blob.
    const openStatic = el("button", {
      class: "ghost-btn",
      type: "button",
      text: "Open static report",
      onclick: async () => {
        try {
          await api.openReportHtml(runId);
        } catch (err) {
          ctx.showBanner(errorText(err));
        }
      },
    });
    // Re-mapping lets a person choose which columns feed the model (§1.3a);
    // it confirms a new mapping and starts a new run, this one is kept.
    const remap = el("a", {
      class: "ghost-btn",
      href: `#/runs/${runId}/mapping`,
      text: "Re-map dataset / choose model features",
    });
    host.appendChild(
      section(
        "Portfolio",
        [
          horizonBand(distribution, onSelect),
          el("p", { class: "row" }, [openStatic, remap]),
        ],
        "first",
      ),
    );
  }

  host.appendChild(
    section(
      "Ranked accounts",
      [
        node4.ranked_accounts.length
          ? ranked.node
          : el("p", { class: "muted", text: "No ranked accounts in this run." }),
      ],
      "",
      node4.ranked_accounts.length,
    ),
  );

  host.appendChild(
    section(
      "Insufficient data",
      [
        el("p", {
          class: "muted",
          text:
            "These accounts could not be assessed. Insufficient data is not the same as low risk.",
        }),
        node4.insufficient_data_accounts.length
          ? insufficient.node
          : el("p", { class: "muted", text: "None." }),
      ],
      "",
      node4.insufficient_data_accounts.length,
    ),
  );

  // Already-churned customers (phase 10): listed verbatim from Node 4, never ranked.
  const churned = node4.churned_accounts ?? [];
  if (churned.length) {
    host.appendChild(
      section(
        "Already churned",
        [
          el("p", {
            class: "muted",
            text: "These customers had already churned by the reference date. They are not ranked.",
          }),
          churnedList(churned),
        ],
        "",
        churned.length,
      ),
    );
  }
}

// Paged list of churned customers, in API order. Display only.
function churnedList(accounts) {
  const list = el("ul", { class: "plain-list code", id: "churned-list" });
  const more = el("button", { class: "ghost-btn", type: "button", text: "Show more" });
  let shown = 0;
  const showNext = () => {
    for (const account of accounts.slice(shown, shown + PAGE_SIZE)) {
      const tenure =
        account.tenure_days === null || account.tenure_days === undefined
          ? ""
          : ` (tenure ${account.tenure_days} days)`;
      list.appendChild(el("li", { text: `${account.customer_id}${tenure}` }));
    }
    shown = Math.min(accounts.length, shown + PAGE_SIZE);
    more.hidden = shown >= accounts.length;
    more.textContent = `Show more (${shown} of ${accounts.length})`;
  };
  more.onclick = showNext;
  showNext();
  return el("div", {}, [list, el("p", {}, [more])]);
}

const PAGE_SIZE = 100;
const FILTER_LEVELS = ["critical", "high", "medium", "low"];

// A searchable, level-filterable, paged view of an account list. It only
// SELECTS rows: the API's order is kept and no value is derived or re-sorted.
function accountList(accounts, reportById, { runId, name, insufficient = false }) {
  let query = "";
  let level = "";
  let limit = PAGE_SIZE;
  let debounce = null;

  const nameOf = (account) => {
    const entry = reportById.get(account.customer_id);
    return entry ? entry.display_name : account.customer_id;
  };
  const matches = () =>
    accounts.filter(
      (a) =>
        (!level || a.combined_risk_level === level) &&
        (!query ||
          nameOf(a).toLowerCase().includes(query) ||
          a.customer_id.toLowerCase().includes(query)),
    );

  const search = el("input", {
    type: "search",
    placeholder: "Search account or ID",
    "aria-label": insufficient ? "Search insufficient-data accounts" : "Search ranked accounts",
    oninput: (e) => {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(() => {
        query = e.target.value.trim().toLowerCase();
        limit = PAGE_SIZE;
        render();
      }, 120);
    },
  });

  // Level chips: only levels that actually occur in this list. Shown even for a
  // single level, so a legend filter always has an "All levels" way back.
  const present = FILTER_LEVELS.filter((l) => accounts.some((a) => a.combined_risk_level === l));
  const chips = insufficient || !present.length
    ? null
    : el("div", { class: "segmented", role: "group", "aria-label": "Filter by risk level" });

  const count = el("span", { class: "muted", role: "status" });
  const exportBtn = el("button", {
    class: "ghost-btn",
    type: "button",
    text: "Export CSV",
    title: "Download the rows matching the current search and filter",
    onclick: () => {
      // Apply a search still waiting on its debounce, so the export matches the box.
      window.clearTimeout(debounce);
      query = search.value.trim().toLowerCase();
      const rows = matches().map((a) => [
        a.rank,
        a.customer_id,
        nameOf(a),
        insufficient ? "insufficient_data" : a.combined_risk_level,
        a.combined_score,
        a.combined_confidence,
      ]);
      downloadCsv(
        `horizon-${runId}-${name}${level ? `-${level}` : ""}.csv`,
        ["rank", "customer_id", "account", "risk_level", "combined_score", "combined_confidence"],
        rows,
      );
    },
  });

  const tbody = el("tbody");
  const more = el("button", {
    class: "ghost-btn more",
    type: "button",
    onclick: () => {
      const firstNew = limit;
      limit += PAGE_SIZE;
      render();
      // This button may now be hidden; keep focus on the first newly shown row.
      const row = tbody.rows[firstNew];
      const target = row && row.querySelector("button");
      if (target) target.focus();
    },
  });

  // Chips are built once and updated in place, so the focused chip survives.
  const chipButtons = chips
    ? ["", ...present].map((value) => {
        const button = el("button", {
          type: "button",
          dataset: { level: value },
          text: value ? sentenceCase(value) : "All levels",
          onclick: () => setLevel(value),
        });
        chips.appendChild(button);
        return button;
      })
    : [];
  const renderChips = () => {
    for (const button of chipButtons) {
      button.setAttribute("aria-pressed", button.dataset.level === level ? "true" : "false");
    }
  };

  const render = () => {
    const rows = matches();
    const shown = rows.slice(0, limit);
    clear(tbody);
    for (const account of shown) tbody.appendChild(accountRow(account, reportById, insufficient));
    if (!rows.length) {
      tbody.appendChild(
        el("tr", { class: "plain" }, [
          el("td", { colspan: "5", class: "muted", text: "No accounts match this search." }),
        ]),
      );
    }
    const total = accounts.length.toLocaleString();
    count.textContent = rows.length === accounts.length
      ? `Showing ${shown.length.toLocaleString()} of ${total}`
      : `Showing ${shown.length.toLocaleString()} of ${rows.length.toLocaleString()} matches (${total} total)`;
    more.hidden = rows.length <= limit;
    more.textContent = `Show ${Math.min(PAGE_SIZE, rows.length - limit).toLocaleString()} more`;
    exportBtn.disabled = !rows.length;
  };

  function setLevel(value) {
    level = value;
    limit = PAGE_SIZE;
    renderChips();
    render();
  }

  const table = el("table", {}, [
    el("thead", {}, [
      el("tr", {}, [
        el("th", { class: "num", text: "Rank" }),
        el("th", { text: "Account" }),
        el("th", { text: "Risk level" }),
        el("th", { class: "num", text: "Combined score" }),
        el("th", { class: "num", text: "Confidence" }),
      ]),
    ]),
    tbody,
  ]);

  const node = el("div", { class: "account-list" }, [
    el("div", { class: "toolbar" }, [search, chips, el("span", { class: "spacer" }), count, exportBtn]),
    tableScroll(insufficient ? "Insufficient-data accounts" : "Ranked accounts", table),
    more,
  ]);
  renderChips();
  render();
  return { node, setLevel };
}

// Row cells are the API's own values; only the rail/badge class is mapped.
function accountRow(account, reportById, insufficient) {
  const reportEntry = reportById.get(account.customer_id);
  const name = reportEntry ? reportEntry.display_name : account.customer_id;
  const level = insufficient ? "insufficient_data" : account.combined_risk_level;
  return actionRow(
    { class: `rail ${levelClass(level)}` },
    [
      el("td", {
        class: "num rank",
        text: account.rank === null ? "—" : String(account.rank),
      }),
      el("td", { class: "truncate", text: name, title: name }),
      el("td", {}, [riskBadge(level)]),
      el("td", { class: "num" }, [
        meter(account.combined_score, formatScore(account.combined_score)),
      ]),
      el("td", {
        class: "num",
        text: formatConfidence(account.combined_confidence),
      }),
    ],
    () => openDetail(account, reportEntry),
    { openCell: 1 },
  );
}

let returnFocus = null;

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function onDrawerKey(event) {
  if (event.key === "Escape") {
    closeDrawer();
    return;
  }
  if (event.key !== "Tab") return;
  // Keep Tab inside the modal drawer.
  const drawer = document.getElementById("drawer");
  if (!drawer) return;
  const items = [...drawer.querySelectorAll(FOCUSABLE)];
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function setBackgroundInert(value) {
  // The page behind the modal drawer is unreachable while it is open.
  for (const node of document.querySelectorAll(".layout, .skip-link")) node.inert = value;
}

function openDetail(account, reportEntry) {
  closeDrawer();
  returnFocus = document.activeElement;
  const name = reportEntry ? reportEntry.display_name : account.customer_id;

  const scrim = el("div", { class: "scrim", id: "drawer-scrim", onclick: closeDrawer });
  const drawer = el("aside", {
    class: "drawer",
    id: "drawer",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "drawer-title",
  });
  const closeBtn = el("button", {
    class: "ghost-btn close",
    type: "button",
    text: "Close",
    onclick: closeDrawer,
  });

  drawer.appendChild(closeBtn);
  drawer.appendChild(el("h2", { id: "drawer-title", text: name }));
  drawer.appendChild(el("p", { class: "muted code", text: account.customer_id }));

  // Headline + summary come from Node 5, with a visible provenance tag.
  if (reportEntry) {
    drawer.appendChild(
      el("div", { class: "row" }, [
        riskBadge(reportEntry.risk_level),
        provenanceTag(reportEntry.explanation_source),
      ]),
    );
    drawer.appendChild(el("p", { class: "headline", text: reportEntry.headline }));
    drawer.appendChild(el("p", { text: reportEntry.summary }));
  } else {
    drawer.appendChild(el("p", { class: "muted", text: "No report text available." }));
  }

  // Decision fields copied verbatim from Node 4.
  drawer.appendChild(
    el("dl", { class: "facts" }, [
      fact("Rank", account.rank === null ? "—" : String(account.rank)),
      fact("Combined score", formatScore(account.combined_score)),
      fact("Confidence", formatConfidence(account.combined_confidence)),
    ]),
  );

  if (reportEntry && reportEntry.recommended_action) {
    drawer.appendChild(el("h3", { class: "section-title", text: "Recommended action" }));
    drawer.appendChild(el("p", { class: "action", text: reportEntry.recommended_action }));
  }

  if (reportEntry && reportEntry.primary_reasons.length) {
    drawer.appendChild(el("h3", { class: "section-title", text: "Why this account" }));
    drawer.appendChild(
      el(
        "ul",
        { class: "plain-list" },
        reportEntry.primary_reasons.map((r) =>
          el("li", {}, [r.statement, el("span", { class: "muted", text: ` (${r.severity})` })]),
        ),
      ),
    );
  }

  drawer.appendChild(el("h3", { class: "section-title", text: "Deterministic reasons" }));
  drawer.appendChild(
    el(
      "ul",
      { class: "plain-list" },
      account.primary_reasons.length
        ? account.primary_reasons.map((r) =>
            el("li", {}, [
              el("span", { class: "code", text: r.reason_type }),
              el("span", { class: "muted", text: ` (${r.source}, ${r.severity})` }),
            ]),
          )
        : [el("li", { class: "muted", text: "None recorded." })],
    ),
  );

  const quant = account.quantitative;
  drawer.appendChild(el("h3", { class: "section-title", text: "Survival model" }));
  drawer.appendChild(
    el("dl", { class: "kv" }, [
      el("dt", { text: "Customer state" }),
      el("dd", { text: sentenceCase(quant.customer_state) }),
      el("dt", { text: "Risk score" }),
      el("dd", { class: "num", text: quant.risk_score === null ? "—" : quant.risk_score.toFixed(3) }),
      el("dt", { text: "90-day survival" }),
      el("dd", {
        class: "num",
        text: quant.survival_prob_90d === null ? "—" : quant.survival_prob_90d.toFixed(3),
      }),
      el("dt", { text: "Top drivers" }),
      el("dd", { text: quant.top_drivers.length ? quant.top_drivers.join(", ") : "—" }),
      ...forwardFacts(quant),
    ]),
  );

  const factors = account.confidence_factors;
  if (factors) {
    drawer.appendChild(el("h3", { class: "section-title", text: "Confidence breakdown" }));
    const rows = [
      ["Model quality", factors.model],
      ["Estimate precision", factors.precision],
      ["Customer history", factors.history],
      ["Quantitative confidence", factors.quantitative],
    ];
    if (factors.support !== null && factors.support !== undefined) {
      rows.push(["Support evidence", factors.support]);
    }
    drawer.appendChild(
      el(
        "dl",
        { class: "kv", id: "confidence-factors" },
        rows.flatMap(([label, value]) => [
          el("dt", { text: label }),
          el("dd", { class: "num", text: formatConfidence(value) }),
        ]),
      ),
    );
  }

  if (reportEntry && reportEntry.evidence.length) {
    drawer.appendChild(el("h3", { class: "section-title", text: "Evidence" }));
    for (const item of reportEntry.evidence) {
      const children = [el("p", { text: item.description })];
      if (item.node3_reference) {
        children.push(
          el("blockquote", {}, [
            `“${item.node3_reference.evidence_text}”`,
            el("br"),
            el("span", { class: "code", text: item.node3_reference.timestamp }),
          ]),
        );
      }
      drawer.appendChild(el("div", {}, children));
    }
  }

  document.body.appendChild(scrim);
  document.body.appendChild(drawer);
  setBackgroundInert(true);
  document.addEventListener("keydown", onDrawerKey);
  closeBtn.focus();
}

// Forward-looking survival facts (phase 10), shown only when the API sends them.
function forwardFacts(quant) {
  const rows = [];
  if (quant.churn_prob_90d_forward !== null && quant.churn_prob_90d_forward !== undefined) {
    rows.push(
      el("dt", { text: "Churn in next 90 days" }),
      el("dd", { class: "num", text: quant.churn_prob_90d_forward.toFixed(3) }),
    );
  }
  if (quant.lift_vs_base !== null && quant.lift_vs_base !== undefined) {
    rows.push(
      el("dt", { text: "Lift vs average" }),
      el("dd", { class: "num", text: `${quant.lift_vs_base.toFixed(2)}×` }),
    );
  }
  if (quant.forward_status === "beyond_follow_up") {
    rows.push(
      el("dt", { text: "Forward estimate" }),
      el("dd", { text: "Beyond the model's observed follow-up" }),
    );
  }
  return rows;
}

function fact(label, value) {
  return el("div", {}, [el("dt", { text: label }), el("dd", { class: "num", text: value })]);
}

function closeDrawer() {
  const existing = document.getElementById("drawer");
  if (!existing) return;
  existing.remove();
  const scrim = document.getElementById("drawer-scrim");
  if (scrim) scrim.remove();
  setBackgroundInert(false);
  document.removeEventListener("keydown", onDrawerKey);
  if (returnFocus && document.contains(returnFocus)) returnFocus.focus();
  returnFocus = null;
}
