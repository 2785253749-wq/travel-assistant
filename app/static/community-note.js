(() => {
  "use strict";

  const api = window.VoyageCommunityClient;
  if (!api) return;
  const { trimText, safeUrl, publicError, sameOriginPath, createBrowserClient } = api;
  const get = (id) => document.getElementById(id);
  const el = {
    content: get("community-note-content"),
    gallery: get("community-note-gallery"),
    category: get("community-note-category"),
    title: get("community-note-title"),
    location: get("community-note-location"),
    author: get("community-note-author"),
    text: get("community-note-text"),
    status: get("community-note-status"),
    error: get("community-note-error"),
    errorMessage: get("community-note-error-message"),
    retry: get("community-note-retry"),
    galleryPrev: get("community-note-gallery-prev"),
    galleryNext: get("community-note-gallery-next"),
    likeButton: get("community-note-like-button"),
    likeCount: get("community-note-like-count"),
    bookmarkButton: get("community-note-bookmark-button"),
    bookmarkCount: get("community-note-bookmark-count"),
    reportButton: get("community-note-report-button"),
    interactionStatus: get("community-note-interaction-status"),
    commentsStatus: get("community-note-comments-status"),
    commentsRetry: get("community-note-comments-retry"),
    commentsList: get("community-note-comments-list"),
    commentForm: get("community-note-comment-form"),
    commentBody: get("community-note-comment-body"),
    commentSubmit: get("community-note-comment-submit"),
    commentStatus: get("community-note-comment-status"),
    commentSignin: get("community-note-comment-signin"),
    reportDialog: get("community-note-report-dialog"),
    reportTarget: get("community-note-report-target"),
    reportForm: get("community-note-report-form"),
    reportReason: get("community-note-report-reason"),
    reportCancel: get("community-note-report-cancel"),
    reportSubmit: get("community-note-report-submit"),
    reportStatus: get("community-note-report-status"),
  };

  let galleryItems = [];
  let galleryIndex = 0;
  let galleryTitle = "旅行游记";
  let currentNote = null;
  let comments = [];
  const interactions = {
    like: { known: false, active: false, count: 0, pending: false },
    bookmark: { known: false, active: false, count: 0, pending: false },
  };
  const client = createBrowserClient();
  const noteId = decodeURIComponent((window.location.pathname.match(/\/community\/notes\/([^/]+)$/) || [])[1] || "");

  function clear(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function currentPath() {
    return window.location.pathname + window.location.search + window.location.hash;
  }

  function showError(message) {
    el.error.hidden = !message;
    el.errorMessage.textContent = message || "";
  }

  function showInteractionStatus(message) {
    el.interactionStatus.textContent = message || "";
  }

  function renderGallery() {
    clear(el.gallery);
    const item = galleryItems[galleryIndex];
    if (item) {
      const image = document.createElement("img");
      image.src = safeUrl(item.image_url);
      image.loading = "lazy";
      image.alt = galleryTitle + " · 第 " + (galleryIndex + 1) + " 张";
      el.gallery.append(image);
    }
    const multiple = galleryItems.length > 1;
    el.galleryPrev.hidden = !multiple;
    el.galleryNext.hidden = !multiple;
    el.galleryPrev.disabled = !multiple;
    el.galleryNext.disabled = !multiple;
  }

  function moveGallery(step) {
    if (galleryItems.length < 2) return;
    galleryIndex = (galleryIndex + step + galleryItems.length) % galleryItems.length;
    renderGallery();
  }

  function renderInteraction(kind) {
    const state = interactions[kind];
    const button = kind === "like" ? el.likeButton : el.bookmarkButton;
    const count = kind === "like" ? el.likeCount : el.bookmarkCount;
    button.classList.toggle("is-active", state.known && state.active);
    button.classList.toggle("is-pending", state.pending);
    button.disabled = state.pending;
    if (state.known) button.setAttribute("aria-pressed", String(state.active));
    else button.removeAttribute("aria-pressed");
    if (count) count.textContent = String(Math.max(0, state.count));
  }

  function resetInteractionState() {
    interactions.like.known = false;
    interactions.like.active = false;
    interactions.like.pending = false;
    interactions.bookmark.known = false;
    interactions.bookmark.active = false;
    interactions.bookmark.pending = false;
    if (currentNote) {
      interactions.like.count = Number(currentNote.like_count) || 0;
      interactions.bookmark.count = 0;
    }
    renderInteraction("like");
    renderInteraction("bookmark");
  }

  function renderAuthUi() {
    const signedIn = client.isSignedIn();
    el.commentForm.hidden = !signedIn;
    el.commentSignin.hidden = signedIn;
  }

  function ensureAuth() {
    if (client.isSignedIn()) return true;
    client.redirectToSignIn(currentPath());
    return false;
  }

  async function mutateInteraction(kind) {
    if (!currentNote || !ensureAuth()) return;
    const state = interactions[kind];
    if (state.pending) return;
    const field = kind === "like" ? "liked" : "bookmarked";
    const label = kind === "like" ? "点赞" : "收藏";
    const endpoint = "/api/community/notes/" + encodeURIComponent(noteId) + "/" + kind;
    const previous = { known: state.known, active: state.active, count: state.count, pending: false };
    const generation = client.getSessionGeneration();
    const method = state.known && state.active ? "DELETE" : "PUT";
    state.pending = true;
    if (method === "PUT") {
      state.active = true;
      state.count += 1;
    } else {
      state.active = false;
      state.count = Math.max(0, state.count - 1);
    }
    renderInteraction(kind);
    try {
      const response = await client.requestJson(endpoint, { method, auth: true });
      if (generation !== client.getSessionGeneration()) return;
      state.known = true;
      state.active = Boolean(response[field]);
      state.count = kind === "like" ? Number(response.like_count) || 0 : state.count;
      state.pending = false;
      renderInteraction(kind);
      showInteractionStatus(method === "PUT" ? label + "成功。" : "已取消" + label + "。");
    } catch (error) {
      if (generation !== client.getSessionGeneration()) return;
      Object.assign(state, previous);
      renderInteraction(kind);
      if (error.code === "AUTH_REQUIRED") {
        resetInteractionState();
        client.redirectToSignIn(currentPath());
        return;
      }
      showInteractionStatus(label + "失败，请稍后重试。");
    }
  }

  function renderComment(comment) {
    const item = document.createElement("article");
    item.className = "community-note__comment";
    const body = document.createElement("p");
    body.className = "community-note__comment-body";
    body.textContent = trimText(comment.body);
    const meta = document.createElement("div");
    meta.className = "community-note__comment-meta";
    const author = document.createElement("span");
    author.textContent = trimText(comment.author_display_name) || "Voyage 旅行者";
    meta.append(author);
    if (comment.status === "pending_review") {
      const pending = document.createElement("span");
      pending.className = "community-note__comment-pending";
      pending.textContent = "审核中";
      meta.append(pending);
    }
    if (comment.status === "approved" && client.isSignedIn()) {
      const report = document.createElement("button");
      report.type = "button";
      report.className = "community-secondary community-note__comment-report";
      report.textContent = "举报评论";
      report.addEventListener("click", () => openReport("comment", comment.id, "这条评论"));
      meta.append(report);
    }
    item.append(body, meta);
    return item;
  }

  function renderComments() {
    clear(el.commentsList);
    for (const comment of comments) el.commentsList.append(renderComment(comment));
    el.commentsStatus.textContent = comments.length ? "" : "暂无评论。";
  }

  async function loadComments() {
    el.commentsStatus.textContent = "正在加载评论…";
    el.commentsRetry.hidden = true;
    try {
      const page = await client.requestJson(
        "/api/community/notes/" + encodeURIComponent(noteId) + "/comments",
        { auth: client.isSignedIn() },
      );
      comments = Array.isArray(page.items) ? page.items : [];
      renderComments();
    } catch (error) {
      el.commentsStatus.textContent = publicError(error);
      el.commentsRetry.hidden = false;
    }
  }

  async function submitComment(event) {
    event.preventDefault();
    if (!ensureAuth()) return;
    const body = trimText(el.commentBody.value);
    if (!body || body.length > 500) {
      el.commentStatus.textContent = "评论需要填写 1-500 个字符。";
      return;
    }
    const generation = client.getSessionGeneration();
    el.commentSubmit.disabled = true;
    el.commentStatus.textContent = "正在提交评论…";
    try {
      const comment = await client.requestJson(
        "/api/community/notes/" + encodeURIComponent(noteId) + "/comments",
        { method: "POST", auth: true, body: { body } },
      );
      if (generation !== client.getSessionGeneration()) return;
      comments.unshift(comment);
      el.commentBody.value = "";
      el.commentStatus.textContent = "评论已提交，审核通过后会公开显示。";
      renderComments();
    } catch (error) {
      if (generation !== client.getSessionGeneration()) return;
      if (error.code === "AUTH_REQUIRED") {
        client.redirectToSignIn(currentPath());
        return;
      }
      el.commentStatus.textContent = "评论提交失败，请稍后重试。";
    } finally {
      if (generation === client.getSessionGeneration()) el.commentSubmit.disabled = false;
    }
  }

  function openReport(targetType, targetId, label) {
    if (!ensureAuth()) return;
    el.reportDialog.hidden = false;
    el.reportTarget.textContent = "举报对象：" + label;
    el.reportReason.value = "";
    el.reportStatus.textContent = "";
    el.reportDialog.dataset.targetType = targetType;
    el.reportDialog.dataset.targetId = String(targetId);
    el.reportReason.focus();
  }

  function closeReport() {
    el.reportDialog.hidden = true;
    el.reportStatus.textContent = "";
  }

  async function submitReport(event) {
    event.preventDefault();
    if (!ensureAuth()) return;
    const reason = trimText(el.reportReason.value);
    if (!reason || reason.length > 500) {
      el.reportStatus.textContent = "举报原因需要填写 1-500 个字符。";
      return;
    }
    const targetType = el.reportDialog.dataset.targetType;
    const targetId = el.reportDialog.dataset.targetId;
    el.reportSubmit.disabled = true;
    el.reportStatus.textContent = "正在提交举报…";
    try {
      await client.requestJson("/api/community/notes/" + encodeURIComponent(noteId) + "/reports", {
        method: "POST",
        auth: true,
        body: { target_type: targetType, target_id: targetId, reason },
      });
      closeReport();
      showInteractionStatus("举报已提交，感谢你的反馈。");
    } catch (error) {
      if (error.code === "AUTH_REQUIRED") {
        closeReport();
        client.redirectToSignIn(currentPath());
        return;
      }
      el.reportStatus.textContent = "举报提交失败，请稍后重试。";
    } finally {
      el.reportSubmit.disabled = false;
    }
  }

  function render(note) {
    currentNote = note;
    galleryTitle = trimText(note.title) || "旅行游记";
    galleryItems = (Array.isArray(note.images) ? note.images : []).filter((item) => safeUrl(item && item.image_url));
    galleryIndex = 0;
    renderGallery();
    el.category.textContent = trimText(note.category);
    el.title.textContent = trimText(note.title);
    el.location.textContent = trimText(note.location_name);
    el.text.textContent = trimText(note.body);
    el.author.textContent = trimText(note.author_display_name) || "Voyage 旅行者";
    el.author.href = sameOriginPath("/community/creators/" + encodeURIComponent(String(note.author_slug || "voyage-traveler")), "/community");
    interactions.like.count = Number(note.like_count) || 0;
    interactions.bookmark.count = 0;
    resetInteractionState();
    el.content.hidden = false;
    el.status.textContent = "游记已加载。";
    void loadComments();
  }

  async function load() {
    if (!noteId) {
      showError("游记地址无效。");
      return;
    }
    el.content.hidden = true;
    showError("");
    el.status.textContent = "正在加载游记…";
    try {
      render(await client.requestJson("/api/community/notes/" + encodeURIComponent(noteId)));
    } catch (error) {
      showError(publicError(error));
      el.status.textContent = "游记加载失败。";
    }
  }

  client.onSessionChange(() => {
    if (!client.isSignedIn()) resetInteractionState();
    renderAuthUi();
    if (currentNote) renderComments();
  });
  el.retry.addEventListener("click", () => { void load(); });
  el.galleryPrev.addEventListener("click", () => moveGallery(-1));
  el.galleryNext.addEventListener("click", () => moveGallery(1));
  el.likeButton.addEventListener("click", () => { void mutateInteraction("like"); });
  el.bookmarkButton.addEventListener("click", () => { void mutateInteraction("bookmark"); });
  el.reportButton.addEventListener("click", () => openReport("note", noteId, "这篇游记"));
  el.commentForm.addEventListener("submit", submitComment);
  el.commentSignin.addEventListener("click", () => client.redirectToSignIn(currentPath()));
  el.commentsRetry.addEventListener("click", () => { void loadComments(); });
  el.reportCancel.addEventListener("click", closeReport);
  el.reportForm.addEventListener("submit", submitReport);
  renderAuthUi();
  void client.initialize().then(load);
})();
