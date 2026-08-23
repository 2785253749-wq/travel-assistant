(() => {
  "use strict";

  const CATEGORIES = ["全部", "摄影控", "美食地图", "独自旅行", "城市漫步", "自然风光", "亲子游"];
  const DEFAULT_PAGE_SIZE = 20;

  function trimText(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function sameOriginPath(value, fallback = "/") {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return fallback;
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin || !target.pathname.startsWith("/")) return fallback;
    return `${target.pathname}${target.search}${target.hash}`;
  }

  function authConfig() {
    const config = window.TRAVEL_ASSISTANT_CONFIG;
    if (!config || typeof config.supabaseUrl !== "string" || !config.supabaseUrl
      || typeof config.supabaseAnonKey !== "string" || !config.supabaseAnonKey) {
      return null;
    }
    return { url: config.supabaseUrl, anonKey: config.supabaseAnonKey };
  }

  function publicError(error) {
    if (!error) return "社区内容加载失败，请稍后重试。";
    if (error.code === "AUTH_REQUIRED") return "登录状态已失效，请重新登录。";
    return "社区内容暂不可用，请稍后重试。";
  }

  function safeUrl(value) {
    if (typeof value !== "string" || !value) return null;
    try {
      const next = new URL(value, window.location.origin);
      if (next.protocol !== "http:" && next.protocol !== "https:") return null;
      if (next.origin === window.location.origin) {
        return `${next.pathname}${next.search}${next.hash}`;
      }
      return next.toString();
    } catch (_error) {
      return null;
    }
  }

  function createBrowserClient() {
    let authClient = null;
    let session = null;
    let sessionGeneration = 0;
    const listeners = [];

    function notify() {
      for (const listener of listeners) listener(session);
    }

    function applySession(nextSession) {
      session = nextSession && typeof nextSession.access_token === "string" ? nextSession : null;
      sessionGeneration += 1;
      notify();
    }

    async function initialize() {
      const config = authConfig();
      if (!config || !window.supabase || typeof window.supabase.createClient !== "function") {
        applySession(null);
        return;
      }
      authClient = window.supabase.createClient(config.url, config.anonKey, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      });
      if (authClient.auth && typeof authClient.auth.onAuthStateChange === "function") {
        authClient.auth.onAuthStateChange((_event, nextSession) => {
          applySession(nextSession);
        });
      }
      if (!authClient.auth || typeof authClient.auth.getSession !== "function") {
        applySession(null);
        return;
      }
      const result = await authClient.auth.getSession();
      applySession(result && result.data ? result.data.session : null);
    }

    function buildSignInUrl(returnTo) {
      const url = new URL("/auth", window.location.origin);
      url.searchParams.set("mode", "signin");
      url.searchParams.set("return_to", sameOriginPath(returnTo, "/community"));
      return url.toString();
    }

    function redirectToSignIn(returnTo) {
      window.location.href = buildSignInUrl(returnTo);
    }

    async function requestJson(url, options = {}) {
      const requestOptions = { method: options.method || "GET", headers: {} };
      if (options.body !== undefined) {
        requestOptions.headers["Content-Type"] = "application/json";
        requestOptions.body = JSON.stringify(options.body);
      }
      if (options.auth && session && typeof session.access_token === "string") {
        requestOptions.headers.Authorization = `Bearer ${session.access_token}`;
      }
      const response = await fetch(url, requestOptions);
      const payload = await response.json().catch(() => ({}));
      if (response.status === 401) {
        const error = new Error("AUTH_REQUIRED");
        error.code = "AUTH_REQUIRED";
        error.status = 401;
        error.payload = payload;
        throw error;
      }
      if (!response.ok) {
        const code = payload && payload.detail && typeof payload.detail.code === "string"
          ? payload.detail.code
          : "REQUEST_FAILED";
        const error = new Error(code);
        error.code = code;
        error.status = response.status;
        error.payload = payload;
        throw error;
      }
      return payload;
    }

    return {
      initialize,
      requestJson,
      redirectToSignIn,
      buildSignInUrl,
      getSupabaseClient: () => authClient,
      getSession: () => session,
      isSignedIn: () => Boolean(session && typeof session.access_token === "string"),
      getSessionGeneration: () => sessionGeneration,
      onSessionChange(listener) {
        listeners.push(listener);
      },
    };
  }

  window.VoyageCommunityClient = {
    CATEGORIES,
    DEFAULT_PAGE_SIZE,
    trimText,
    sameOriginPath,
    safeUrl,
    publicError,
    createBrowserClient,
  };
})();
