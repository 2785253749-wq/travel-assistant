(() => {
  "use strict";

  const api = window.VoyageCommunityClient;
  if (!api) return;
  const { trimText, safeUrl, createBrowserClient } = api;
  const get = (id) => document.getElementById(id);
  const el = {
    page: get("admin-community-page"),
    status: get("admin-community-status"),
    list: get("admin-community-list"),
    empty: get("admin-community-empty"),
    retry: get("admin-community-retry"),
    loadMore: get("admin-community-load-more"),
    dialog: get("admin-community-review-dialog"),
    dialogTarget: get("admin-community-dialog-target"),
    reviewForm: get("admin-community-review-form"),
    reason: get("admin-community-review-reason"),
    reviewStatus: get("admin-community-review-status"),
    reviewCancel: get("admin-community-review-cancel"),
    reviewSubmit: get("admin-community-review-submit"),
    tabs: ["notes", "comments", "reports"].map((tab) => get("admin-community-tab-" + tab)),
    panel: get("admin-community-panel"), authRequired: get("admin-community-auth-required"), forbidden: get("admin-community-forbidden"), signIn: get("admin-community-signin"), returnExplore: get("admin-community-return-explore"), selfNav: get("admin-community-self-nav"),
  };
  const client = createBrowserClient();
  const tabState = new Map(["notes", "comments", "reports"].map((tab) => [tab, {
    loaded: false,
    items: [],
    nextCursor: null,
    loading: false,
    generation: 0,
  }]));
  let activeTab = "notes";
  let initialized = false;
  let mounted = false;
  let embedded = false;
  let sessionUnsubscribe = null;
  let listenersBound = false;
  let access = "unknown";

  function setSelfNavVisible(visible) {
    if (el.selfNav) el.selfNav.hidden = !visible;
  }
  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  let redirectingToSignIn = false;

  async function redirectToSignIn({ clearSession = false } = {}) {
    if (redirectingToSignIn) return;
    redirectingToSignIn = true;
    if (clearSession) {
      try {
        const authClient = client.getSupabaseClient();
        if (authClient && authClient.auth && typeof authClient.auth.signOut === "function") {
          await authClient.auth.signOut();
        }
      } catch (_) {
        // Local cleanup is still applied when the provider cannot sign out.
      }
    }
    clearAllQueues();
    setSelfNavVisible(false);
    if (el.authRequired) { el.authRequired.hidden = false; el.authRequired.textContent = "管理员登录状态已失效，请重新登录后再试。"; }
    if (el.forbidden) el.forbidden.hidden = true;
    if (el.panel) el.panel.hidden = true;
    if (el.status) el.status.textContent = "请登录后访问社区审核。";
    redirectingToSignIn = false;  }

  function clearQueue() {
    clear(el.list);
    el.empty.hidden = true;
    el.retry.hidden = true;
    el.loadMore.hidden = true;
    el.status.textContent = "";
  }

  function clearAllQueues() {
    for (const state of tabState.values()) {
      state.items = [];
      state.nextCursor = null;
      state.loaded = false;
      state.loading = false;
      state.generation += 1;
    }
    clearQueue();
  }

  function showForbidden() {
    access = "denied";
    clearAllQueues();
    setSelfNavVisible(false);
    setSelfNavVisible(false);
    if (el.authRequired) el.authRequired.hidden = true;
    if (el.forbidden) { el.forbidden.hidden = false; el.forbidden.textContent = "当前账户没有社区管理员权限"; }
    if (el.panel) el.panel.hidden = true;
    if (el.status) el.status.textContent = "当前账户没有社区管理员权限";
  }

  function queueUrl(tab, cursor = null) {
    const targetType = { notes: "note", comments: "comment", reports: "report" }[tab];
    const base = "/api/admin/community/review-queue?target_type=" + encodeURIComponent(targetType);
    return cursor
      ? base + "&cursor=" + encodeURIComponent(cursor) + "&limit=20"
      : base + "&limit=20";
  }

  function setTab(tab) {
    activeTab = tab;
    for (const button of el.tabs) {
      const selected = button.dataset.adminTab === tab;
      button.setAttribute("aria-selected", String(selected));
    }
    clearQueue();
    const state = tabState.get(tab);
    if (!state.loaded) void loadQueue(tab);
    else renderQueue();
  }

  function setError(message) {
    el.status.textContent = message;
    el.retry.hidden = false;
    el.loadMore.hidden = true;
  }

  function renderImage(item) {
    const imageUrl = safeUrl(item && item.image_url);
    if (!imageUrl) return null;
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = "审核内容图片";
    image.loading = "lazy";
    return image;
  }

  function addAction(buttons, label, className, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", handler);
    buttons.append(button);
  }

  function renderNote(item) {
    const card = document.createElement("article");
    card.className = "admin-community-card";
    card.dataset.itemId = String(item.id);
    const content = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = trimText(item.title);
    const body = document.createElement("p");
    body.textContent = trimText(item.body);
    const meta = document.createElement("p");
    meta.className = "admin-community-card-meta";
    meta.textContent = [trimText(item.location_name), trimText(item.category), trimText(item.author_display_name)].filter(Boolean).join(" · ");
    const actions = document.createElement("div");
    actions.className = "admin-community-card-actions";
    addAction(actions, "通过", "admin-community-approve", () => void reviewItem("notes", item.id, "approved"));
    addAction(actions, "驳回", "admin-community-reject", () => openReview("notes", item.id, item.title));
    content.append(title, meta, body, actions);
    const image = renderImage((Array.isArray(item.images) ? item.images : [])[0]);
    if (image) card.append(content, image);
    else card.append(content);
    return card;
  }

  function renderComment(item) {
    const card = document.createElement("article");
    card.className = "admin-community-card";
    card.dataset.itemId = String(item.id);
    const content = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = "评论审核";
    const body = document.createElement("p");
    body.textContent = trimText(item.body);
    const meta = document.createElement("p");
    meta.className = "admin-community-card-meta";
    meta.textContent = trimText(item.author_display_name) || "Voyage 旅行者";
    const actions = document.createElement("div");
    actions.className = "admin-community-card-actions";
    addAction(actions, "通过", "admin-community-approve", () => void reviewItem("comments", item.id, "approved"));
    addAction(actions, "驳回", "admin-community-reject", () => openReview("comments", item.id, "评论"));
    content.append(title, meta, body, actions);
    card.append(content);
    return card;
  }

  function renderReport(item) {
    const card = document.createElement("article");
    card.className = "admin-community-card";
    card.dataset.itemId = String(item.id);
    const content = document.createElement("div");
    const title = document.createElement("h2");
    title.textContent = "用户举报";
    const body = document.createElement("p");
    body.textContent = trimText(item.reason);
    const meta = document.createElement("p");
    meta.className = "admin-community-card-meta";
    meta.textContent = trimText(item.target_type) + " · " + String(item.target_id || "");
    const actions = document.createElement("div");
    actions.className = "admin-community-card-actions";
    addAction(actions, "关闭举报", "admin-community-report-dismiss", () => void resolveReport(item.id, "dismissed"));
    addAction(actions, "已处理并关闭", "admin-community-report-action", () => void resolveReport(item.id, "actioned"));
    if (item.target_type === "note" || item.target_type === "comment") {
      addAction(actions, "隐藏内容", "admin-community-hide", () => void hideContent(item.target_type, item.target_id));
    }
    content.append(title, meta, body, actions);
    card.append(content);
    return card;
  }

  function renderQueue() {
    const state = tabState.get(activeTab);
    clear(el.list);
    const renderers = { notes: renderNote, comments: renderComment, reports: renderReport };
    for (const item of state.items) el.list.append(renderers[activeTab](item));
    el.empty.hidden = state.items.length !== 0;
    el.loadMore.hidden = !state.nextCursor;
    el.retry.hidden = false;
    el.status.textContent = state.items.length ? "审核队列已加载。" : "";
  }

  async function loadQueue(tab, append = false) {
    if (!client.isSignedIn()) {
      await redirectToSignIn();
      return;
    }
    if (el.authRequired) el.authRequired.hidden = true;
    if (el.forbidden) el.forbidden.hidden = true;
    if (el.panel) el.panel.hidden = false;
    const state = tabState.get(tab);
    if (state.loading) return;
    state.loading = true;
    const generation = ++state.generation;
    const cursor = append ? state.nextCursor : null;
    if (!append) {
      state.items = [];
      state.nextCursor = null;
    }
    el.status.textContent = append ? "正在加载更多…" : "正在加载审核队列…";
    el.retry.hidden = true;
    el.loadMore.hidden = true;
    try {
      const page = await client.requestJson(queueUrl(tab, cursor), { auth: true });
      if (generation !== state.generation || tab !== activeTab) return;
      const items = Array.isArray(page.items) ? page.items : [];
      state.items = append ? state.items.concat(items) : items;
      state.nextCursor = typeof page.next_cursor === "string" && page.next_cursor ? page.next_cursor : null;
      state.loaded = true;
      access = "granted";
      setSelfNavVisible(true);
      renderQueue();
    } catch (error) {
      if (generation !== state.generation || tab !== activeTab) return;
      state.loaded = false;
      if (error && error.status === 403) {
        showForbidden();
        return;
      }
      if (error && error.status === 401) {
        clearQueue();
        await redirectToSignIn({ clearSession: true });
        return;
      }
      setError("审核队列加载失败，请重试。");
    } finally {
      if (generation === state.generation) state.loading = false;
    }
  }

  function openReview(targetType, targetId, label) {
    el.dialog.dataset.targetType = targetType;
    el.dialog.dataset.targetId = String(targetId);
    el.dialogTarget.textContent = "审核对象：" + trimText(label);
    el.reason.value = "";
    el.reviewStatus.textContent = "";
    el.dialog.hidden = false;
    if (typeof el.dialog.showModal === "function") el.dialog.showModal();
    else el.dialog.open = true;
    if (typeof el.reason.focus === "function") el.reason.focus();
  }

  function closeReview() {
    if (el.dialog.open && typeof el.dialog.close === "function") el.dialog.close();
    el.dialog.hidden = true;
    el.dialog.open = false;
    el.reason.value = "";
    el.dialogTarget.textContent = "";
    el.reviewStatus.textContent = "";
    delete el.dialog.dataset.targetType;
    delete el.dialog.dataset.targetId;
  }

  async function reviewItem(tab, itemId, decision, reason = null) {
    const targetType = { notes: "note", comments: "comment" }[tab];
    const reviewAction = decision === "approved" ? "approve" : "reject";
    const endpoint = "/api/admin/community/reviews/" + targetType + "/" + encodeURIComponent(String(itemId)) + "/" + reviewAction;
    try {
      await client.requestJson(endpoint, {
        method: "POST",
        auth: true,
        body: reason ? { reason } : {},
      });
      closeReview();
      await loadQueue(tab);
    } catch (error) {
      if (error && error.status === 403) { showForbidden(); return; }
      if (error && error.status === 401) {
        await redirectToSignIn({ clearSession: true });
        return;
      }
      el.reviewStatus.textContent = "审核操作失败，请稍后重试。";
    }
  }

  async function submitReview(event) {
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    const reason = trimText(el.reason.value);
    if (!reason || reason.length > 500) {
      el.reviewStatus.textContent = "驳回原因需要填写 1-500 个字符。";
      return;
    }
    el.reviewSubmit.disabled = true;
    try {
      await reviewItem(el.dialog.dataset.targetType, el.dialog.dataset.targetId, "rejected", reason);
    } finally {
      el.reviewSubmit.disabled = false;
    }
  }

  async function hideContent(targetType, targetId) {
    try {
      await client.requestJson(
        "/api/admin/community/hide/" + encodeURIComponent(String(targetType)) + "/" + encodeURIComponent(String(targetId)),
        { method: "POST", auth: true },
      );
      await loadQueue("reports");
    } catch (error) {
      if (error && error.status === 403) { showForbidden(); return; }
      if (error && error.status === 401) {
        await redirectToSignIn({ clearSession: true });
        return;
      }
      setError("Hide content failed. Please retry.");
    }
  }

  async function resolveReport(reportId, decision) {
    try {
      await client.requestJson("/api/admin/community/reports/" + encodeURIComponent(String(reportId)) + "/resolve", {
        method: "POST",
        auth: true,
        body: { decision },
      });
      await loadQueue("reports");
    } catch (error) {
      if (error && error.status === 403) { showForbidden(); return; }
      if (error && error.status === 401) {
        await redirectToSignIn({ clearSession: true });
        return;
      }
      setError("举报处理失败，请稍后重试。");
    }
  }

  function bindListeners() {
    if (listenersBound) return;
    listenersBound = true;
    sessionUnsubscribe = client.onSessionChange(() => {
      if (initialized && !client.isSignedIn()) {
        void redirectToSignIn();
      }
    });
    for (const tab of el.tabs) tab.addEventListener("click", () => setTab(tab.dataset.adminTab));
    el.retry.addEventListener("click", () => void loadQueue(activeTab));
    el.loadMore.addEventListener("click", () => void loadQueue(activeTab, true));
    el.reviewCancel.addEventListener("click", closeReview);
    el.dialog.addEventListener("close", closeReview);
    el.reviewForm.addEventListener("submit", submitReview);
    el.reviewSubmit.addEventListener("click", submitReview);
  }

  async function mount(options = {}) {
    if (mounted) return;
    mounted = true;
    setSelfNavVisible(false);
    embedded = options.embedded === true;
    initialized = false;
    access = "unknown";
    redirectingToSignIn = false;
    bindListeners();
    clearAllQueues();
    await client.initialize();
    initialized = true;
    if (!client.isSignedIn()) {
      await redirectToSignIn();
      return;
    }
    await loadQueue(activeTab);
  }

  async function unmount() {
    if (!mounted) return;
    mounted = false;
    initialized = false;
    access = "unknown";
    setSelfNavVisible(false);
    clearAllQueues();
    if (sessionUnsubscribe) sessionUnsubscribe();
    sessionUnsubscribe = null;
    embedded = false;
  }

  window.VoyageAdminController = Object.freeze({ mount, unmount, hasAccess: () => access === "granted" });
  if (!document.body.dataset.appShell) void mount();;
})();
