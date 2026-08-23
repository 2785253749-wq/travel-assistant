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
