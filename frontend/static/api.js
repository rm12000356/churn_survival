// api.js — single fetch wrapper. Every GET is treated as idempotent and
// repeatable; the client never derives a run_id, fingerprint, risk level, score,
// rank, or confidence. Those are only ever read verbatim from responses.

const KEY_STORAGE = "horizon.apiKey";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

// The key lives in sessionStorage (cleared when the tab closes), never
// localStorage. Storage access can throw (blocked site data, some private
// modes), so every access is guarded and falls back to memory for this page.
let memoryKey = "";

function keyStore() {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function getApiKey() {
  try {
    const store = keyStore();
    return (store && store.getItem(KEY_STORAGE)) || memoryKey;
  } catch {
    return memoryKey;
  }
}

export function setApiKey(value) {
  memoryKey = value || "";
  try {
    const store = keyStore();
    if (!store) return;
    if (value) store.setItem(KEY_STORAGE, value);
    else store.removeItem(KEY_STORAGE);
    // Drop any key an older version left in localStorage.
    window.localStorage.removeItem(KEY_STORAGE);
  } catch {
    /* storage blocked: the in-memory key still works for this page */
  }
}

function authHeaders() {
  const key = getApiKey();
  return key ? { "X-API-Key": key } : {};
}

async function request(method, path, { body, json, formData } = {}) {
  const headers = { ...authHeaders() };
  let payload;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(json);
  } else if (formData !== undefined) {
    payload = formData; // browser sets multipart boundary
  } else {
    payload = body;
  }

  let response;
  try {
    response = await fetch(path, { method, headers, body: payload });
  } catch (err) {
    throw new ApiError(0, `Network error: ${err.message}`);
  }

  if (response.status === 204) return null;

  // Parse by content-type: JSON endpoints yield objects; text/plain (support
  // threads) and text/html (static report) yield the raw string. Never blind-
  // parse, so a JSON body served as text/plain is returned verbatim for the
  // caller to parse (fixes support-threads loading).
  const contentType = response.headers.get("content-type") || "";
  const text = await response.text();
  let data = null;
  if (text) {
    if (contentType.includes("application/json")) {
      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }
    } else {
      data = text;
    }
  }

  if (!response.ok) {
    const detail = data && data.detail !== undefined ? data.detail : data;
    throw new ApiError(response.status, detail);
  }
  return data;
}

// --- Read endpoints (never recompute; safe to call repeatedly) ---
export const api = {
  health: () => request("GET", "/status"),
  listRuns: ({ limit = 50, offset = 0, status = "", modelVersion = "" } = {}) => {
    const params = new URLSearchParams();
    params.set("limit", String(limit));
    if (offset) params.set("offset", String(offset));
    if (status) params.set("status", status);
    if (modelVersion) params.set("model_version", modelVersion);
    return request("GET", `/runs?${params.toString()}`);
  },
  getRun: (runId) => request("GET", `/runs/${encodeURIComponent(runId)}`),
  getReport: (runId) =>
    request("GET", `/runs/${encodeURIComponent(runId)}/report`),
  getReportHtml: (runId) =>
    request("GET", `/runs/${encodeURIComponent(runId)}/report.html`),
  // The static report needs the X-API-Key header when a key is configured, which
  // a plain link cannot send. Fetch it with the key and show it in a new tab
  // inside a sandboxed iframe (no scripts, opaque origin), matching the
  // `sandbox` CSP the server sends with report.html.
  openReportHtml: async (runId) => {
    // Open the tab synchronously (inside the click) so popup blockers allow it.
    const tab = window.open("", "_blank");
    if (!tab) throw new Error("The browser blocked the new tab; allow pop-ups for this site.");
    try {
      const html = await request("GET", `/runs/${encodeURIComponent(runId)}/report.html`);
      const doc = tab.document;
      doc.title = `Report ${runId}`;
      doc.body.style.margin = "0";
      const frame = doc.createElement("iframe");
      frame.setAttribute("sandbox", "");
      frame.setAttribute("title", `Static report for run ${runId}`);
      frame.style.cssText = "border:0;width:100vw;height:100vh;display:block";
      frame.srcdoc = html;
      doc.body.appendChild(frame);
    } catch (err) {
      tab.close();
      throw err;
    }
  },
  getRankedAccounts: (runId) =>
    request("GET", `/runs/${encodeURIComponent(runId)}/ranked-accounts`),
  listRawFiles: () => request("GET", "/raw-files"),
  readRawFile: (name) =>
    request("GET", `/raw-files/${encodeURIComponent(name)}`),
  listNode1Configs: () => request("GET", "/node1-configs"),
  listModels: () => request("GET", "/models"),
  getModel: (version) =>
    request("GET", `/models/${encodeURIComponent(version)}`),

  // --- Write endpoints (auth + writes-gated) ---
  upload: (file) => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    return request("POST", "/uploads", { formData });
  },
  triggerRun: (spec, { force = false } = {}) => {
    const suffix = force ? "?force=true" : "";
    return request("POST", `/runs${suffix}`, { json: spec });
  },
  draftMapping: (rawPath, useLlm = false) =>
    request("POST", "/mappings/draft", {
      json: { raw_path: rawPath, use_llm: useLlm },
    }),
  confirmMapping: (body) => request("POST", "/mappings/confirm", { json: body }),
  mappingCandidates: (report, rawPath) =>
    request("POST", "/mappings/candidates", { json: { report, raw_path: rawPath } }),
};

export function isWritesDisabled(err) {
  return err instanceof ApiError && err.status === 403;
}

export function isAuthError(err) {
  return err instanceof ApiError && err.status === 401;
}

// FastAPI returns `detail` either as a string (HTTPException) or as an array of
// validation errors (body validation). Render both readably, never raw JSON.
function detailText(detail) {
  if (detail === null || detail === undefined) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object") {
          const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
          return `${loc ? `${loc}: ` : ""}${item.msg || JSON.stringify(item)}`;
        }
        return String(item);
      })
      .join("; ");
  }
  if (typeof detail === "object" && typeof detail.message === "string") return detail.message;
  return JSON.stringify(detail);
}

export function errorText(err) {
  if (isWritesDisabled(err)) {
    return (
      "Writes are disabled on this server (API_ENABLE_WRITES=false). " +
      "Enable writes and set an API key to trigger runs or confirm mappings."
    );
  }
  if (isAuthError(err)) {
    return "Invalid or missing API key. Set the X-API-Key in the sidebar.";
  }
  if (err instanceof ApiError) {
    const detail = detailText(err.detail);
    if (detail) return detail;
    return err.message || `Request failed (${err.status}).`;
  }
  return String(err);
}
