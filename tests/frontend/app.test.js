const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FakeElement, FakeSupabaseAuth, createHarness, descendants, findByText, jsonResponse, settle } = require("./dom-harness");

const SESSION = { access_token: "access-one", refresh_token: "refresh-one", expires_at: 2000000000, user: { id: "user-a", email: "owner@example.test" } };
const REFRESHED = { access_token: "access-two", refresh_token: "refresh-two", expires_at: 2000003600, user: { id: "user-a", email: "owner@example.test" } };
const CURRENT_SESSION = { access_token: "access-current", refresh_token: "refresh-current", expires_at: 2000007200, user: { id: "user-a", email: "current@example.test" } };
const OTHER_SESSION = { access_token: "access-other", refresh_token: "refresh-other", expires_at: 2000007200, user: { id: "user-b", email: "other@example.test" } };

function observeHidden(element, label, events) {
  let value = element.hidden;
  Object.defineProperty(element, "hidden", {
    configurable: true,
    get() { return value; },
    set(next) { value = next; if (next === true) events.push(label); },
  });
}

function observeChildClear(element, label, events) {
  const removeChild = element.removeChild.bind(element);
  element.removeChild = (child) => { events.push(label); return removeChild(child); };
}

function observeEmptyText(element, label, events) {
  let value = element.textContent;
  Object.defineProperty(element, "textContent", {
    configurable: true,
    get() { return value; },
    set(next) { value = String(next); if (next === "") events.push(label); },
  });
}

function assertBefore(events, first, second) {
  assert.notEqual(events.indexOf(first), -1, `${first} was not observed: ${events.join(", ")}`);
  assert.notEqual(events.indexOf(second), -1, `${second} was not observed: ${events.join(", ")}`);
  assert.ok(events.indexOf(first) < events.indexOf(second), `${first} must precede ${second}: ${events.join(", ")}`);
}

async function dispatchPointer(element, type, properties) {
  const event = { preventDefault() {}, target: element, currentTarget: element, ...properties };
  for (const listener of element.listeners.get(type) || []) await listener(event);
}

async function dispatchKey(element, key) {
  let defaultPrevented = false;
  const event = { key, target: element, currentTarget: element, preventDefault() { defaultPrevented = true; } };
  for (const listener of element.listeners.get("keydown") || []) await listener(event);
  return defaultPrevented;
}

test("navigation switches between explore, trips, and community without a reload", async () => {
  const harness = createHarness();
  await settle();

  const tripsNav = harness.elements.get("trips-nav-button");
  const communityNav = harness.elements.get("community-nav-button");
  const explore = harness.elements.get("explore-page");
  const trips = harness.elements.get("trips-page");
  const community = harness.elements.get("community-page");

  await tripsNav.dispatch("click");
  assert.equal(explore.hidden, true);
  assert.equal(trips.hidden, false);
  assert.equal(community.hidden, true);
  assert.equal(tripsNav.getAttribute("aria-current"), "page");

  await communityNav.dispatch("click");
  assert.equal(trips.hidden, true);
  assert.equal(community.hidden, false);
  assert.equal(harness.elements.get("explore-nav-button").getAttribute("aria-current"), null);
});

test("user navigation focuses each programmatically focusable view heading", async () => {
  const harness = createHarness();
  await settle();

  const exploreTitle = harness.elements.get("explore-title");
  const tripsTitle = harness.elements.get("trips-page-title");
  const communityTitle = harness.elements.get("community-page-title");
  assert.equal(exploreTitle.focused, undefined, "initialization does not steal focus");
  for (const heading of [exploreTitle, tripsTitle, communityTitle]) {
    assert.equal(heading.getAttribute("tabindex"), "-1");
  }

  await harness.elements.get("trips-nav-button").dispatch("click");
  assert.equal(tripsTitle.focused, true);

  await harness.elements.get("community-nav-button").dispatch("click");
  assert.equal(communityTitle.focused, true);

  await harness.elements.get("explore-nav-button").dispatch("click");
  assert.equal(exploreTitle.focused, true);
});

test("signed-out Trips heading precedes its primary login action in DOM order", () => {
  const harness = createHarness();
  const heading = harness.elements.get("trips-page-title");
  const login = harness.elements.get("trips-login-button");

  assert.ok(harness.created.indexOf(heading) < harness.created.indexOf(login));
});

test("brand link returns to Explore through SPA navigation and focuses its heading", async () => {
  const harness = createHarness();
  await settle();

  await harness.elements.get("trips-nav-button").dispatch("click");
  await harness.elements.get("voyage-brand").dispatch("click");

  assert.equal(harness.elements.get("explore-page").hidden, false);
  assert.equal(harness.elements.get("trips-page").hidden, true);
  assert.equal(harness.elements.get("explore-nav-button").getAttribute("aria-current"), "page");
  assert.equal(harness.elements.get("explore-title").focused, true);
});

test("skip link targets a stable focusable main region", () => {
  const harness = createHarness();
  const skipLink = harness.elements.get("skip-link");
  const main = harness.elements.get("main-content");

  assert.ok(skipLink, "skip link has a stable id");
  assert.ok(main, "main content has a stable id");
  assert.equal(skipLink.getAttribute("href"), "#main-content");
  assert.equal(main.getAttribute("tabindex"), "-1");
});

test("Explore output is hidden with its view without losing generated state", async () => {
  const harness = createHarness();
  await settle();
  const output = harness.elements.get("explore-output");
  const profile = harness.elements.get("profile-confirmation");
  const trip = harness.elements.get("trip-view");
  assert.ok(output, "Explore has a managed output region");
  profile.hidden = false;
  trip.hidden = false;

  await harness.elements.get("trips-nav-button").dispatch("click");
  assert.equal(output.hidden, true);
  assert.equal(profile.hidden, false);
  assert.equal(trip.hidden, false);

  await harness.elements.get("explore-nav-button").dispatch("click");
  assert.equal(output.hidden, false);
  assert.equal(profile.hidden, false);
  assert.equal(trip.hidden, false);
});

test("provider notice is visible only while the Explore view is active", async () => {
  const harness = createHarness({ fetch: async (call) => call.url === "/api/chat"
    ? jsonResponse(200, { reply: "请继续", stage: "collecting", profile: {}, warnings: ["provider unavailable"] })
    : jsonResponse(200, {}) });
  await settle();
  harness.elements.get("message-input").value = "查看降级提示";
  await harness.elements.get("chat-form").dispatch("submit");

  const notice = harness.elements.get("provider-notice");
  assert.equal(notice.hidden, false);

  await harness.elements.get("trips-nav-button").dispatch("click");
  assert.equal(notice.hidden, true);

  await harness.elements.get("explore-nav-button").dispatch("click");
  assert.equal(notice.hidden, false);

  await harness.elements.get("community-nav-button").dispatch("click");
  assert.equal(notice.hidden, true);
});

test("opening a saved trip moves its generated content into the Explore view", async () => {
  const savedTrip = { id: "trip-open", title: "厦门周末", profile: {}, itinerary: { title: "厦门周末", days: [] } };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url === "/api/trips") return jsonResponse(200, [savedTrip]);
    if (call.url === "/api/trips/trip-open") return jsonResponse(200, savedTrip);
    return jsonResponse(200, {});
  } });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  await findByText(harness.elements.get("trip-history-list"), "打开").dispatch("click");

  assert.equal(harness.elements.get("trips-page").hidden, true);
  assert.equal(harness.elements.get("explore-page").hidden, false);
  assert.equal(harness.elements.get("explore-output").hidden, false);
  assert.equal(harness.elements.get("trip-view").hidden, false);
  assert.equal(harness.elements.get("explore-title").focused, true);
});

test("a saved-trip response that succeeds after sign-out cannot restore private content", async () => {
  const savedTrip = { id: "trip-open", title: "账户 A 私有行程", profile: {}, itinerary: { title: "账户 A 私有行程", days: [] } };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveOpen;
  const pendingOpen = new Promise((resolve) => { resolveOpen = resolve; });
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url === "/api/trips") return jsonResponse(200, [savedTrip]);
    if (call.url === "/api/trips/trip-open") return pendingOpen;
    return jsonResponse(200, {});
  } });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  const opening = findByText(harness.elements.get("trip-history-list"), "打开").dispatch("click");
  await settle(1);
  auth.emit("SIGNED_OUT", null);
  resolveOpen(jsonResponse(200, savedTrip));
  await opening;
  await settle();

  assert.equal(harness.document.body.dataset.appState, "signed_out");
  assert.equal(harness.elements.get("trips-page").hidden, false);
  assert.equal(harness.elements.get("explore-page").hidden, true);
  assert.equal(harness.elements.get("trip-view").hidden, true);
  assert.equal(harness.elements.get("trip-content").textContent, "");
  assert.equal(harness.elements.get("trip-title").textContent, "");
  assert.doesNotMatch(harness.elements.get("trip-history-list").textContent, /账户 A 私有行程/);
});

test("signed-out trips view shows a login prompt instead of requesting private trips", async () => {
  const harness = createHarness();
  await settle();

  await harness.elements.get("trips-nav-button").dispatch("click");

  assert.equal(harness.elements.get("trips-auth-prompt").hidden, false);
  assert.equal(harness.elements.get("trip-history").hidden, true);
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/trips"), false);
});

test("trips login prompt opens the account menu and focuses the email field", async () => {
  const harness = createHarness();
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  await harness.elements.get("trips-login-button").dispatch("click");

  assert.equal(harness.elements.get("account-menu").open, true);
  assert.equal(harness.elements.get("email").focused, true);
});

test("signed-in trips view renders the empty and populated states", async () => {
  const emptyAuth = new FakeSupabaseAuth({ initialSession: SESSION });
  const emptyHarness = createHarness({
    auth: emptyAuth,
    fetch: async (call) => call.url === "/api/trips" ? jsonResponse(200, []) : jsonResponse(200, {}),
  });
  await settle();
  await emptyHarness.elements.get("trips-nav-button").dispatch("click");

  assert.match(emptyHarness.elements.get("trip-history-list").textContent, /还没有保存的行程。/);

  const populatedAuth = new FakeSupabaseAuth({ initialSession: SESSION });
  const populatedHarness = createHarness({
    auth: populatedAuth,
    fetch: async (call) => call.url === "/api/trips"
      ? jsonResponse(200, [{ id: "trip-1", title: "厦门三日游", updated_at: "2026-08-14T00:00:00Z" }])
      : jsonResponse(200, {}),
  });
  await settle();
  await populatedHarness.elements.get("trips-nav-button").dispatch("click");

  assert.match(populatedHarness.elements.get("trip-history-list").textContent, /厦门三日游/);
  assert.equal(populatedHarness.elements.get("trips-auth-prompt").hidden, true);
});

test("trips request failure renders a retryable error", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let attempts = 0;
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/trips"
    ? (attempts += 1) === 1
      ? jsonResponse(503, { detail: { code: "REQUEST_FAILED" } })
      : jsonResponse(200, [{ id: "trip-2", title: "重试后的行程", updated_at: "2026-08-14T00:00:00Z" }])
    : jsonResponse(200, {}) });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  assert.match(harness.elements.get("trip-history-list").textContent, /行程加载失败，请重试。/);
  assert.ok(
    harness.elements.get("trip-history-list").children.every((child) => child.tagName === "LI"),
    "trip history lists contain only li children",
  );
  await findByText(harness.elements.get("trip-history-list"), "重试").dispatch("click");

  assert.equal(attempts, 2);
  assert.match(harness.elements.get("trip-history-list").textContent, /重试后的行程/);
});

test("newest trips response wins when requests complete out of order", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveOlder;
  let resolveNewer;
  const olderResponse = new Promise((resolve) => { resolveOlder = resolve; });
  const newerResponse = new Promise((resolve) => { resolveNewer = resolve; });
  let tripLoads = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/trips") return jsonResponse(200, {});
    tripLoads += 1;
    return tripLoads === 1 ? olderResponse : newerResponse;
  } });
  await settle();
  const tripsNav = harness.elements.get("trips-nav-button");

  const olderLoad = tripsNav.dispatch("click");
  await settle(1);
  const newerLoad = tripsNav.dispatch("click");
  await settle(1);
  resolveNewer(jsonResponse(200, [{ id: "new", title: "较新的行程" }]));
  await newerLoad;
  assert.match(harness.elements.get("trip-history-list").textContent, /较新的行程/);

  resolveOlder(jsonResponse(200, [{ id: "old", title: "过时的行程" }]));
  await olderLoad;
  assert.match(harness.elements.get("trip-history-list").textContent, /较新的行程/);
  assert.doesNotMatch(harness.elements.get("trip-history-list").textContent, /过时的行程/);
});

test("stale trips authentication failure cannot replace a newer response or sign out", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveOlder;
  let resolveNewer;
  const olderResponse = new Promise((resolve) => { resolveOlder = resolve; });
  const newerResponse = new Promise((resolve) => { resolveNewer = resolve; });
  let tripLoads = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/trips") return jsonResponse(200, {});
    tripLoads += 1;
    return tripLoads === 1 ? olderResponse : newerResponse;
  } });
  await settle();
  const tripsNav = harness.elements.get("trips-nav-button");

  const olderLoad = tripsNav.dispatch("click");
  await settle(1);
  const newerLoad = tripsNav.dispatch("click");
  await settle(1);
  resolveNewer(jsonResponse(200, [{ id: "new", title: "保留的新行程" }]));
  await newerLoad;

  resolveOlder(jsonResponse(401, { detail: { code: "AUTH_INVALID" } }));
  await olderLoad;
  assert.match(harness.elements.get("trip-history-list").textContent, /保留的新行程/);
  assert.doesNotMatch(harness.elements.get("trip-history-list").textContent, /加载失败/);
  assert.equal(harness.elements.get("account-summary").hidden, false);
  assert.equal(auth.refreshCalls, 0);
});

test("stale deferred Trips refresh cannot mutate the session or newer result", async (t) => {
  const scenarios = [
    { name: "obsolete refresh succeeds", result: { data: { session: REFRESHED }, error: null } },
    { name: "obsolete refresh fails", result: { data: { session: null }, error: new Error("refresh failed") } },
  ];

  for (const scenario of scenarios) {
    await t.test(scenario.name, async () => {
      const auth = new FakeSupabaseAuth({ initialSession: SESSION });
      let resolveRefresh;
      auth.refreshSession = async () => {
        auth.refreshCalls += 1;
        return new Promise((resolve) => { resolveRefresh = resolve; });
      };
      let tripLoads = 0;
      const harness = createHarness({ auth, fetch: async (call) => {
        if (call.url !== "/api/trips") return jsonResponse(200, {});
        tripLoads += 1;
        return tripLoads === 1
          ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } })
          : jsonResponse(200, [{ id: "current", title: "当前账户的新行程" }]);
      } });
      await settle();

      const staleLoad = harness.elements.get("trips-nav-button").dispatch("click");
      await settle(1);
      assert.equal(auth.refreshCalls, 1);
      assert.equal(typeof resolveRefresh, "function");

      auth.emit("TOKEN_REFRESHED", CURRENT_SESSION);
      await settle();
      assert.equal(tripLoads, 2);
      assert.match(harness.elements.get("trip-history-list").textContent, /当前账户的新行程/);

      resolveRefresh(scenario.result);
      await staleLoad;
      await settle();

      assert.equal(tripLoads, 2, "the stale request must not retry");
      assert.equal(auth.signOutCalls, 0);
      assert.equal(harness.elements.get("account-summary").hidden, false);
      assert.equal(harness.elements.get("account-email").textContent, "current@example.test");
      assert.match(harness.elements.get("trip-history-list").textContent, /当前账户的新行程/);
      assert.doesNotMatch(harness.elements.get("trip-history-list").textContent, /加载失败/);

      await harness.elements.get("trips-nav-button").dispatch("click");
      assert.equal(tripLoads, 3);
      assert.equal(harness.fetchCalls.at(-1).options.headers.Authorization, "Bearer access-current");
    });
  }
});

test("a newer account 401 starts its own refresh instead of joining the older account refresh", async () => {
  const refreshedOtherSession = {
    access_token: "access-other-refreshed", refresh_token: "refresh-other-refreshed", expires_at: 2000010800,
    user: { id: "user-b", email: "other@example.test" },
  };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const refreshResolvers = [];
  auth.refreshSession = async () => {
    auth.refreshCalls += 1;
    return new Promise((resolve) => { refreshResolvers.push(resolve); });
  };
  let tripLoads = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/trips") return jsonResponse(200, {});
    tripLoads += 1;
    if (tripLoads <= 2) return jsonResponse(401, { detail: { code: "AUTH_INVALID" } });
    return jsonResponse(200, [{ id: "trip-b", title: "账户 B 的行程" }]);
  } });
  await settle();

  const olderLoad = harness.elements.get("trips-nav-button").dispatch("click");
  await settle(1);
  assert.equal(auth.refreshCalls, 1);

  auth.session = OTHER_SESSION;
  auth.emit("SIGNED_IN", OTHER_SESSION);
  await settle(2);
  assert.equal(auth.refreshCalls, 2, "the newer generation must not join the older generation refresh");

  refreshResolvers[1]({ data: { session: refreshedOtherSession }, error: null });
  await settle();
  assert.match(harness.elements.get("trip-history-list").textContent, /账户 B 的行程/);
  assert.equal(harness.fetchCalls.at(-1).options.headers.Authorization, "Bearer access-other-refreshed");

  refreshResolvers[0]({ data: { session: REFRESHED }, error: null });
  await olderLoad;
  await settle();

  assert.equal(auth.signOutCalls, 0);
  assert.equal(harness.elements.get("account-email").textContent, "other@example.test");
  assert.match(harness.elements.get("trip-history-list").textContent, /账户 B 的行程/);
  assert.doesNotMatch(harness.elements.get("trip-history-list").textContent, /加载失败/);
});

test("sign-out invalidates a pending trips failure", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveTrips;
  const tripsResponse = new Promise((resolve) => { resolveTrips = resolve; });
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/trips"
    ? tripsResponse
    : jsonResponse(200, {}) });
  await settle();

  const pendingLoad = harness.elements.get("trips-nav-button").dispatch("click");
  await settle(1);
  auth.emit("SIGNED_OUT", null);
  await settle(1);
  const statusBeforeResponse = harness.elements.get("status-message").textContent;
  resolveTrips(jsonResponse(401, { detail: { code: "AUTH_INVALID" } }));
  await pendingLoad;

  assert.equal(harness.elements.get("trips-auth-prompt").hidden, false);
  assert.equal(harness.document.body.dataset.appState, "signed_out");
  assert.equal(harness.elements.get("status-message").textContent, statusBeforeResponse);
});

test("mouse drag handle moves the open assistant and clamps it within 12px viewport margins", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = {};
  panel.getBoundingClientRect = () => ({ left: 500, top: 300, width: 390, height: 400 });
  const capturedPointers = [];
  handle.setPointerCapture = (pointerId) => capturedPointers.push(pointerId);
  await harness.elements.get("assistant-toggle").dispatch("click");

  await dispatchPointer(handle, "pointerdown", { pointerId: 4, pointerType: "mouse", isPrimary: true, button: 0, clientX: 760, clientY: 420 });
  assert.deepEqual(capturedPointers, [4]);
  await dispatchPointer(handle, "pointermove", { pointerId: 4, pointerType: "mouse", clientX: -400, clientY: 2000 });
  await dispatchPointer(handle, "pointerup", { pointerId: 4, pointerType: "mouse", clientX: -400, clientY: 2000 });

  assert.equal(panel.style.left, "12px");
  assert.equal(panel.style.top, "288px");
  assert.equal(panel.hidden, false);
  assert.equal(harness.fetchCalls.length, 0);
});

test("touch drag handle positions the open assistant from its title bar", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = {};
  panel.getBoundingClientRect = () => ({ left: 400, top: 200, width: 390, height: 400 });
  handle.setPointerCapture = () => {};
  await harness.elements.get("assistant-toggle").dispatch("click");

  await dispatchPointer(handle, "pointerdown", { pointerId: 9, pointerType: "touch", clientX: 460, clientY: 260 });
  await dispatchPointer(handle, "pointermove", { pointerId: 9, pointerType: "touch", clientX: 500, clientY: 340 });

  assert.equal(panel.style.left, "440px");
  assert.equal(panel.style.top, "280px");
});

test("keyboard arrow keys move the open assistant by 40px and keep every edge within 12px", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = { left: "12px", top: "12px", right: "auto", bottom: "auto" };
  panel.getBoundingClientRect = () => ({ left: 12, top: 12, width: 390, height: 400 });
  await harness.elements.get("assistant-toggle").dispatch("click");

  assert.equal(handle.getAttribute("tabindex"), "0");
  assert.equal(await dispatchKey(handle, "ArrowLeft"), true);
  assert.equal(await dispatchKey(handle, "ArrowUp"), true);
  assert.equal(panel.style.left, "12px");
  assert.equal(panel.style.top, "12px");
  panel.style.left = "498px";
  panel.style.top = "288px";

  assert.equal(await dispatchKey(handle, "ArrowRight"), true);
  assert.equal(await dispatchKey(handle, "ArrowDown"), true);
  assert.equal(panel.style.left, "498px");
  assert.equal(panel.style.top, "288px");
  assert.equal(panel.hidden, false);
  assert.equal(harness.fetchCalls.length, 0);
});

test("an active assistant drag cannot be replaced by another primary pointer", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = {};
  panel.getBoundingClientRect = () => ({ left: 400, top: 200, width: 390, height: 400 });
  const capturedPointers = [];
  handle.setPointerCapture = (pointerId) => capturedPointers.push(pointerId);
  await harness.elements.get("assistant-toggle").dispatch("click");

  await dispatchPointer(handle, "pointerdown", { pointerId: 10, pointerType: "mouse", isPrimary: true, button: 0, clientX: 460, clientY: 260 });
  await dispatchPointer(handle, "pointerdown", { pointerId: 11, pointerType: "pen", isPrimary: true, button: 0, clientX: 480, clientY: 280 });
  await dispatchPointer(handle, "pointermove", { pointerId: 10, pointerType: "mouse", clientX: 500, clientY: 300 });

  assert.deepEqual(capturedPointers, [10]);
  assert.equal(panel.style.left, "440px");
  assert.equal(panel.style.top, "240px");
});

test("assistant drag keeps its coordinates visible when the panel is larger than the viewport", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 300;
  harness.window.innerHeight = 300;
  panel.style = {};
  panel.getBoundingClientRect = () => ({ left: 12, top: 12, width: 400, height: 400 });
  handle.setPointerCapture = () => {};
  await harness.elements.get("assistant-toggle").dispatch("click");

  await dispatchPointer(handle, "pointerdown", { pointerId: 5, pointerType: "mouse", isPrimary: true, button: 0, clientX: 30, clientY: 30 });
  await dispatchPointer(handle, "pointermove", { pointerId: 5, pointerType: "mouse", clientX: -100, clientY: -100 });

  assert.equal(panel.style.left, "12px");
  assert.equal(panel.style.top, "12px");
});

test("viewport resize pulls an open assistant back within 12px margins", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = { left: "498px", top: "288px", right: "auto", bottom: "auto" };
  panel.getBoundingClientRect = () => ({ left: 498, top: 288, width: 390, height: 400 });
  await harness.elements.get("assistant-toggle").dispatch("click");
  harness.window.innerWidth = 600;
  harness.window.innerHeight = 500;

  await harness.window.dispatch("resize");

  assert.equal(panel.style.left, "198px");
  assert.equal(panel.style.top, "88px");
});

test("orientation change pulls an open assistant back within 12px margins", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = { left: "498px", top: "288px", right: "auto", bottom: "auto" };
  panel.getBoundingClientRect = () => ({ left: 498, top: 288, width: 390, height: 400 });
  await harness.elements.get("assistant-toggle").dispatch("click");
  harness.window.innerWidth = 600;
  harness.window.innerHeight = 500;

  await harness.window.dispatch("orientationchange");

  assert.equal(panel.style.left, "198px");
  assert.equal(panel.style.top, "88px");
});

test("reopening the assistant re-clamps its saved inline position", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = { left: "498px", top: "288px", right: "auto", bottom: "auto" };
  panel.getBoundingClientRect = () => ({ left: 498, top: 288, width: 390, height: 400 });
  const toggle = harness.elements.get("assistant-toggle");
  await toggle.dispatch("click");
  await toggle.dispatch("click");
  harness.window.innerWidth = 600;
  harness.window.innerHeight = 500;

  await toggle.dispatch("click");

  assert.equal(panel.style.left, "198px");
  assert.equal(panel.style.top, "88px");
});

test("an accessible reset control restores the assistant's default position", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const reset = harness.elements.get("assistant-reset-position");
  panel.style = { left: "300px", top: "200px", right: "auto", bottom: "auto" };

  assert.ok(reset, "a keyboard-focusable reset button is present");
  assert.equal(reset.tagName, "BUTTON");
  assert.equal(reset.getAttribute("aria-label"), "重置 AI 助手位置");
  await reset.dispatch("click");

  assert.equal(panel.style.left, "");
  assert.equal(panel.style.top, "");
  assert.equal(panel.style.right, "");
  assert.equal(panel.style.bottom, "");
});

test("assistant drag ignores secondary mouse buttons and non-primary pointers", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = {};
  panel.getBoundingClientRect = () => ({ left: 400, top: 200, width: 390, height: 400 });
  const capturedPointers = [];
  handle.setPointerCapture = (pointerId) => capturedPointers.push(pointerId);
  await harness.elements.get("assistant-toggle").dispatch("click");

  await dispatchPointer(handle, "pointerdown", { pointerId: 6, pointerType: "mouse", isPrimary: true, button: 2, clientX: 460, clientY: 260 });
  await dispatchPointer(handle, "pointermove", { pointerId: 6, pointerType: "mouse", clientX: 500, clientY: 300 });
  await dispatchPointer(handle, "pointerdown", { pointerId: 7, pointerType: "touch", isPrimary: false, clientX: 460, clientY: 260 });
  await dispatchPointer(handle, "pointermove", { pointerId: 7, pointerType: "touch", clientX: 500, clientY: 300 });

  assert.deepEqual(capturedPointers, []);
  assert.equal(panel.style.left, undefined);
  assert.equal(panel.style.top, undefined);
});

test("lost pointer capture stops later assistant movement", async () => {
  const harness = createHarness();
  await settle();

  const panel = harness.elements.get("assistant-panel");
  const handle = harness.elements.get("assistant-drag-handle");
  harness.window.innerWidth = 900;
  harness.window.innerHeight = 700;
  panel.style = {};
  panel.getBoundingClientRect = () => ({ left: 400, top: 200, width: 390, height: 400 });
  handle.setPointerCapture = () => {};
  await harness.elements.get("assistant-toggle").dispatch("click");

  await dispatchPointer(handle, "pointerdown", { pointerId: 8, pointerType: "mouse", isPrimary: true, button: 0, clientX: 460, clientY: 260 });
  await dispatchPointer(handle, "pointermove", { pointerId: 8, pointerType: "mouse", clientX: 500, clientY: 300 });
  await dispatchPointer(handle, "lostpointercapture", { pointerId: 8 });
  await dispatchPointer(handle, "pointermove", { pointerId: 8, pointerType: "mouse", clientX: 600, clientY: 400 });

  assert.equal(panel.style.left, "440px");
  assert.equal(panel.style.top, "240px");
});

test("assistant toggle opens and closes the panel without a dedicated close button", async () => {
  const harness = createHarness();
  await settle();

  const assistant = harness.elements.get("assistant-panel");
  const toggle = harness.elements.get("assistant-toggle");
  const toggleLabel = harness.elements.get("assistant-toggle-label");
  assistant.style = {};
  assert.ok(assistant, "assistant panel is present");
  assert.ok(toggle, "assistant launcher is present");
  assert.ok(toggleLabel, "assistant launcher exposes a visible action label");
  assert.equal(harness.elements.get("assistant-close"), undefined);
  assert.equal(assistant.hidden, true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(toggle.getAttribute("aria-label"), "打开 AI 助手");
  assert.equal(toggleLabel.textContent, "打开 AI 助手");

  await toggle.dispatch("click");
  assert.equal(assistant.hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(toggle.getAttribute("aria-label"), "关闭 AI 助手");
  assert.equal(toggleLabel.textContent, "关闭 AI 助手");

  await toggle.dispatch("click");
  assert.equal(assistant.hidden, true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(toggle.getAttribute("aria-label"), "打开 AI 助手");
  assert.equal(toggleLabel.textContent, "打开 AI 助手");
});

test("assistant launcher stays above a dragged panel so the same control can close it", () => {
  const root = path.resolve(__dirname, "..", "..");
  const css = fs.readFileSync(path.join(root, "app", "static", "styles.css"), "utf8");
  const zIndexFor = (selector) => {
    const rule = css.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
    assert.ok(rule, `${selector} rule is present`);
    const zIndex = rule[1].match(/z-index:\s*(\d+)/);
    assert.ok(zIndex, `${selector} declares a z-index`);
    return Number(zIndex[1]);
  };

  assert.ok(zIndexFor(".assistant-toggle") > zIndexFor(".assistant-panel"));
});

test("Escape closes the assistant and restores launcher focus", async () => {
  const harness = createHarness();
  await settle();

  const assistant = harness.elements.get("assistant-panel");
  const toggle = harness.elements.get("assistant-toggle");
  assistant.style = {};
  await toggle.dispatch("click");

  await harness.window.dispatch("keydown", { key: "Escape" });
  assert.equal(assistant.hidden, true);
  assert.equal(toggle.focused, true);
});

test("page shell exposes Chinese navigation while the assistant stays initially closed", async () => {
  const harness = createHarness();
  await settle();

  const navigation = harness.elements.get("main-navigation");
  assert.ok(navigation, "main navigation is present");
  assert.equal(navigation.hidden, false);
  assert.equal(harness.elements.get("trip-view").hidden, true);
  assert.equal(harness.elements.get("assistant-panel").hidden, true);
});

test("real map explorer wiring renders data-driven cards and a selected place without a chat request", async () => {
  const harness = createHarness();
  await harness.settle();

  assert.ok(harness.elements.get("explore-page"), "探索页存在");
  assert.ok(harness.elements.get("explore-map"), "地图容器存在");
  assert.ok(harness.elements.get("explore-recommendations"), "推荐区存在");
  const map = harness.elements.get("explore-map");
  await findByText(map, "福建").dispatch("click");
  assert.match(harness.elements.get("recommendations-title").textContent, /福建/);
  assert.match(harness.elements.get("recommendation-grid").textContent, /厦门/);

  await findByText(map, "厦门").dispatch("click");
  assert.match(harness.elements.get("recommendations-title").textContent, /厦门/);
  assert.match(harness.elements.get("recommendation-grid").textContent, /鼓浪屿/);

  await findByText(map, "鼓浪屿").dispatch("click");

  assert.equal(harness.elements.get("assistant-panel").hidden, true);
  assert.match(harness.elements.get("chat-messages").textContent, /鼓浪屿安排半天步行/);
  assert.equal(harness.elements.get("explore-place-card").hidden, false);
  assert.match(harness.elements.get("explore-place-card").textContent, /鼓浪屿/);
  assert.match(harness.elements.get("explore-place-card").textContent, /万国建筑/);
  assert.match(harness.elements.get("explore-place-card").textContent, /鼓浪屿安排半天步行/);
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/chat"), false);
});

test("selecting Xiamen fetches one weather card without opening the assistant or calling chat", async () => {
  const harness = createHarness({ fetch: async (call) => {
    if (call.url === "/api/weather/cities/xiamen") {
      return jsonResponse(200, { city: "厦门", status: "available", summary: "晴 26℃", report_time: "2026-08-13T09:00:00+08:00" });
    }
    throw new Error(`unexpected ${call.url}`);
  } });
  await harness.settle();

  await findByText(harness.elements.get("explore-map"), "福建").dispatch("click");
  await findByText(harness.elements.get("explore-map"), "厦门").dispatch("click");
  await harness.settle();

  const weatherCalls = harness.fetchCalls.filter((call) => call.url === "/api/weather/cities/xiamen");
  assert.equal(weatherCalls.length, 1);
  assert.equal(harness.elements.get("assistant-panel").hidden, true);
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/chat"), false);
  assert.equal(
    descendants(harness.document.body).find((node) => node.className === "city-weather-card").textContent,
    "厦门：实时天气；晴 26℃；报告时间：2026-08-13 09:00:00 GMT+8",
  );
});

test("repeated city selections share one in-flight weather request", async () => {
  let releaseWeather;
  const weatherPending = new Promise((resolve) => { releaseWeather = resolve; });
  const harness = createHarness({ fetch: async (call) => {
    if (call.url === "/api/weather/cities/xiamen") return weatherPending;
    throw new Error(`unexpected ${call.url}`);
  } });
  await harness.settle();

  await findByText(harness.elements.get("explore-map"), "福建").dispatch("click");
  const xiamen = findByText(harness.elements.get("explore-map"), "厦门");
  await Promise.all([xiamen.dispatch("click"), xiamen.dispatch("click")]);
  assert.equal(harness.fetchCalls.filter((call) => call.url === "/api/weather/cities/xiamen").length, 1);

  releaseWeather(jsonResponse(200, { city: "厦门", status: "available", summary: "晴 26℃" }));
  await harness.settle();
});

test("weather endpoint failure shows unavailable copy while the city map stays usable", async () => {
  const harness = createHarness({ fetch: async (call) => {
    if (call.url === "/api/weather/cities/xiamen") throw new Error("offline");
    throw new Error(`unexpected ${call.url}`);
  } });
  await harness.settle();

  await findByText(harness.elements.get("explore-map"), "福建").dispatch("click");
  await findByText(harness.elements.get("explore-map"), "厦门").dispatch("click");
  await harness.settle();

  assert.match(descendants(harness.document.body).find((node) => node.className === "city-weather-card").textContent, /天气暂不可用/);
  assert.equal(harness.elements.get("explore-map").dataset.mapLevel, "city");
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/chat"), false);
});

test("itinerary renders only its structured daily weather with Chinese labels", async () => {
  const itinerary = {
    title: "厦门两日行程",
    days: [{
      date: "2026-08-13",
      weather: { status: "available", summary: "晴 26℃", report_time: "2026-08-13T09:00:00+08:00" },
      morning: { title: "鼓浪屿", start_time: "09:00", end_time: "11:00", citations: [] },
    }],
    citations: [],
  };
  const harness = createHarness({ hash: "#share=opaque", fetch: async () => jsonResponse(200, {
    id: "trip-1", title: itinerary.title, status: "planned", profile: {}, itinerary, updated_at: null,
  }) });
  await harness.settle();

  const weather = descendants(harness.document.body).find((node) => node.className === "itinerary-weather");
  assert.equal(weather.textContent, "天气类型：实时天气；晴 26℃；报告时间：2026-08-13 09:00:00 GMT+8");
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/chat"), false);
});

test("seasonal itinerary weather is explicitly marked non-live without a report time", async () => {
  const itinerary = {
    title: "厦门四日行程",
    days: [{
      date: "2026-08-16",
      weather: { status: "seasonal", summary: "非实时天气：夏秋季留意台风。", report_time: null },
      morning: { title: "城市漫步", start_time: "09:00", end_time: "11:00", citations: [] },
    }],
    citations: [],
  };
  const harness = createHarness({ hash: "#share=opaque", fetch: async () => jsonResponse(200, {
    id: "trip-1", title: itinerary.title, status: "planned", profile: {}, itinerary, updated_at: null,
  }) });
  await harness.settle();

  const weather = descendants(harness.document.body).find((node) => node.className === "itinerary-weather");
  assert.equal(weather.textContent, "天气类型：非实时天气；非实时天气：夏秋季留意台风。；报告时间：无实时报告");
});

test("real map navigation clears a selected place card before province and city context changes", async () => {
  const harness = createHarness();
  await harness.settle();
  const map = harness.elements.get("explore-map");
  const card = harness.elements.get("explore-place-card");

  await findByText(map, "福建").dispatch("click");
  await findByText(map, "厦门").dispatch("click");
  await findByText(map, "鼓浪屿").dispatch("click");
  assert.equal(card.hidden, false);
  assert.match(card.textContent, /鼓浪屿/);

  await findByText(map, "返回省份").dispatch("click");
  assert.equal(card.hidden, true);
  assert.equal(card.textContent, "");

  await findByText(map, "福州").dispatch("click");
  assert.equal(card.hidden, true);
  assert.equal(card.textContent, "");
});

test("public shared itinerary hides the assistant launcher", async () => {
  const harness = createHarness({ hash: "#share=opaque", fetch: async () => jsonResponse(200, {
    id: "trip-1", title: "共享行程", status: "planned", profile: {}, itinerary: { title: "共享行程", days: [] }, updated_at: null,
  }) });

  await settle();

  assert.equal(harness.elements.get("assistant-toggle").hidden, true);
  assert.equal(harness.elements.get("assistant-panel").hidden, true);
});

test("login uses the Supabase session lifecycle and starts a fresh authenticated conversation", async () => {
  const chatCalls = [];
  const auth = new FakeSupabaseAuth({ loginSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url === "/api/trips") return jsonResponse(200, []);
    if (call.url === "/api/chat") {
      chatCalls.push(JSON.parse(call.options.body));
      return jsonResponse(200, { reply: "请继续", stage: "collecting", profile: { origin: "上海" } });
    }
    throw new Error(`unexpected ${call.url}`);
  } });
  await settle();
  harness.elements.get("message-input").value = "匿名资料";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();
  harness.elements.get("email").value = "owner@example.test";
  harness.elements.get("password").value = "password1";
  await harness.elements.get("auth-form").dispatch("submit");
  await settle();
  harness.elements.get("message-input").value = "登录资料";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();

  assert.equal(harness.window.supabaseCreate.clientOptions.auth.persistSession, true);
  assert.equal(harness.window.supabaseCreate.clientOptions.auth.autoRefreshToken, true);
  assert.equal(harness.window.supabaseCreate.clientOptions.auth.detectSessionInUrl, true);
  assert.equal(chatCalls.length, 2);
  assert.notEqual(chatCalls[0].thread_id, chatCalls[1].thread_id);
  assert.match(harness.elements.get("status-message").textContent, /已切换登录会话|资料/);
  const authenticatedCall = harness.fetchCalls.filter((call) => call.url === "/api/chat")[1];
  assert.equal(authenticatedCall.options.headers.Authorization, "Bearer access-one");
});

test("SIGNED_IN for a different stable user clears account A state before rendering account B", async () => {
  const events = [];
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/trips"
    ? jsonResponse(200, [{ id: "trip-a", title: "账户 A 历史" }])
    : jsonResponse(200, {}) });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  const tripContent = harness.elements.get("trip-content");
  const profileFields = harness.elements.get("profile-fields");
  const messages = harness.elements.get("chat-messages");
  const accountEmail = harness.elements.get("account-email");
  tripContent.append(Object.assign(new FakeElement("p"), { textContent: "账户 A 私有行程" }));
  profileFields.append(Object.assign(new FakeElement("dd"), { textContent: "账户 A 私有资料" }));
  messages.append(Object.assign(new FakeElement("p"), { textContent: "账户 A 私有对话" }));
  observeChildClear(tripContent, "trip-clear", events);
  let accountEmailText = accountEmail.textContent;
  Object.defineProperty(accountEmail, "textContent", {
    configurable: true,
    get() { return accountEmailText; },
    set(next) {
      accountEmailText = String(next);
      if (next === OTHER_SESSION.user.email) events.push("account-b-render");
    },
  });

  auth.session = OTHER_SESSION;
  auth.emit("SIGNED_IN", OTHER_SESSION);

  assertBefore(events, "trip-clear", "account-b-render");
  assert.equal(accountEmail.textContent, "other@example.test");
  assert.equal(tripContent.textContent, "");
  assert.equal(profileFields.textContent, "");
  assert.doesNotMatch(messages.textContent, /账户 A 私有对话/);
  assert.doesNotMatch(harness.elements.get("trip-history-list").textContent, /账户 A 历史/);
  assert.equal(harness.elements.get("trip-view").hidden, true);
  assert.equal(harness.elements.get("profile-confirmation").hidden, true);
  assert.equal(harness.elements.get("share-dialog").open, false);
  assert.equal(harness.elements.get("rename-dialog").open, false);
  assert.equal(harness.elements.get("account-summary").hidden, false);
});

test("logout clears every private value and private DOM region", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/trips" ? jsonResponse(200, []) : jsonResponse(200, {}) });
  await settle();
  harness.elements.get("profile-fields").append(Object.assign(new FakeElement("dd"), { textContent: "private profile" }));
  harness.elements.get("trip-content").append(Object.assign(new FakeElement("p"), { textContent: "private trip" }));
  harness.elements.get("trip-history-list").append(Object.assign(new FakeElement("li"), { textContent: "private history" }));
  harness.elements.get("chat-messages").append(Object.assign(new FakeElement("p"), { textContent: "private chat" }));
  harness.elements.get("trip-view").hidden = false;
  harness.elements.get("profile-confirmation").hidden = false;
  harness.elements.get("email").value = "owner@example.test";
  harness.elements.get("password").value = "private password";
  harness.elements.get("share-link").value = "https://travel.example/#share=secret";
  harness.elements.get("share-expiry").textContent = "private expiry";
  harness.elements.get("rename-input").value = "private title";
  harness.elements.get("message-input").value = "private draft";
  harness.elements.get("trip-title").textContent = "private trip title";
  harness.elements.get("share-dialog").showModal();
  harness.elements.get("rename-dialog").showModal();

  await harness.elements.get("sign-out-button").dispatch("click");
  await settle();

  assert.equal(auth.signOutCalls, 1);
  assert.equal(harness.elements.get("trip-content").textContent, "");
  assert.equal(harness.elements.get("profile-fields").textContent, "");
  assert.equal(harness.elements.get("trip-history-list").textContent, "");
  assert.equal(harness.elements.get("chat-messages").textContent, "");
  assert.equal(harness.elements.get("trip-title").textContent, "");
  assert.equal(harness.elements.get("trip-view").hidden, true);
  assert.equal(harness.elements.get("profile-confirmation").hidden, true);
  assert.equal(harness.elements.get("trip-history").hidden, true);
  assert.equal(harness.elements.get("share-link").value, "");
  assert.equal(harness.elements.get("share-expiry").textContent, "");
  assert.equal(harness.elements.get("email").value, "");
  assert.equal(harness.elements.get("password").value, "");
  assert.equal(harness.elements.get("rename-input").value, "");
  assert.equal(harness.elements.get("message-input").value, "");
  assert.equal(harness.elements.get("account-email").textContent, "");
  assert.equal(harness.elements.get("share-dialog").open, false);
  assert.equal(harness.elements.get("rename-dialog").open, false);
  assert.equal(harness.elements.get("account-summary").hidden, true);
  assert.equal(harness.elements.get("auth-form").hidden, false);
});

test("logout clears private nodes before hiding each containing region", async () => {
  const events = [];
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/trips" ? jsonResponse(200, []) : jsonResponse(200, {}) });
  await settle();
  const historyList = harness.elements.get("trip-history-list");
  const history = harness.elements.get("trip-history");
  const tripContent = harness.elements.get("trip-content");
  const tripView = harness.elements.get("trip-view");
  const profileFields = harness.elements.get("profile-fields");
  const profileCard = harness.elements.get("profile-confirmation");
  const notice = harness.elements.get("provider-notice");
  const updatedAt = harness.elements.get("provider-updated-at");
  historyList.append(new FakeElement("li"));
  tripContent.append(new FakeElement("p"));
  profileFields.append(new FakeElement("dd"));
  notice.append(new FakeElement("span"));
  updatedAt.textContent = "private provider time";
  history.hidden = false;
  tripView.hidden = false;
  profileCard.hidden = false;
  notice.hidden = false;
  observeChildClear(historyList, "history-clear", events);
  observeHidden(history, "history-hide", events);
  observeChildClear(tripContent, "trip-clear", events);
  observeHidden(tripView, "trip-hide", events);
  observeChildClear(profileFields, "profile-clear", events);
  observeHidden(profileCard, "profile-hide", events);
  observeEmptyText(updatedAt, "provider-time-clear", events);
  observeChildClear(notice, "provider-notice-clear", events);
  observeHidden(notice, "provider-notice-hide", events);

  await harness.elements.get("sign-out-button").dispatch("click");
  await settle();

  assertBefore(events, "history-clear", "history-hide");
  assertBefore(events, "trip-clear", "trip-hide");
  assertBefore(events, "profile-clear", "profile-hide");
  assertBefore(events, "provider-time-clear", "provider-notice-hide");
  assertBefore(events, "provider-notice-clear", "provider-notice-hide");
});

test("a private API 401 refreshes once and retries with the new Authorization token", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION, refreshedSession: REFRESHED });
  let privateCalls = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/trips") throw new Error(`unexpected ${call.url}`);
    privateCalls += 1;
    return privateCalls === 1 ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } }) : jsonResponse(200, []);
  } });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  assert.equal(auth.refreshCalls, 1);
  assert.equal(privateCalls, 2);
  assert.equal(harness.fetchCalls[0].options.headers.Authorization, "Bearer access-one");
  assert.equal(harness.fetchCalls[1].options.headers.Authorization, "Bearer access-two");
  assert.equal(harness.elements.get("account-summary").hidden, false);
});

test("the token event emitted by the current refresh still allows its chat retry", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  auth.refreshSession = async () => {
    auth.refreshCalls += 1;
    auth.session = REFRESHED;
    auth.emit("TOKEN_REFRESHED", REFRESHED);
    return { data: { session: REFRESHED }, error: null };
  };
  let chatCalls = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/chat") return jsonResponse(200, {});
    chatCalls += 1;
    return chatCalls === 1
      ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } })
      : jsonResponse(200, { reply: "刷新后继续", stage: "collecting", profile: {} });
  } });
  await settle();

  harness.elements.get("message-input").value = "需要刷新";
  await harness.elements.get("chat-form").dispatch("submit");

  assert.equal(auth.refreshCalls, 1);
  assert.equal(chatCalls, 2);
  assert.equal(harness.fetchCalls.at(-1).options.headers.Authorization, "Bearer access-two");
  assert.match(harness.elements.get("chat-messages").textContent, /刷新后继续/);
});

test("a matching token event arriving during the current refresh retry keeps that retry current", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  auth.refreshSession = async () => {
    auth.refreshCalls += 1;
    auth.session = REFRESHED;
    setImmediate(() => auth.emit("TOKEN_REFRESHED", REFRESHED));
    return { data: { session: REFRESHED }, error: null };
  };
  let chatCalls = 0;
  let resolveRetry;
  const retryResponse = new Promise((resolve) => { resolveRetry = resolve; });
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/chat") return jsonResponse(200, {});
    chatCalls += 1;
    return chatCalls === 1
      ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } })
      : retryResponse;
  } });
  await settle();

  harness.elements.get("message-input").value = "等待刷新回调";
  const chatRequest = harness.elements.get("chat-form").dispatch("submit");
  await settle(2);
  resolveRetry(jsonResponse(200, { reply: "当前刷新回复", stage: "collecting", profile: {} }));
  await chatRequest;

  assert.equal(auth.refreshCalls, 1);
  assert.equal(chatCalls, 2);
  assert.equal(auth.signOutCalls, 0);
  assert.match(harness.elements.get("chat-messages").textContent, /当前刷新回复/);
});

test("same-session authenticated callers share one refresh and both retry", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveRefresh;
  auth.refreshSession = async () => {
    auth.refreshCalls += 1;
    return new Promise((resolve) => { resolveRefresh = resolve; });
  };
  let chatCalls = 0;
  let tripCalls = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url === "/api/chat") {
      chatCalls += 1;
      return chatCalls === 1
        ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } })
        : jsonResponse(200, { reply: "刷新后的回复", stage: "collecting", profile: {} });
    }
    if (call.url === "/api/trips") {
      tripCalls += 1;
      return tripCalls === 1
        ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } })
        : jsonResponse(200, []);
    }
    return jsonResponse(200, {});
  } });
  await settle();

  const tripsLoad = harness.elements.get("trips-nav-button").dispatch("click");
  await settle(1);
  harness.elements.get("message-input").value = "并发请求";
  const chatRequest = harness.elements.get("chat-form").dispatch("submit");
  await settle(1);

  assert.equal(auth.refreshCalls, 1);
  resolveRefresh({ data: { session: REFRESHED }, error: null });
  await Promise.all([tripsLoad, chatRequest]);

  assert.equal(auth.refreshCalls, 1);
  assert.equal(tripCalls, 2);
  assert.equal(chatCalls, 2);
  assert.equal(harness.fetchCalls.filter((call) => call.url === "/api/trips").at(-1).options.headers.Authorization, "Bearer access-two");
  assert.equal(harness.fetchCalls.filter((call) => call.url === "/api/chat").at(-1).options.headers.Authorization, "Bearer access-two");
});

test("a newer same-user token event invalidates a stale chat refresh success", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveRefresh;
  auth.refreshSession = async () => {
    auth.refreshCalls += 1;
    return new Promise((resolve) => { resolveRefresh = resolve; });
  };
  let chatCalls = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/chat") return jsonResponse(200, {});
    chatCalls += 1;
    if (chatCalls === 1) return jsonResponse(401, { detail: { code: "AUTH_INVALID" } });
    const current = call.options.headers.Authorization === "Bearer access-current";
    return jsonResponse(200, { reply: current ? "当前会话回复" : "过时刷新回复", stage: "collecting", profile: {} });
  } });
  await settle();

  harness.elements.get("message-input").value = "触发旧刷新";
  const staleChat = harness.elements.get("chat-form").dispatch("submit");
  await settle(1);
  assert.equal(auth.refreshCalls, 1);

  auth.session = CURRENT_SESSION;
  auth.emit("TOKEN_REFRESHED", CURRENT_SESSION);
  resolveRefresh({ data: { session: REFRESHED }, error: null });
  await staleChat;
  await settle();

  assert.equal(chatCalls, 1, "the stale request must not retry");
  assert.equal(auth.signOutCalls, 0);
  assert.equal(harness.elements.get("account-email").textContent, "current@example.test");
  assert.doesNotMatch(harness.elements.get("chat-messages").textContent, /过时刷新回复/);

  harness.elements.get("message-input").value = "使用当前令牌";
  await harness.elements.get("chat-form").dispatch("submit");
  assert.equal(harness.fetchCalls.at(-1).options.headers.Authorization, "Bearer access-current");
  assert.match(harness.elements.get("chat-messages").textContent, /当前会话回复/);
});

test("a newer same-user token event invalidates a stale chat refresh failure", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveRefresh;
  auth.refreshSession = async () => {
    auth.refreshCalls += 1;
    return new Promise((resolve) => { resolveRefresh = resolve; });
  };
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/chat"
    ? jsonResponse(401, { detail: { code: "AUTH_INVALID" } })
    : jsonResponse(200, {}) });
  await settle();

  harness.elements.get("message-input").value = "触发失败刷新";
  const staleChat = harness.elements.get("chat-form").dispatch("submit");
  await settle(1);
  assert.equal(auth.refreshCalls, 1);

  auth.session = CURRENT_SESSION;
  auth.emit("TOKEN_REFRESHED", CURRENT_SESSION);
  resolveRefresh({ data: { session: null }, error: new Error("refresh failed") });
  await staleChat;
  await settle();

  assert.equal(auth.signOutCalls, 0);
  assert.equal(harness.elements.get("account-summary").hidden, false);
  assert.equal(harness.elements.get("auth-form").hidden, true);
  assert.equal(harness.elements.get("account-email").textContent, "current@example.test");
  assert.equal(harness.document.body.dataset.appState, "collecting");
});

test("a newer same-user token event blocks an older chat success consumer", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolveOlderChat;
  const olderChat = new Promise((resolve) => { resolveOlderChat = resolve; });
  let chatCalls = 0;
  const harness = createHarness({ auth, fetch: async (call) => {
    if (call.url !== "/api/chat") return jsonResponse(200, {});
    chatCalls += 1;
    return chatCalls === 1
      ? olderChat
      : jsonResponse(200, { reply: "当前会话回复", stage: "collecting", profile: {} });
  } });
  await settle();

  harness.elements.get("message-input").value = "等待旧回复";
  const staleChat = harness.elements.get("chat-form").dispatch("submit");
  await settle(1);

  auth.session = CURRENT_SESSION;
  auth.emit("TOKEN_REFRESHED", CURRENT_SESSION);
  resolveOlderChat(jsonResponse(200, { reply: "过时会话回复", stage: "collecting", profile: {} }));
  await staleChat;

  assert.doesNotMatch(harness.elements.get("chat-messages").textContent, /过时会话回复/);
  assert.equal(harness.elements.get("account-email").textContent, "current@example.test");

  harness.elements.get("message-input").value = "读取当前回复";
  await harness.elements.get("chat-form").dispatch("submit");
  assert.equal(harness.fetchCalls.at(-1).options.headers.Authorization, "Bearer access-current");
  assert.match(harness.elements.get("chat-messages").textContent, /当前会话回复/);
});

test("a retry that is still 401 signs out and clears the restored session", async () => {
  const successfulAuth = new FakeSupabaseAuth({ initialSession: SESSION, refreshedSession: REFRESHED });
  const signedOutAfterRetry = createHarness({ auth: successfulAuth, fetch: async () => jsonResponse(401, { detail: { code: "AUTH_INVALID" } }) });
  await settle();
  await signedOutAfterRetry.elements.get("trips-nav-button").dispatch("click");
  assert.equal(successfulAuth.refreshCalls, 1);
  assert.equal(successfulAuth.signOutCalls, 1);
  assert.equal(signedOutAfterRetry.fetchCalls.filter((call) => call.url === "/api/trips").length, 2);
  assert.equal(signedOutAfterRetry.elements.get("account-summary").hidden, true);
  assert.match(signedOutAfterRetry.elements.get("status-message").textContent, /登录已过期/);
});

test("refresh failure signs out and clears the restored session", async () => {
  const failedAuth = new FakeSupabaseAuth({ initialSession: SESSION, refreshedSession: null });
  const signedOut = createHarness({ auth: failedAuth, fetch: async () => jsonResponse(401, { detail: { code: "AUTH_INVALID" } }) });
  await settle();
  await signedOut.elements.get("trips-nav-button").dispatch("click");
  assert.equal(failedAuth.refreshCalls, 1);
  assert.equal(failedAuth.signOutCalls, 1);
  assert.equal(signedOut.elements.get("account-summary").hidden, true);
  assert.equal(signedOut.elements.get("auth-form").hidden, false);
});

test("refresh exception signs out and clears the restored session", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  auth.refreshSession = async () => { auth.refreshCalls += 1; throw new Error("sdk refresh exploded"); };
  const harness = createHarness({ auth, fetch: async () => jsonResponse(401, { detail: { code: "AUTH_INVALID" } }) });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");
  assert.equal(auth.refreshCalls, 1);
  assert.equal(auth.signOutCalls, 1);
  assert.equal(harness.elements.get("account-summary").hidden, true);
  assert.match(harness.elements.get("status-message").textContent, /登录已过期/);
});

test("non-401 API failures never refresh or sign out", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION, refreshedSession: REFRESHED });
  const harness = createHarness({ auth, fetch: async () => jsonResponse(503, { detail: { code: "CHAT_UNAVAILABLE" } }) });
  await settle();
  assert.equal(auth.refreshCalls, 0);
  assert.equal(auth.signOutCalls, 0);
  assert.equal(harness.elements.get("account-summary").hidden, false);
});

test("Supabase auth state changes replace the token used by later private calls", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => call.url === "/api/trips" ? jsonResponse(200, []) : jsonResponse(200, { reply: "继续", stage: "collecting", profile: {} }) });
  await settle();
  harness.elements.get("trip-content").append(Object.assign(new FakeElement("p"), { textContent: "same-user private trip" }));
  harness.elements.get("chat-messages").append(Object.assign(new FakeElement("p"), { textContent: "same-user private chat" }));

  auth.emit("TOKEN_REFRESHED", REFRESHED);

  assert.match(harness.elements.get("trip-content").textContent, /same-user private trip/);
  assert.match(harness.elements.get("chat-messages").textContent, /same-user private chat/);
  harness.elements.get("message-input").value = "刷新后请求";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();
  const chatCall = harness.fetchCalls.find((call) => call.url === "/api/chat");
  assert.equal(chatCall.options.headers.Authorization, "Bearer access-two");
});

test("busy state prevents duplicate fetches and disables static and dynamic actions", async () => {
  let release;
  let chatCalls = 0;
  const pending = new Promise((resolve) => { release = resolve; });
  const harness = createHarness({ fetch: async (call) => {
    if (call.url !== "/api/chat") return jsonResponse(200, []);
    chatCalls += 1;
    await pending;
    return jsonResponse(200, { reply: "继续", stage: "collecting", profile: {} });
  } });
  await settle();
  const dynamicAction = harness.document.createElement("button");
  dynamicAction.textContent = "动态删除";
  harness.elements.get("trip-history-list").append(dynamicAction);
  harness.elements.get("message-input").value = "第一次";
  const first = harness.elements.get("chat-form").dispatch("submit");
  await settle(1);
  harness.elements.get("message-input").value = "第二次";
  const second = harness.elements.get("chat-form").dispatch("submit");

  assert.equal(chatCalls, 1);
  assert.equal(dynamicAction.disabled, true);
  assert.equal(harness.elements.get("close-share-dialog").disabled, true);
  release();
  await Promise.all([first, second]);
  await settle();
  assert.equal(dynamicAction.disabled, false);
  assert.equal(harness.elements.get("message-input").disabled, false);
});

test("planning starts only after an explicit confirmation and never prints itinerary JSON in chat", async () => {
  const profile = {
    origin: "上海", destination: "成都", start_date: "2026-10-01", end_date: "2026-10-02",
    travelers: 2, budget_cny: 5000, preferences: [], constraints: [],
  };
  const itinerary = {
    title: "成都两日行程",
    days: [{ date: "2026-10-01", morning: { title: "人民公园", start_time: "09:00", end_time: "11:00", citations: [] } }],
    citations: [],
  };
  const chatBodies = [];
  const harness = createHarness({ fetch: async (call) => {
    if (call.url !== "/api/chat") throw new Error(`unexpected ${call.url}`);
    const body = JSON.parse(call.options.body);
    chatBodies.push(body);
    if (body.action === "collect") {
      return jsonResponse(200, { reply: "资料已完整，请确认。", stage: "confirming", profile });
    }
    if (body.action === "confirm") {
      return jsonResponse(200, {
        reply: JSON.stringify(itinerary), stage: "planned", profile, itinerary,
        trip_id: "trip-confirmed", warnings: [],
      });
    }
    throw new Error(`unexpected chat action ${body.action}`);
  } });
  await settle();

  harness.elements.get("message-input").value = "上海到成都，两人，10月1日至2日，预算5000元";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();

  assert.equal(chatBodies.length, 1);
  assert.equal(chatBodies[0].action, "collect");
  assert.equal(harness.elements.get("profile-confirmation").hidden, false);
  assert.equal(harness.elements.get("confirm-profile-button").focused, true);
  assert.doesNotMatch(harness.elements.get("chat-messages").textContent, /成都两日行程|\"days\"/);
  assert.equal(harness.elements.get("trip-view").hidden, true);

  await harness.elements.get("confirm-profile-button").dispatch("click");
  await settle();

  assert.equal(chatBodies.length, 2);
  assert.deepEqual(
    { action: chatBodies[1].action, thread_id: chatBodies[1].thread_id },
    { action: "confirm", thread_id: chatBodies[0].thread_id },
  );
  assert.equal(harness.elements.get("trip-view").hidden, false);
  assert.match(harness.elements.get("trip-content").textContent, /人民公园/);
  assert.doesNotMatch(harness.elements.get("chat-messages").textContent, /成都两日行程|\"days\"/);
});

test("an explain response reuses the saved itinerary and renders its grounded explanation immediately", async () => {
  const itinerary = {
    title: "成都慢游两日计划",
    days: [{
      date: "2026-10-01",
      morning: { title: "人民公园与茶馆体验", start_time: "09:00", end_time: "11:00", notes: [], citations: [] },
    }],
    citations: [],
  };
  const harness = createHarness({ fetch: async (call) => {
    assert.equal(call.url, "/api/chat");
    assert.equal(JSON.parse(call.options.body).action, "collect");
    return jsonResponse(200, {
      reply: "推荐人民公园是为了匹配你已确认的慢节奏偏好。",
      stage: "planned",
      profile: { destination: "成都" },
      itinerary,
      trip_id: "trip-1",
    });
  } });
  await settle();

  harness.elements.get("message-input").value = "为什么推荐人民公园？";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();

  assert.match(harness.elements.get("chat-messages").textContent, /推荐人民公园是为了匹配/);
  assert.match(harness.elements.get("trip-content").textContent, /人民公园与茶馆体验/);
  assert.equal(harness.elements.get("trip-view").hidden, false);
  assert.equal(harness.document.body.dataset.appState, "planned");
});

test("Task 7 activity citations render canonical freshness and reject malicious links", async () => {
  const root = path.resolve(__dirname, "..", "..");
  const itinerary = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "task7_itinerary.json"), "utf8"));
  itinerary.title = "<img src=x onerror=alert(1)>";
  itinerary.days[0].afternoon.citations.push({
    evidence_id: "evil-1", source_url: "https://api.open-meteo.com.evil.example/path",
    source_type: "official", fetched_at: "2099-01-01T00:00:00Z", freshness: "fresh", fact: "forged",
  });
  itinerary.days[0].evening.citations.push({
    evidence_id: "evil-2", source_url: "https://user@api.open-meteo.com/path",
    source_type: "official", fetched_at: "2099-01-01T00:00:00Z", freshness: "fresh", fact: "userinfo",
  });
  itinerary.days[1].morning.citations.push({
    evidence_id: "booking-1", source_url: "https://www.12306.cn/index/index.html",
    source_type: "official", fetched_at: "2026-09-30T08:30:00Z", freshness: "Fetched 2026-09-30T08:30:00Z; reference only.", fact: "铁路搜索入口",
  });
  itinerary.days[1].afternoon.citations.push({
    evidence_id: "evil-3", source_url: "https://api.open-meteo.com:444/path",
    source_type: "official", fetched_at: "2099-01-01T00:00:00Z", freshness: "fresh", fact: "port",
  });
  itinerary.days[1].evening.citations.push({
    evidence_id: "evil-4", source_url: "https://unknown.example/path",
    source_type: "official", fetched_at: "2099-01-01T00:00:00Z", freshness: "FORGED-FRESHNESS-MARKER", fact: "unknown",
  });
  itinerary.days[1].evening.citations.push({
    evidence_id: "evil-5", source_url: "https://api.open-meteo.com/v1/forecast",
    source_type: "forged", fetched_at: "2098-01-01T00:00:00Z", freshness: "FORGED-ALLOWED-HOST-MARKER", fact: "forged shape",
  });
  const harness = createHarness({ hash: "#share=opaque", fetch: async (call) => {
    assert.equal(call.url, "/api/shared/resolve");
    assert.equal(call.options.method, "POST");
    assert.deepEqual(JSON.parse(call.options.body), { token: "opaque" });
    return jsonResponse(200, { id: "trip-1", title: "shared", status: "planned", profile: {}, itinerary, updated_at: null });
  } });
  await settle();
  const nodes = descendants(harness.elements.get("trip-content"));
  const links = nodes.filter((node) => node.tagName === "A");

  assert.equal(links.length, 2);
  assert.equal(links[0].href, "https://api.open-meteo.com/v1/forecast");
  assert.equal(links[0].rel, "noopener noreferrer");
  assert.equal(links[1].href, "https://www.12306.cn/index/index.html");
  assert.match(harness.elements.get("trip-content").textContent, /成都 2026-10-01 的最高气温为 24°C/);
  assert.match(harness.elements.get("trip-content").textContent, /2026-09-30T08:30:00\+00:00/);
  assert.match(harness.elements.get("trip-content").textContent, /reference only/);
  assert.match(harness.elements.get("trip-content").textContent, /来源不可验证|更新时间未知/);
  assert.doesNotMatch(harness.elements.get("trip-content").textContent, /2099|2098|FORGED-FRESHNESS-MARKER|FORGED-ALLOWED-HOST-MARKER/);
  assert.equal(nodes.some((node) => node.tagName === "IMG"), false);
  assert.equal(links.some((link) => link.href.includes("evil.example") || link.href.includes("user@") || link.href.includes(":444") || link.href.includes("unknown.example")), false);
});

test("RAG government citations accept the canonical freshness protocol", async () => {
  const root = path.resolve(__dirname, "..", "..");
  const itinerary = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "task7_itinerary.json"), "utf8"));
  itinerary.days[0].morning.citations = [{
    evidence_id: "rag:xiamen-attractions:2026-08-12:0001",
    source_url: "https://www.xm.gov.cn/",
    source_type: "government",
    fetched_at: "2026-08-13T01:00:00Z",
    freshness: "Fetched 2026-08-13T01:00:00Z; reference only.",
    fact: "鼓浪屿需核对官方航班安排。",
    source_label: "厦门市文化和旅游局公开信息（试点整理）",
  }];
  const harness = createHarness({ hash: "#share=opaque", fetch: async () => jsonResponse(200, {
    id: "trip-1", title: "shared", status: "planned", profile: {}, itinerary, updated_at: null,
  }) });
  await settle();
  const links = descendants(harness.elements.get("trip-content")).filter((node) => node.tagName === "A");
  assert.equal(links.some((link) => link.href === "https://www.xm.gov.cn/"), true);
  assert.match(harness.elements.get("trip-content").textContent, /厦门市文化和旅游局|鼓浪屿/);
});

test("readable itinerary renders notes facts assumptions and server booking search links", async () => {
  const root = path.resolve(__dirname, "..", "..");
  const itinerary = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "task7_itinerary.json"), "utf8"));
  itinerary.title = "成都慢游两日计划";
  itinerary.notes = ["每天保留机动时间，按体力调整。"];
  itinerary.days[0].morning.title = "人民公园与茶馆体验";
  itinerary.days[0].morning.notes = ["优先步行，途中安排休息。"];
  itinerary.budget.insurance = 50;
  itinerary.booking_links = {
    train: "https://www.12306.cn/index/index.html?fromStation=%E4%B8%8A%E6%B5%B7&toStation=%E6%88%90%E9%83%BD",
    hotel: "https://www.ctrip.com/hotels/list?city=%E6%88%90%E9%83%BD",
    flight: "https://www.ctrip.com/flights?from=%E4%B8%8A%E6%B5%B7&to=%E6%88%90%E9%83%BD",
    disclaimer: "价格和库存以第三方平台为准；链接仅用于搜索跳转。",
  };
  const harness = createHarness({ hash: "#share=opaque", fetch: async () => jsonResponse(200, {
    id: "trip-1", title: itinerary.title, status: "planned", profile: {}, itinerary, updated_at: null,
  }) });

  await settle();

  const content = harness.elements.get("trip-content");
  const links = descendants(content).filter((node) => node.tagName === "A");
  const bookingHosts = links
    .map((link) => new URL(link.href).hostname)
    .filter((hostname) => hostname === "www.12306.cn" || hostname === "www.ctrip.com");
  assert.match(content.textContent, /人民公园与茶馆体验/);
  assert.match(content.textContent, /优先步行，途中安排休息/);
  assert.match(content.textContent, /成都 2026-10-01 的最高气温为 24°C/);
  assert.match(content.textContent, /按已确认总预算分配/);
  assert.match(content.textContent, /价格和库存以第三方平台为准/);
  assert.match(content.textContent, /交通：1200 CNY/);
  assert.match(content.textContent, /住宿：1400 CNY/);
  assert.match(content.textContent, /合计：4500 CNY/);
  assert.match(content.textContent, /保险：50 CNY|insurance：50 CNY/);
  assert.match(content.textContent, /上午：人民公园与茶馆体验/);
  assert.doesNotMatch(content.textContent, /transport:|morning:/);
  assert.deepEqual(bookingHosts, [
    "www.12306.cn", "www.ctrip.com", "www.ctrip.com",
  ]);
});

test("provider warning without canonical citation time says the update time is unknown", async () => {
  const harness = createHarness({ fetch: async () => jsonResponse(200, {
    reply: "基础框架", stage: "collecting", profile: {}, warnings: ["WEATHER_TIMEOUT"],
  }) });
  await settle();
  harness.elements.get("message-input").value = "需要天气";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();
  assert.match(harness.elements.get("provider-updated-at").textContent, /更新时间未知|数据可能降级/);
});

test("provider warning uses only the backend canonical citation timestamp and freshness", async () => {
  const root = path.resolve(__dirname, "..", "..");
  const itinerary = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "task7_itinerary.json"), "utf8"));
  const profile = { origin: "上海", destination: "成都", start_date: "2026-10-01", end_date: "2026-10-02", travelers: 2, budget_cny: 5000 };
  const harness = createHarness({ fetch: async (call) => {
    const body = JSON.parse(call.options.body);
    return body.action === "collect"
      ? jsonResponse(200, { reply: "请确认资料。", stage: "confirming", profile })
      : jsonResponse(200, { reply: JSON.stringify(itinerary), stage: "planned", profile, itinerary, warnings: ["PLACES_TIMEOUT"] });
  } });
  await settle();
  harness.elements.get("message-input").value = "生成行程";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();
  await harness.elements.get("confirm-profile-button").dispatch("click");
  await settle();
  assert.match(harness.elements.get("provider-updated-at").textContent, /2026-09-30T08:30:00\+00:00/);
  assert.match(harness.elements.get("provider-updated-at").textContent, /reference only/);
});

test("authenticated confirmed plans are already server-saved and are never reposted from browser state", async () => {
  const profile = {
    origin: "上海", destination: "成都", start_date: "2026-10-01", end_date: "2026-10-02",
    travelers: 2, budget_cny: 5000, preferences: [], constraints: [],
  };
  const itinerary = { title: "成都行程", days: [], citations: [] };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => {
    const method = call.options.method || "GET";
    if (call.url === "/api/trips" && method === "GET") return jsonResponse(200, []);
    if (call.url === "/api/chat") {
      const body = JSON.parse(call.options.body);
      return body.action === "collect"
        ? jsonResponse(200, { reply: "请确认。", stage: "confirming", profile })
        : jsonResponse(200, { reply: JSON.stringify(itinerary), stage: "planned", profile, itinerary, trip_id: "trip-saved" });
    }
    if (call.url === "/api/trips" && method === "POST") return jsonResponse(201, { id: "client-created" });
    if (call.url.startsWith("/api/trips/") && method === "PATCH") return jsonResponse(200, { id: "client-created", profile, itinerary });
    throw new Error(`unexpected ${method} ${call.url}`);
  } });
  await settle();
  harness.elements.get("message-input").value = "完整资料";
  await harness.elements.get("chat-form").dispatch("submit");
  await settle();
  await harness.elements.get("confirm-profile-button").dispatch("click");
  await settle();

  await harness.elements.get("save-trip-button").dispatch("click");
  await settle();

  const browserWrites = harness.fetchCalls.filter((call) => {
    const method = call.options.method || "GET";
    return call.url.startsWith("/api/trips") && ["POST", "PATCH"].includes(method);
  });
  assert.deepEqual(browserWrites, []);
  assert.match(harness.elements.get("status-message").textContent, /已.*保存/);
});

test("private history executes authenticated CRUD and revocable sharing", async () => {
  const trip = { id: "trip-1", title: "成都", status: "planned", profile: {}, itinerary: { title: "成都", days: [], budget: null }, updated_at: "2026-01-01T00:00:00Z" };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => {
    const method = call.options.method || "GET";
    if (call.url === "/api/trips" && method === "GET") return jsonResponse(200, [trip]);
    if (call.url === "/api/trips/trip-1/copy" && method === "POST") return jsonResponse(201, { ...trip, id: "trip-copy" });
    if (call.url === "/api/trips/trip-1" && method === "GET") return jsonResponse(200, trip);
    if (call.url.startsWith("/api/trips/") && method === "PATCH") return jsonResponse(200, trip);
    if (call.url === "/api/trips/trip-1" && method === "DELETE") return jsonResponse(204, {});
    if (call.url === "/api/trips/trip-1/share" && method === "POST") return jsonResponse(201, { token: "opaque-token" });
    if (call.url === "/api/trips/trip-1/share" && method === "DELETE") return jsonResponse(204, {});
    throw new Error(`unexpected ${method} ${call.url}`);
  } });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");
  const history = harness.elements.get("trip-history-list");
  await findByText(history, "打开").dispatch("click");
  await settle();
  await harness.elements.get("share-trip-button").dispatch("click");
  await settle();
  assert.match(harness.elements.get("share-link").value, /#share=opaque-token$/);
  await harness.elements.get("revoke-share-link").dispatch("click");
  await settle();
  await findByText(history, "重命名").dispatch("click");
  harness.elements.get("rename-input").value = "成都新版";
  await harness.elements.get("rename-form").dispatch("submit");
  await settle();
  await findByText(history, "复制").dispatch("click");
  await settle();
  await findByText(history, "删除").dispatch("click");
  await settle();

  const shareCallsBeforeStaleAttempt = harness.fetchCalls.filter((call) => call.url.endsWith("/share") && call.options.method === "POST").length;
  assert.equal(harness.elements.get("trip-view").hidden, true);
  await harness.elements.get("share-trip-button").dispatch("click");
  await settle();
  assert.equal(
    harness.fetchCalls.filter((call) => call.url.endsWith("/share") && call.options.method === "POST").length,
    shareCallsBeforeStaleAttempt,
  );

  const privateCalls = harness.fetchCalls.filter((call) => call.url.startsWith("/api/trips"));
  assert.ok(privateCalls.some((call) => call.url.endsWith("/copy") && call.options.method === "POST"));
  assert.ok(privateCalls.some((call) => call.options.method === "PATCH"));
  assert.ok(privateCalls.some((call) => call.options.method === "DELETE"));
  assert.ok(privateCalls.every((call) => !call.options.body || !Object.hasOwn(JSON.parse(call.options.body), "itinerary")));
  assert.ok(privateCalls.every((call) => call.options.headers.Authorization === "Bearer access-one"));
});
test("Explore city shortcuts expose stable unique ids and the Xiamen selector opens that city", async () => {
  const harness = createHarness();
  await settle();

  const xiamen = harness.document.getElementById("explore-city-xiamen");
  assert.ok(xiamen, "#explore-city-xiamen must remain available to integrations");
  assert.equal(xiamen.tagName, "BUTTON");
  assert.equal(
    descendants(harness.document.body).filter((node) => node.id && node.id.startsWith("explore-city-")).length,
    4,
  );

  await xiamen.dispatch("click");

  assert.equal(harness.elements.get("explore-map").dataset.mapLevel, "city");
});

test("Explore keeps the Xiamen city control selector unique while its map controls are visible", async () => {
  const harness = createHarness();
  await settle();

  const fujian = findByText(harness.elements.get("explore-map"), "福建");
  await fujian.dispatch("click");
  const xiamenControls = descendants(harness.document.body).filter((node) => node.id === "explore-city-xiamen");

  assert.equal(xiamenControls.length, 1, "#explore-city-xiamen must not be duplicated");
  await xiamenControls[0].dispatch("click");
  assert.equal(harness.elements.get("explore-map").dataset.mapLevel, "city");
});
