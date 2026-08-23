(() => {
  "use strict";

  if (!window.VoyageCommunityClient) return;

  const {
    CATEGORIES,
    DEFAULT_PAGE_SIZE,
    trimText,
    safeUrl,
    publicError,
    sameOriginPath,
    createBrowserClient,
    hasAdminMarker,
  } = window.VoyageCommunityClient;
  const elements = {
    body: document.body,
    authLink: document.getElementById("community-auth-link"),
    adminNav: document.getElementById("community-admin-nav"),
    createLink: document.getElementById("community-create-link"),
    empty: document.getElementById("community-empty"),
    error: document.getElementById("community-error"),
    errorMessage: document.getElementById("community-error-message"),
    filters: document.getElementById("community-filters"),
    grid: document.getElementById("community-grid"),
    loadMore: document.getElementById("community-load-more"),
    retry: document.getElementById("community-retry"),
    searchForm: document.getElementById("community-search-form"),
    searchInput: document.getElementById("community-search-input"),
    status: document.getElementById("community-status"),
  };
  const state = {
    activeCategory: "全部",
    items: [],
    nextCursor: null,
    query: "",
    requestGeneration: 0,
    signedIn: false,
    loadState: "idle",
    errorMessage: "",
    retryMode: "reload",
    sessionGeneration: 0,
  };
  const client = createBrowserClient();

  function clearChildren(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function createIcon(name) {
    const icon = document.createElement("span");
    icon.className = `voyage-icon voyage-icon--${name}`;
    icon.setAttribute("aria-hidden", "true");
    return icon;
  }
  function initials(name) {
    const normalized = trimText(name);
    if (!normalized) return "V";
    const words = normalized.split(/\s+/).filter(Boolean);
    if (words.length >= 2) return `${words[0][0]}${words[1][0]}`.toUpperCase();
    return normalized.slice(0, 2).toUpperCase();
  }

  function detailPath(noteId) {
    return `/community/notes/${encodeURIComponent(String(noteId))}`;
  }

  function creatorPath(slug) {
    return `/community/creators/${encodeURIComponent(String(slug))}`;
  }

  function updateHistory() {
    const url = new URL(window.location.href);
    if (state.activeCategory === "全部") url.searchParams.delete("category");
    else url.searchParams.set("category", state.activeCategory);
    if (state.query) url.searchParams.set("q", state.query);
    else url.searchParams.delete("q");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function navigate(path) {
    window.location.href = sameOriginPath(path, "/community");
  }

  function syncAuthAffordances() {
    state.signedIn = client.isSignedIn();
    state.sessionGeneration = client.getSessionGeneration();
    elements.body.dataset.communityAuth = state.signedIn ? "signed_in" : "signed_out";
    elements.authLink.hidden = state.signedIn;
    if (elements.adminNav) elements.adminNav.hidden = !hasAdminMarker(client.getSession());
  }

  function normalizedPage(payload) {
    const page = payload && typeof payload === "object" ? payload : {};
    const items = Array.isArray(page.items) ? page.items : [];
    const nextCursor = typeof page.next_cursor === "string" && page.next_cursor ? page.next_cursor : null;
    return { items, nextCursor };
  }

  function mergeItems(items) {
    const seen = new Set();
    const result = [];
    for (const item of items) {
      const id = item && item.id ? String(item.id) : "";
      if (!id || seen.has(id)) continue;
      seen.add(id);
      result.push(item);
    }
    return result;
  }

  function queryString(cursor = null) {
    const params = new URLSearchParams();
    params.set("limit", String(DEFAULT_PAGE_SIZE));
    if (state.activeCategory !== "全部") params.set("category", state.activeCategory);
    if (state.query) params.set("q", state.query);
    if (cursor) params.set("cursor", cursor);
    return `/api/community/notes?${params.toString()}`;
  }

  function setStatus(message) {
    elements.status.textContent = message;
  }

  function renderFilters() {
    for (const category of CATEGORIES) {
      const id = category === "全部" ? "community-filter-all" : `community-filter-${category}`;
      const button = document.getElementById(id);
      if (!button) continue;
      const active = state.activeCategory === category;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }

  function renderCard(note) {
    const card = document.createElement("article");
    card.className = "community-card";
    card.dataset.layout = "post";
    card.dataset.postId = String(note && note.id);
    card.setAttribute("tabindex", "0");
    card.setAttribute("role", "link");
    card.setAttribute("aria-label", `打开游记：${trimText(note && note.title) || "未命名游记"}`);
    const openDetail = (event) => {
      const target = event && event.target;
      if (target && typeof target.closest === "function" && target.closest("a,button")) return;
      navigate(detailPath(note && note.id));
    };
    card.addEventListener("click", openDetail);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") openDetail(event);
    });

    const mediaUrl = safeUrl(note && note.cover_image_url);
    if (mediaUrl) {
      const image = document.createElement("img");
      image.className = "community-card__image";
      image.src = mediaUrl;
      image.alt = trimText(note && note.cover_image_alt) || trimText(note && note.title) || "旅行封面图";
      image.setAttribute("loading", "lazy");
      image.setAttribute("decoding", "async");
      card.append(image);
    }

    const content = document.createElement("div");
    content.className = "community-card__content";

    const category = document.createElement("p");
    category.className = "community-card__category";
    category.textContent = trimText(note && note.category) || "旅行灵感";
    content.append(category);

    const titleLink = document.createElement("a");
    titleLink.className = "community-card__title";
    titleLink.href = detailPath(note && note.id);
    titleLink.textContent = trimText(note && note.title) || "未命名游记";
    titleLink.addEventListener("click", (event) => {
      event.preventDefault();
      navigate(detailPath(note && note.id));
    });
    content.append(titleLink);

    const location = document.createElement("p");
    location.className = "community-card__location";
    location.textContent = trimText(note && note.location_name) || "未注明地点";
    content.append(location);

    const excerpt = document.createElement("p");
    excerpt.className = "community-card__excerpt";
    excerpt.textContent = trimText(note && (note.body_preview || note.excerpt)) || "这篇游记还没有摘要。";
    content.append(excerpt);

    const authorRow = document.createElement("div");
    authorRow.className = "community-card__author";

    const avatarUrl = safeUrl(note && note.author_avatar_url);
    if (avatarUrl) {
      const avatar = document.createElement("img");
      avatar.className = "community-card__avatar";
      avatar.src = avatarUrl;
      avatar.alt = "";
      avatar.setAttribute("loading", "lazy");
      authorRow.append(avatar);
    } else {
      const fallback = document.createElement("span");
      fallback.className = "community-card__avatar-fallback";
      fallback.textContent = initials(note && note.author_display_name);
      authorRow.append(fallback);
    }

    const authorLink = document.createElement("a");
    authorLink.className = "community-card__author-link";
    authorLink.href = creatorPath(note && note.creator_slug);
    authorLink.textContent = trimText(note && note.author_display_name) || "Voyage 旅行者";
    authorLink.addEventListener("click", (event) => {
      event.preventDefault();
      navigate(creatorPath(note && note.creator_slug));
    });
    authorRow.append(authorLink);
    content.append(authorRow);

    const footer = document.createElement("div");
    footer.className = "community-card__footer";

    const counts = document.createElement("p");
    counts.className = "community-card__counts";
    const likes = document.createElement("span");
    const likeCount = document.createElement("span");
    const comments = document.createElement("span");
    comments.append(createIcon("comment"), `评论 ${Number(note && note.comment_count) || 0}`);
    counts.append(likes, " · ", comments);
    footer.append(counts);

    const likeState = {
      known: false,
      active: false,
      pending: false,
      count: Number(note && note.like_count) || 0,
    };
    const likeButton = document.createElement("button");
    likeButton.type = "button";
    likeButton.className = "community-card__action";

    function renderLike() {
      likeCount.textContent = `点赞 ${likeState.count}`;
      while (likes.firstChild) likes.removeChild(likes.firstChild);
      likes.append(createIcon("like"), likeCount);
      while (likeButton.firstChild) likeButton.removeChild(likeButton.firstChild);
      likeButton.append(createIcon("like"), '点赞');
      likeButton.disabled = likeState.pending;
      likeButton.classList.toggle("is-active", likeState.active && (likeState.known || likeState.pending));
      likeButton.classList.toggle("is-pending", likeState.pending);
      if (likeState.known) likeButton.setAttribute("aria-pressed", String(likeState.active));
      else likeButton.removeAttribute("aria-pressed");
    }

    async function mutateLike() {
      if (!state.signedIn) {
        client.redirectToSignIn(detailPath(note && note.id));
        return;
      }
      if (likeState.pending) return;
      const previous = { known: likeState.known, active: likeState.active, count: likeState.count };
      const generation = client.getSessionGeneration();
      const method = likeState.known && likeState.active ? "DELETE" : "PUT";
      likeState.pending = true;
      likeState.active = method === "PUT";
      likeState.count = Math.max(0, likeState.count + (method === "PUT" ? 1 : -1));
      renderLike();
      try {
        const response = await client.requestJson(
          "/api/community/notes/" + encodeURIComponent(String(note && note.id)) + "/like",
          { method, auth: true },
        );
        if (generation !== client.getSessionGeneration()) return;
        likeState.known = true;
        likeState.active = Boolean(response.liked);
        likeState.count = Number(response.like_count) || 0;
        likeState.pending = false;
        renderLike();
      } catch (error) {
        if (generation !== client.getSessionGeneration()) return;
        Object.assign(likeState, previous, { pending: false });
        renderLike();
        if (error && error.code === "AUTH_REQUIRED") {
          client.redirectToSignIn(detailPath(note && note.id));
        }
      }
    }

    likeButton.addEventListener("click", () => { void mutateLike(); });
    renderLike();
    footer.append(likeButton);
    content.append(footer);
    card.append(content);
    return card;
  }

  function render() {
    renderFilters();
    clearChildren(elements.grid);
    for (const item of state.items) elements.grid.append(renderCard(item));

    const showEmpty = state.loadState === "empty";
    const showError = state.loadState === "error" || state.loadState === "append_error";
    elements.empty.hidden = !showEmpty;
    elements.error.hidden = !showError;
    elements.retry.hidden = !showError;
    elements.loadMore.hidden = !(state.loadState === "ready" && state.nextCursor);

    if (state.loadState === "loading" && !state.items.length) {
      setStatus("正在加载社区内容…");
    } else if (state.loadState === "loading" && state.items.length) {
      setStatus("正在加载更多社区内容…");
    } else if (state.loadState === "ready" && state.items.length) {
      setStatus(`已加载 ${state.items.length} 篇公开游记。`);
    } else if (showEmpty) {
      setStatus("还没有公开游记。");
    } else if (state.loadState === "append_error") {
      setStatus(state.errorMessage);
    } else if (showError) {
      setStatus(state.errorMessage);
    } else {
      setStatus("浏览最新公开游记。");
    }

    elements.errorMessage.textContent = state.errorMessage || "社区内容暂不可用，请稍后重试。";
  }

  async function loadFeed(options = {}) {
    const append = options.append === true;
    const cursor = append ? state.nextCursor : null;
    const generation = state.requestGeneration + 1;
    state.requestGeneration = generation;
    if (!append) {
      state.items = [];
      state.nextCursor = null;
    }
    state.loadState = "loading";
    state.errorMessage = "";
    state.retryMode = append ? "append" : "reload";
    render();

    try {
      const page = normalizedPage(await client.requestJson(queryString(cursor)));
      if (generation !== state.requestGeneration) return;
      state.items = append ? mergeItems([...state.items, ...page.items]) : mergeItems(page.items);
      state.nextCursor = page.nextCursor;
      state.loadState = state.items.length ? "ready" : "empty";
      updateHistory();
    } catch (error) {
      if (generation !== state.requestGeneration) return;
      state.loadState = append && state.items.length ? "append_error" : "error";
      state.errorMessage = publicError(error);
    } finally {
      if (generation === state.requestGeneration) render();
    }
  }

  function applyInitialState() {
    const params = new URLSearchParams(window.location.search);
    const category = trimText(params.get("category"));
    const query = trimText(params.get("q"));
    if (CATEGORIES.includes(category)) state.activeCategory = category;
    state.query = query;
    elements.searchInput.value = query;
  }

  function onCategoryClick(event) {
    const nextCategory = trimText(event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.category);
    if (!CATEGORIES.includes(nextCategory) || nextCategory === state.activeCategory) return;
    state.activeCategory = nextCategory;
    state.nextCursor = null;
    loadFeed({ append: false });
  }

  function onSearchSubmit(event) {
    event.preventDefault();
    const nextQuery = trimText(elements.searchInput.value);
    if (nextQuery === state.query && state.loadState !== "error") return;
    state.query = nextQuery;
    state.nextCursor = null;
    loadFeed({ append: false });
  }

  function bindEvents() {
    for (const category of CATEGORIES) {
      const id = category === "全部" ? "community-filter-all" : `community-filter-${category}`;
      const button = document.getElementById(id);
      if (button) button.addEventListener("click", onCategoryClick);
    }
    elements.searchForm.addEventListener("submit", onSearchSubmit);
    elements.retry.addEventListener("click", () => loadFeed({ append: state.retryMode === "append" }));
    elements.loadMore.addEventListener("click", () => {
      if (!state.nextCursor) return;
      loadFeed({ append: true });
    });
    elements.createLink.addEventListener("click", (event) => {
      event.preventDefault();
      if (!state.signedIn) {
        client.redirectToSignIn("/community/notes/new");
        return;
      }
      navigate("/community/notes/new");
    });
    client.onSessionChange(() => {
      syncAuthAffordances();
      render();
    });
  }

  async function initialize() {
    applyInitialState();
    bindEvents();
    render();
    await client.initialize();
    syncAuthAffordances();
    render();
    await loadFeed({ append: false });
  }

  void initialize();
})();
