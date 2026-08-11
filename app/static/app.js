(() => {
  "use strict";

  const STATES = new Set(["signed_out", "collecting", "confirming", "planning", "planned", "error"]);
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
    "www.12306.cn", "www.ctrip.com",
  ]);
  const $ = (id) => document.getElementById(id);
  const elements = {
    body: document.body, authForm: $("auth-form"), email: $("email"), password: $("password"),
    signIn: $("sign-in-button"), signUp: $("sign-up-button"), signOut: $("sign-out-button"),
    account: $("account-summary"), accountEmail: $("account-email"), authFormPanel: $("auth-form"),
    authHelp: $("auth-help"), status: $("status-message"), providerNotice: $("provider-notice"),
    providerUpdatedAt: $("provider-updated-at"), chatForm: $("chat-form"), message: $("message-input"),
    send: $("send-button"), progress: $("request-progress"), messages: $("chat-messages"),
    assistantPanel: $("assistant-panel"), assistantToggle: $("assistant-toggle"), assistantReset: $("assistant-reset-position"),
    explorePage: $("explore-page"), exploreMap: $("explore-map"), exploreStatus: $("explore-status"),
    mapBreadcrumb: $("map-breadcrumb"), mapTitle: $("map-title"), exploreShortcuts: $("explore-shortcuts"),
    recommendationsTitle: $("recommendations-title"), recommendationCount: $("recommendation-count"),
    recommendationGrid: $("recommendation-grid"), explorePlaceCard: $("explore-place-card"),
    profileCard: $("profile-confirmation"), profileFields: $("profile-fields"), confirm: $("confirm-profile-button"),
    edit: $("edit-profile-button"), tripView: $("trip-view"), tripTitle: $("trip-title"),
    tripContent: $("trip-content"), tripActions: $("trip-actions"), save: $("save-trip-button"),
    share: $("share-trip-button"), history: $("trip-history"), historyList: $("trip-history-list"),
    shareDialog: $("share-dialog"), shareLink: $("share-link"), shareExpiry: $("share-expiry"),
    copyShare: $("copy-share-link"), revokeShare: $("revoke-share-link"), closeShare: $("close-share-dialog"), renameDialog: $("rename-dialog"),
    renameForm: $("rename-form"), renameInput: $("rename-input"), cancelRename: $("cancel-rename"),
  };
  const state = {
    name: "signed_out", busy: false, session: null, authClient: null, user: null, profile: null,
    pendingResult: null, currentTrip: null, renameTripId: null, shareTripId: null,
    threadId: makeThreadId(),
  };
  let refreshPromise = null;
  let mapExplorer = null;

  function makeThreadId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") return window.crypto.randomUUID();
    return `thread-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function setState(next) {
    if (!STATES.has(next)) throw new Error("Invalid page state");
    state.name = next;
    elements.body.dataset.appState = next;
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
    elements.assistantPanel.hidden = !open;
    if (open) clampAssistantPosition();
    elements.assistantToggle.setAttribute("aria-expanded", String(open));
    elements.assistantToggle.setAttribute("aria-label", open ? "关闭 AI 助手" : "打开 AI 助手");
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

    handle.addEventListener("pointerdown", (event) => {
      if (elements.assistantPanel.hidden || event.isPrimary === false || (event.pointerType === "mouse" && event.button !== 0)) return;
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

  function handleExploreSelection(selection) {
    if (!selection || typeof selection.recommendation !== "string") return;
    const item = exploreItem(selection);
    elements.exploreStatus.textContent = `已选择${selection.name}，Voyage AI 助手已准备本地建议。`;
    if (selection.kind === "place") renderSelectedPlace(item);
    appendExploreRecommendation(selection);
  }

  function renderExploreCards(view) {
    clearSelectedPlace();
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

  async function requestJson(path, options = {}, allowRefresh = true) {
    const response = await fetch(path, {
      method: options.method || "GET",
      headers: authorizationHeaders({ "Content-Type": "application/json", ...(options.headers || {}) }),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      if (allowRefresh && state.session) {
        const refreshed = await refreshBrowserSession();
        if (refreshed) return requestJson(path, options, false);
      } else if (state.session) {
        await signOutAndClearSession();
      }
      const detail = payload && payload.detail;
      const authCode = detail && ["AUTH_REQUIRED", "AUTH_INVALID", "AUTH_UNAVAILABLE"].includes(detail.code)
        ? detail.code : "AUTH_INVALID";
      const authError = new Error(authCode);
      authError.code = authCode;
      authError.status = 401;
      throw authError;
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
    setState("error");
    setStatus(publicError(error && error.code), true);
  }

  function showProviderNotice(warnings, itinerary = null) {
    if (!Array.isArray(warnings) || warnings.length === 0) {
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
    elements.providerNotice.hidden = false;
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

  async function authRequest(mode) {
    if (state.busy) return;
    const config = browserAuthConfig();
    if (!config || !state.authClient) {
      setStatus("当前部署尚未配置浏览器认证；你仍可进行临时规划。", true);
      return;
    }
    const email = elements.email.value.trim();
    const password = elements.password.value;
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
        return;
      }
      applySession(data.session, { resetConversation: true });
      elements.password.value = "";
      await refreshHistory();
    } catch (error) {
      showError(error);
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

  function clearSession() {
    state.session = null;
    state.user = null;
    clearConversationState({ showWelcome: false });
    state.profile = null;
    state.pendingResult = null;
    state.currentTrip = null;
    state.renameTripId = null;
    state.shareTripId = null;
    elements.password.value = "";
    elements.email.value = "";
    elements.authFormPanel.hidden = false;
    elements.account.hidden = true;
    clearChildren(elements.historyList);
    elements.history.hidden = true;
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
    elements.providerNotice.hidden = true;
  }

  function applySession(session, options = {}) {
    state.session = session;
    state.user = session.user || {};
    if (options.resetConversation) clearConversationState();
    elements.accountEmail.textContent = state.user.email || "已登录账户";
    elements.authFormPanel.hidden = true;
    elements.account.hidden = false;
    elements.history.hidden = false;
    setState("collecting");
    if (options.resetConversation) setStatus("已切换登录会话，请重新确认行程资料。", false);
  }

  async function refreshBrowserSession() {
    if (!state.authClient) return false;
    if (!refreshPromise) {
      refreshPromise = (async () => {
        try {
          const { data, error } = await state.authClient.auth.refreshSession();
          if (error || !data || !data.session) {
            await signOutAndClearSession();
            return false;
          }
          applySession(data.session);
          return true;
        } catch (_) {
          await signOutAndClearSession();
          return false;
        }
      })().finally(() => { refreshPromise = null; });
    }
    return refreshPromise;
  }

  async function signOutAndClearSession() {
    try {
      if (state.authClient && state.authClient.auth && typeof state.authClient.auth.signOut === "function") {
        await state.authClient.auth.signOut();
      }
    } catch (_) {
      // Local privacy cleanup is mandatory even if the SDK cannot reach auth.
    } finally {
      clearSession();
    }
  }

  function requireAuthentication() {
    if (state.session) return true;
    setState("signed_out");
    setStatus("请先登录后再管理私有行程。", true);
    elements.email.focus();
    return false;
  }

  async function refreshHistory() {
    if (!state.session) return;
    try {
      const trips = await requestJson("/api/trips");
      clearChildren(elements.historyList);
      if (!Array.isArray(trips) || trips.length === 0) {
        appendTextBlock(elements.historyList, "li", "还没有保存的行程。", "empty-state");
        return;
      }
      for (const trip of trips) elements.historyList.append(historyItem(trip));
    } catch (error) {
      showError(error);
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
      try { renderTrip(await requestJson(`/api/trips/${encodeURIComponent(trip.id)}`)); } catch (error) { showError(error); } finally { setBusy(false); }
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
    const token = decodeURIComponent(match[1]);
    setBusy(true, "正在打开只读分享…");
    try {
      const trip = await requestJson("/api/shared/resolve", { method: "POST", body: { token } });
      renderTrip(trip, { public: true });
      elements.messages.closest("section").hidden = true;
      setAssistantOpen(false);
      elements.assistantToggle.hidden = true;
      elements.explorePage.hidden = true;
      elements.history.hidden = true;
      setStatus("这是只读分享视图，不包含账户信息或聊天记录。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
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
      applySession(data.session);
      await refreshHistory();
    }
  }

  async function initializeApp() {
    if (await showPublicShare()) return;
    initializeExplore();
    await initializeAuth();
  }

  elements.chatForm.addEventListener("submit", sendMessage);
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
  elements.signUp.addEventListener("click", () => authRequest("signup"));
  elements.signOut.addEventListener("click", signOut);
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
