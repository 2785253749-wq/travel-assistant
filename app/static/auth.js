(() => {
  "use strict";

  const elements = {
    body: document.body,
    title: document.getElementById("auth-page-title"),
    description: document.getElementById("auth-page-description"),
    form: document.getElementById("auth-page-form"),
    email: document.getElementById("auth-page-email"),
    password: document.getElementById("auth-page-password"),
    submit: document.getElementById("auth-page-submit"),
    alternate: document.getElementById("auth-page-alternate"),
    forgot: document.getElementById("auth-page-forgot"),
    status: document.getElementById("auth-page-status"),
  };
  let mode = new URLSearchParams(window.location.search).get("mode") === "signup" ? "signup" : "signin";
  let busy = false;
  let client = null;

  function setStatus(message, isError = false) {
    elements.status.textContent = message;
    elements.status.dataset.error = isError ? "true" : "false";
  }

  function setBusy(nextBusy) {
    busy = nextBusy;
    for (const control of elements.form.querySelectorAll("button,input")) control.disabled = nextBusy;
    elements.forgot.disabled = nextBusy;
  }

  function setMode(nextMode) {
    mode = nextMode === "signup" ? "signup" : "signin";
    const signup = mode === "signup";
    elements.body.dataset.authMode = mode;
    elements.title.textContent = signup ? "注册 Voyage 账户" : "登录 Voyage";
    elements.description.textContent = signup
      ? "注册后可以保存、打开和管理你的私有行程。"
      : "登录后可以保存、打开和管理你的私有行程。";
    elements.submit.textContent = signup ? "注册账户" : "登录";
    elements.alternate.textContent = signup ? "已有账户，去登录" : "注册账户";
    elements.password.autocomplete = signup ? "new-password" : "current-password";
    document.title = signup ? "注册 Voyage 账户" : "登录 Voyage";
  }

  function redirectHome() {
    window.location.href = "/";
  }

  function authConfig() {
    const config = window.TRAVEL_ASSISTANT_CONFIG;
    if (!config || typeof config.supabaseUrl !== "string" || !config.supabaseUrl
      || typeof config.supabaseAnonKey !== "string" || !config.supabaseAnonKey) return null;
    return { url: config.supabaseUrl, anonKey: config.supabaseAnonKey };
  }

  async function submitAuth(event) {
    event.preventDefault();
    if (busy) return;
    const email = elements.email.value.trim();
    const password = elements.password.value;
    if (!email || !password) {
      setStatus("请输入邮箱和密码。", true);
      return;
    }
    if (!client) {
      setStatus("当前部署尚未配置账户服务，请稍后再试。", true);
      return;
    }
    setBusy(true);
    setStatus(mode === "signup" ? "正在注册…" : "正在登录…");
    try {
      const result = mode === "signup"
        ? await client.auth.signUp({ email, password })
        : await client.auth.signInWithPassword({ email, password });
      if (result.error) throw result.error;
      if (mode === "signup" && (!result.data || !result.data.session)) {
        setStatus("注册请求已提交，请按邮箱提示完成验证后登录。");
        elements.password.value = "";
        return;
      }
      redirectHome();
    } catch (_) {
      setStatus(mode === "signup" ? "注册失败，请检查邮箱格式和密码要求。" : "登录失败，请检查邮箱和密码。", true);
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword() {
    if (busy) return;
    if (!client || typeof client.auth.resetPasswordForEmail !== "function") {
      setStatus("当前部署尚未配置密码重置服务，请稍后再试。", true);
      return;
    }
    const email = elements.email.value.trim();
    if (!email) {
      setStatus("请先输入注册邮箱。", true);
      elements.email.focus();
      return;
    }
    setBusy(true);
    setStatus("正在发送重置邮件…");
    try {
      const { error } = await client.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth?mode=signin`,
      });
      if (error) throw error;
      setStatus("密码重置邮件已发送，请检查邮箱。");
    } catch (_) {
      setStatus("密码重置失败，请检查邮箱后重试。", true);
    } finally {
      setBusy(false);
    }
  }

  async function initialize() {
    setMode(mode);
    const config = authConfig();
    if (!config || !window.supabase || typeof window.supabase.createClient !== "function") {
      setStatus("当前部署尚未配置账户服务，请稍后再试。", true);
      return;
    }
    client = window.supabase.createClient(config.url, config.anonKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
    client.auth.onAuthStateChange((_event, session) => {
      if (session) redirectHome();
    });
    const { data, error } = await client.auth.getSession();
    if (!error && data && data.session) redirectHome();
  }

  elements.form.addEventListener("submit", submitAuth);
  elements.alternate.addEventListener("click", () => setMode(mode === "signup" ? "signin" : "signup"));
  elements.forgot.addEventListener("click", resetPassword);
  initialize();
})();
