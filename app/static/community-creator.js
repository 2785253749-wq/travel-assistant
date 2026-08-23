(() => {
  "use strict";
  const api = window.VoyageCommunityClient;
  if (!api) return;
  const { trimText, safeUrl, publicError, sameOriginPath, createBrowserClient } = api;
  const el = { profile: document.getElementById("community-creator-profile"), avatar: document.getElementById("community-creator-avatar"), name: document.getElementById("community-creator-name"), bio: document.getElementById("community-creator-bio"), grid: document.getElementById("community-creator-grid"), status: document.getElementById("community-creator-status"), error: document.getElementById("community-creator-error"), errorMessage: document.getElementById("community-creator-error-message"), retry: document.getElementById("community-creator-retry"), loadMore: document.getElementById("community-creator-load-more") };
  const client = createBrowserClient(); const slug = decodeURIComponent((window.location.pathname.match(/\/community\/creators\/([^/]+)$/) || [])[1] || "");
  let cursor = null; let loading = false; let items = [];
  function initials(value) { const text = trimText(value); return text ? text.slice(0, 2).toUpperCase() : "V"; }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function error(message) { el.error.hidden = !message; el.errorMessage.textContent = message || ""; }
  function renderProfile(profile) {
    el.profile.dataset.creatorSlug = trimText(profile.creator_slug);
    el.name.textContent = trimText(profile.display_name) || "Voyage 旅行者";
    el.bio.textContent = trimText(profile.bio) || "这位创作者还没有留下简介。";
    clear(el.avatar);
    const url = safeUrl(profile.avatar_url);
    if (url) {
      const image = document.createElement("img");
      image.src = url;
      image.alt = "";
      el.avatar.append(image);
    } else {
      el.avatar.textContent = initials(profile.display_name);
    }
    el.profile.hidden = false;
  }
  function renderItems() { clear(el.grid); for (const note of items) { const card = document.createElement("article"); card.className = "community-card"; const url = safeUrl(note.cover_image_url); if (url) { const image = document.createElement("img"); image.src = url; image.loading = "lazy"; image.alt = trimText(note.title) || "旅行封面图"; card.append(image); } const content = document.createElement("div"); content.className = "community-card__content"; const category = document.createElement("p"); category.className = "community-card__category"; category.textContent = trimText(note.category); content.append(category); const link = document.createElement("a"); link.className = "community-card__title"; link.href = sameOriginPath(`/community/notes/${encodeURIComponent(String(note.id))}`, "/community"); link.textContent = trimText(note.title) || "未命名游记"; content.append(link); const location = document.createElement("p"); location.className = "community-card__location"; location.textContent = trimText(note.location_name); content.append(location); card.append(content); el.grid.append(card); } }
  async function load(append = false) { if (loading || !slug) return; loading = true; error(""); el.status.textContent = append ? "正在加载更多…" : "正在加载创作者…"; try { const query = new URLSearchParams({ limit: "20" }); if (cursor) query.set("cursor", cursor); const payload = await client.requestJson(`/api/community/creators/${encodeURIComponent(slug)}?${query}`); if (!append) renderProfile(payload.creator || {}); items = append ? [...items, ...(Array.isArray(payload.items) ? payload.items : [])] : (Array.isArray(payload.items) ? payload.items : []); cursor = typeof payload.next_cursor === "string" && payload.next_cursor ? payload.next_cursor : null; renderItems(); el.loadMore.hidden = !cursor; el.status.textContent = `已加载 ${items.length} 篇公开游记。`; } catch (caught) { error(publicError(caught)); el.status.textContent = "创作者加载失败。"; } finally { loading = false; } }
  el.retry.addEventListener("click", () => { cursor = null; void load(false); }); el.loadMore.addEventListener("click", () => { void load(true); }); void client.initialize().then(() => load(false));
})();
