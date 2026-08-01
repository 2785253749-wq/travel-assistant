const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FakeElement, FakeSupabaseAuth, createHarness, descendants, findByText, jsonResponse, settle } = require("./dom-harness");

const SESSION = { access_token: "access-one", refresh_token: "refresh-one", expires_at: 2000000000, user: { email: "owner@example.test" } };
const REFRESHED = { access_token: "access-two", refresh_token: "refresh-two", expires_at: 2000003600, user: { email: "owner@example.test" } };

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

  assert.equal(auth.refreshCalls, 1);
  assert.equal(privateCalls, 2);
  assert.equal(harness.fetchCalls[0].options.headers.Authorization, "Bearer access-one");
  assert.equal(harness.fetchCalls[1].options.headers.Authorization, "Bearer access-two");
  assert.equal(harness.elements.get("account-summary").hidden, false);
});

test("a retry that is still 401 signs out and clears the restored session", async () => {
  const successfulAuth = new FakeSupabaseAuth({ initialSession: SESSION, refreshedSession: REFRESHED });
  const signedOutAfterRetry = createHarness({ auth: successfulAuth, fetch: async () => jsonResponse(401, { detail: { code: "AUTH_INVALID" } }) });
  await settle();
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
  auth.emit("TOKEN_REFRESHED", REFRESHED);
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

test("private history executes authenticated CRUD and revocable sharing", async () => {
  const trip = { id: "trip-1", title: "成都", status: "planned", profile: {}, itinerary: { title: "成都", days: [], budget: null }, updated_at: "2026-01-01T00:00:00Z" };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ auth, fetch: async (call) => {
    const method = call.options.method || "GET";
    if (call.url === "/api/trips" && method === "GET") return jsonResponse(200, [trip]);
    if (call.url === "/api/trips" && method === "POST") return jsonResponse(201, { ...trip, id: "trip-copy" });
    if (call.url === "/api/trips/trip-1" && method === "GET") return jsonResponse(200, trip);
    if (call.url.startsWith("/api/trips/") && method === "PATCH") return jsonResponse(200, trip);
    if (call.url === "/api/trips/trip-1" && method === "DELETE") return jsonResponse(204, {});
    if (call.url === "/api/trips/trip-1/share" && method === "POST") return jsonResponse(201, { token: "opaque-token" });
    if (call.url === "/api/trips/trip-1/share" && method === "DELETE") return jsonResponse(204, {});
    throw new Error(`unexpected ${method} ${call.url}`);
  } });
  await settle();
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
  assert.ok(privateCalls.some((call) => (call.options.method || "GET") === "POST"));
  assert.ok(privateCalls.some((call) => call.options.method === "PATCH"));
  assert.ok(privateCalls.some((call) => call.options.method === "DELETE"));
  assert.ok(privateCalls.every((call) => call.options.headers.Authorization === "Bearer access-one"));
});
