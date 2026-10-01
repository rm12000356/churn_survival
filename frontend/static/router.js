// router.js — hash router. Routes never construct a run_id; they read it from
// the URL that the app navigated to using a value returned by the API.

export function parseHash(hash) {
  const clean = (hash || "").replace(/^#/, "");
  const [pathPart, queryPart] = clean.split("?");
  const segments = pathPart.split("/").filter(Boolean);
  const params = Object.fromEntries(new URLSearchParams(queryPart || ""));
  return { segments, params };
}

export function createRouter({ routes, onRoute, fallback }) {
  const handle = () => {
    const { segments, params } = parseHash(window.location.hash);
    const name = segments[0] || "";
    const route = routes[name] || fallback;
    onRoute(route, { segments, params });
  };
  window.addEventListener("hashchange", handle);
  // `refresh` re-renders the current hash (navigating to the same hash fires
  // no hashchange, e.g. a resubmit that returns the same run_id).
  return { start: handle, refresh: handle };
}
