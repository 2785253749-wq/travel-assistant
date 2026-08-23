(() => {
  "use strict";

  const AVATAR_BUCKET = "community-media";
  const AVATAR_MAX_FILE_SIZE = 10 * 1024 * 1024;
  const AVATAR_MAX_EDGE = 1024;
  const AVATAR_OUTPUT_TYPE = "image/webp";
  const AVATAR_OUTPUT_QUALITY = 0.82;
  const TRAVEL_STYLE_FIELDS = [
    { id: "travel-style-food", value: "美食", label: "旅行风格" },
    { id: "travel-style-culture", value: "人文", label: "旅行风格" },
    { id: "travel-style-nature", value: "自然", label: "旅行风格" },
    { id: "travel-style-family", value: "亲子", label: "旅行风格" },
    { id: "travel-style-outdoor", value: "户外", label: "旅行风格" },
    { id: "travel-style-leisure", value: "休闲", label: "旅行风格" },
  ];
  const FIELD_LABELS = {
    display_name: "昵称",
    bio: "简介",
    home_city: "常驻城市",
    travel_styles: "旅行风格",
  };
  const elements = {
    body: document.body,
    form: document.getElementById("profile-form"),
    loading: document.getElementById("profile-loading"),
    error: document.getElementById("profile-error"),
    retry: document.getElementById("profile-retry-button"),
    status: document.getElementById("profile-status"),
    errors: document.getElementById("profile-errors"),
    avatarImage: document.getElementById("profile-avatar-image"),
    avatarFallback: document.getElementById("profile-avatar-fallback"),
    avatarInput: document.getElementById("profile-avatar-input"),
    email: document.getElementById("profile-email"),
    displayName: document.getElementById("profile-display-name"),
    bio: document.getElementById("profile-bio"),
    homeCity: document.getElementById("profile-home-city"),
    updatedAt: document.getElementById("profile-updated-at"),
    save: document.getElementById("profile-save-button"),
    backLink: document.getElementById("profile-back-link"),
  };
  const travelStyleInputs = TRAVEL_STYLE_FIELDS.map((field) => document.getElementById(field.id));
  let client = null;
  let session = null;
  let busy = false;
  let profileLoadGeneration = 0;
  let sessionGeneration = 0;
  let avatarPreviewUrl = null;
  let pendingAvatarBlob = null;
  let savedAvatarUrl = null;

  function setStatus(message, isError = false) {
    elements.status.textContent = message;
    elements.status.dataset.error = isError ? "true" : "false";
  }

  function setBusy(nextBusy) {
    busy = nextBusy;
    for (const control of elements.form.querySelectorAll("button,input,textarea")) control.disabled = nextBusy;
  }

  function setView(state) {
    elements.body.dataset.profileState = state;
    elements.loading.hidden = state !== "loading";
    elements.error.hidden = state !== "error";
    elements.form.hidden = state === "loading" || state === "error";
  }

  function sameOriginReturnTo(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin || !target.pathname.startsWith("/")) return "/";
    return `${target.pathname}${target.search}${target.hash}`;
  }

  function signInUrl() {
    const url = new URL("/auth", window.location.origin);
    url.searchParams.set("mode", "signin");
    url.searchParams.set("return_to", "/profile");
    return url.toString();
  }

  function redirectToSignIn() {
    sessionGeneration += 1;
    profileLoadGeneration += 1;
    session = null;
    setBusy(false);
    clearProfileForm();
    clearValidationErrors();
    setView("loading");
    setStatus("正在跳转到登录页…");
    window.location.href = signInUrl();
  }

  function authConfig() {
    const config = window.TRAVEL_ASSISTANT_CONFIG;
    if (!config || typeof config.supabaseUrl !== "string" || !config.supabaseUrl
      || typeof config.supabaseAnonKey !== "string" || !config.supabaseAnonKey) return null;
    return { url: config.supabaseUrl, anonKey: config.supabaseAnonKey };
  }

  function selectedTravelStyles() {
    return TRAVEL_STYLE_FIELDS
      .filter((field, index) => travelStyleInputs[index] && travelStyleInputs[index].checked)
      .map((field) => field.value);
  }

  function sessionUserId(value) {
    const userId = value && value.user && value.user.id;
    return typeof userId === "string" && userId ? userId : null;
  }

  function currentSessionOwner() {
    const userId = sessionUserId(session);
    if (!userId || !session || typeof session.access_token !== "string" || !session.access_token) return null;
    return { accessToken: session.access_token, generation: sessionGeneration, userId };
  }

  function sessionOwnerIsCurrent(owner) {
    return Boolean(owner
      && owner.generation === sessionGeneration
      && owner.userId === sessionUserId(session));
  }

  function responseBelongsToOwner(profile, owner) {
    return Boolean(profile
      && typeof profile.user_id === "string"
      && profile.user_id === owner.userId);
  }

  function deriveInitials(displayName, email) {
    const normalizedName = typeof displayName === "string" ? displayName.trim() : "";
    if (normalizedName) {
      const words = normalizedName.split(/\s+/).filter(Boolean);
      if (words.length >= 2) {
        return `${words[0][0] || ""}${words[1][0] || ""}`.toUpperCase();
      }
      return normalizedName.slice(0, 2).toUpperCase();
    }
    const localPart = typeof email === "string" ? email.split("@")[0].trim() : "";
    if (localPart) return localPart.slice(0, 2).toUpperCase();
    return "V";
  }

  function revokeAvatarPreviewUrl() {
    if (!avatarPreviewUrl) return;
    if (window.URL && typeof window.URL.revokeObjectURL === "function") {
      window.URL.revokeObjectURL(avatarPreviewUrl);
    }
    avatarPreviewUrl = null;
  }

  function resetPendingAvatar() {
    pendingAvatarBlob = null;
    revokeAvatarPreviewUrl();
    if (elements.avatarInput) elements.avatarInput.value = "";
  }

  function renderAvatar(profile = {}) {
    const previewUrl = avatarPreviewUrl;
    const remoteUrl = typeof profile.avatar_url === "string" ? profile.avatar_url : savedAvatarUrl;
    const displayName = typeof profile.display_name === "string"
      ? profile.display_name
      : elements.displayName.value;
    const email = typeof profile.email === "string" ? profile.email : elements.email.value;
    const imageUrl = previewUrl || remoteUrl || null;
    if (imageUrl) {
      elements.avatarImage.src = imageUrl;
      elements.avatarImage.hidden = false;
      elements.avatarFallback.hidden = true;
      elements.avatarFallback.textContent = "";
      return;
    }
    elements.avatarImage.hidden = true;
    elements.avatarImage.src = "";
    elements.avatarFallback.hidden = false;
    elements.avatarFallback.textContent = deriveInitials(displayName, email);
  }

  function applyProfile(profile) {
    const styles = new Set(Array.isArray(profile.travel_styles) ? profile.travel_styles : []);
    savedAvatarUrl = typeof profile.avatar_url === "string" ? profile.avatar_url : null;
    elements.email.value = typeof profile.email === "string" ? profile.email : "";
    elements.displayName.value = typeof profile.display_name === "string" ? profile.display_name : "";
    elements.bio.value = typeof profile.bio === "string" ? profile.bio : "";
    elements.homeCity.value = typeof profile.home_city === "string" ? profile.home_city : "";
    travelStyleInputs.forEach((input, index) => {
      input.checked = styles.has(TRAVEL_STYLE_FIELDS[index].value);
    });
    renderAvatar(profile);
    elements.updatedAt.textContent = profile.updated_at
      ? `最近更新：${String(profile.updated_at).replace("T", " ").replace("Z", "")}`
      : "尚未保存个人信息。";
  }

  function clearProfileForm() {
    savedAvatarUrl = null;
    resetPendingAvatar();
    applyProfile({
      email: "",
      display_name: "",
      bio: "",
      home_city: "",
      travel_styles: [],
      avatar_url: null,
      updated_at: null,
    });
  }

  function clearValidationErrors() {
    elements.errors.hidden = true;
    elements.errors.textContent = "";
  }

  function renderValidationErrors(detail) {
    const messages = Array.isArray(detail)
      ? detail.map((item) => {
        const field = Array.isArray(item && item.loc) ? item.loc.find((segment) => typeof segment === "string" && FIELD_LABELS[segment]) : null;
        const label = FIELD_LABELS[field] || "输入内容";
        const message = item && typeof item.msg === "string" ? item.msg : "格式无效";
        return `${label}：${message}`;
      })
      : [];
    elements.errors.textContent = messages.join("；") || "输入内容无效，请检查后重试。";
    elements.errors.hidden = false;
  }

  async function requestProfile(owner, method = "GET", body = undefined) {
    const response = await fetch("/api/profile", {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${owner.accessToken}`,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (response.status === 401) {
      const error = new Error("AUTH_REQUIRED");
      error.code = "AUTH_REQUIRED";
      throw error;
    }
    if (!response.ok) {
      const error = new Error("PROFILE_REQUEST_FAILED");
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function communityMediaBucket() {
    if (!client || !client.storage) throw new Error("STORAGE_UNAVAILABLE");
    if (typeof client.storage.from === "function") return client.storage.from(AVATAR_BUCKET);
    if (typeof client.storage.from_ === "function") return client.storage.from_(AVATAR_BUCKET);
    throw new Error("STORAGE_UNAVAILABLE");
  }

  async function compressAvatarFile(file) {
    if (!file || typeof file.type !== "string") throw new Error("IMAGE_REQUIRED");
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      throw new Error("UNSUPPORTED_IMAGE");
    }
    if (typeof file.size === "number" && file.size > AVATAR_MAX_FILE_SIZE) {
      throw new Error("IMAGE_TOO_LARGE");
    }
    if (typeof createImageBitmap !== "function") return file;
    const bitmap = await createImageBitmap(file);
    try {
      const longestEdge = Math.max(bitmap.width || 1, bitmap.height || 1);
      const scale = Math.min(1, AVATAR_MAX_EDGE / longestEdge);
      const width = Math.max(1, Math.round((bitmap.width || 1) * scale));
      const height = Math.max(1, Math.round((bitmap.height || 1) * scale));
      const canvas = document.createElement("canvas");
      if (!canvas || typeof canvas.getContext !== "function" || typeof canvas.toBlob !== "function") {
        return file;
      }
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d");
      if (!context || typeof context.drawImage !== "function") return file;
      context.drawImage(bitmap, 0, 0, width, height);
      const blob = await new Promise((resolve, reject) => {
        canvas.toBlob((nextBlob) => {
          if (nextBlob) resolve(nextBlob);
          else reject(new Error("IMAGE_COMPRESSION_FAILED"));
        }, AVATAR_OUTPUT_TYPE, AVATAR_OUTPUT_QUALITY);
      });
      return blob;
    } finally {
      if (bitmap && typeof bitmap.close === "function") bitmap.close();
    }
  }

  async function onAvatarSelected() {
    const [file] = Array.isArray(elements.avatarInput.files) ? elements.avatarInput.files : elements.avatarInput.files || [];
    if (!file) return;
    try {
      const blob = await compressAvatarFile(file);
      resetPendingAvatar();
      pendingAvatarBlob = blob;
      if (window.URL && typeof window.URL.createObjectURL === "function") {
        avatarPreviewUrl = window.URL.createObjectURL(blob);
      }
      renderAvatar();
      setStatus("头像已准备上传，保存后会同步更新。");
    } catch (error) {
      resetPendingAvatar();
      renderAvatar();
      setStatus("头像处理失败，请更换 JPG、PNG 或 WebP 图片后重试。", true);
    }
  }

  async function uploadPendingAvatar(owner) {
    if (!pendingAvatarBlob) return null;
    const path = `${owner.userId}/avatar/${window.crypto.randomUUID()}.webp`;
    const bucket = communityMediaBucket();
    const result = await bucket.upload(path, pendingAvatarBlob, {
      contentType: AVATAR_OUTPUT_TYPE,
      upsert: false,
    });
    if (result && result.error) throw result.error;
    return path;
  }

  async function cleanupUploadedAvatar(path) {
    if (!path) return;
    try {
      await communityMediaBucket().remove([path]);
    } catch (_error) {
      // A later server-side cleanup pass will catch any orphaned path.
    }
  }

  async function loadProfile() {
    const owner = currentSessionOwner();
    if (!owner) {
      redirectToSignIn();
      return;
    }
    const loadGeneration = ++profileLoadGeneration;
    const isCurrentLoad = () => (
      loadGeneration === profileLoadGeneration && sessionOwnerIsCurrent(owner)
    );
    clearValidationErrors();
    setView("loading");
    setStatus("正在加载个人信息…");
    try {
      const profile = await requestProfile(owner);
      if (!isCurrentLoad()) return;
      if (!responseBelongsToOwner(profile, owner)) {
        throw new Error("PROFILE_IDENTITY_MISMATCH");
      }
      applyProfile(profile);
      setView("ready");
      setStatus("个人信息已加载。");
    } catch (error) {
      if (!isCurrentLoad()) return;
      if (error && error.code === "AUTH_REQUIRED") {
        redirectToSignIn();
        return;
      }
      setView("error");
      setStatus("个人信息加载失败，请重试。", true);
    }
  }

  async function saveProfile(event) {
    event.preventDefault();
    if (busy || elements.body.dataset.profileState !== "ready") return;
    const owner = currentSessionOwner();
    if (!owner) {
      redirectToSignIn();
      return;
    }
    const isCurrentSave = () => sessionOwnerIsCurrent(owner);
    clearValidationErrors();
    setBusy(true);
    elements.body.dataset.profileState = "saving";
    setStatus("正在保存个人信息…");
    let uploadedAvatarPath = null;
    try {
      uploadedAvatarPath = await uploadPendingAvatar(owner);
      if (!isCurrentSave()) {
        await cleanupUploadedAvatar(uploadedAvatarPath);
        return;
      }
      const payload = {
        display_name: elements.displayName.value,
        bio: elements.bio.value,
        home_city: elements.homeCity.value,
        travel_styles: selectedTravelStyles(),
      };
      if (uploadedAvatarPath) payload.avatar_path = uploadedAvatarPath;
      const profile = await requestProfile(owner, "PUT", payload);
      if (!isCurrentSave()) return;
      if (!responseBelongsToOwner(profile, owner)) {
        throw new Error("PROFILE_IDENTITY_MISMATCH");
      }
      resetPendingAvatar();
      applyProfile(profile);
      setView("ready");
      setStatus("个人信息已保存。");
    } catch (error) {
      if (!isCurrentSave()) return;
      if (error && error.code === "AUTH_REQUIRED") {
        redirectToSignIn();
        return;
      }
      if (uploadedAvatarPath) {
        await cleanupUploadedAvatar(uploadedAvatarPath);
      }
      if (error && error.status === 422) {
        renderValidationErrors(error.payload && error.payload.detail);
        setView("ready");
        setStatus("请检查输入内容后重新保存。", true);
      } else {
        setView("ready");
        setStatus("个人信息保存失败，请稍后重试。", true);
      }
    } finally {
      if (isCurrentSave()) setBusy(false);
    }
  }

  async function applyAuthSession(nextSession) {
    const nextUserId = sessionUserId(nextSession);
    if (!nextUserId || !nextSession || typeof nextSession.access_token !== "string" || !nextSession.access_token) {
      redirectToSignIn();
      return;
    }

    const identityChanged = nextUserId !== sessionUserId(session);
    session = nextSession;
    if (!identityChanged) return;

    sessionGeneration += 1;
    profileLoadGeneration += 1;
    setBusy(false);
    clearProfileForm();
    clearValidationErrors();
    setView("loading");
    setStatus("正在加载个人信息…");
    await loadProfile();
  }

  async function initialize() {
    const params = new URLSearchParams(window.location.search);
    elements.backLink.href = sameOriginReturnTo(params.get("return_to") || "/");
    elements.backLink.addEventListener("click", (event) => {
      event.preventDefault();
      window.location.href = new URL(elements.backLink.href, window.location.origin).toString();
    });
    const config = authConfig();
    if (!config || !window.supabase || typeof window.supabase.createClient !== "function") {
      setView("error");
      setStatus("当前部署尚未配置账户服务，请稍后再试。", true);
      return;
    }
    client = window.supabase.createClient(config.url, config.anonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    client.auth.onAuthStateChange((_event, nextSession) => {
      void applyAuthSession(nextSession);
    });
    const initialSessionGeneration = sessionGeneration;
    const result = await client.auth.getSession();
    if (initialSessionGeneration !== sessionGeneration) return;
    await applyAuthSession(result && result.data ? result.data.session : null);
  }

  elements.avatarInput.addEventListener("change", () => {
    void onAvatarSelected();
  });
  elements.displayName.addEventListener("input", () => {
    if (elements.avatarImage.hidden) renderAvatar();
  });
  elements.form.addEventListener("submit", saveProfile);
  elements.retry.addEventListener("click", loadProfile);
  initialize();
})();
