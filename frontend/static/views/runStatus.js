// runStatus.js — poll GET /runs/{id} until terminal, then branch. Stage and
// execution_status are rendered verbatim; no progress percentage is invented.

import { api, errorText } from "../api.js";
import { el, clear, section, statusBadge, emptyState } from "../components/ui.js";
import { stageTracker } from "../components/stageTracker.js";

const POLL_MS = 2000;
// Transient poll failures (network, 5xx, 429) back off and retry this many times
// in a row before the screen reports the run as unreachable.
const MAX_POLL_RETRIES = 5;
const TERMINAL = new Set([
  "COMPLETED",
  "STOPPED_VALIDATION",
  "STOPPED_NEEDS_MAPPING",
  "FAILED",
  "INTERRUPTED",
]);

export function renderRunStatus(root, ctx) {
  clear(root);
  const runId = ctx.params.id;
  if (!runId) {
    ctx.setTitle("Run");
    root.appendChild(
      emptyState(
        "No run selected",
        "Open a run from the history, or start one from Upload.",
        el("a", { href: "#/upload", text: "Start a run" }),
      ),
    );
    return;
  }
  ctx.setTitle(`Run ${runId}`);

  let timer = null;
  let stopped = false;
  let failures = 0;
  let lastAnnounced = "";

  const stageHost = el("div");
  const statusLine = el("div", { class: "status-line" }, [
    el("span", { class: "loading", text: "Loading run" }),
  ]);
  // Announce only real changes; rebuilding a live region on every poll would
  // re-read it every 2 s.
  const announcer = el("p", { class: "sr-only", role: "status", "aria-live": "polite" });
  const summaryHost = el("div");

  const stop = () => {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    timer = null;
  };
  ctx.onTeardown(stop);

  const render = (summary) => {
    clear(stageHost);
    stageHost.appendChild(
      stageTracker(summary.stage, TERMINAL.has(summary.execution_status)),
    );
    clear(statusLine);
    statusLine.appendChild(statusBadge(summary.execution_status));
    const stageText = `Current stage: ${summary.stage || "not started"}`;
    statusLine.appendChild(el("span", { class: "muted", text: stageText }));
    const status = String(summary.execution_status || "").toLowerCase().replace(/_/g, " ");
    const message = `Run ${status}. ${stageText}.`;
    if (message !== lastAnnounced) {
      lastAnnounced = message;
      announcer.textContent = message;
    }

    clear(summaryHost);
    if (summary.raw_path) {
      summaryHost.appendChild(
        el("dl", { class: "kv" }, [
          el("dt", { text: "Input" }),
          el("dd", { class: "code", text: summary.raw_path }),
          el("dt", { text: "Adapter" }),
          el("dd", { text: summary.adapter_used || summary.routing_adapter || "—" }),
        ]),
      );
    }
  };

  const onTerminal = async (summary) => {
    stop();
    if (summary.execution_status === "COMPLETED") {
      ctx.navigate(`#/runs/${runId}/report`);
      return;
    }
    if (summary.execution_status === "STOPPED_NEEDS_MAPPING") {
      // Pass the returned fingerprint through to the mapping screen.
      ctx.navigate(`#/runs/${runId}/mapping`);
      return;
    }

    clear(summaryHost);
    if (summary.execution_status === "STOPPED_VALIDATION") {
      summaryHost.appendChild(
        alertSection("Validation stopped the run", validationDetail(summary)),
      );
    } else if (summary.execution_status === "FAILED") {
      summaryHost.appendChild(
        alertSection("Run failed", [
          el("dl", { class: "kv" }, [
            el("dt", { text: "Error code" }),
            el("dd", { class: "code", text: summary.error_code || "UNKNOWN" }),
            el("dt", { text: "Stage" }),
            el("dd", { text: summary.stage || "—" }),
          ]),
          el("p", {
            class: "muted",
            text: "No stack trace is shown; see server logs for diagnostics.",
          }),
        ]),
      );
    } else if (summary.execution_status === "INTERRUPTED") {
      const resubmit = el("button", {
        class: "primary-btn",
        type: "button",
        text: "Resubmit dataset",
      });
      resubmit.addEventListener("click", async () => {
        resubmit.disabled = true;
        try {
          // Only the dataset path is recorded with a run; support threads and
          // version overrides are not stored, so this reruns with defaults.
          const result = await api.triggerRun({ raw_path: summary.raw_path });
          ctx.navigateToRun(result.run_id);
        } catch (err) {
          ctx.showBanner(errorText(err));
          resubmit.disabled = false;
        }
      });
      summaryHost.appendChild(
        alertSection("Run interrupted", [
          el("p", {
            class: "muted",
            text:
              "The server restarted mid-run. Resubmitting reruns the same dataset with " +
              "default settings. Support threads and config overrides are not stored " +
              "with a run, so start from Upload to rerun with them.",
          }),
          el("div", { class: "row" }, [
            resubmit,
            el("a", { href: "#/upload", text: "Start again from Upload" }),
          ]),
        ]),
      );
    }
  };

  const poll = async () => {
    if (stopped) return;
    try {
      const summary = await api.getRun(runId);
      if (stopped) return;
      failures = 0;
      render(summary);
      if (TERMINAL.has(summary.execution_status)) {
        await onTerminal(summary);
        return;
      }
      timer = window.setTimeout(poll, POLL_MS);
    } catch (err) {
      if (stopped) return;
      const transient = err && (err.status === 0 || err.status === 429 || err.status >= 500);
      if (transient && failures < MAX_POLL_RETRIES) {
        // Back off (4 s, 8 s, 16 s ..., capped at 30 s) and keep polling.
        failures += 1;
        timer = window.setTimeout(poll, Math.min(POLL_MS * 2 ** failures, 30000));
        return;
      }
      stop();
      clear(statusLine);
      clear(summaryHost);
      const notFound = err && err.status === 404;
      summaryHost.appendChild(
        section(
          notFound ? "Run not found" : "Could not load run",
          [
            el("p", {
              class: "muted",
              text: notFound
                ? "This run does not exist — it may have been pruned by retention/GC."
                : errorText(err),
            }),
          ],
        ),
      );
    }
  };

  root.appendChild(section("Pipeline", [stageHost, statusLine, announcer]));
  root.appendChild(summaryHost);
  poll();
}

// A terminal outcome is announced when it appears.
function alertSection(title, children) {
  const node = section(title, children);
  node.setAttribute("role", "alert");
  return node;
}

function validationDetail(summary) {
  const nodes = [];
  if (summary.warnings && summary.warnings.length) {
    nodes.push(
      el("ul", { class: "plain-list" }, summary.warnings.map((w) => el("li", { text: w }))),
    );
  }
  if (summary.errors && summary.errors.length) {
    nodes.push(
      el(
        "ul",
        { class: "plain-list" },
        summary.errors.map((e) =>
          el("li", {
            class: "code",
            text:
              typeof e === "string"
                ? e
                : `${e.code || "ERROR"}${e.stage ? ` @ ${e.stage}` : ""}: ${e.message || ""}`,
          }),
        ),
      ),
    );
  }
  if (!nodes.length) {
    nodes.push(
      el("p", {
        class: "muted",
        text: "Node 1 rejected the whole batch. No reasons were recorded.",
      }),
    );
  }
  return nodes;
}
