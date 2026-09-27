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
} from "../components/ui.js";

export async function renderReport(root, ctx) {
  clear(root);
  const runId = ctx.params.id;
  ctx.setTitle("Ranked report");
  if (!runId) {
    root.appendChild(
      section("No run selected", [
        el("p", {
          class: "muted",
          text: "Open a completed run from the history.",
        }),
      ]),
    );
    return;
  }

  const host = el("div");
  root.appendChild(host);

  let node4;
  let report;
  try {
    [node4, report] = await Promise.all([
      api.getRankedAccounts(runId),
      api.getReport(runId).catch(() => null),
    ]);
  } catch (err) {
    host.appendChild(section("Report unavailable", [errorText(err)]));
    return;
  }

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

  const distribution = report ? report.report.risk_distribution : null;
  if (distribution) {
    host.appendChild(
      section("Portfolio", [
        el("div", { class: "auth-box" }, [
          distChip("critical", distribution.critical),
          distChip("high", distribution.high),
          distChip("medium", distribution.medium),
          distChip("low", distribution.low),
          distChip("insufficient_data", distribution.insufficient_data),
        ]),
        el("p", {}, [
          el("a", {
            href: api.reportHtmlUrl(runId),
            target: "_blank",
            rel: "noopener",
            text: "Open static report",
          }),
        ]),
      ]),
    );
  }

  host.appendChild(
    section(
      "Ranked accounts",
      [
        node4.ranked_accounts.length
          ? rankedTable(node4.ranked_accounts, reportById, ctx)
          : el("p", { class: "muted", text: "No ranked accounts." }),
      ],
      "first",
    ),
  );

  host.appendChild(
    section("Insufficient data", [
      el("p", {
        class: "muted",
        text: "Insufficient data is not equivalent to low risk.",
      }),
      node4.insufficient_data_accounts.length
        ? rankedTable(node4.insufficient_data_accounts, reportById, ctx, {
            insufficient: true,
          })
        : el("p", { class: "muted", text: "None." }),
    ]),
  );
}

function distChip(level, count) {
  return el("span", { class: "auth-box" }, [
    riskBadge(level, level.replace(/_/g, " ")),
    el("span", { class: "num", text: String(count) }),
  ]);
}

function rankedTable(accounts, reportById, ctx, { insufficient = false } = {}) {
  const rows = accounts.map((account) => {
    const reportEntry = reportById.get(account.customer_id);
    const name = reportEntry ? reportEntry.display_name : account.customer_id;
    return el(
      "tr",
      {
        onclick: () => openDetail(ctx, account, reportEntry),
      },
      [
        el("td", {
          class: "num",
          text: account.rank === null ? "—" : String(account.rank),
        }),
        el("td", { text: name }),
        el(
          "td",
          {},
          insufficient
            ? riskBadge("insufficient_data", "insufficient data")
            : [riskBadge(account.combined_risk_level)],
        ),
        el("td", { class: "num", text: formatScore(account.combined_score) }),
        el("td", {
          class: "num",
          text: formatConfidence(account.combined_confidence),
        }),
      ],
    );
  });

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
    el("tbody", {}, rows),
  ]);
  return el("div", { class: "table-scroll" }, [table]);
}

function openDetail(ctx, account, reportEntry) {
  closeDrawer();
  const drawer = el("aside", { class: "drawer", id: "drawer" });
  const closeBtn = el("button", {
    class: "ghost-btn close",
    type: "button",
    text: "Close",
    onclick: closeDrawer,
  });

  const name = reportEntry ? reportEntry.display_name : account.customer_id;
  drawer.appendChild(closeBtn);
  drawer.appendChild(el("h2", { text: name }));
  drawer.appendChild(
    el("p", { class: "muted", text: account.customer_id }),
  );

  // Headline + summary come from Node 5, with a visible provenance tag.
  if (reportEntry) {
    drawer.appendChild(
      el("div", { class: "auth-box" }, [
        riskBadge(reportEntry.risk_level),
        provenanceTag(reportEntry.explanation_source),
      ]),
    );
    drawer.appendChild(el("h3", { text: reportEntry.headline }));
    drawer.appendChild(el("p", { text: reportEntry.summary }));
  } else {
    drawer.appendChild(el("p", { class: "muted", text: "No report text available." }));
  }

  // Decision fields copied verbatim from Node 4.
  drawer.appendChild(
    el("dl", { class: "kv" }, [
      el("dt", { text: "Rank" }),
      el("dd", { class: "num", text: account.rank === null ? "—" : String(account.rank) }),
      el("dt", { text: "Combined score" }),
      el("dd", { class: "num", text: formatScore(account.combined_score) }),
      el("dt", { text: "Confidence" }),
      el("dd", { class: "num", text: formatConfidence(account.combined_confidence) }),
    ]),
  );

  drawer.appendChild(
    el("h3", { class: "section-title", text: "Deterministic reasons" }),
  );
  drawer.appendChild(
    el(
      "ul",
      { class: "plain-list" },
      account.primary_reasons.length
        ? account.primary_reasons.map((r) =>
            el("li", {
              text: `${r.reason_type} (${r.source}, ${r.severity})`,
            }),
          )
        : [el("li", { class: "muted", text: "none" })],
    ),
  );

  if (reportEntry && reportEntry.primary_reasons.length) {
    drawer.appendChild(
      el("h3", { class: "section-title", text: "Reason statements" }),
    );
    drawer.appendChild(
      el(
        "ul",
        { class: "plain-list" },
        reportEntry.primary_reasons.map((r) =>
          el("li", { text: `${r.statement} (${r.severity})` }),
        ),
      ),
    );
  }

  const quant = account.quantitative;
  drawer.appendChild(
    el("h3", { class: "section-title", text: "Quantitative" }),
  );
  drawer.appendChild(
    el("dl", { class: "kv" }, [
      el("dt", { text: "Customer state" }),
      el("dd", { text: quant.customer_state }),
      el("dt", { text: "Risk score" }),
      el("dd", { class: "num", text: quant.risk_score === null ? "—" : quant.risk_score.toFixed(3) }),
      el("dt", { text: "90d survival" }),
      el("dd", {
        class: "num",
        text: quant.survival_prob_90d === null ? "—" : quant.survival_prob_90d.toFixed(3),
      }),
      el("dt", { text: "Top drivers" }),
      el("dd", { text: quant.top_drivers.length ? quant.top_drivers.join(", ") : "—" }),
    ]),
  );

  if (reportEntry && reportEntry.evidence.length) {
    drawer.appendChild(
      el("h3", { class: "section-title", text: "Evidence" }),
    );
    for (const item of reportEntry.evidence) {
      const children = [el("p", { text: item.description })];
      if (item.node3_reference) {
        children.push(
          el("blockquote", {
            text: `“${item.node3_reference.evidence_text}” — ${item.node3_reference.timestamp}`,
          }),
        );
      }
      drawer.appendChild(el("div", {}, children));
    }
  }

  if (reportEntry && reportEntry.recommended_action) {
    drawer.appendChild(
      el("h3", { class: "section-title", text: "Recommended action" }),
    );
    drawer.appendChild(el("p", { text: reportEntry.recommended_action }));
  }

  document.body.appendChild(drawer);
}

function closeDrawer() {
  const existing = document.getElementById("drawer");
  if (existing) existing.remove();
}
