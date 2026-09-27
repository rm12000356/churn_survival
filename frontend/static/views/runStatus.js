// runStatus.js — poll GET /runs/{id} until terminal, then branch. Stage and
// execution_status are rendered verbatim; no progress percentage is invented.

import { api, errorText } from "../api.js";
import { el, clear, section } from "../components/ui.js";
import { stageTracker } from "../components/stageTracker.js";

const POLL_MS = 2000;
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
      section("No run selected", [
        el("p", {
          class: "muted",
          text: "Open a run from the history, or trigger one from Upload.",
        }),
      ]),
    );
    return;
  }
  ctx.setTitle(`Run ${runId}`);

  let timer = null;
  let stopped = false;

  const stageHost = el("div");
  const statusLine = el("p", { class: "muted", text: "Loading…" });
  const summaryHost = el("div");

  const stop = () => {
    stopped = true;
    if (timer) window.clearTimeout(timer);
    timer = null;
  };
  ctx.onTeardown(stop);

  const render = (summary) => {
    clear(stageHost);
    stageHost.appendChild(stageTracker(summary.stage));
    statusLine.textContent = `execution status: ${summary.execution_status}  ·  stage: ${summary.stage || "—"}`;

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
        section("Validation stopped the run", validationDetail(summary)),
      );
    } else if (summary.execution_status === "FAILED") {
      summaryHost.appendChild(
        section("Run failed", [
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
        text: "Resubmit run",
      });
      resubmit.addEventListener("click", async () => {
        resubmit.disabled = true;
        try {
          const result = await api.triggerRun({ raw_path: summary.raw_path });
          ctx.navigateToRun(result.run_id);
        } catch (err) {
          ctx.showBanner(errorText(err));
          resubmit.disabled = false;
        }
      });
      summaryHost.appendChild(
        section("Run interrupted", [
          el("p", {
            class: "muted",
            text: "The server restarted mid-run. Resubmitting reruns the same input in place.",
          }),
          resubmit,
        ]),
      );
    }
  };

  const poll = async () => {
    if (stopped) return;
    try {
      const summary = await api.getRun(runId);
      if (stopped) return;
      render(summary);
      if (TERMINAL.has(summary.execution_status)) {
        await onTerminal(summary);
        return;
      }
      timer = window.setTimeout(poll, POLL_MS);
    } catch (err) {
      stop();
      statusLine.textContent = "";
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

  root.appendChild(section("Pipeline", [stageHost, statusLine]));
  root.appendChild(summaryHost);
  poll();
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
