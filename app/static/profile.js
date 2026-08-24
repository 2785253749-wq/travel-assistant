(() => {
  "use strict";

  const elements = {
    body: document.body,
    form: document.getElementById("profile-form"),
    passwordForm: document.getElementById("password-form"),
    authPrompt: document.getElementById("profile-auth-prompt"),
    status: document.getElementById("profile-status"),
    back: document.getElementById("profile-back-link"),
    signIn: document.getElementById("profile-signin-link"),
    signOut: document.getElementById("profile-sign-out-button"),
    avatarInput: document.getElementById("profile-avatar-input"),
    avatarImage: document.getElementById("profile-avatar-image"),
    avatarFallback: document.getElementById("profile-avatar-fallback"),
    email: document.getElementById("profile-email"),
    displayName: document.getElementById("profile-display-name"),
    phone: document.getElementById("profile-phone"),
    password: document.getElementById("profile-password"),
    passwordConfirm: document.getElementById("profile-password-confirm"),
  };
  let client = null;
  let session = null;
  let avatarDataUrl = null;
  let busy = false;

  function sameOriginReturnTo(value) {
    if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
    const target = new URL(value, window.location.origin);
    if (target.origin !== window.location.origin) return "/";
    return `${target.pathname}${target.search}${target.hash}`;
  }

  function authConfig() {
    const config = window.TRAVEL_ASSISTANT_CONFIG;
    if (!config || typeof config.supabaseUrl !== "string" || !config.supabaseUrl
      || typeof config.supabaseAnonKey !== "string" || !config.supabaseAnonKey) return null;
    return { url: config.supabaseUrl, anonKey: config.supabaseAnonKey };
  }

  function setStatus(message, isError = false) {
    elements.status.textContent = message;
    elements.status.dataset.error = isError ? "true" : "false";
  }

  function setBusy(nextBusy) {
    busy = nextBusy;
    for (const control of document.querySelectorAll("button,input")) control.disabled = nextBusy;
  }

  function initials(user) {
    const metadata = user && user.user_metadata && typeof user.user_metadata === "object" ? user.user_metadata : {};
    const value = typeof metadata.display_name === "string" && metadata.display_name.trim()
      ? metadata.display_name.trim() : (user && user.email ? user.email : "V");
    return value.slice(0, 1).toUpperCase();
  }

  function renderAvatar(user) {
    const metadata = user && user.user_metadata && typeof user.user_metadata === "object" ? user.user_metadata : {};
    const url = avatarDataUrl || (typeof metadata.avatar_url === "string" ? metadata.avatar_url : "");
    if (url) {
      elements.avatarImage.src = url;
      elements.avatarImage.hidden = false;
      elements.avatarFallback.hidden = true;
      return;
    }
    elements.avatarImage.src = "";
    elements.avatarImage.hidden = true;
    elements.avatarFallback.hidden = false;
    elements.avatarFallback.textContent = initials(user);
  }

  function showSignedOut() {
    elements.body.dataset.profileState = "signed_out";
    elements.form.hidden = true;
    elements.passwordForm.hidden = true;
    elements.authPrompt.hidden = false;
  }

  function applyUser(user) {
    const metadata = user && user.user_metadata && typeof user.user_metadata === "object" ? user.user_metadata : {};
    elements.email.value = user && user.email ? user.email : "";
    elements.displayName.value = typeof metadata.display_name === "string" ? metadata.display_name : "";
    elements.phone.value = typeof metadata.phone === "string" ? metadata.phone : "";
    avatarDataUrl = typeof metadata.avatar_url === "string" ? metadata.avatar_url : null;
    renderAvatar(user);
    elements.body.dataset.profileState = "ready";
    elements.authPrompt.hidden = true;
    elements.form.hidden = false;
    elements.passwordForm.hidden = false;
  }

  function redirectToSignIn() {
    const url = new URL("/auth", window.location.origin);
    url.searchParams.set("mode", "signin");
    url.searchParams.set("return_to", "/profile");
    window.location.href = url.toString();
  }

  function readAvatar(file) {
    if (!file) return Promise.resolve(null);
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      return Promise.reject(new Error("IMAGE_INVALID"));
    }
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.addEventListener("load", () => resolve(typeof reader.result === "string" ? reader.result : null));
      reader.addEventListener("error", () => reject(new Error("IMAGE_READ_FAILED")));
      reader.readAsDataURL(file);
    });
  }

  async function saveProfile(event) {
    event.preventDefault();
    if (busy || !session) return;
    setBusy(true);
    setStatus("正在保存个人信息…");
    try {
      const result = await client.auth.updateUser({
        data: {
          ...(session.user.user_metadata || {}),
          display_name: elements.displayName.value.trim(),
          phone: elements.phone.value.trim(),
          ...(avatarDataUrl ? { avatar_url: avatarDataUrl } : {}),
        },
      });
      if (result.error) throw result.error;
      session.user = result.data.user;
      applyUser(session.user);
      setStatus("个人信息已保存。");
    } catch (_) {
      setStatus("个人信息保存失败，请稍后重试。", true);
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(event) {
    event.preventDefault();
    if (busy || !session) return;
    if (elements.password.value.length < 8 || elements.password.value !== elements.passwordConfirm.value) {
      setStatus("请确认两次输入的密码一致，且不少于 8 位。", true);
      return;
    }
    setBusy(true);
    setStatus("正在修改密码…");
    try {
      const result = await client.auth.updateUser({ password: elements.password.value });
      if (result.error) throw result.error;
      elements.password.value = "";
      elements.passwordConfirm.value = "";
      setStatus("密码已修改。");
    } catch (_) {
      setStatus("密码修改失败，请稍后重试。", true);
    } finally {
      setBusy(false);
    }
  }

  async function initialize() {
    const config = authConfig();
    if (!config || !window.supabase || typeof window.supabase.createClient !== "function") {
      setStatus("当前部署尚未配置账户服务，请稍后再试。", true);
      return;
    }
    client = window.supabase.createClient(config.url, config.anonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    client.auth.onAuthStateChange((_event, nextSession) => {
      session = nextSession;
      if (session && session.user) applyUser(session.user);
      else showSignedOut();
    });
    const result = await client.auth.getSession();
    session = result && result.data ? result.data.session : null;
    if (session && session.user) applyUser(session.user);
    else showSignedOut();
  }

  elements.back.href = sameOriginReturnTo(new URLSearchParams(window.location.search).get("return_to"));
  elements.signIn.href = `/auth?mode=signin&return_to=${encodeURIComponent("/profile")}`;
  elements.avatarInput.addEventListener("change", async () => {
    try {
      avatarDataUrl = await readAvatar(elements.avatarInput.files && elements.avatarInput.files[0]);
      renderAvatar(session && session.user);
      setStatus("头像已准备好，保存后生效。");
    } catch (_) {
      elements.avatarInput.value = "";
      setStatus("头像必须是 2 MB 以内的 JPG、PNG 或 WebP 图片。", true);
    }
  });
  elements.form.addEventListener("submit", saveProfile);
  elements.passwordForm.addEventListener("submit", changePassword);
  elements.signOut.addEventListener("click", async () => {
    if (busy || !client) return;
    setBusy(true);
    await client.auth.signOut();
    setBusy(false);
    redirectToSignIn();
  });
  initialize();
})();
