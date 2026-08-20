(() => {
  "use strict";

  const STATES = new Set(["signed_out", "collecting", "confirming", "planning", "planned", "error"]);
  const VIEWS = new Set(["explore", "trips", "community"]);
  const PROFILE_LABELS = {
    origin: "出发地", destination: "目的地", start_date: "出发日期", end_date: "返回日期",
    travelers: "出行人数", budget_cny: "总预算（元）", preferences: "偏好", constraints: "限制",
  };
  const BUDGET_LABELS = Object.freeze({
    transport: "交通", hotel: "住宿", food: "餐饮", tickets: "门票",
    reserve: "预留", other: "其他", total: "合计",
  });
  const ACTIVITY_SLOT_LABELS = Object.freeze({
    morning: "上午", afternoon: "下午", evening: "晚上",
  });
  const ALLOWED_EXTERNAL_HOSTS = new Set([
    "api.open-meteo.com", "geocoding-api.open-meteo.com", "photon.komoot.io",
    "www.12306.cn", "www.ctrip.com", "www.gov.cn", "www.xm.gov.cn",
    "www.fujian.gov.cn", "www.yn.gov.cn",
  ]);
  const WEATHER_STATUS_LABELS = Object.freeze({
    available: "实时天气", seasonal: "非实时天气", unavailable: "天气不可用",
  });
  const $ = (id) => document.getElementById(id);
  const elements = {
    body: document.body, brand: $("voyage-brand"), navigationPanel: $("main-navigation"),
    authPanel: $("auth-panel"), authForm: $("auth-form"), email: $("email"), password: $("password"),
    signIn: $("sign-in-button"), signUp: $("sign-up-button"), signOut: $("sign-out-button"), accountPageLink: $("account-page-link"),
    account: $("account-summary"), accountEmail: $("account-email"), authFormPanel: $("auth-form"), accountMenu: $("account-menu"),
    authPage: $("auth-page"), authPageTitle: $("auth-page-title"), authPageDescription: $("auth-page-description"),
    authPageForm: $("auth-page-form"), authPageEmail: $("auth-page-email"), authPagePassword: $("auth-page-password"),
    authPageSubmit: $("auth-page-submit"), authPageAlternate: $("auth-page-alternate"), authPageForgot: $("auth-page-forgot"),
    authHelp: $("auth-help"), status: $("status-message"), providerNotice: $("provider-notice"),
    providerUpdatedAt: $("provider-updated-at"), chatPanel: $("chat-panel"), chatForm: $("chat-form"), message: $("message-input"),
    send: $("send-button"), progress: $("request-progress"), messages: $("chat-messages"),
    assistantPanel: $("assistant-panel"), assistantToggle: $("assistant-toggle"), assistantToggleLabel: $("assistant-toggle-label"), assistantReset: $("assistant-reset-position"),
    explorePage: $("explore-page"), exploreOutput: $("explore-output"), tripsPage: $("trips-page"), communityPage: $("community-page"),
    navigation: [$("explore-nav-button"), $("trips-nav-button"), $("community-nav-button")],
    viewHeadings: { explore: $("explore-title"), trips: $("trips-page-title"), community: $("community-page-title") },
    exploreMap: $("explore-map"), exploreStatus: $("explore-status"),
    mapBreadcrumb: $("map-breadcrumb"), mapTitle: $("map-title"), exploreShortcuts: $("explore-shortcuts"),
    recommendationsTitle: $("recommendations-title"), recommendationCount: $("recommendation-count"),
    recommendationGrid: $("recommendation-grid"), explorePlaceCard: $("explore-place-card"),
    profileCard: $("profile-confirmation"), profileFields: $("profile-fields"), confirm: $("confirm-profile-button"),
    edit: $("edit-profile-button"), tripView: $("trip-view"), tripTitle: $("trip-title"),
    tripContent: $("trip-content"), tripActions: $("trip-actions"), save: $("save-trip-button"),
    share: $("share-trip-button"), history: $("trip-history"), historyList: $("trip-history-list"),
    tripsAuthPrompt: $("trips-auth-prompt"), tripsLogin: $("trips-login-button"),
    shareDialog: $("share-dialog"), shareLink: $("share-link"), shareExpiry: $("share-expiry"),
    copyShare: $("copy-share-link"), revokeShare: $("revoke-share-link"), closeShare: $("close-share-dialog"), renameDialog: $("rename-dialog"),
    renameForm: $("rename-form"), renameInput: $("rename-input"), cancelRename: $("cancel-rename"),
  };
  const state = {
    name: "signed_out", activeView: "explore", busy: false, session: null, authClient: null, user: null, profile: null,
    pendingResult: null, currentTrip: null, renameTripId: null, shareTripId: null, providerNoticeActive: false,
    threadId: makeThreadId(), cityWeather: new Map(), cityWeatherRequests: new Map(), selectedExploreCityId: null,
  };
  let authGeneration = 0;
  let sessionRevision = 0;
  let refreshRequest = null;
  let tripsLoadGeneration = 0;
  let mapExplorer = null;
  let cityWeatherCard = null;
  let exploreInitialized = false;
  let authInitializationPromise = null;
  let publicShareActive = false;
  let authPageMode = "signin";

  function makeThreadId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return `thread-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function setState(next) {
    if (!STATES.has(next)) throw new Error("Invalid page state");
    state.name = next;
    elements.body.dataset.appState = next;
  }

  function invalidateTripsLoads() {
    tripsLoadGeneration += 1;
  }

  function stableUserId(session) {
    const id = session && session.user && session.user.id;
    return typeof id === "string" && id.trim() ? id : null;
  }

  function identitiesDiffer(currentSession, nextSession) {
    if (!currentSession) return Boolean(nextSession);
    const currentUserId = stableUserId(currentSession);
    const nextUserId = stableUserId(nextSession);
    return !currentUserId || !nextUserId || currentUserId !== nextUserId;
  }

  function sessionsShareAccessToken(left, right) {
    return Boolean(left && right && typeof left.access_token === "string" && left.access_token
      && left.access_token === right.access_token);
  }

  function sessionsShareTokens(left, right) {
    return sessionsShareAccessToken(left, right) && typeof left.refresh_token === "string"
      && left.refresh_token && left.refresh_token === right.refresh_token;
  }

  async function switchView(view, { focusHeading = false } = {}) {
    if (!VIEWS.has(view)) return;
    if (state.activeView !== view) invalidateTripsLoads();
    state.activeView = view;
    elements.authPage.hidden = true;
    for (const [name, element] of [["explore", elements.explorePage], ["trips", elements.tripsPage], ["community", elements.communityPage]]) {
      element.hidden = name !== view;
    }
    for (const button of elements.navigation) {
      const active = button.dataset.view === view;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    elements.exploreOutput.hidden = view !== "explore";
    elements.providerNotice.hidden = view !== "explore" || !state.providerNoticeActive;
    if (focusHeading) elements.viewHeadings[view].focus();
    if (view === "trips") await renderTripsPage();
  }

  function setAuthPageMode(mode) {
    authPageMode = mode === "signup" ? "signup" : "signin";
    const signup = authPageMode === "signup";
    elements.authPageTitle.textContent = signup ? "注册 Voyage 账户" : "登录 Voyage";
    elements.authPageDescription.textContent = signup
      ? "注册后可以保存、打开和管理你的私有行程。"
      : "登录后可以保存、打开和管理你的私有行程。";
    elements.authPageSubmit.textContent = signup ? "注册账户" : "登录";
    elements.authPageAlternate.textContent = signup ? "已有账户，去登录" : "注册账户";
    elements.authPagePassword.autocomplete = signup ? "new-password" : "current-password";
  }

  function openAuthPage(mode = "signin") {
    if (state.busy) return;
    setAuthPageMode(mode);
    elements.accountMenu.open = false;
    elements.authPage.hidden = false;
    elements.explorePage.hidden = true;
    elements.tripsPage.hidden = true;
    elements.communityPage.hidden = true;
    elements.exploreOutput.hidden = true;
    elements.providerNotice.hidden = true;
    setAssistantOpen(false);
    for (const button of elements.navigation) button.removeAttribute("aria-current");
    elements.authPageTitle.focus();
  }

  function closeAuthPage() {
    elements.authPage.hidden = true;
    return switchView("explore", { focusHeading: true });
  }

  function setStatus(message, isError = false) {
    elements.status.textContent = message;
    elements.status.dataset.error = isError ? "true" : "false";
  }

  function setBusy(busy, message = "") {
    state.busy = busy;
    for (const control of document.querySelectorAll("button,input,textarea")) control.disabled = busy;
    elements.progress.textContent = busy ? message || "正在处理，请稍候。" : "";
  }

  function setAssistantOpen(open, { focusInput = false, restoreFocus = false } = {}) {
    const actionLabel = open ? "关闭 AI 助手" : "打开 AI 助手";
    elements.assistantPanel.hidden = !open;
    if (open) clampAssistantPosition();
    elements.assistantToggle.setAttribute("aria-expanded", String(open));
    elements.assistantToggle.setAttribute("aria-label", actionLabel);
    elements.assistantToggleLabel.textContent = actionLabel;
    if (open && focusInput) elements.message.focus();
    if (!open && restoreFocus) elements.assistantToggle.focus();
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), Math.max(min, max));
  }

  function setAssistantPosition(left, top) {
    const rect = elements.assistantPanel.getBoundingClientRect();
    const maxLeft = Math.max(12, window.innerWidth - rect.width - 12);
    const maxTop = Math.max(12, window.innerHeight - rect.height - 12);
    Object.assign(elements.assistantPanel.style, {
      left: `${clamp(left, 12, maxLeft)}px`, top: `${clamp(top, 12, maxTop)}px`, right: "auto", bottom: "auto",
    });
  }

  function clampAssistantPosition() {
    if (elements.assistantPanel.hidden) return;
    const left = Number.parseFloat(elements.assistantPanel.style.left);
    const top = Number.parseFloat(elements.assistantPanel.style.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return;
    setAssistantPosition(left, top);
  }

  function resetAssistantPosition() {
    Object.assign(elements.assistantPanel.style, { left: "", top: "", right: "", bottom: "" });
  }

  function initializeAssistantDrag() {
    const handle = $("assistant-drag-handle");
    let drag = null;
    const keyboardMoves = {
      ArrowLeft: [-40, 0], ArrowRight: [40, 0], ArrowUp: [0, -40], ArrowDown: [0, 40],
    };

    handle.addEventListener("pointerdown", (event) => {
      if (drag || elements.assistantPanel.hidden || event.isPrimary === false || (event.pointerType === "mouse" && event.button !== 0)) return;
      const rect = elements.assistantPanel.getBoundingClientRect();
      drag = {
        pointerId: event.pointerId,
        offsetX: event.clientX - rect.left,
        offsetY: event.clientY - rect.top,
      };
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove", (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      setAssistantPosition(event.clientX - drag.offsetX, event.clientY - drag.offsetY);
    });
    const stopDrag = (event) => {
      if (drag && drag.pointerId === event.pointerId) drag = null;
    };
    handle.addEventListener("pointerup", stopDrag);
    handle.addEventListener("pointercancel", stopDrag);
    handle.addEventListener("lostpointercapture", stopDrag);
    handle.addEventListener("keydown", (event) => {
      const move = keyboardMoves[event.key];
      if (!move || elements.assistantPanel.hidden) return;
      const rect = elements.assistantPanel.getBoundingClientRect();
      const left = Number.parseFloat(elements.assistantPanel.style.left);
      const top = Number.parseFloat(elements.assistantPanel.style.top);
      event.preventDefault();
      setAssistantPosition((Number.isFinite(left) ? left : rect.left) + move[0], (Number.isFinite(top) ? top : rect.top) + move[1]);
    });
    handle.setAttribute("tabindex", "0");
    handle.setAttribute("aria-label", "旅行助手位置控制。可拖动，或使用方向键每次移动 40 像素。");
    window.addEventListener("resize", clampAssistantPosition);
    window.addEventListener("orientationchange", clampAssistantPosition);
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function addMessage(text, kind) {
    const message = document.createElement("p");
    message.className = `message ${kind}`;
    message.textContent = String(text || "");
    elements.messages.append(message);
    elements.messages.scrollTop = elements.messages.scrollHeight;
  }

  function appendExploreRecommendation(selection) {
    addMessage(selection.recommendation, "assistant");
  }

  function exploreItem(selection) {
    const data = window.TravelMapExplorer?.EXPLORE_TRIAL;
    if (!data || !selection) return null;
    if (selection.kind === "province") return data.provinces.find((item) => item.id === selection.id) || null;
    if (selection.kind === "city") return data.cities.find((item) => item.id === selection.id) || null;
    for (const city of data.cities) {
      const place = city.places.find((item) => item.id === selection.id);
      if (place) return place;
    }
    return null;
  }

  function renderSelectedPlace(item) {
    if (!item) return;
    clearChildren(elements.explorePlaceCard);
    const visual = document.createElement("div");
    visual.className = `selected-place-visual ${item.visual}`;
    visual.setAttribute("aria-hidden", "true");
    const copy = document.createElement("div");
    const label = document.createElement("p");
    label.className = "eyebrow";
    label.textContent = "本地景点推荐";
    const title = document.createElement("h3");
    title.textContent = item.name;
    const description = document.createElement("p");
    description.textContent = item.description;
    const recommendation = document.createElement("p");
    recommendation.textContent = item.recommendation;
    copy.append(label, title, description, recommendation);
    elements.explorePlaceCard.append(visual, copy);
    elements.explorePlaceCard.hidden = false;
  }

  function clearSelectedPlace() {
    clearChildren(elements.explorePlaceCard);
    elements.explorePlaceCard.hidden = true;
  }

  function clearCityWeatherCard() {
    if (cityWeatherCard && cityWeatherCard.parentNode) cityWeatherCard.parentNode.removeChild(cityWeatherCard);
    cityWeatherCard = null;
  }

  function renderCityWeatherCard(weather, fallbackCity) {
    clearCityWeatherCard();
    const card = document.createElement("article");
    card.className = "city-weather-card";
    const summary = document.createElement("p");
    const city = typeof weather?.city === "string" && weather.city.trim() ? weather.city : fallbackCity;
    const details = typeof weather?.summary === "string" && weather.summary.trim() ? weather.summary : "天气暂不可用";
    const status = WEATHER_STATUS_LABELS[weather?.status] || WEATHER_STATUS_LABELS.unavailable;
    const reportTime = formatWeatherReportTime(weather?.report_time);
    summary.textContent = `${city}：${status}；${details}；报告时间：${reportTime}`;
    card.append(summary);
    elements.explorePlaceCard.parentNode.append(card);
    cityWeatherCard = card;
  }

  function formatWeatherReportTime(value) {
    if (typeof value !== "string" || !value.trim()) return "无实时报告";
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/.exec(value.trim());
    if (!match) return "无实时报告";
    const zone = match[3] === "Z"
      ? "GMT+0"
      : `GMT${match[3].replace(":00", "").replace(/^\+0?/, "+").replace(/^-0?/, "-")}`;
    return `${match[1]} ${match[2]} ${zone}`;
  }

  async function renderCityWeather(cityId, fallbackCity) {
    let weather = state.cityWeather.get(cityId);
    if (!weather) {
      let weatherRequest = state.cityWeatherRequests.get(cityId);
      if (!weatherRequest) {
        weatherRequest = requestJson(`/api/weather/cities/${encodeURIComponent(cityId)}`)
          .catch((error) => error && error.code === "STALE_REQUEST"
            ? null
            : { city: fallbackCity, status: "unavailable", summary: "天气暂不可用" });
        state.cityWeatherRequests.set(cityId, weatherRequest);
      }
      weather = await weatherRequest;
      state.cityWeatherRequests.delete(cityId);
      if (!weather) return;
      state.cityWeather.set(cityId, weather);
    }
    if (state.selectedExploreCityId === cityId) renderCityWeatherCard(weather, fallbackCity);
  }

  function handleExploreSelection(selection) {
    if (!selection || typeof selection.recommendation !== "string") return;
    const item = exploreItem(selection);
    elements.exploreStatus.textContent = `已选择${selection.name}，Voyage AI 助手已准备本地建议。`;
    if (selection.kind === "city") {
      state.selectedExploreCityId = selection.id;
      renderCityWeather(selection.id, selection.name);
    }
    if (selection.kind === "place") renderSelectedPlace(item);
    appendExploreRecommendation(selection);
  }

  function renderExploreCards(view) {
    clearSelectedPlace();
    if (view.level !== "city") {
      state.selectedExploreCityId = null;
      clearCityWeatherCard();
    }
    elements.mapBreadcrumb.textContent = view.breadcrumb.join(" › ");
    elements.mapTitle.textContent = view.title;
    elements.recommendationsTitle.textContent = view.title;
    elements.recommendationCount.textContent = `${view.items.length} 个推荐`;
    clearChildren(elements.recommendationGrid);
    view.items.forEach((item) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "destination-card";
      const visual = document.createElement("span");
      visual.className = `destination-visual ${item.visual}`;
      visual.setAttribute("aria-hidden", "true");
      visual.textContent = item.name.slice(0, 2);
      const copy = document.createElement("span");
      copy.className = "destination-copy";
      const title = document.createElement("strong");
      title.textContent = item.name;
      const description = document.createElement("span");
      description.textContent = item.description;
      copy.append(title, description);
      card.append(visual, copy);
      card.addEventListener("click", () => {
        if (view.level === "nation") mapExplorer.showProvince(item.id);
        else if (view.level === "province") mapExplorer.showCity(item.id);
        else handleExploreSelection({ kind: "place", id: item.id, name: item.name, recommendation: item.recommendation });
      });
      elements.recommendationGrid.append(card);
    });
    renderExploreShortcuts(view.level === "province" ? new Set(view.items.map((item) => item.id)) : new Set());
  }

  function renderExploreShortcuts(excludedCityIds = new Set()) {
    const mapModule = window.TravelMapExplorer;
    clearChildren(elements.exploreShortcuts);
    (mapModule?.EXPLORE_TRIAL?.cities || []).forEach((city) => {
      if (excludedCityIds.has(city.id)) return;
      const button = document.createElement("button");
      button.type = "button";
      button.id = `explore-city-${city.id}`;
      button.className = "map-chip";
      button.textContent = city.name;
      button.addEventListener("click", () => mapExplorer ? mapExplorer.showCity(city.id) : handleExploreSelection({
        kind: "city", id: city.id, name: city.name, recommendation: city.recommendation,
      }));
      elements.exploreShortcuts.append(button);
    });
  }

  function initializeExplore() {
    const mapModule = window.TravelMapExplorer;
    if (mapModule && typeof mapModule.createMapExplorer === "function") {
      mapExplorer = mapModule.createMapExplorer(elements.exploreMap, {
        amapKey: window.TRAVEL_ASSISTANT_CONFIG?.amapJsKey || null,
        securityJsCode: window.TRAVEL_ASSISTANT_CONFIG?.amapSecurityJsCode || null,
        onSelect: handleExploreSelection,
        onStateChange: renderExploreCards,
      });
    } else {
      elements.exploreStatus.textContent = "地图组件暂未加载，可使用热门城市快捷入口。";
    }

    renderExploreShortcuts();
  }

  function browserAuthConfig() {
    const config = window.TRAVEL_ASSISTANT_CONFIG || {};
    if (typeof config.supabaseUrl !== "string" || typeof config.supabaseAnonKey !== "string") return null;
    const url = config.supabaseUrl.replace(/\/$/, "");
    return url.startsWith("https://") && config.supabaseAnonKey.trim() ? { url, anonKey: config.supabaseAnonKey } : null;
  }

  function authorizationHeaders(headers = {}) {
    if (!state.session || !state.session.access_token) return headers;
    return { ...headers, Authorization: `Bearer ${state.session.access_token}` };
  }

  function requestCurrencyIsCurrent(currency) {
    return currency.callerIsCurrent() && currency.generation === authGeneration
      && currency.revision === sessionRevision && stableUserId(state.session) === currency.userId;
  }

  function currencyAtRevision(currency, revision) {
    const next = { ...currency, allowRefresh: false, revision };
    next.isCurrent = () => requestCurrencyIsCurrent(next);
    return next;
  }

  function captureRequestCurrency(options) {
    const currency = {
      allowRefresh: true,
      callerIsCurrent: typeof options.isCurrent === "function" ? options.isCurrent : () => true,
      generation: authGeneration,
      revision: sessionRevision,
      userId: stableUserId(state.session),
    };
    currency.isCurrent = () => requestCurrencyIsCurrent(currency);
    return currency;
  }

  function staleRequestError() {
    const error = new Error("STALE_REQUEST");
    error.code = "STALE_REQUEST";
    return error;
  }

  function authenticationError(payload, sessionInvalidated = false) {
    const detail = payload && payload.detail;
    const authCode = detail && ["AUTH_REQUIRED", "AUTH_INVALID", "AUTH_UNAVAILABLE"].includes(detail.code)
      ? detail.code : "AUTH_INVALID";
    const error = new Error(authCode);
    error.code = authCode;
    error.status = 401;
    error.sessionInvalidated = sessionInvalidated;
    return error;
  }

  async function requestJson(path, options = {}, existingCurrency = null) {
    const currency = existingCurrency || captureRequestCurrency(options);
    if (!currency.isCurrent()) throw staleRequestError();
    let response;
    try {
      response = await fetch(path, {
        method: options.method || "GET",
        headers: authorizationHeaders({ "Content-Type": "application/json", ...(options.headers || {}) }),
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
    } catch (error) {
      if (!currency.isCurrent()) throw staleRequestError();
      throw error;
    }
    const payload = await response.json().catch(() => ({}));
    if (!currency.isCurrent()) throw staleRequestError();
    if (response.status === 401) {
      let sessionInvalidated = false;
      if (currency.allowRefresh && state.session) {
        const refreshResult = await refreshBrowserSession(currency);
        sessionInvalidated = refreshResult.sessionInvalidated;
        if (sessionInvalidated) throw authenticationError(payload, true);
        if (refreshResult.refreshed) {
          if (!refreshResult.currency.isCurrent()) throw staleRequestError();
          return requestJson(path, options, refreshResult.currency);
        }
        if (!currency.isCurrent()) throw staleRequestError();
        sessionInvalidated = !state.session;
      } else if (state.session) {
        sessionInvalidated = await signOutAndClearSession(currency.isCurrent);
        if (sessionInvalidated) throw authenticationError(payload, true);
        if (!currency.isCurrent()) throw staleRequestError();
      }
      throw authenticationError(payload, sessionInvalidated);
    }
    if (!response.ok) {
      const detail = payload && payload.detail;
      const code = (detail && detail.code) || payload.code || "REQUEST_FAILED";
      const error = new Error(code);
      error.code = code;
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function publicError(code) {
    const messages = {
      AUTH_REQUIRED: "请先登录后再管理私有行程。",
      AUTH_INVALID: "登录已过期，请重新登录。",
      AUTH_UNAVAILABLE: "账户服务暂不可用，请稍后重试。",
      AI_DAILY_LIMIT_REACHED: "今日规划额度已用完，请明天再试。",
      AI_GLOBAL_DAILY_LIMIT_REACHED: "规划服务当前繁忙，请稍后再试。",
      AI_DISABLED: "规划服务暂时关闭，请稍后再试。",
      AI_CIRCUIT_OPEN: "规划服务正在保护性恢复，请稍后再试。",
      AI_RATE_LIMITED: "请求过于频繁，请稍后再试。",
      AI_UNAVAILABLE: "规划服务暂不可用，请稍后再试。",
      CHAT_UNAVAILABLE: "规划服务暂不可用，请稍后再试。",
      TRIP_NOT_FOUND: "未找到该行程，可能已被删除。",
      SHARE_NOT_FOUND: "此分享链接已失效、过期或被撤销。",
    };
    return messages[code] || "请求暂时无法完成，请稍后重试。";
  }

  function showError(error) {
    if (error && error.code === "STALE_REQUEST") return;
    setState("error");
    setStatus(publicError(error && error.code), true);
  }

  function showAuthError(mode) {
    setState("error");
    setStatus(
      mode === "signup" ? "注册失败，请检查邮箱格式和密码要求。" : "登录失败，请检查邮箱和密码。",
      true,
    );
  }

  function showProviderNotice(warnings, itinerary = null) {
    if (!Array.isArray(warnings) || warnings.length === 0) {
      state.providerNoticeActive = false;
      elements.providerUpdatedAt.textContent = "";
      clearChildren(elements.providerNotice);
      elements.providerNotice.hidden = true;
      return;
    }
    const citation = canonicalCitations(itinerary)[0];
    clearChildren(elements.providerNotice);
    const summary = document.createElement("strong");
    summary.textContent = "部分外部信息暂不可用。";
    elements.providerUpdatedAt.textContent = citation
      ? ` 数据获取时间：${citation.fetched_at}；${citation.freshness}`
      : " 更新时间未知；数据可能降级。";
    const fallback = document.createElement("span");
    fallback.textContent = " 仍可查看不依赖实时数据的行程框架。";
    elements.providerNotice.append(summary, elements.providerUpdatedAt, fallback);
    state.providerNoticeActive = true;
    elements.providerNotice.hidden = state.activeView !== "explore";
  }

  function canonicalCitations(itinerary) {
    if (!itinerary || typeof itinerary !== "object") return [];
    const citations = Array.isArray(itinerary.citations) ? [...itinerary.citations] : [];
    for (const day of Array.isArray(itinerary.days) ? itinerary.days : []) {
      for (const slot of ["morning", "afternoon", "evening"]) {
        if (day && day[slot] && Array.isArray(day[slot].citations)) citations.push(...day[slot].citations);
      }
    }
    return citations.filter(isCanonicalCitation);
  }

  function renderProfile(profile) {
    state.profile = profile || {};
    clearChildren(elements.profileFields);
    for (const [key, label] of Object.entries(PROFILE_LABELS)) {
      const term = document.createElement("dt");
      term.textContent = label;
      const detail = document.createElement("dd");
      const raw = state.profile[key];
      detail.textContent = Array.isArray(raw) ? (raw.join("、") || "未填写") : (raw === null || raw === undefined || raw === "" ? "未填写" : String(raw));
      elements.profileFields.append(term, detail);
    }
    elements.profileCard.hidden = false;
  }

  function isCompleteProfile(profile) {
    return ["origin", "destination", "start_date", "end_date", "travelers", "budget_cny"].every((key) => profile && profile[key] !== null && profile[key] !== undefined && profile[key] !== "");
  }

  function appendTextBlock(parent, tag, text, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = String(text || "");
    parent.append(node);
    return node;
  }

  function allowedExternalUrl(url) {
    try {
      const raw = String(url);
      const authority = /^https:\/\/([^/?#]+)/i.exec(raw);
      const parsed = new URL(raw);
      if (
        parsed.protocol !== "https:" || !authority || authority[1].toLowerCase() !== parsed.hostname.toLowerCase()
        || parsed.username || parsed.password || parsed.port || !ALLOWED_EXTERNAL_HOSTS.has(parsed.hostname.toLowerCase())
      ) return null;
      return parsed;
    } catch (_) {
      return null;
    }
  }

  function safeExternalLink(url, text) {
    try {
      const parsed = allowedExternalUrl(url);
      if (!parsed) return null;
      const link = document.createElement("a");
      link.href = parsed.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `${text || parsed.hostname}（搜索跳转）`;
      return link;
    } catch (_) {
      return null;
    }
  }

  function isCanonicalCitation(citation) {
    if (!citation || typeof citation !== "object") return false;
    const fetchedAt = citation.fetched_at;
    return typeof citation.evidence_id === "string" && citation.evidence_id.trim()
      && typeof citation.fact === "string" && citation.fact.trim()
      && typeof fetchedAt === "string" && fetchedAt.trim() && !Number.isNaN(Date.parse(fetchedAt))
      && citation.freshness === `Fetched ${fetchedAt}; reference only.`
      && ["official", "government", "trusted_provider"].includes(citation.source_type)
      && Boolean(allowedExternalUrl(citation.source_url || citation.source));
  }

  function renderStructuredItinerary(itinerary) {
    const container = document.createElement("div");
    const title = itinerary && itinerary.title ? itinerary.title : "行程建议";
    elements.tripTitle.textContent = String(title);
    if (itinerary && itinerary.budget) {
      const budget = document.createElement("section");
      budget.className = "budget-card";
      appendTextBlock(budget, "h3", "预算估算");
      const list = document.createElement("ul");
      const standardBudgetKeys = Object.keys(BUDGET_LABELS);
      const extraBudgetKeys = Object.keys(itinerary.budget).filter((key) => (
        !standardBudgetKeys.includes(key)
        && !["trip_total", "traveler_count"].includes(key)
        && typeof itinerary.budget[key] === "number"
      ));
      for (const key of [...standardBudgetKeys, ...extraBudgetKeys]) {
        if (Object.prototype.hasOwnProperty.call(itinerary.budget, key)) {
          appendTextBlock(list, "li", `${BUDGET_LABELS[key] || key}：${itinerary.budget[key]} ${itinerary.budget.currency || "CNY"}`);
        }
      }
      budget.append(list);
      appendTextBlock(budget, "p", "以上为预算估算，不是实时价格、库存或余票。", "help-text");
      container.append(budget);
    }
    if (Array.isArray(itinerary && itinerary.notes) && itinerary.notes.length) {
      const notes = document.createElement("section");
      appendTextBlock(notes, "h3", "行程提醒");
      const list = document.createElement("ul");
      for (const note of itinerary.notes) appendTextBlock(list, "li", note);
      notes.append(list);
      container.append(notes);
    }
    const days = document.createElement("div");
    days.className = "itinerary-days";
    for (const day of Array.isArray(itinerary && itinerary.days) ? itinerary.days : []) {
      const card = document.createElement("article");
      card.className = "day-card";
      appendTextBlock(card, "h3", `日期：${day.date || "待确认"}`);
      if (day.weather && typeof day.weather === "object") {
        const weather = document.createElement("p");
        weather.className = "itinerary-weather";
        const summary = typeof day.weather.summary === "string" && day.weather.summary.trim()
          ? day.weather.summary : "天气暂不可用";
        const status = WEATHER_STATUS_LABELS[day.weather.status] || WEATHER_STATUS_LABELS.unavailable;
        weather.textContent = `天气类型：${status}；${summary}；报告时间：${formatWeatherReportTime(day.weather.report_time)}`;
        card.append(weather);
      }
      const slots = document.createElement("ul");
      for (const slot of ["morning", "afternoon", "evening"]) {
        const activity = day[slot];
        if (!activity) continue;
        const titleText = `${ACTIVITY_SLOT_LABELS[slot] || slot}：${activity.title || "待确认"} (${activity.start_time || ""}-${activity.end_time || ""})`;
        const item = appendTextBlock(slots, "li", titleText);
        if (Array.isArray(activity.notes)) {
          for (const note of activity.notes) appendTextBlock(item, "p", note, "activity-note");
        }
        renderSources(item, activity.citations);
      }
      card.append(slots);
      days.append(card);
    }
    if (days.childNodes.length) container.append(days);
    if (Array.isArray(itinerary && itinerary.assumptions) && itinerary.assumptions.length) {
      const assumptions = document.createElement("section");
      appendTextBlock(assumptions, "h3", "规划假设与待确认项");
      const list = document.createElement("ul");
      for (const assumption of itinerary.assumptions) {
        appendTextBlock(list, "li", assumption && assumption.description ? assumption.description : "待确认");
      }
      assumptions.append(list);
      container.append(assumptions);
    }
    const bookingLinks = itinerary && itinerary.booking_links;
    if (bookingLinks && typeof bookingLinks === "object") {
      const booking = document.createElement("section");
      appendTextBlock(booking, "h3", "第三方搜索入口");
      const list = document.createElement("ul");
      for (const [field, label] of [["train", "火车"], ["hotel", "酒店"], ["flight", "航班"]]) {
        const link = safeExternalLink(bookingLinks[field], label);
        if (!link) continue;
        const item = document.createElement("li");
        item.append(link);
        list.append(item);
      }
      if (list.childNodes.length) booking.append(list);
      if (bookingLinks.disclaimer) appendTextBlock(booking, "p", bookingLinks.disclaimer, "help-text");
      if (booking.childNodes.length) container.append(booking);
    }
    renderSources(container, itinerary && itinerary.citations);
    return container;
  }

  function renderSources(parent, citations) {
    if (!Array.isArray(citations) || citations.length === 0) return;
    const section = document.createElement("section");
    section.className = "source-list";
    appendTextBlock(section, "h3", "来源与更新时间");
    const list = document.createElement("ul");
    for (const citation of citations) {
      const item = document.createElement("li");
      if (!isCanonicalCitation(citation)) {
        item.textContent = "来源不可验证；更新时间未知。";
        list.append(item);
        continue;
      }
      item.append(safeExternalLink(citation.source_url || citation.source, citation.source_type));
      appendTextBlock(item, "p", `事实：${citation.fact}`);
      appendTextBlock(item, "p", `获取时间：${citation.fetched_at}`);
      appendTextBlock(item, "p", `新鲜度：${citation.freshness}`);
      list.append(item);
    }
    section.append(list);
    parent.append(section);
  }

  function renderReply(reply) {
    elements.tripTitle.textContent = "行程建议";
    const block = document.createElement("p");
    block.className = "message assistant";
    block.textContent = String(reply || "暂未返回行程内容。");
    return block;
  }

  function asItinerary(reply) {
    if (typeof reply !== "string") return null;
    try {
      const value = JSON.parse(reply);
      return value && typeof value === "object" && Array.isArray(value.days) ? value : null;
    } catch (_) {
      return null;
    }
  }

  function renderTrip(trip, options = {}) {
    clearChildren(elements.tripContent);
    const itinerary = trip && trip.itinerary && typeof trip.itinerary === "object" ? trip.itinerary : asItinerary(trip && trip.reply);
    elements.tripContent.append(itinerary ? renderStructuredItinerary(itinerary) : renderReply(trip && trip.reply));
    state.currentTrip = options.public ? null : trip;
    elements.tripActions.hidden = Boolean(options.public || !state.session);
    elements.save.hidden = Boolean(options.public || (trip && trip.id));
    elements.tripView.hidden = false;
    elements.profileCard.hidden = true;
    setState("planned");
  }

  async function sendMessage(event) {
    event.preventDefault();
    const message = elements.message.value.trim();
    if (!message || state.busy) return;
    addMessage(message, "user");
    elements.message.value = "";
    setBusy(true, "正在整理旅行资料…");
    setState("planning");
    setStatus("正在处理你的旅行需求。");
    try {
      const tripId = (state.currentTrip && state.currentTrip.id) || null;
      const body = { message, thread_id: state.threadId, action: "collect" };
      if (tripId) body.trip_id = tripId;
      const response = await requestJson("/api/chat", { method: "POST", body });
      showProviderNotice(response.warnings, null);
      if (response.stage === "planned" && response.itinerary && typeof response.itinerary === "object") {
        addMessage(response.reply, "assistant");
        state.profile = response.profile || state.profile || {};
        state.pendingResult = {
          reply: response.reply, profile: state.profile, itinerary: response.itinerary,
          trip_id: response.trip_id || tripId,
        };
        renderTrip({
          id: state.pendingResult.trip_id,
          title: response.itinerary.title || "行程建议",
          status: "planned",
          profile: state.profile,
          itinerary: response.itinerary,
        });
        setStatus("已根据保存的行程给出解释。", false);
        return;
      }
      addMessage(response.reply, "assistant");
      state.pendingResult = {
        reply: response.reply, profile: response.profile || {}, itinerary: null,
        trip_id: response.trip_id || tripId,
      };
      if (response.stage === "confirming" && isCompleteProfile(response.profile)) {
        renderProfile(response.profile);
        setState("confirming");
        setStatus("资料已收集，请确认后查看行程。", false);
      } else {
        elements.profileCard.hidden = true;
        setState("collecting");
        setStatus("已更新旅行资料；请继续补充缺少的信息。", false);
      }
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      if (state.name === "confirming") elements.confirm.focus();
      else elements.message.focus();
    }
  }

  async function confirmProfile() {
    if (state.busy || !state.pendingResult) return;
    setBusy(true, "正在生成行程建议…");
    setState("planning");
    setStatus("已确认资料，正在生成行程建议。", false);
    try {
      const body = { message: "confirm", thread_id: state.threadId, action: "confirm" };
      const tripId = state.pendingResult.trip_id || (state.currentTrip && state.currentTrip.id);
      if (tripId) body.trip_id = tripId;
      const response = await requestJson("/api/chat", { method: "POST", body });
      const itinerary = response.itinerary && typeof response.itinerary === "object"
        ? response.itinerary : asItinerary(response.reply);
      if (response.stage !== "planned" || !itinerary) {
        throw Object.assign(new Error("CHAT_UNAVAILABLE"), { code: "CHAT_UNAVAILABLE" });
      }
      state.profile = response.profile || state.profile || {};
      state.pendingResult = {
        reply: response.reply, profile: state.profile, itinerary,
        trip_id: response.trip_id || tripId || null,
      };
      showProviderNotice(response.warnings, itinerary);
      renderTrip({
        id: state.pendingResult.trip_id, title: itinerary.title || "行程建议", status: "planned",
        profile: state.profile, itinerary,
      });
      addMessage("行程已生成，可在下方查看。", "assistant");
      if (state.session && state.pendingResult.trip_id) await refreshHistory();
      setStatus(state.pendingResult.trip_id ? "行程已生成并保存。" : "行程已生成。", false);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function editProfile() {
    if (state.busy) return;
    setState("collecting");
    elements.profileCard.hidden = true;
    elements.message.focus();
    setStatus("请在对话中告诉我需要修改的资料。", false);
  }

  async function authRequest(mode, controls = {
    email: elements.email,
    password: elements.password,
  }) {
    if (state.busy) return;
    const config = browserAuthConfig();
    if (!config || !state.authClient) {
      setStatus("当前部署尚未配置浏览器认证；你仍可进行临时规划。", true);
      return;
    }
    const email = controls.email.value.trim();
    const password = controls.password.value;
    if (!email || !password) {
      setStatus("请输入邮箱和密码。", true);
      return;
    }
    setBusy(true, mode === "signup" ? "正在注册…" : "正在登录…");
    try {
      const operation = mode === "signup" ? state.authClient.auth.signUp({ email, password }) : state.authClient.auth.signInWithPassword({ email, password });
      const { data, error } = await operation;
      if (error) throw Object.assign(new Error("AUTH_FAILED"), { code: "AUTH_REQUIRED", status: 401 });
      if (!data || !data.session) {
        setStatus("注册请求已提交，请按邮箱提示完成验证后登录。", false);
        controls.password.value = "";
        return;
      }
      await applySession(data.session, { resetConversation: true });
      controls.password.value = "";
      if (!elements.authPage.hidden) await closeAuthPage();
    } catch (error) {
      showAuthError(mode);
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword() {
    if (state.busy) return;
    if (!state.authClient || typeof state.authClient.auth.resetPasswordForEmail !== "function") {
      setStatus("当前部署尚未配置密码重置服务，请稍后再试。", true);
      return;
    }
    const email = elements.authPageEmail.value.trim();
    if (!email) {
      setStatus("请先输入注册邮箱。", true);
      elements.authPageEmail.focus();
      return;
    }
    setBusy(true, "正在发送重置邮件…");
    try {
      const { error } = await state.authClient.auth.resetPasswordForEmail(email, {
        redirectTo: window.location.origin,
      });
      if (error) throw Object.assign(new Error("AUTH_FAILED"), { code: "AUTH_REQUIRED", status: 401 });
      setStatus("密码重置邮件已发送，请检查邮箱。", false);
    } catch (error) {
      setState("error");
      setStatus("密码重置失败，请检查邮箱后重试。", true);
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    if (state.busy) return;
    setBusy(true, "正在退出…");
    try {
      if (state.authClient) await state.authClient.auth.signOut();
    } finally {
      clearSession();
      setBusy(false);
      setStatus("已退出登录；临时对话不会保存到你的账户。", false);
    }
  }

  function clearAccountScopedState(options = {}) {
    invalidateTripsLoads();
    clearConversationState(options);
    state.renameTripId = null;
    state.shareTripId = null;
    elements.password.value = "";
    elements.email.value = "";
    clearChildren(elements.historyList);
    elements.history.hidden = true;
  }

  function clearSession() {
    authGeneration += 1;
    sessionRevision += 1;
    state.session = null;
    state.user = null;
    clearAccountScopedState({ showWelcome: false });
    elements.authFormPanel.hidden = false;
    elements.account.hidden = true;
    if (state.activeView === "trips") renderTripsPage();
    setState("signed_out");
  }

  function resetMessages() {
    clearChildren(elements.messages);
    addMessage("你好！我可以帮你规划国内 2 至 7 天的自由行。", "assistant");
  }

  function clearConversationState(options = {}) {
    state.profile = null;
    state.pendingResult = null;
    state.currentTrip = null;
    state.threadId = makeThreadId();
    clearChildren(elements.profileFields);
    clearChildren(elements.tripContent);
    elements.tripTitle.textContent = "";
    elements.providerUpdatedAt.textContent = "";
    clearChildren(elements.providerNotice);
    elements.shareLink.value = "";
    elements.shareExpiry.textContent = "";
    elements.renameInput.value = "";
    elements.message.value = "";
    elements.accountEmail.textContent = "";
    if (elements.shareDialog.open) elements.shareDialog.close();
    if (elements.renameDialog.open) elements.renameDialog.close();
    clearChildren(elements.messages);
    if (options.showWelcome !== false) resetMessages();
    elements.profileCard.hidden = true;
    elements.tripView.hidden = true;
    elements.tripActions.hidden = true;
    state.providerNoticeActive = false;
    elements.providerNotice.hidden = true;
  }

  function applySession(session, options = {}) {
    const identityChanged = identitiesDiffer(state.session, session);
    const tokensChanged = !sessionsShareTokens(state.session, session);
    const refreshTrips = identityChanged || options.refreshTrips !== false;
    if (tokensChanged) sessionRevision += 1;
    if (identityChanged) {
      authGeneration += 1;
      clearAccountScopedState();
    } else if (refreshTrips) {
      invalidateTripsLoads();
    }
    state.session = session;
    state.user = session.user || {};
    if (options.resetConversation && !identityChanged) clearConversationState();
    elements.accountEmail.textContent = state.user.email || "已登录账户";
    elements.authFormPanel.hidden = true;
    elements.account.hidden = false;
    elements.history.hidden = state.activeView !== "trips";
    setState("collecting");
    if (identityChanged || options.resetConversation) setStatus("已切换登录会话，请重新确认行程资料。", false);
    if (state.activeView === "trips" && refreshTrips) return renderTripsPage();
  }

  async function refreshBrowserSession(currency) {
    if (!state.authClient || !currency.isCurrent()) {
      return { refreshed: false, sessionInvalidated: false };
    }
    let request = refreshRequest;
    if (!request || request.generation !== currency.generation || request.revision !== currency.revision) {
      request = {
        acceptedRevision: null,
        generation: currency.generation,
        promise: null,
        revision: currency.revision,
        userId: currency.userId,
      };
      refreshRequest = request;
      request.promise = (async () => {
        try {
          return await state.authClient.auth.refreshSession();
        } catch (error) {
          return { data: null, error };
        }
      })().finally(() => {
        if (refreshRequest === request) refreshRequest = null;
      });
    }
    const { data, error } = await request.promise;
    if (error || !data || !data.session) {
      if (!currency.isCurrent()) return { refreshed: false, sessionInvalidated: false };
      const sessionInvalidated = await signOutAndClearSession(currency.isCurrent);
      return { refreshed: false, sessionInvalidated };
    }

    if (request.acceptedRevision !== null) {
      const nextCurrency = currencyAtRevision(currency, request.acceptedRevision);
      if (!nextCurrency.isCurrent() || !sessionsShareAccessToken(state.session, data.session)) {
        return { refreshed: false, sessionInvalidated: false };
      }
      return { refreshed: true, sessionInvalidated: false, currency: nextCurrency };
    }

    if (!currency.isCurrent()) {
      const resultAlreadyCurrent = request.generation === authGeneration
        && request.userId === stableUserId(state.session)
        && sessionsShareAccessToken(state.session, data.session);
      if (!resultAlreadyCurrent) return { refreshed: false, sessionInvalidated: false };
      request.acceptedRevision = sessionRevision;
      const nextCurrency = currencyAtRevision(currency, request.acceptedRevision);
      if (!nextCurrency.isCurrent()) return { refreshed: false, sessionInvalidated: false };
      return { refreshed: true, sessionInvalidated: false, currency: nextCurrency };
    }

    applySession(data.session, { refreshTrips: false });
    if (request.generation !== authGeneration || request.userId !== stableUserId(state.session)
      || !sessionsShareAccessToken(state.session, data.session)) {
      return { refreshed: false, sessionInvalidated: false };
    }
    request.acceptedRevision = sessionRevision;
    const nextCurrency = currencyAtRevision(currency, request.acceptedRevision);
    if (!nextCurrency.isCurrent()) return { refreshed: false, sessionInvalidated: false };
    return { refreshed: true, sessionInvalidated: false, currency: nextCurrency };
  }

  async function signOutAndClearSession(isCurrent = () => true) {
    if (!isCurrent()) return false;
    let cleared = false;
    try {
      if (state.authClient && state.authClient.auth && typeof state.authClient.auth.signOut === "function") {
        if (!isCurrent()) return false;
        await state.authClient.auth.signOut();
      }
    } catch (_) {
      // Local privacy cleanup is mandatory even if the SDK cannot reach auth.
    } finally {
      if (isCurrent()) {
        clearSession();
        cleared = true;
      }
    }
    return cleared || !state.session;
  }

  function requireAuthentication() {
    if (state.session) return true;
    setState("signed_out");
    setStatus("请先登录后再管理私有行程。", true);
    elements.email.focus();
    return false;
  }

  function renderTripsState(stateName) {
    const signedOut = stateName === "signed_out";
    elements.tripsAuthPrompt.hidden = !signedOut;
    elements.history.hidden = signedOut;
    if (signedOut) return;
    clearChildren(elements.historyList);
    if (stateName === "loading") {
      appendTextBlock(elements.historyList, "li", "正在加载行程…", "empty-state");
    } else if (stateName === "error") {
      const errorItem = document.createElement("li");
      errorItem.className = "empty-state";
      appendTextBlock(errorItem, "p", "行程加载失败，请重试。");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "重试";
      retry.addEventListener("click", refreshHistory);
      errorItem.append(retry);
      elements.historyList.append(errorItem);
    }
  }

  async function renderTripsPage() {
    if (!state.session) {
      renderTripsState("signed_out");
      return;
    }
    elements.tripsAuthPrompt.hidden = true;
    elements.history.hidden = false;
    await refreshHistory();
  }

  async function refreshHistory() {
    if (!state.session || state.activeView !== "trips") return;
    const loadGeneration = ++tripsLoadGeneration;
    const isCurrentLoad = () => loadGeneration === tripsLoadGeneration && Boolean(state.session) && state.activeView === "trips";
    renderTripsState("loading");
    try {
      const trips = await requestJson("/api/trips", { isCurrent: isCurrentLoad });
      if (!isCurrentLoad()) return;
      clearChildren(elements.historyList);
      if (!Array.isArray(trips) || trips.length === 0) {
        appendTextBlock(elements.historyList, "li", "还没有保存的行程。", "empty-state");
        return;
      }
      for (const trip of trips) elements.historyList.append(historyItem(trip));
    } catch (error) {
      if (error && error.sessionInvalidated) {
        showError(error);
        return;
      }
      if (!isCurrentLoad()) return;
      renderTripsState("error");
    }
  }

  function historyItem(trip) {
    const item = document.createElement("li");
    appendTextBlock(item, "strong", trip.title || "未命名行程");
    appendTextBlock(item, "p", trip.updated_at ? `更新于 ${new Date(trip.updated_at).toLocaleString("zh-CN")}` : "刚刚更新");
    const controls = document.createElement("div");
    controls.className = "button-row";
    for (const [label, operation] of [["打开", "open"], ["重命名", "rename"], ["复制", "copy"], ["删除", "delete"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = operation === "delete" ? "danger" : "secondary";
      button.textContent = label;
      button.disabled = state.busy;
      button.addEventListener("click", () => historyOperation(operation, trip));
      controls.append(button);
    }
    item.append(controls);
    return item;
  }

  async function historyOperation(operation, trip) {
    if (!requireAuthentication() || state.busy) return;
    if (operation === "open") {
      setBusy(true, "正在打开行程…");
      try {
        renderTrip(await requestJson(`/api/trips/${encodeURIComponent(trip.id)}`));
        await switchView("explore", { focusHeading: true });
      } catch (error) { showError(error); } finally { setBusy(false); }
      return;
    }
    if (operation === "rename") {
      state.renameTripId = trip.id;
      elements.renameInput.value = trip.title || "";
      elements.renameDialog.showModal();
      elements.renameInput.focus();
      return;
    }
    if (operation === "delete") {
      if (!window.confirm(`删除“${trip.title || "该行程"}”？此操作无法恢复。`)) return;
      setBusy(true, "正在删除行程…");
      try {
        await requestJson(`/api/trips/${encodeURIComponent(trip.id)}`, { method: "DELETE" });
        if (state.currentTrip && state.currentTrip.id === trip.id) {
          state.currentTrip = null;
          state.pendingResult = null;
          clearChildren(elements.tripContent);
          elements.tripTitle.textContent = "";
          elements.tripActions.hidden = true;
          elements.tripView.hidden = true;
          setState("collecting");
        }
        if (state.shareTripId === trip.id) {
          state.shareTripId = null;
          elements.shareLink.value = "";
          elements.shareExpiry.textContent = "";
          elements.shareDialog.close();
        }
        await refreshHistory();
        setStatus("行程已删除。", false);
      } catch (error) { showError(error); } finally { setBusy(false); }
      return;
    }
    if (operation === "copy") {
      setBusy(true, "正在复制行程…");
      try {
        await requestJson(`/api/trips/${encodeURIComponent(trip.id)}/copy`, { method: "POST" });
        await refreshHistory(); setStatus("已复制行程。", false);
      } catch (error) { showError(error); } finally { setBusy(false); }
    }
  }

  async function renameTrip(event) {
    event.preventDefault();
    if (state.busy) return;
    const title = elements.renameInput.value.trim();
    if (!state.renameTripId || !title) return;
    setBusy(true, "正在更新名称…");
    try {
      await requestJson(`/api/trips/${encodeURIComponent(state.renameTripId)}`, { method: "PATCH", body: { title } });
      elements.renameDialog.close(); state.renameTripId = null; await refreshHistory(); setStatus("行程名称已更新。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
  }

  async function saveTrip() {
    if (!requireAuthentication() || state.busy) return;
    if (state.currentTrip && state.currentTrip.id) {
      setStatus("行程已由服务器保存到你的私有历史。", false);
      return;
    }
    setStatus("请先在对话中确认资料；已确认的行程会由服务器自动保存。", true);
  }

  async function createShare() {
    if (state.busy) return;
    if (!requireAuthentication() || !state.currentTrip || !state.currentTrip.id) {
      setStatus("请先保存行程后再创建分享链接。", true);
      return;
    }
    setBusy(true, "正在创建分享链接…");
    try {
      const response = await requestJson(`/api/trips/${encodeURIComponent(state.currentTrip.id)}/share`, { method: "POST", body: { expires_in_days: 30 } });
      const url = new URL(window.location.href);
      url.hash = `share=${encodeURIComponent(response.token)}`;
      elements.shareLink.value = url.toString();
      elements.shareExpiry.textContent = `有效期至 ${new Date(Date.now() + 30 * 86400000).toLocaleString("zh-CN")}。`;
      state.shareTripId = state.currentTrip.id;
      elements.shareDialog.showModal();
      elements.shareLink.focus();
    } catch (error) { showError(error); } finally { setBusy(false); }
  }

  async function revokeShare() {
    if (state.busy || !state.shareTripId) return;
    setBusy(true, "正在撤销分享链接…");
    try {
      await requestJson(`/api/trips/${encodeURIComponent(state.shareTripId)}/share`, { method: "DELETE" });
      elements.shareDialog.close();
      setStatus("分享链接已撤销。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
  }

  async function copyShareLink() {
    if (state.busy) return;
    try {
      await navigator.clipboard.writeText(elements.shareLink.value);
      setStatus("分享链接已复制。", false);
    } catch (_) {
      elements.shareLink.select();
      setStatus("请手动复制分享链接。", false);
    }
  }

  async function showPublicShare() {
    if (state.busy) return false;
    const match = /^#share=([^&]+)$/.exec(window.location.hash);
    if (!match) return false;
    const shareHash = window.location.hash;
    const isCurrentShare = () => publicShareActive && window.location.hash === shareHash;
    const token = decodeURIComponent(match[1]);
    setPublicShareMode(true);
    elements.explorePage.hidden = true;
    elements.tripsPage.hidden = true;
    elements.communityPage.hidden = true;
    elements.history.hidden = true;
    setBusy(true, "正在打开只读分享…");
    try {
      const trip = await requestJson("/api/shared/resolve", { method: "POST", body: { token }, isCurrent: isCurrentShare });
      renderTrip(trip, { public: true });
      setStatus("这是只读分享视图，不包含账户信息或聊天记录。", false);
    } catch (error) {
      if (isCurrentShare()) showError(error);
    } finally {
      if (isCurrentShare()) setBusy(false);
    }
    return true;
  }

  async function initializeAuth() {
    const config = browserAuthConfig();
    if (!config || !window.supabase || typeof window.supabase.createClient !== "function") return;
    state.authClient = window.supabase.createClient(config.url, config.anonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    state.authClient.auth.onAuthStateChange((event, session) => {
      if (event === "SIGNED_OUT") clearSession();
      else if (session) applySession(session);
    });
    const { data, error } = await state.authClient.auth.getSession();
    if (!error && data && data.session) {
      await applySession(data.session);
    }
  }

  function initializeExploreOnce() {
    if (exploreInitialized) return;
    exploreInitialized = true;
    initializeExplore();
  }

  function initializeAuthOnce() {
    if (!authInitializationPromise) authInitializationPromise = initializeAuth();
    return authInitializationPromise;
  }

  function setPublicShareMode(active) {
    publicShareActive = active;
    elements.navigationPanel.hidden = active;
    elements.authPanel.hidden = active;
    elements.chatPanel.hidden = active;
    elements.assistantToggle.hidden = active;
    elements.authPage.hidden = active;
    setAssistantOpen(false);
  }

  function exitPublicShareMode() {
    setPublicShareMode(false);
    const url = new URL(window.location.href);
    url.hash = "";
    window.history.replaceState(null, "", url.toString());
    setBusy(false);
    clearConversationState();
    setState("signed_out");
    setStatus("");
  }

  async function initializeNormalApp({ focusHeading = false } = {}) {
    setPublicShareMode(false);
    initializeExploreOnce();
    await initializeAuthOnce();
    await switchView("explore", { focusHeading });
  }

  async function initializeApp() {
    if (await showPublicShare()) return;
    await initializeNormalApp();
  }

  elements.chatForm.addEventListener("submit", sendMessage);
  elements.brand.addEventListener("click", async (event) => {
    event.preventDefault();
    if (publicShareActive || /^#share=([^&]+)$/.test(window.location.hash)) exitPublicShareMode();
    await initializeNormalApp({ focusHeading: true });
  });
  for (const button of elements.navigation) button.addEventListener("click", () => switchView(button.dataset.view, { focusHeading: true }));
  elements.assistantToggle.addEventListener("click", () => {
    if (state.busy) return;
    const open = elements.assistantPanel.hidden;
    setAssistantOpen(open, { focusInput: open });
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !state.busy && !elements.assistantPanel.hidden) setAssistantOpen(false, { restoreFocus: true });
  });
  elements.confirm.addEventListener("click", confirmProfile);
  elements.edit.addEventListener("click", editProfile);
  elements.authForm.addEventListener("submit", (event) => { event.preventDefault(); authRequest("signin"); });
  elements.signUp.addEventListener("click", () => openAuthPage("signup"));
  elements.accountPageLink.addEventListener("click", () => openAuthPage("signin"));
  elements.authPageForm.addEventListener("submit", (event) => {
    event.preventDefault();
    authRequest(authPageMode, { email: elements.authPageEmail, password: elements.authPagePassword });
  });
  elements.authPageAlternate.addEventListener("click", () => {
    setAuthPageMode(authPageMode === "signup" ? "signin" : "signup");
    elements.authPageTitle.focus();
  });
  elements.authPageForgot.addEventListener("click", resetPassword);
  elements.signOut.addEventListener("click", signOut);
  elements.tripsLogin.addEventListener("click", () => {
    elements.accountMenu.open = true;
    elements.email.focus();
  });
  elements.save.addEventListener("click", saveTrip);
  elements.share.addEventListener("click", createShare);
  elements.copyShare.addEventListener("click", copyShareLink);
  elements.revokeShare.addEventListener("click", revokeShare);
  elements.closeShare.addEventListener("click", () => { if (!state.busy) elements.shareDialog.close(); });
  elements.renameForm.addEventListener("submit", renameTrip);
  elements.cancelRename.addEventListener("click", () => { if (!state.busy) elements.renameDialog.close(); });
  elements.assistantReset.setAttribute("aria-label", "重置 AI 助手位置");
  elements.assistantReset.addEventListener("click", resetAssistantPosition);
  initializeAssistantDrag();
  setAssistantOpen(false);
  initializeApp();
})();
