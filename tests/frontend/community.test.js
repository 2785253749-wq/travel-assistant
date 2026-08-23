const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FakeSupabaseAuth, createHarness, descendants, findByText, jsonResponse, settle } = require("./dom-harness");

const SESSION = {
  access_token: "access-one",
  refresh_token: "refresh-one",
  expires_at: 2000000000,
  user: { id: "user-a", email: "owner@example.test" },
};

function itinerarySnapshot(title) {
  const fixture = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "fixtures", "task7_itinerary.json"), "utf8"),
  );
  fixture.title = title;
  fixture.days[0].morning.title = `${title} 上午`;
  fixture.days[0].afternoon.title = `${title} 下午`;
  fixture.days[0].evening.title = `${title} 晚上`;
  return fixture;
}

function communityPost(overrides = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    author_display_name: "Voyage 旅行者",
    title: "厦门周末公开快照",
    destination: "厦门",
    summary: "海边散步和沙茶面。",
    itinerary_snapshot: itinerarySnapshot("厦门周末公开快照"),
    created_at: "2026-08-20T09:00:00Z",
    updated_at: "2026-08-20T09:00:00Z",
    can_delete: false,
    ...overrides,
  };
}

function communityPage(items, nextCursor = null) {
  return { items, next_cursor: nextCursor };
}

function findButton(root, text) {
  return descendants(root).find((node) => node.tagName === "BUTTON" && node.textContent === text);
}

function travelNote(overrides = {}) {
  return {
    id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    title: "大理慢旅行",
    body_preview: "洱海边的慢节奏散步。",
    excerpt: "洱海边的慢节奏散步。",
    location_name: "大理",
    category: "自然风光",
    cover_image_url: "https://signed.example.test/cover",
    author_display_name: "Voyage Alice",
    author_avatar_url: null,
    creator_slug: "voyage-alice",
    published_at: "2026-08-22T09:00:00Z",
    like_count: 3,
    comment_count: 1,
    ...overrides,
  };
}

test("dedicated community cards like optimistically, roll back failures, and redirect anonymous users", async () => {
  const note = travelNote();
  let resolveLike;
  const pendingLike = new Promise((resolve) => { resolveLike = resolve; });
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    hash: "#community-page",
    page: "community",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/notes?limit=20") return jsonResponse(200, { items: [note], next_cursor: null });
      if (call.url === `/api/community/notes/${note.id}/like`) return pendingLike;
      return jsonResponse(200, {});
    },
  });
  await settle();

  const card = descendants(harness.elements.get("community-grid")).find((node) => node.className === "community-card");
  const like = descendants(card).find((node) => node.tagName === "BUTTON" && node.className === "community-card__action");
  await like.dispatch("click");
  await settle(1);
  assert.equal(like.classList.contains("is-active"), true);
  assert.match(card.textContent, /点赞 4/);

  resolveLike(jsonResponse(503, { detail: { code: "COMMUNITY_INTERACTION_UNAVAILABLE" } }));
  await settle();
  assert.equal(like.getAttribute("aria-pressed"), null);
  assert.match(card.textContent, /点赞 3/);

  const anonymous = createHarness({
    hash: "#community-page",
    page: "community",
    fetch: async (call) => call.url === "/api/community/notes?limit=20"
      ? jsonResponse(200, { items: [note], next_cursor: null })
      : jsonResponse(200, {}),
  });
  await settle();
  const anonymousCard = descendants(anonymous.elements.get("community-grid")).find((node) => node.className === "community-card");
  const anonymousLike = descendants(anonymousCard).find((node) => node.tagName === "BUTTON" && node.className === "community-card__action");
  await anonymousLike.dispatch("click");
  assert.equal(anonymous.window.location.pathname, "/auth");
  assert.equal(new URLSearchParams(anonymous.window.location.search).get("return_to"), `/community/notes/${note.id}`);
});

test("anonymous community view loads public posts and opens detail snapshots", async () => {
  const listPost = communityPost();
  const harness = createHarness({
    hash: "#community-page",
    fetch: async (call) => {
      if (call.url === "/api/community/posts") return jsonResponse(200, communityPage([listPost]));
      if (call.url === `/api/community/posts/${listPost.id}`) return jsonResponse(200, listPost);
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();

  assert.ok(harness.elements.get("community-feed-list"), "community feed list should exist");
  assert.ok(harness.elements.get("community-signin-button"), "signed-out publish prompt should exist");
  assert.match(harness.elements.get("community-feed-list").textContent, /厦门周末公开快照/);
  assert.equal(harness.fetchCalls[0].options.headers.Authorization, undefined);

  await findButton(harness.elements.get("community-feed-list"), "查看详情").dispatch("click");
  await settle();

  assert.match(harness.elements.get("community-detail-card").textContent, /厦门周末公开快照|厦门周末公开快照 上午/);
  assert.equal(harness.elements.get("community-withdraw-button").hidden, true);
});

test("community shows empty state for visitors and signed-out publish redirects back to the community view", async () => {
  const harness = createHarness({
    hash: "#community-page",
    fetch: async (call) => call.url === "/api/community/posts"
      ? jsonResponse(200, communityPage([]))
      : jsonResponse(200, {}),
  });
  await settle();

  await settle();

  assert.match(harness.elements.get("community-feed-status").textContent, /还没有公开发布/);
  await harness.elements.get("community-signin-button").dispatch("click");

  assert.equal(harness.window.location.pathname, "/auth");
  const params = new URLSearchParams(harness.window.location.search);
  assert.equal(params.get("mode"), "signin");
  assert.equal(params.get("return_to"), "/#community-page");
});

test("community list failure renders retry and recovers on retry", async () => {
  let attempts = 0;
  const harness = createHarness({
    hash: "#community-page",
    fetch: async (call) => {
      if (call.url !== "/api/community/posts") return jsonResponse(200, {});
      attempts += 1;
      return attempts === 1
        ? jsonResponse(503, { detail: { code: "COMMUNITY_PUBLISH_FAILED" } })
        : jsonResponse(200, communityPage([communityPost({ title: "重试后的公开发布" })]));
    },
  });
  await settle();

  await settle();

  assert.match(harness.elements.get("community-feed-status").textContent, /加载失败|暂不可用/);
  await harness.elements.get("community-retry-button").dispatch("click");
  await settle();

  assert.equal(attempts, 2);
  assert.match(harness.elements.get("community-feed-list").textContent, /重试后的公开发布/);
});

test("community pagination appends older pages through the load-more control", async () => {
  const newer = communityPost({ id: "11111111-1111-1111-1111-111111111111", title: "较新的发布" });
  const older = communityPost({ id: "22222222-2222-2222-2222-222222222222", title: "较早的发布" });
  const harness = createHarness({
    hash: "#community-page",
    fetch: async (call) => {
      if (call.url === "/api/community/posts") return jsonResponse(200, communityPage([newer], "cursor-1"));
      if (call.url === "/api/community/posts?cursor=cursor-1") return jsonResponse(200, communityPage([older]));
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();
  assert.match(harness.elements.get("community-feed-list").textContent, /较新的发布/);

  await harness.elements.get("community-load-more-button").dispatch("click");
  await settle();

  assert.match(harness.elements.get("community-feed-list").textContent, /较新的发布/);
  assert.match(harness.elements.get("community-feed-list").textContent, /较早的发布/);
  assert.equal(harness.fetchCalls[1].url, "/api/community/posts?cursor=cursor-1");
});

test("signed-in community loads planned trips, validates the summary, and publishes a public post", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const plannedTrip = { id: "trip-planned", title: "我的厦门计划", status: "planned", updated_at: "2026-08-20T09:30:00Z" };
  const published = communityPost({
    id: "33333333-3333-3333-3333-333333333333",
    title: "我的厦门计划",
    summary: "在海边慢慢走，顺手吃一碗沙茶面。",
    can_delete: true,
  });
  const harness = createHarness({
    hash: "#community-page",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/posts") {
        return (call.options.method || "GET") === "POST"
          ? jsonResponse(201, published)
          : jsonResponse(200, communityPage([]));
      }
      if (call.url === "/api/trips?status=planned") return jsonResponse(200, [plannedTrip]);
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();

  assert.ok(harness.elements.get("community-publish-form"), "signed-in publish form should exist");
  harness.elements.get("community-trip-select").value = plannedTrip.id;
  harness.elements.get("community-summary").value = "   ";
  await harness.elements.get("community-publish-form").dispatch("submit");
  await settle();

  assert.match(harness.elements.get("community-publish-feedback").textContent, /1.*300|摘要/);
  assert.equal(
    harness.fetchCalls.filter((call) => call.url === "/api/community/posts" && (call.options.method || "GET") === "POST").length,
    0,
  );

  harness.elements.get("community-summary").value = "  在海边慢慢走，顺手吃一碗沙茶面。  ";
  await harness.elements.get("community-publish-form").dispatch("submit");
  await settle();

  const publishCall = harness.fetchCalls.find((call) => call.url === "/api/community/posts" && call.options.method === "POST");
  assert.deepEqual(JSON.parse(publishCall.options.body), {
    trip_id: plannedTrip.id,
    summary: "在海边慢慢走，顺手吃一碗沙茶面。",
  });
  assert.equal(publishCall.options.headers.Authorization, "Bearer access-one");
  assert.match(harness.elements.get("community-feed-list").textContent, /我的厦门计划/);
  assert.equal(descendants(harness.elements.get("community-feed-list")).some((node) => node.textContent === "撤下"), true);
});

test("publish cleanup survives leaving Community before the request settles", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const plannedTrip = { id: "trip-planned", title: "我的厦门计划", status: "planned", updated_at: "2026-08-20T09:30:00Z" };
  const published = communityPost({
    id: "33333333-3333-3333-3333-333333333333",
    title: "我的厦门计划",
    summary: "在海边慢慢走，顺手吃一碗沙茶面。",
    can_delete: true,
  });
  let resolvePublish;
  const pendingPublish = new Promise((resolve) => { resolvePublish = resolve; });
  const harness = createHarness({
    hash: "#community-page",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/posts") {
        return (call.options.method || "GET") === "POST"
          ? pendingPublish
          : jsonResponse(200, communityPage([published]));
      }
      if (call.url === "/api/trips?status=planned") return jsonResponse(200, [plannedTrip]);
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();

  harness.elements.get("community-trip-select").value = plannedTrip.id;
  harness.elements.get("community-summary").value = "在海边慢慢走，顺手吃一碗沙茶面。";
  const publishing = harness.elements.get("community-publish-form").dispatch("submit");
  await settle(1);

  assert.equal(harness.elements.get("community-publish-button").disabled, true);

  await harness.elements.get("explore-nav-button").dispatch("click");
  await settle(1);
  resolvePublish(jsonResponse(201, published));
  await publishing;
  await settle();

  await settle();

  assert.equal(harness.elements.get("community-publish-button").disabled, false);
  assert.equal(harness.elements.get("community-summary").disabled, false);
});

test("authors can withdraw their own public post after confirming", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const ownPost = communityPost({
    id: "44444444-4444-4444-4444-444444444444",
    title: "作者自己的发布",
    can_delete: true,
  });
  const harness = createHarness({
    hash: "#community-page",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/posts") return jsonResponse(200, communityPage([ownPost]));
      if (call.url === `/api/community/posts/${ownPost.id}` && (call.options.method || "GET") === "GET") return jsonResponse(200, ownPost);
      if (call.url === `/api/community/posts/${ownPost.id}` && call.options.method === "DELETE") return jsonResponse(204, {});
      if (call.url === "/api/trips?status=planned") return jsonResponse(200, []);
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();
  await findButton(harness.elements.get("community-feed-list"), "查看详情").dispatch("click");
  await settle();
  await harness.elements.get("community-withdraw-button").dispatch("click");
  await settle();

  assert.equal(harness.confirmCalls.length, 1);
  const deleteCall = harness.fetchCalls.find((call) => call.url === `/api/community/posts/${ownPost.id}` && call.options.method === "DELETE");
  assert.ok(deleteCall, "withdraw should issue DELETE");
  assert.equal(deleteCall.options.headers.Authorization, "Bearer access-one");
  assert.match(harness.elements.get("community-feed-status").textContent, /还没有公开发布/);
  assert.equal(harness.elements.get("community-detail-card").hidden, true);
});

test("withdraw cleanup survives leaving Community before the request settles", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const ownPost = communityPost({
    id: "44444444-4444-4444-4444-444444444444",
    title: "作者自己的发布",
    can_delete: true,
  });
  let resolveWithdraw;
  const pendingWithdraw = new Promise((resolve) => { resolveWithdraw = resolve; });
  const harness = createHarness({
    hash: "#community-page",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/posts") return jsonResponse(200, communityPage([ownPost]));
      if (call.url === `/api/community/posts/${ownPost.id}` && (call.options.method || "GET") === "GET") return jsonResponse(200, ownPost);
      if (call.url === `/api/community/posts/${ownPost.id}` && call.options.method === "DELETE") return pendingWithdraw;
      if (call.url === "/api/trips?status=planned") return jsonResponse(200, []);
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();
  await findButton(harness.elements.get("community-feed-list"), "查看详情").dispatch("click");
  await settle();

  const withdrawing = harness.elements.get("community-withdraw-button").dispatch("click");
  await settle(1);

  assert.equal(harness.elements.get("community-withdraw-button").disabled, true);

  await harness.elements.get("explore-nav-button").dispatch("click");
  await settle(1);
  resolveWithdraw(jsonResponse(204, {}));
  await withdrawing;
  await settle();

  await settle();
  await findButton(harness.elements.get("community-feed-list"), "查看详情").dispatch("click");
  await settle();

  assert.equal(harness.elements.get("community-withdraw-button").disabled, false);
});

test("publish and withdraw mutations are serialized without wedging controls", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const ownPost = communityPost({
    id: "66666666-6666-6666-6666-666666666666",
    title: "准备撤下的发布",
    can_delete: true,
  });
  const plannedTrip = {
    id: "trip-after-withdraw",
    title: "撤下后再发布",
    status: "planned",
    updated_at: "2026-08-20T12:00:00Z",
  };
  const published = communityPost({
    id: "77777777-7777-7777-7777-777777777777",
    title: plannedTrip.title,
    can_delete: true,
  });
  let resolveWithdraw;
  const pendingWithdraw = new Promise((resolve) => { resolveWithdraw = resolve; });
  const harness = createHarness({
    hash: "#community-page",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/posts") {
        return call.options.method === "POST"
          ? jsonResponse(201, published)
          : jsonResponse(200, communityPage([ownPost]));
      }
      if (call.url === `/api/community/posts/${ownPost.id}` && call.options.method === "DELETE") {
        return pendingWithdraw;
      }
      if (call.url === "/api/trips?status=planned") return jsonResponse(200, [plannedTrip]);
      return jsonResponse(200, {});
    },
  });
  await settle();

  await settle();

  const withdrawing = findButton(
    harness.elements.get("community-feed-list"),
    "撤下",
  ).dispatch("click");
  await settle(1);
  harness.elements.get("community-trip-select").value = plannedTrip.id;
  harness.elements.get("community-summary").value = "这次发布必须等待撤下结束。";
  await harness.elements.get("community-publish-form").dispatch("submit");
  await settle(1);

  assert.equal(
    harness.fetchCalls.filter((call) => call.url === "/api/community/posts" && call.options.method === "POST").length,
    0,
  );
  assert.equal(harness.elements.get("community-publish-button").disabled, true);

  resolveWithdraw(jsonResponse(204, {}));
  await withdrawing;
  await settle();

  assert.equal(harness.elements.get("community-publish-button").disabled, false);
  assert.equal(harness.elements.get("community-summary").disabled, false);

  harness.elements.get("community-trip-select").value = plannedTrip.id;
  harness.elements.get("community-summary").value = "撤下完成后可以正常发布。";
  await harness.elements.get("community-publish-form").dispatch("submit");
  await settle();

  assert.equal(
    harness.fetchCalls.filter((call) => call.url === "/api/community/posts" && call.options.method === "POST").length,
    1,
  );
  assert.equal(harness.elements.get("community-summary").disabled, false);
  assert.match(harness.elements.get("community-publish-feedback").textContent, /已发布/);
});

test("stale signed-in community responses cannot restore private publish controls after sign-out", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const staleOwnPost = communityPost({ title: "账户 A 的公开发布", can_delete: true });
  const publicPost = communityPost({ id: "55555555-5555-5555-5555-555555555555", title: "匿名仍可浏览的公开发布", can_delete: false });
  let resolveSignedInList;
  let resolveSignedInTrips;
  let resolveSignedOutList;
  const signedInList = new Promise((resolve) => { resolveSignedInList = resolve; });
  const signedInTrips = new Promise((resolve) => { resolveSignedInTrips = resolve; });
  const signedOutList = new Promise((resolve) => { resolveSignedOutList = resolve; });
  const harness = createHarness({
    hash: "#community-page",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/posts") {
        return call.options.headers.Authorization === "Bearer access-one" ? signedInList : signedOutList;
      }
      if (call.url === "/api/trips?status=planned") return signedInTrips;
      return jsonResponse(200, {});
    },
  });
  await settle();

  const opening = harness.elements.get("community-nav-button").dispatch("click");
  await settle(1);
  auth.emit("SIGNED_OUT", null);
  await settle(1);

  resolveSignedOutList(jsonResponse(200, communityPage([publicPost])));
  resolveSignedInTrips(jsonResponse(200, [{ id: "trip-a", title: "账户 A 的待发布行程" }]));
  resolveSignedInList(jsonResponse(200, communityPage([staleOwnPost])));
  await opening;
  await settle();

  assert.equal(harness.elements.get("account-summary").hidden, true);
  assert.equal(harness.elements.get("community-signin-button").hidden, false);
  assert.match(harness.elements.get("community-feed-list").textContent, /匿名仍可浏览的公开发布/);
  assert.doesNotMatch(harness.elements.get("community-page").textContent, /账户 A 的待发布行程/);
  assert.equal(descendants(harness.elements.get("community-feed-list")).some((node) => node.textContent === "撤下"), false);
});
