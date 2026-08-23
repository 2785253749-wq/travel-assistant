(() => {
  "use strict";

  if (!window.VoyageCommunityClient) return;

  const { sameOriginPath, trimText, createBrowserClient } = window.VoyageCommunityClient;
  const elements = {
    empty: document.getElementById("community-mine-empty"),
    error: document.getElementById("community-mine-error"),
    errorMessage: document.getElementById("community-mine-error-message"),
    list: document.getElementById("community-mine-list"),
    retry: document.getElementById("community-mine-retry"),
    status: document.getElementById("community-mine-status"),
    tabs: {
      draft: document.getElementById("community-mine-tab-draft"),
      pending_review: document.getElementById("community-mine-tab-pending_review"),
      approved: document.getElementById("community-mine-tab-approved"),
      rejected: document.getElementById("community-mine-tab-rejected"),
    },
  };
  const state = {
    activeStatus: "draft",
    items: [],
    requestGeneration: 0,
  };
  const client = createBrowserClient();

  function setStatus(message) {
    elements.status.textContent = message;
  }

  function clearChildren(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function renderTabs() {
    for (const [status, button] of Object.entries(elements.tabs)) {
      const active = status === state.activeStatus;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function filteredItems() {
    return state.items.filter((item) => item && item.status === state.activeStatus);
  }

  function actionLink(label, href) {
    const link = document.createElement("a");
    link.className = "community-card__action";
    link.href = href;
    link.textContent = label;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      window.location.href = sameOriginPath(href, "/community/mine");
    });
    return link;
  }

  function deleteButton(noteId) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "community-card__action";
    button.textContent = "删除";
    button.dataset.noteId = String(noteId);
    button.dataset.action = "delete";
    button.addEventListener("click", () => {
      void deleteNote(String(noteId));
    });
    return button;
  }

  function renderCard(item) {
    const card = document.createElement("article");
    card.className = "community-card community-mine-card";

    const content = document.createElement("div");
    content.className = "community-card__content";

    const category = document.createElement("p");
    category.className = "community-card__category";
    category.textContent = trimText(item.category) || "旅行灵感";
    content.append(category);

    const title = document.createElement("p");
    title.className = "community-card__title";
    title.textContent = trimText(item.title) || "未命名游记";
    content.append(title);

    const location = document.createElement("p");
    location.className = "community-card__location";
    location.textContent = trimText(item.location_name) || "未注明地点";
    content.append(location);

    if (item.status === "rejected" && trimText(item.review_reason)) {
      const reason = document.createElement("p");
      reason.className = "community-mine-reason";
      reason.textContent = `驳回原因：${trimText(item.review_reason)}`;
      content.append(reason);
    }

    const actions = document.createElement("div");
    actions.className = "community-actions";
    if (item.status === "draft" || item.status === "rejected") {
      actions.append(actionLink("编辑", `/community/notes/${encodeURIComponent(String(item.id))}/edit`));
    } else {
      actions.append(actionLink("查看", `/community/notes/${encodeURIComponent(String(item.id))}`));
    }
    actions.append(deleteButton(item.id));
    content.append(actions);
    card.append(content);
    return card;
  }

  function render() {
    renderTabs();
    const items = filteredItems();
    clearChildren(elements.list);
    for (const item of items) elements.list.append(renderCard(item));
    elements.error.hidden = true;
    elements.empty.hidden = items.length !== 0;
    setStatus(items.length ? `当前共 ${items.length} 篇${elements.tabs[state.activeStatus].textContent}游记。` : "当前状态下还没有游记。");
  }

  async function loadItems() {
    const generation = state.requestGeneration + 1;
    state.requestGeneration = generation;
    setStatus("正在加载我的游记…");
    try {
      const payload = await client.requestJson("/api/me/travel-notes", { auth: true });
      if (generation !== state.requestGeneration) return;
      state.items = Array.isArray(payload && payload.items) ? payload.items : [];
      render();
    } catch (error) {
      if (error && error.code === "AUTH_REQUIRED") {
        client.redirectToSignIn("/community/mine");
        return;
      }
      elements.error.hidden = false;
      elements.errorMessage.textContent = "我的游记暂不可用，请稍后重试。";
      elements.empty.hidden = true;
      clearChildren(elements.list);
      setStatus("我的游记加载失败。");
    }
  }

  async function deleteNote(noteId) {
    try {
      await client.requestJson(`/api/community/notes/${encodeURIComponent(noteId)}`, {
        method: "DELETE",
        auth: true,
      });
      state.items = state.items.filter((item) => String(item.id) !== String(noteId));
      render();
    } catch (error) {
      if (error && error.code === "AUTH_REQUIRED") {
        client.redirectToSignIn("/community/mine");
        return;
      }
      elements.error.hidden = false;
      elements.errorMessage.textContent = "删除游记失败，请稍后重试。";
      setStatus("删除游记失败。");
    }
  }

  function bindEvents() {
    for (const [status, button] of Object.entries(elements.tabs)) {
      button.addEventListener("click", () => {
        if (state.activeStatus === status) return;
        state.activeStatus = status;
        render();
      });
    }
    elements.retry.addEventListener("click", () => {
      void loadItems();
    });
    client.onSessionChange(() => {
      if (!client.isSignedIn()) {
        client.redirectToSignIn("/community/mine");
        return;
      }
      void loadItems();
    });
  }

  async function initialize() {
    bindEvents();
    await client.initialize();
    if (!client.isSignedIn()) {
      client.redirectToSignIn("/community/mine");
      return;
    }
    await loadItems();
  }

  void initialize();
})();
