(() => {
  "use strict";

  const STATES = new Set(["signed_out", "collecting", "confirming", "planning", "planned", "error"]);
  const PROFILE_LABELS = {
    origin: "出发地", destination: "目的地", start_date: "出发日期", end_date: "返回日期",
    travelers: "出行人数", budget_cny: "总预算（元）", preferences: "偏好", constraints: "限制",
  };
  const $ = (id) => document.getElementById(id);
  const elements = {
    body: document.body, authForm: $("auth-form"), email: $("email"), password: $("password"),
    signIn: $("sign-in-button"), signUp: $("sign-up-button"), signOut: $("sign-out-button"),
    account: $("account-summary"), accountEmail: $("account-email"), authFormPanel: $("auth-form"),
    authHelp: $("auth-help"), status: $("status-message"), providerNotice: $("provider-notice"),
    providerUpdatedAt: $("provider-updated-at"), chatForm: $("chat-form"), message: $("message-input"),
    send: $("send-button"), progress: $("request-progress"), messages: $("chat-messages"),
    profileCard: $("profile-confirmation"), profileFields: $("profile-fields"), confirm: $("confirm-profile-button"),
    edit: $("edit-profile-button"), tripView: $("trip-view"), tripTitle: $("trip-title"),
    tripContent: $("trip-content"), tripActions: $("trip-actions"), save: $("save-trip-button"),
    share: $("share-trip-button"), history: $("trip-history"), historyList: $("trip-history-list"),
    shareDialog: $("share-dialog"), shareLink: $("share-link"), shareExpiry: $("share-expiry"),
    copyShare: $("copy-share-link"), revokeShare: $("revoke-share-link"), renameDialog: $("rename-dialog"),
    renameForm: $("rename-form"), renameInput: $("rename-input"), cancelRename: $("cancel-rename"),
  };
  const state = {
    name: "signed_out", busy: false, accessToken: null, user: null, profile: null,
    pendingResult: null, currentTrip: null, renameTripId: null, shareTripId: null,
    threadId: makeThreadId(),
  };

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
    for (const control of [elements.send, elements.signIn, elements.signUp, elements.signOut, elements.confirm, elements.edit, elements.save, elements.share]) {
      if (control) control.disabled = busy;
    }
    elements.progress.textContent = busy ? message || "正在处理，请稍候。" : "";
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

  function browserAuthConfig() {
    const config = window.TRAVEL_ASSISTANT_CONFIG || {};
    if (typeof config.supabaseUrl !== "string" || typeof config.supabaseAnonKey !== "string") return null;
    const url = config.supabaseUrl.replace(/\/$/, "");
    return url.startsWith("https://") && config.supabaseAnonKey.trim() ? { url, anonKey: config.supabaseAnonKey } : null;
  }

  function authorizationHeaders(headers = {}) {
    if (!state.accessToken) return headers;
    return { ...headers, Authorization: `Bearer ${state.accessToken}` };
  }

  async function requestJson(path, options = {}) {
    const response = await fetch(path, {
      method: options.method || "GET",
      headers: authorizationHeaders({ "Content-Type": "application/json", ...(options.headers || {}) }),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const payload = await response.json().catch(() => ({}));
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
    if (error && error.status === 401) clearSession();
    setState("error");
    setStatus(publicError(error && error.code), true);
  }

  function showProviderNotice(warnings) {
    if (!Array.isArray(warnings) || warnings.length === 0) {
      elements.providerNotice.hidden = true;
      return;
    }
    elements.providerUpdatedAt.textContent = ` 数据更新于 ${new Date().toLocaleString("zh-CN")}。`;
    elements.providerNotice.hidden = false;
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

  function safeExternalLink(url, text) {
    try {
      const parsed = new URL(String(url));
      if (parsed.protocol !== "https:") return null;
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

  function renderStructuredItinerary(itinerary) {
    const container = document.createElement("div");
    const title = itinerary && itinerary.title ? itinerary.title : "行程建议";
    elements.tripTitle.textContent = String(title);
    if (itinerary && itinerary.budget) {
      const budget = document.createElement("section");
      budget.className = "budget-card";
      appendTextBlock(budget, "h3", "预算估算");
      const list = document.createElement("ul");
      for (const key of ["transport", "hotel", "food", "tickets", "reserve", "other", "total"]) {
        if (Object.prototype.hasOwnProperty.call(itinerary.budget, key)) {
          appendTextBlock(list, "li", `${key}: ${itinerary.budget[key]} ${itinerary.budget.currency || "CNY"}`);
        }
      }
      budget.append(list);
      appendTextBlock(budget, "p", "以上为预算估算，不是实时价格、库存或余票。", "help-text");
      container.append(budget);
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
        const titleText = `${slot}: ${activity.title || "待确认"} (${activity.start_time || ""}-${activity.end_time || ""})`;
        appendTextBlock(slots, "li", titleText);
      }
      card.append(slots);
      days.append(card);
    }
    if (days.childNodes.length) container.append(days);
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
      const link = safeExternalLink(citation && (citation.source_url || citation.source), citation && citation.source_type);
      if (link) item.append(link);
      appendTextBlock(item, "span", citation && citation.fetched_at ? `；获取时间：${citation.fetched_at}` : "；仅供参考");
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
    elements.tripActions.hidden = Boolean(options.public || !state.accessToken);
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
      const response = await requestJson("/api/chat", { method: "POST", body: { message, thread_id: state.threadId } });
      addMessage(response.reply, "assistant");
      showProviderNotice(response.warnings);
      state.pendingResult = { reply: response.reply, profile: response.profile || {}, itinerary: asItinerary(response.reply) };
      if (isCompleteProfile(response.profile)) {
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
      elements.message.focus();
    }
  }

  function confirmProfile() {
    if (!state.pendingResult) return;
    renderTrip(state.pendingResult);
    setStatus("已根据确认资料展示行程建议。", false);
  }

  function editProfile() {
    setState("collecting");
    elements.profileCard.hidden = true;
    elements.message.focus();
    setStatus("请在对话中告诉我需要修改的资料。", false);
  }

  async function authRequest(mode) {
    const config = browserAuthConfig();
    if (!config) {
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
      const endpoint = mode === "signup" ? "/auth/v1/signup" : "/auth/v1/token?grant_type=password";
      const response = await fetch(`${config.url}${endpoint}`, {
        method: "POST", headers: { apikey: config.anonKey, "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(new Error("AUTH_FAILED"), { code: "AUTH_REQUIRED", status: response.status });
      if (!payload.access_token) {
        setStatus("注册请求已提交，请按邮箱提示完成验证后登录。", false);
        return;
      }
      state.accessToken = payload.access_token;
      state.user = payload.user || {};
      elements.password.value = "";
      elements.accountEmail.textContent = state.user.email || email;
      elements.authFormPanel.hidden = true;
      elements.account.hidden = false;
      elements.history.hidden = false;
      setState("collecting");
      setStatus("已登录。现在可以保存、管理和分享行程。", false);
      await refreshHistory();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    const config = browserAuthConfig();
    const token = state.accessToken;
    setBusy(true, "正在退出…");
    try {
      if (config && token) {
        await fetch(`${config.url}/auth/v1/logout`, { method: "POST", headers: { apikey: config.anonKey, Authorization: `Bearer ${token}` } });
      }
    } finally {
      clearSession();
      setBusy(false);
      setStatus("已退出登录；临时对话不会保存到你的账户。", false);
    }
  }

  function clearSession() {
    state.accessToken = null;
    state.user = null;
    state.currentTrip = null;
    state.threadId = makeThreadId();
    elements.password.value = "";
    elements.authFormPanel.hidden = false;
    elements.account.hidden = true;
    elements.history.hidden = true;
    clearChildren(elements.historyList);
    setState("signed_out");
  }

  function requireAuthentication() {
    if (state.accessToken) return true;
    setState("signed_out");
    setStatus("请先登录后再管理私有行程。", true);
    elements.email.focus();
    return false;
  }

  async function refreshHistory() {
    if (!state.accessToken) return;
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
      try { await requestJson(`/api/trips/${encodeURIComponent(trip.id)}`, { method: "DELETE" }); await refreshHistory(); setStatus("行程已删除。", false); } catch (error) { showError(error); } finally { setBusy(false); }
      return;
    }
    if (operation === "copy") {
      setBusy(true, "正在复制行程…");
      try {
        const original = await requestJson(`/api/trips/${encodeURIComponent(trip.id)}`);
        const created = await requestJson("/api/trips", { method: "POST", body: { profile: original.profile || {} } });
        await requestJson(`/api/trips/${encodeURIComponent(created.id)}`, { method: "PATCH", body: { title: `${original.title || "行程"}（副本）`, status: original.status, itinerary: original.itinerary } });
        await refreshHistory(); setStatus("已复制行程。", false);
      } catch (error) { showError(error); } finally { setBusy(false); }
    }
  }

  async function renameTrip(event) {
    event.preventDefault();
    const title = elements.renameInput.value.trim();
    if (!state.renameTripId || !title) return;
    setBusy(true, "正在更新名称…");
    try {
      await requestJson(`/api/trips/${encodeURIComponent(state.renameTripId)}`, { method: "PATCH", body: { title } });
      elements.renameDialog.close(); state.renameTripId = null; await refreshHistory(); setStatus("行程名称已更新。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
  }

  async function saveTrip() {
    if (!requireAuthentication() || !state.profile || state.busy) return;
    setBusy(true, "正在保存行程…");
    try {
      const itinerary = asItinerary(state.pendingResult && state.pendingResult.reply) || state.pendingResult && state.pendingResult.itinerary;
      const created = await requestJson("/api/trips", { method: "POST", body: { profile: state.profile } });
      const saved = await requestJson(`/api/trips/${encodeURIComponent(created.id)}`, { method: "PATCH", body: { status: "planned", itinerary: itinerary || { summary: state.pendingResult && state.pendingResult.reply } } });
      state.currentTrip = saved;
      await refreshHistory();
      setStatus("行程已保存到你的私有历史。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
  }

  async function createShare() {
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
    if (!state.shareTripId) return;
    setBusy(true, "正在撤销分享链接…");
    try {
      await requestJson(`/api/trips/${encodeURIComponent(state.shareTripId)}/share`, { method: "DELETE" });
      elements.shareDialog.close();
      setStatus("分享链接已撤销。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
  }

  async function copyShareLink() {
    try {
      await navigator.clipboard.writeText(elements.shareLink.value);
      setStatus("分享链接已复制。", false);
    } catch (_) {
      elements.shareLink.select();
      setStatus("请手动复制分享链接。", false);
    }
  }

  async function showPublicShare() {
    const match = /^#share=([^&]+)$/.exec(window.location.hash);
    if (!match) return false;
    const token = decodeURIComponent(match[1]);
    setBusy(true, "正在打开只读分享…");
    try {
      const trip = await requestJson(`/api/shared/${encodeURIComponent(token)}`);
      renderTrip(trip, { public: true });
      elements.messages.closest("section").hidden = true;
      elements.history.hidden = true;
      setStatus("这是只读分享视图，不包含账户信息或聊天记录。", false);
    } catch (error) { showError(error); } finally { setBusy(false); }
    return true;
  }

  elements.chatForm.addEventListener("submit", sendMessage);
  elements.confirm.addEventListener("click", confirmProfile);
  elements.edit.addEventListener("click", editProfile);
  elements.authForm.addEventListener("submit", (event) => { event.preventDefault(); authRequest("signin"); });
  elements.signUp.addEventListener("click", () => authRequest("signup"));
  elements.signOut.addEventListener("click", signOut);
  elements.save.addEventListener("click", saveTrip);
  elements.share.addEventListener("click", createShare);
  elements.copyShare.addEventListener("click", copyShareLink);
  elements.revokeShare.addEventListener("click", revokeShare);
  elements.renameForm.addEventListener("submit", renameTrip);
  elements.cancelRename.addEventListener("click", () => elements.renameDialog.close());
  showPublicShare();
})();
