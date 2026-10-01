// Small DOM + presentation helpers. No risk/score/rank math anywhere here.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(
      typeof child === "string" ? document.createTextNode(child) : child,
    );
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// Risk level is rendered from the API string; we only map it to a CSS class.
export function levelClass(level) {
  return `level-${String(level || "").toLowerCase()}`;
}

export function sentenceCase(text) {
  const s = String(text || "").replace(/_/g, " ").toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function riskBadge(level, label) {
  const text = sentenceCase(label || level);
  return el("span", { class: `badge ${levelClass(level)}`, text });
}

// Run execution status — its own vocabulary, deliberately not a risk colour.
const STATUS_TONE = {
  COMPLETED: "completed",
  STOPPED_NEEDS_MAPPING: "stopped",
  STOPPED_VALIDATION: "stopped",
  FAILED: "failed",
  INTERRUPTED: "failed",
  RUNNING: "running",
  PENDING: "running",
};

export function statusBadge(status) {
  const tone = STATUS_TONE[status] || "";
  return el("span", {
    class: `status ${tone ? `status-${tone}` : ""}`.trim(),
    text: sentenceCase(status || "unknown"),
  });
}

// Portfolio band: segment widths are the API's own counts, drawn to scale.
// Presentation only — no level, score, or rank is derived here.
export const LEVEL_ORDER = ["critical", "high", "medium", "low", "insufficient_data"];

// `onSelect(level)` (optional) turns each legend entry into a filter button.
export function horizonBand(distribution, onSelect) {
  const counts = LEVEL_ORDER.map((level) => [level, Number(distribution[level] || 0)]);
  const total = counts.reduce((sum, [, n]) => sum + n, 0);
  const band = el(
    "div",
    {
      class: "horizon-band",
      role: "img",
      "aria-label": counts.map(([l, n]) => `${sentenceCase(l)} ${n}`).join(", "),
    },
    counts
      .filter(([, n]) => n > 0)
      .map(([level, n], i) =>
        el("span", {
          class: levelClass(level),
          style: `flex-grow:${n};animation-delay:${i * 60}ms`,
          title: `${sentenceCase(level)}: ${n}`,
        }),
      ),
  );
  const legend = el(
    "ul",
    { class: "horizon-legend" },
    counts.map(([level, n]) => {
      const content = [
        el("span", { class: "n", text: n.toLocaleString() }),
        el("span", { class: "lbl", text: sentenceCase(level) }),
      ];
      return el("li", { class: levelClass(level) }, [
        onSelect && n > 0
          ? el(
              "button",
              {
                type: "button",
                class: "legend-btn",
                title: `Show only ${sentenceCase(level).toLowerCase()} accounts`,
                onclick: () => onSelect(level),
              },
              content,
            )
          : el("span", {}, content),
      ]);
    }),
  );
  return el("div", { class: "horizon" }, [
    el("div", { class: "horizon-total" }, [
      el("strong", { text: total.toLocaleString() }),
      el("span", { class: "muted", text: "accounts assessed" }),
    ]),
    band,
    legend,
  ]);
}

// Thin bar beside a verbatim 0–1 value. The number printed is the API value.
export function meter(value, text) {
  if (value === null || value === undefined) return el("span", { class: "muted", text: "—" });
  const pct = Math.max(0, Math.min(1, Number(value))) * 100;
  return el("span", { class: "meter" }, [
    el("span", { text }),
    el("span", { class: "meter-track", "aria-hidden": "true" }, [
      el("span", { class: "meter-fill", style: `width:${pct}%` }),
    ]),
  ]);
}

// A table row that opens something: clickable and reachable by keyboard.
export function actionRow(attrs, cells, onActivate) {
  return el(
    "tr",
    {
      ...attrs,
      tabindex: "0",
      onclick: onActivate,
      onkeydown: (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onActivate();
        }
      },
    },
    cells,
  );
}

export function emptyState(title, text, action) {
  return el("div", { class: "empty" }, [
    el("h2", { text: title }),
    text ? el("p", { text }) : null,
    action || null,
  ]);
}

// A numbered section for screens whose inputs form a real sequence
// (Upload, Mapping). `optional` adds a quiet "Optional" tag to the title.
export function step(n, title, optional, children, className = "") {
  return el("section", { class: `section step ${className}`.trim() }, [
    el("h2", { class: "section-title" }, [
      el("span", { class: "step-n", text: String(n) }),
      el("span", {}, [
        title,
        optional ? el("span", { class: "step-optional", text: "Optional" }) : null,
      ]),
    ]),
    ...children,
  ]);
}

// Download rows as CSV. Values are written exactly as the API returned them.
export function downloadCsv(filename, header, rows) {
  const cell = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [header, ...rows].map((r) => r.map(cell).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = el("a", { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function loading(text = "Loading") {
  return el("p", { class: "loading", text });
}

// LLM-vs-template provenance is a trust feature and must be visible even when
// nothing failed. The value comes verbatim from the API (`explanation_source`).
export function provenanceTag(source) {
  const isLlm = source === "llm";
  return el("span", {
    class: `prov ${isLlm ? "prov-llm" : "prov-template"}`,
    text: isLlm ? "LLM-drafted" : "template",
    title: isLlm
      ? "Headline/summary drafted by the LLM, then deterministically validated."
      : "Headline/summary generated by the deterministic template.",
  });
}

export function formatScore(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(3);
}

export function formatConfidence(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(2);
}

export function relativeTime(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  return `${days} d ago`;
}

export function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

export function section(title, children, className = "", count) {
  return el("section", { class: `section ${className}`.trim() }, [
    title
      ? el("h2", { class: "section-title" }, [
          title,
          count === undefined ? null : el("span", { class: "count", text: String(count) }),
        ])
      : null,
    ...[].concat(children),
  ]);
}
