// stageTracker.js — renders the five-stage pipeline sequence from the API's
// `stage` field. Never infers a percentage or a stage the API did not return.

import { el } from "./ui.js";

export const STAGES = [
  { key: "router", label: "Router", matches: ["routing", "mapping_confirmation"] },
  { key: "survival", label: "Survival", matches: ["node1", "node2"] },
  { key: "signals", label: "Signals", matches: ["node3"] },
  { key: "synthesis", label: "Synthesis", matches: ["node4"] },
  { key: "report", label: "Report", matches: ["node5", "done"] },
];

// Map the API stage string to an index. Unknown/missing stages return -1.
export function stageIndex(stage) {
  if (!stage) return -1;
  return STAGES.findIndex((s) => s.matches.includes(stage));
}

export function stageTracker(stage) {
  const current = stageIndex(stage);
  const done = stage === "done";
  const nodes = STAGES.map((s, i) => {
    let state = "";
    if (done || (current >= 0 && i < current)) state = "done";
    if (!done && i === current) state = "active";
    return el("div", { class: `stage ${state}`.trim() }, [
      el("span", { class: "idx", text: String(i + 1) }),
      el("span", { text: s.label }),
    ]);
  });
  return el("div", { class: "stages", "aria-label": "Pipeline stages" }, nodes);
}
