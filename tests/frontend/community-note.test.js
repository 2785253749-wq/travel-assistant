const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FakeSupabaseAuth, createHarness, jsonResponse, settle } = require("./dom-harness");

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "app", "static", "community-note.html"), "utf8");
const script = fs.readFileSync(path.join(ROOT, "app", "static", "community-note.js"), "utf8");

test("detail page renders public gallery controls with safe DOM APIs", () => {
  assert.match(html, /id="community-note-content"/);
  assert.match(html, /id="community-note-gallery-prev"/);
  assert.match(html, /id="community-note-gallery-next"/);
  assert.match(script, /safeUrl/);
  assert.match(script, /textContent/);
  assert.doesNotMatch(script, /innerHTML\s*=/);
  assert.doesNotMatch(script, /storage_path/);
});


const SESSION = {
  access_token: "access-one",
  refresh_token: "refresh-one",
  expires_at: 2000000000,
  user: { id: "user-a", email: "owner@example.test" },
};

function notePayload(overrides = {}) {
  return {
    id: "note-1",
    title: "厦门周末",
    body: "海边散步和沙茶面。",
    location_name: "厦门",
    category: "城市漫步",
    author_display_name: "Voyage 旅行者",
    author_slug: "voyage-traveler",
    like_count: 3,
    comment_count: 1,
    images: [{ id: "image-1", image_url: "https://cdn.example.test/note.webp", sort_order: 0, width: 800, height: 600 }],
    ...overrides,
  };
}

function interactionState(overrides = {}) {
  return {
    note_id: "note-1",
    liked: false,
    bookmarked: false,
    like_count: 3,
    comment_count: 1,
    ...overrides,
  };
}

function noteFetch(call, payload = notePayload()) {
  if (call.url === "/api/community/notes/note-1") return jsonResponse(200, payload);
  if (call.url === "/api/community/notes/note-1/comments") return jsonResponse(200, { items: [], next_cursor: null });
  return null;
}

test("interaction buttons stay neutral until the first successful mutation", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "community-note",
    auth,
    fetch: async (call) => {
      const base = noteFetch(call);
      if (base) return base;
      if (call.url.endsWith("/like") && call.options.method === "PUT") {
        return jsonResponse(200, interactionState({ liked: true, like_count: 4 }));
      }
      if (call.url.endsWith("/like") && call.options.method === "DELETE") {
        return jsonResponse(200, interactionState({ liked: false, like_count: 3 }));
      }
      if (call.url.endsWith("/bookmark") && call.options.method === "PUT") {
        return jsonResponse(200, interactionState({ bookmarked: true }));
      }
      return jsonResponse(200, {});
    },
  });
  await settle();

  const like = harness.elements.get("community-note-like-button");
  const bookmark = harness.elements.get("community-note-bookmark-button");
  assert.equal(like.getAttribute("aria-pressed"), null);
  assert.equal(bookmark.getAttribute("aria-pressed"), null);

  await like.dispatch("click");
  await settle();
  assert.equal(like.getAttribute("aria-pressed"), "true");
  assert.equal(harness.elements.get("community-note-like-count").textContent, "4");

  await like.dispatch("click");
  await settle();
  assert.equal(like.getAttribute("aria-pressed"), "false");

  await bookmark.dispatch("click");
  await settle();
  assert.equal(bookmark.getAttribute("aria-pressed"), "true");

  const mutationCalls = harness.fetchCalls.filter((call) => call.url.includes("/like") || call.url.includes("/bookmark"));
  assert.deepEqual(mutationCalls.map((call) => call.options.method), ["PUT", "DELETE", "PUT"]);
});

test("failed or expired interaction restores the neutral state and supports sign-in redirect", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let shouldExpire = false;
  const harness = createHarness({
    page: "community-note",
    auth,
    fetch: async (call) => {
      const base = noteFetch(call);
      if (base) return base;
      if (call.url.endsWith("/like")) {
        return shouldExpire
          ? jsonResponse(401, { detail: { code: "AUTH_REQUIRED" } })
          : jsonResponse(503, { detail: { code: "REQUEST_FAILED" } });
      }
      return jsonResponse(200, {});
    },
  });
  await settle();

  const like = harness.elements.get("community-note-like-button");
  await like.dispatch("click");
  await settle();
  assert.equal(like.getAttribute("aria-pressed"), null);
  assert.equal(like.classList.contains("is-pending"), false);
  assert.match(harness.elements.get("community-note-interaction-status").textContent, /失败|不可用/);

  shouldExpire = true;
  await like.dispatch("click");
  await settle();
  assert.equal(harness.window.location.pathname, "/auth");
  assert.match(harness.window.location.search, /return_to=%2Fcommunity%2Fnotes%2Fnote-1/);
});

test("comments expose approved content, pending review, and report form", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const approved = {
    id: "comment-1",
    note_id: "note-1",
    author_display_name: "Voyage Alice",
    body: "日落时去海边最好。",
    status: "approved",
    published_at: "2026-08-20T09:00:00Z",
  };
  const pending = {
    id: "comment-2",
    note_id: "note-1",
    author_display_name: "Voyage Alice",
    body: "请问最佳拍摄时间？",
    status: "pending_review",
    published_at: null,
  };
  const harness = createHarness({
    page: "community-note",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/community/notes/note-1") return jsonResponse(200, notePayload());
      if (call.options.method === "POST" && call.url.endsWith("/comments")) return jsonResponse(201, pending);
      if (call.url === "/api/community/notes/note-1/comments") return jsonResponse(200, { items: [approved], next_cursor: null });
      if (call.options.method === "POST" && call.url.endsWith("/reports")) return jsonResponse(201, {
        id: "report-1", target_type: "note", target_id: "note-1", status: "pending",
      });
      return jsonResponse(200, {});
    },
  });
  await settle();

  assert.match(harness.elements.get("community-note-comments-list").textContent, /日落时去海边最好/);
  const commentsCall = harness.fetchCalls.find((call) => call.url === "/api/community/notes/note-1/comments");
  assert.equal(commentsCall.options.headers.Authorization, "Bearer access-one");
  harness.elements.get("community-note-comment-body").value = "请问最佳拍摄时间？";
  await harness.elements.get("community-note-comment-form").dispatch("submit");
  await settle();
  await settle();
  assert.equal(harness.fetchCalls.filter((call) => call.options.method === "POST" && call.url.endsWith("/comments")).length, 1);
  assert.match(harness.elements.get("community-note-comments-list").textContent, /审核中/);

  await harness.elements.get("community-note-report-button").dispatch("click");
  assert.equal(harness.elements.get("community-note-report-dialog").hidden, false);
  harness.elements.get("community-note-report-reason").value = "内容不实";
  await harness.elements.get("community-note-report-form").dispatch("submit");
  await settle();
  assert.match(harness.elements.get("community-note-interaction-status").textContent, /举报已提交/);

  const reportCall = harness.fetchCalls.find((call) => call.url.endsWith("/reports"));
  assert.deepEqual(JSON.parse(reportCall.options.body), {
    target_type: "note",
    target_id: "note-1",
    reason: "内容不实",
  });
});

test("signed-out interaction controls redirect to sign-in", async () => {
  const harness = createHarness({
    page: "community-note",
    fetch: async (call) => noteFetch(call) || jsonResponse(200, {}),
  });
  await settle();

  await harness.elements.get("community-note-like-button").dispatch("click");
  assert.equal(harness.window.location.pathname, "/auth");
  assert.match(harness.window.location.search, /return_to=%2Fcommunity%2Fnotes%2Fnote-1/);
  assert.equal(harness.elements.get("community-note-comment-form").hidden, true);
  assert.equal(harness.elements.get("community-note-comment-signin").hidden, false);
});
