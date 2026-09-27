// app.js — shell: navigation, theme, API-key management, banner, routing.
// All views re-fetch on entry (idempotent reads); no decision state is cached.

import { api, errorText, getApiKey, setApiKey } from "./api.js";
import { createRouter } from "./router.js";
import { renderUpload } from "./views/upload.js";
import { renderRunStatus } from "./views/runStatus.js";
import { renderMapping } from "./views/mapping.js";
import { renderReport } from "./views/report.js";
import { renderHistory } from "./views/history.js";
import { renderModels } from "./views/models.js";
import { clear } from "./components/ui.js";

const root = document.getElementById("view");
const titleEl = document.getElementById("view-title");
const bannerEl = document.getElementById("banner");
const navEl = document.getElementById("nav");
const apiStateEl = document.getElementById("api-state");

let teardowns = [];
let currentNav = "";

const routeTable = {
  upload: { name: "upload", title: "Upload", render: renderUpload, nav: "upload" },
  runs: {
    name: "runs",
    title: "Runs",
    render: (r, ctx) => renderRunRoute(r, ctx),
    nav: "runs",
  },
  models: { name: "models", title: "Models", render: renderModels, nav: "models" },
  "": { name: "upload", title: "Upload", render: renderUpload, nav: "upload" },
};

function renderRunRoute(r, ctx) {
  const segments = ctx.segments;
  const sub = segments[2];
  if (!segments[1]) return renderHistory(r, ctx);
  if (sub === "report") return renderReport(r, ctx);
  if (sub === "mapping") return renderMapping(r, ctx);
  return renderRunStatus(r, ctx);
}

const ctx = {
  params: {},
  segments: [],
  setTitle(title) {
    titleEl.textContent = title;
  },
  navigate(hash) {
    window.location.hash = hash;
  },
  navigateToRun(runId) {
    // runId comes verbatim from the API response, never constructed locally.
    window.location.hash = `#/runs/${runId}`;
  },
  showBanner(message, kind = "error") {
    bannerEl.hidden = false;
    bannerEl.textContent = message;
    bannerEl.style.color = kind === "info" ? "var(--line)" : "var(--critical)";
    bannerEl.style.borderColor = kind === "info" ? "var(--line)" : "var(--critical)";
  },
  clearBanner() {
    bannerEl.hidden = true;
    bannerEl.textContent = "";
  },
  onTeardown(fn) {
    teardowns.push(fn);
  },
  routes: routeTable,
};

function teardown() {
  for (const fn of teardowns) {
    try {
      fn();
    } catch {
      /* ignore teardown errors */
    }
  }
  teardowns = [];
}

function setActiveNav(navKey) {
  currentNav = navKey;
  for (const link of navEl.querySelectorAll("a")) {
    link.classList.toggle("active", link.dataset.nav === navKey);
  }
}

async function checkHealth() {
  try {
    const health = await api.health();
    apiStateEl.textContent = health.writes_enabled
      ? "API online · writes enabled"
      : "API online · writes disabled";
  } catch {
    apiStateEl.textContent = "API unreachable";
  }
}

const router = createRouter({
  routes: routeTable,
  fallback: routeTable.upload,
  onRoute(route, parsed) {
    teardown();
    ctx.clearBanner();
    ctx.params = parsed.params;
    ctx.segments = parsed.segments;
    // The run id lives in the path (#/runs/<id>[/<sub>]), not the query string.
    // Centralize it as ctx.params.id so no view repeats the parsing mistake.
    ctx.params.id = parsed.segments[1] || "";
    ctx.setTitle(route.title);
    setActiveNav(route.nav);
    clear(root);
    Promise.resolve(route.render(root, ctx)).catch((err) => {
      clear(root);
      ctx.showBanner(errorText(err));
    });
  },
});

// --- Theme ---
const themeToggle = document.getElementById("theme-toggle");
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeToggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  localStorage.setItem("horizon.theme", theme);
}
applyTheme(localStorage.getItem("horizon.theme") || "light");
themeToggle.addEventListener("click", () => {
  applyTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  );
});

// --- API key ---
const apiKeyInput = document.getElementById("api-key");
apiKeyInput.value = getApiKey();
document.getElementById("api-key-save").addEventListener("click", () => {
  setApiKey(apiKeyInput.value.trim());
  ctx.showBanner("API key saved for this browser.", "info");
  checkHealth();
});

checkHealth();
router.start();
