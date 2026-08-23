const assert = require("node:assert/strict");
const test = require("node:test");
const { FakeSupabaseAuth, createHarness, jsonResponse, settle } = require("./dom-harness");

const SESSION = {
  access_token: "access-one",
  refresh_token: "refresh-one",
  expires_at: 2000000000,
  user: { id: "user-a", email: "owner@example.test" },
};

test("auth page accepts same-origin absolute return_to on successful sign-in", async () => {
  const auth = new FakeSupabaseAuth({ loginSession: SESSION });
  const harness = createHarness({
    page: "auth",
    auth,
    search: "?mode=signin&return_to=%2Fprofile",
    fetch: async () => jsonResponse(200, {}),
  });
  await settle();

  harness.elements.get("auth-page-email").value = "owner@example.test";
  harness.elements.get("auth-page-password").value = "correct-horse-battery";
  await harness.elements.get("auth-page-form").dispatch("submit");
  await settle();

  assert.equal(harness.window.location.pathname, "/profile");
  assert.equal(harness.window.location.search, "");
});

test("auth page rejects external return_to when an existing session is already present", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "auth",
    auth,
    search: "?mode=signin&return_to=https%3A%2F%2Fevil.example%2Fsteal",
    fetch: async () => jsonResponse(200, {}),
  });

  await settle();

  assert.equal(harness.window.location.pathname, "/");
  assert.equal(harness.window.location.search, "");
});

test("auth page rejects protocol-relative return_to on successful sign-up", async () => {
  const auth = new FakeSupabaseAuth({ loginSession: SESSION });
  const harness = createHarness({
    page: "auth",
    auth,
    search: "?mode=signup&return_to=%2F%2Fevil.example%2Fsteal",
    fetch: async () => jsonResponse(200, {}),
  });
  await settle();

  harness.elements.get("auth-page-email").value = "owner@example.test";
  harness.elements.get("auth-page-password").value = "correct-horse-battery";
  await harness.elements.get("auth-page-form").dispatch("submit");
  await settle();

  assert.equal(harness.window.location.pathname, "/");
  assert.equal(harness.window.location.search, "");
});


test("auth page exposes a safe return button for login and registration", async () => {
  const harness = createHarness({
    page: "auth",
    search: "?mode=signup&return_to=%2Fcommunity",
    fetch: async () => jsonResponse(200, {}),
  });
  await settle();

  assert.equal(harness.elements.get("auth-page-back").href, "/community");
  const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "../../app/static/auth.html"), "utf8");
  assert.match(html, /id="auth-page-back"[^>]*>返回<\/a>/);
});

test("auth page exposes a user-admin login switch", () => {
  const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "../../app/static/auth.html"), "utf8");
  assert.match(html, /id="auth-page-role-switch"[^>]*role="switch"[^>]*aria-checked="false"/);
  assert.match(html, /id="auth-page-role-label"[^>]*>用户登录<\/span>/);
  assert.match(html, />管理员登录<\/button>/);
});

test("auth page role switch sends administrator sign-in to the admin view", async () => {
  const auth = new FakeSupabaseAuth({ loginSession: SESSION });
  const harness = createHarness({
    page: "auth",
    auth,
    search: "?mode=signin",
    fetch: async () => jsonResponse(200, {}),
  });
  await settle();

  await harness.elements.get("auth-page-role-switch").dispatch("click");
  assert.equal(harness.elements.get("auth-page-role-switch").getAttribute("aria-checked"), "true");
  assert.equal(harness.elements.get("auth-page-role-label").textContent, "管理员登录");

  harness.elements.get("auth-page-email").value = "owner@example.test";
  harness.elements.get("auth-page-password").value = "correct-horse-battery";
  await harness.elements.get("auth-page-form").dispatch("submit");
  await settle();

  assert.equal(harness.window.location.pathname, "/admin/community");
});

test("auth page keeps forgot password in its own left-aligned action row", () => {
  const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "../../app/static/auth.html"), "utf8");
  const styles = require("node:fs").readFileSync(require("node:path").join(__dirname, "../../app/static/styles.css"), "utf8");
  assert.match(html, /id="auth-page-actions" class="auth-page-actions"/);
  assert.match(html, /class="auth-page-forgot-row"[\s\S]*id="auth-page-forgot"/);
  assert.match(styles, /\.auth-page-forgot-row\s*\{[^}]*align-items:\s*flex-start/);
});
