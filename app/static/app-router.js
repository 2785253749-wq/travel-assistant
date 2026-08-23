/* global window, history */
(function installVoyageRouter(global) {
  "use strict";

  const ROUTE_BY_PATH = Object.freeze({
    "/": "explore",
    "/community": "community",
    "/profile": "profile",
    "/admin/community": "admin",
  });
  const PATH_BY_VIEW = Object.freeze({
    explore: "/",
    trips: "/#trips-page",
    community: "/community",
    profile: "/profile",
    admin: "/admin/community",
  });
  const listeners = new Set();
  let started = false;
  let activeView = null;

  function normalizePath(pathname) {
    const path = String(pathname || "/").split("?")[0];
    if (!path || path === "/index.html") return "/";
    if (path.length > 1 && path.endsWith("/")) return path.slice(0, -1);
    return path;
  }

  function viewFromLocation(source = global.location) {
    const location = source || {};
    if (location.hash === "#trips-page") return "trips";
    if (location.hash === "#community-page") return "community";
    return ROUTE_BY_PATH[normalizePath(location.pathname)] || "explore";
  }

  function urlForView(view) {
    return PATH_BY_VIEW[view] || PATH_BY_VIEW.explore;
  }

  async function notify(view, options = {}) {
    activeView = view;
    const results = [];
    for (const listener of listeners) results.push(listener(view, options));
    await Promise.all(results);
    return view;
  }

  function navigate(view, options = {}) {
    const nextView = PATH_BY_VIEW[view] ? view : "explore";
    const nextUrl = urlForView(nextView);
    const method = options.replace === true ? "replaceState" : "pushState";
    global.history[method]({ voyageView: nextView }, "", nextUrl);
    return notify(nextView, { ...options, initial: false });
  }

  function subscribe(listener) {
    if (typeof listener !== "function") throw new TypeError("A route listener is required");
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function start(listener) {
    if (listener) subscribe(listener);
    if (!started) {
      started = true;
      global.addEventListener("popstate", () => notify(viewFromLocation(global.location), { popstate: true }));
    }
    return notify(viewFromLocation(global.location), { initial: true });
  }

  global.VoyageRouter = Object.freeze({
    navigate,
    subscribe,
    start,
    urlForView,
    viewFromLocation,
    get activeView() { return activeView; },
  });
})(window);