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
const noticeEl = document.getElementById("notice");
const announcerEl = document.getElementById("route-announcer");
const navEl = document.getElementById("nav");
const apiStateEl = document.getElementById("api-state");

const APP_NAME = "Horizon";

let teardowns = [];
// Incremented on every route change. A render that is still awaiting data when
// the user navigates away holds an older generation, and every write it makes
// (DOM, banner, title, teardown) is ignored, so it cannot leak into the next view.
let generation = 0;
let firstRoute = true;

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

function setTitle(title) {
  titleEl.textContent = title;
  document.title = `${title} — ${APP_NAME}`;
}

function showBanner(message, kind = "error") {
  // Errors go to the role=alert region, information to the role=status one.
  // Both stay in the DOM, so writing text is what triggers the announcement.
  const target = kind === "info" ? noticeEl : bannerEl;
  const other = kind === "info" ? bannerEl : noticeEl;
  other.textContent = "";
  target.textContent = message;
}

function clearBanner() {
  bannerEl.textContent = "";
  noticeEl.textContent = "";
}

// One context per route render. Every side effect checks that the render is
// still current.
function routeContext(myGeneration, params, segments) {
  const live = () => myGeneration === generation;
  return {
    params,
    segments,
    isCurrent: live,
    setTitle(title) {
      if (live()) setTitle(title);
    },
    navigate(hash) {
      if (live()) window.location.hash = hash;
    },
    navigateToRun(runId) {
      // runId comes verbatim from the API response, never constructed locally.
      if (!live()) return;
      const hash = `#/runs/${encodeURIComponent(runId)}`;
      if (window.location.hash === hash) router.refresh();
      else window.location.hash = hash;
    },
    showBanner(message, kind) {
      if (live()) showBanner(message, kind);
    },
    clearBanner() {
      if (live()) clearBanner();
    },
    onTeardown(fn) {
      // A stale render registering cleanup (e.g. a poll timer) runs it at once.
      if (live()) teardowns.push(fn);
      else fn();
    },
    routes: routeTable,
  };
}

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
  for (const link of navEl.querySelectorAll("a")) {
    const active = link.dataset.nav === navKey;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

async function checkHealth() {
  try {
    const health = await api.health();
    apiStateEl.textContent = health.writes_enabled
      ? "API online, runs enabled"
      : "API online, read-only";
    apiStateEl.className = "api-state ok";
  } catch {
    apiStateEl.textContent = "API unreachable";
    apiStateEl.className = "api-state down";
  }
}

const router = createRouter({
  routes: routeTable,
  fallback: routeTable.upload,
  onRoute(route, parsed) {
    teardown();
    generation += 1;
    const myGeneration = generation;
    clearBanner();
    const params = { ...parsed.params };
    // The run id lives in the path (#/runs/<id>[/<sub>]), not the query string.
    // Centralize it as params.id so no view repeats the parsing mistake.
    params.id = parsed.segments[1] ? decodeURIComponent(parsed.segments[1]) : "";
    const ctx = routeContext(myGeneration, params, parsed.segments);
    setTitle(route.title);
    setActiveNav(route.nav);
    // Each render gets its own host; a stale render writes into a detached node.
    clear(root);
    const host = document.createElement("div");
    root.appendChild(host);
    Promise.resolve(route.render(host, ctx))
      .catch((err) => {
        if (myGeneration !== generation) return;
        clear(host);
        showBanner(errorText(err));
      })
      .finally(() => {
        if (myGeneration !== generation) return;
        announcerEl.textContent = `${titleEl.textContent} page loaded`;
      });
    // Move focus to the page heading on navigation (not on first load, so the
    // initial page does not steal focus from the address bar).
    if (!firstRoute) titleEl.focus({ preventScroll: true });
    firstRoute = false;
  },
});

// --- Theme ---
const themeToggle = document.getElementById("theme-toggle");
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  themeToggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  try {
    localStorage.setItem("horizon.theme", theme);
  } catch {
    /* storage blocked: theme still applies for this page */
  }
}
function initialTheme() {
  try {
    const saved = localStorage.getItem("horizon.theme");
    if (saved) return saved;
  } catch {
    /* storage blocked */
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
applyTheme(initialTheme());
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
  showBanner("API key saved for this browser session.", "info");
  checkHealth();
  // Re-render the current view so lists that failed with 401 reload.
  router.refresh();
});

checkHealth();
router.start();
