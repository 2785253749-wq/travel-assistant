(() => {
  "use strict";

  if (!window.VoyageCommunityClient) return;

  const {
    CATEGORIES,
    trimText,
    safeUrl,
    createBrowserClient,
  } = window.VoyageCommunityClient;
  const MAX_IMAGES = 9;
  const MAX_IMAGE_SIZE = 10 * 1024 * 1024;
  const elements = {
    body: document.body,
    errors: document.getElementById("community-editor-errors"),
    fileInput: document.getElementById("community-editor-file-input"),
    previewList: document.getElementById("community-editor-preview-list"),
    save: document.getElementById("community-editor-save"),
    submit: document.getElementById("community-editor-submit"),
    status: document.getElementById("community-editor-status"),
    title: document.getElementById("community-editor-title"),
    location: document.getElementById("community-editor-location"),
    category: document.getElementById("community-editor-category"),
    bodyField: document.getElementById("community-editor-body"),
    pageTitle: document.getElementById("community-editor-page-title"),
    pageCopy: document.getElementById("community-editor-page-copy"),
  };
  const state = {
    noteId: null,
    images: [],
    saving: false,
    dirty: false,
  };
  const client = createBrowserClient();

  function currentMode() {
    return /\/edit$/.test(window.location.pathname) ? "edit" : "create";
  }

  function editingNoteId() {
    const match = window.location.pathname.match(/\/community\/notes\/([^/]+)\/edit$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  function setStatus(message) {
    elements.status.textContent = message;
  }

  function setError(message) {
    if (!message) {
      elements.errors.hidden = true;
      elements.errors.textContent = "";
      return;
    }
    elements.errors.hidden = false;
    elements.errors.textContent = message;
  }

  function setDirty(value) {
    state.dirty = value;
    elements.body.dataset.communityEditorDirty = value ? "true" : "false";
  }

  function safePreviewUrl(item) {
    if (!item || typeof item.previewUrl !== "string") return null;
    return safeUrl(item.previewUrl);
  }

  function clearChildren(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function updateButtons() {
    elements.save.disabled = state.saving;
    elements.submit.disabled = state.saving;
  }

  function markLoadedState() {
    if (currentMode() !== "edit") return;
    elements.pageTitle.textContent = "编辑旅行游记";
    elements.pageCopy.textContent = "修改后的内容会继续保存在草稿中，你也可以再次提交审核。";
  }

  function revokePreview(item) {
    if (!item || !item.localPreview || !item.previewUrl) return;
    window.URL.revokeObjectURL(item.previewUrl);
  }

  function render() {
    clearChildren(elements.previewList);
    for (const [index, item] of state.images.entries()) {
      const row = document.createElement("li");
      row.className = "community-editor-preview-item";

      const previewUrl = safePreviewUrl(item);
      if (previewUrl) {
        const image = document.createElement("img");
        image.className = "community-editor-preview-image";
        image.src = previewUrl;
        image.alt = trimText(item.fileName) || `第 ${index + 1} 张图片`;
        row.append(image);
      }

      const meta = document.createElement("div");
      meta.className = "community-editor-preview-meta";
      const title = document.createElement("p");
      title.className = "community-card__title";
      title.textContent = trimText(item.fileName) || `图片 ${index + 1}`;
      meta.append(title);

      const info = document.createElement("p");
      info.className = "community-card__location";
      info.textContent = `${item.width} × ${item.height}`;
      meta.append(info);

      const actions = document.createElement("div");
      actions.className = "community-actions";

      const up = document.createElement("button");
      up.type = "button";
      up.className = "community-card__action";
      up.textContent = "上移";
      up.addEventListener("click", () => moveImage(index, -1));
      actions.append(up);

      const down = document.createElement("button");
      down.type = "button";
      down.className = "community-card__action";
      down.textContent = "下移";
      down.addEventListener("click", () => moveImage(index, 1));
      actions.append(down);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "community-card__action";
      remove.textContent = "移除";
      remove.addEventListener("click", () => removeImage(index));
      actions.append(remove);

      meta.append(actions);
      row.append(meta);
      elements.previewList.append(row);
    }
    updateButtons();
  }

  function moveImage(index, delta) {
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= state.images.length) return;
    const images = state.images.slice();
    const [item] = images.splice(index, 1);
    images.splice(nextIndex, 0, item);
    state.images = images;
    setDirty(true);
    render();
  }

  function removeImage(index) {
    const images = state.images.slice();
    const [removed] = images.splice(index, 1);
    revokePreview(removed);
    state.images = images;
    setDirty(true);
    render();
  }

  async function canvasToWebp(bitmap, scale, quality) {
    const canvas = document.createElement("canvas");
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context || typeof context.drawImage !== "function") {
      throw new Error("IMAGE_PROCESSING_FAILED");
    }
    context.drawImage(bitmap, 0, 0, width, height);
    return await new Promise((resolve, reject) => {
      canvas.toBlob((blob) => {
        if (!blob) {
          reject(new Error("IMAGE_PROCESSING_FAILED"));
          return;
        }
        resolve({ blob, width, height });
      }, "image/webp", quality);
    });
  }

  async function prepareCommunityImage(file) {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) throw new Error("UNSUPPORTED_IMAGE");
    if (file.size > MAX_IMAGE_SIZE) throw new Error("IMAGE_TOO_LARGE");
    const bitmap = await window.createImageBitmap(file);
    const scale = Math.min(1, 2048 / Math.max(bitmap.width, bitmap.height));
    const coverScale = Math.min(scale, 720 / Math.max(bitmap.width, 1));
    const processed = await canvasToWebp(bitmap, scale, 0.82);
    const cover = await canvasToWebp(bitmap, coverScale, 0.8);
    if (typeof bitmap.close === "function") bitmap.close();
    return {
      id: window.crypto.randomUUID(),
      fileName: file.name,
      width: processed.width,
      height: processed.height,
      blob: processed.blob,
      coverBlob: cover.blob,
      previewUrl: window.URL.createObjectURL(processed.blob),
      localPreview: true,
      existing: false,
      storagePath: null,
    };
  }

  async function addFiles(files) {
    const nextFiles = Array.from(files || []);
    if (!nextFiles.length) return;
    if (state.images.length + nextFiles.length > MAX_IMAGES) {
      setError("最多上传 9 张图片，请先移除部分图片后再继续。");
      return;
    }
    setError("");
    const prepared = [];
    try {
      for (const file of nextFiles) prepared.push(await prepareCommunityImage(file));
    } catch (error) {
      for (const item of prepared) revokePreview(item);
      if (error && error.message === "UNSUPPORTED_IMAGE") setError("仅支持 JPG、PNG 或 WebP 图片。");
      else if (error && error.message === "IMAGE_TOO_LARGE") setError("单张图片不能超过 10 MB。");
      else setError("图片处理失败，请重试。");
      return;
    }
    state.images = [...state.images, ...prepared];
    elements.fileInput.value = "";
    setDirty(true);
    render();
  }

  function validateDraft() {
    const value = {
      title: trimText(elements.title.value),
      body: trimText(elements.bodyField.value),
      location_name: trimText(elements.location.value),
      category: trimText(elements.category.value),
    };
    if (!value.title || !value.body || !value.location_name || !value.category) {
      throw new Error("请先填写标题、地点、分类和正文。");
    }
    if (!CATEGORIES.includes(value.category) || value.category === "全部") {
      throw new Error("请选择有效的社区分类。");
    }
    if (!state.images.length) {
      throw new Error("请至少上传 1 张图片。");
    }
    return value;
  }
  function createAuthRequiredError() {
    const error = new Error("AUTH_REQUIRED");
    error.code = "AUTH_REQUIRED";
    return error;
  }

  function currentUser() {
    const session = client.getSession();
    const user = session && session.user;
    if (!user || typeof user.id !== "string" || !user.id) {
      throw createAuthRequiredError();
    }
    return user;
  }

  function manifestFor(folderId) {
    const user = currentUser();
    return state.images.map((image, index) => ({
      storage_path: image.existing && image.storagePath && !image.blob
        ? image.storagePath
        : `${user.id}/${folderId}/${image.id}.webp`,
      sort_order: index,
      width: image.width,
      height: image.height,
    }));
  }

  function storageBucket() {
    currentUser();
    const supabaseClient = client.getSupabaseClient();
    if (!supabaseClient || !supabaseClient.storage || typeof supabaseClient.storage.from !== "function") {
      throw createAuthRequiredError();
    }
    return supabaseClient.storage.from("community-media");
  }

  async function createDraft(payload) {
    const created = await client.requestJson("/api/community/notes", {
      method: "POST",
      auth: true,
      body: { ...payload, images: [] },
    });
    state.noteId = created && created.id ? String(created.id) : null;
  }

  async function rollbackUploadedImages(uploaded) {
    if (!uploaded.length) return;
    const bucket = storageBucket();
    await bucket.remove(uploaded.map((entry) => entry.path));
    for (const entry of uploaded) {
      entry.image.storagePath = entry.previousStoragePath;
      entry.image.existing = entry.previousExisting;
    }
  }

  async function uploadImages(folderId) {
    const bucket = storageBucket();
    const uploaded = [];
    try {
      for (const image of state.images) {
        if (!image.blob || (image.existing && image.storagePath)) continue;
        const user = currentUser();
        const pathname = `${user.id}/${folderId}/${image.id}.webp`;
        const result = await bucket.upload(pathname, image.blob, {
          contentType: "image/webp",
          upsert: true,
        });
        if (result && result.error) throw result.error;
        uploaded.push({
          image,
          path: pathname,
          previousStoragePath: image.storagePath,
          previousExisting: image.existing,
        });
        image.storagePath = pathname;
        image.existing = true;
      }
      return uploaded;
    } catch (error) {
      await rollbackUploadedImages(uploaded);
      throw error;
    }
  }

  async function saveDraft() {
    if (state.saving) return null;
    const payload = validateDraft();
    state.saving = true;
    updateButtons();
    setError("");
    setStatus("正在保存草稿…");
    let uploaded = [];
    try {
      if (!state.noteId) {
        await createDraft(payload);
      }
      uploaded = await uploadImages(state.noteId);
      const saved = await client.requestJson(`/api/community/notes/${encodeURIComponent(state.noteId)}`, {
        method: "PUT",
        auth: true,
        body: { ...payload, images: manifestFor(state.noteId) },
      });
      setDirty(false);
      setStatus("草稿已保存。");
      return saved;
    } catch (error) {
      if (uploaded.length) {
        await rollbackUploadedImages(uploaded);
      }
      if (error && error.code === "AUTH_REQUIRED") {
        client.redirectToSignIn(window.location.pathname);
        return null;
      }
      setError("草稿保存失败，请稍后重试。");
      setStatus("草稿保存失败。");
      return null;
    } finally {
      state.saving = false;
      updateButtons();
      render();
    }
  }

  async function submitDraft() {
    if (state.saving) return;
    const saved = state.noteId && !state.dirty ? { id: state.noteId } : await saveDraft();
    if (!saved || !state.noteId) return;
    state.saving = true;
    updateButtons();
    setError("");
    setStatus("正在提交审核…");
    try {
      await client.requestJson(`/api/community/notes/${encodeURIComponent(state.noteId)}/submit`, {
        method: "POST",
        auth: true,
      });
      setDirty(false);
      setStatus("游记已提交审核，当前状态为待审核。");
    } catch (error) {
      if (error && error.code === "AUTH_REQUIRED") {
        client.redirectToSignIn(window.location.pathname);
        return;
      }
      setError("提交审核失败，请稍后重试。");
      setStatus("提交审核失败。");
    } finally {
      state.saving = false;
      updateButtons();
    }
  }

  function bindDirty(input) {
    input.addEventListener("input", () => setDirty(true));
    input.addEventListener("change", () => setDirty(true));
  }

  function setDraftFields(note) {
    elements.title.value = trimText(note && note.title);
    elements.location.value = trimText(note && note.location_name);
    elements.category.value = trimText(note && note.category);
    elements.bodyField.value = trimText(note && note.body);
  }

  async function loadExistingDraft() {
    const noteId = editingNoteId();
    if (!noteId) return;
    state.noteId = noteId;
    setStatus("正在加载已有草稿…");
    try {
      const payload = await client.requestJson("/api/me/travel-notes", { auth: true });
      const items = Array.isArray(payload && payload.items) ? payload.items : [];
      const note = items.find((item) => item && String(item.id) === noteId);
      if (!note) {
        setError("未找到要编辑的游记。");
        setStatus("未找到要编辑的游记。");
        return;
      }
      setDraftFields(note);
      state.images = Array.isArray(note.images)
        ? note.images.map((image, index) => ({
            id: String(image.id || window.crypto.randomUUID()),
            fileName: `图片 ${index + 1}`,
            width: Number(image.width) || 1440,
            height: Number(image.height) || 1920,
            blob: null,
            coverBlob: null,
            previewUrl: index === 0 ? safeUrl(note.cover_image_url) : null,
            localPreview: false,
            existing: true,
            storagePath: typeof image.storage_path === "string" ? image.storage_path : null,
          }))
        : [];
      setDirty(false);
      setError("");
      setStatus("已加载可编辑草稿。");
      render();
    } catch (error) {
      if (error && error.code === "AUTH_REQUIRED") {
        client.redirectToSignIn(window.location.pathname);
        return;
      }
      setError("草稿加载失败，请稍后重试。");
      setStatus("草稿加载失败。");
    }
  }

  function bindEvents() {
    elements.fileInput.addEventListener("change", async () => addFiles(elements.fileInput.files));
    elements.save.addEventListener("click", () => {
      void saveDraft();
    });
    elements.submit.addEventListener("click", () => {
      void submitDraft();
    });
    for (const input of [elements.title, elements.location, elements.category, elements.bodyField]) {
      bindDirty(input);
    }
    window.addEventListener("beforeunload", (event) => {
      if (!state.dirty || state.saving) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  async function initialize() {
    markLoadedState();
    bindEvents();
    render();
    await client.initialize();
    if (!client.isSignedIn()) {
      client.redirectToSignIn(window.location.pathname);
      return;
    }
    if (currentMode() === "edit") {
      await loadExistingDraft();
      return;
    }
    setStatus("请先整理图片，再保存草稿或提交审核。");
  }

  void initialize();
})();
