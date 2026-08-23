const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { FakeSupabaseAuth, createHarness, descendants, jsonResponse, settle } = require("./dom-harness");

const ROOT = path.resolve(__dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "app", "static", "admin-community.html"), "utf8");
const script = fs.readFileSync(path.join(ROOT, "app", "static", "admin-community.js"), "utf8");

const SESSION = {
  access_token: "admin-token",
  refresh_token: "refresh-admin",
  expires_at: 2000000000,
  user: { id: "admin-a", email: "admin@example.test" },
};

function notePayload(overrides = {}) {
  return {
    id: "note-1",
    title: "安全标题",
    body: "管理员审核内容。",
    location_name: "厦门",
    category: "城市漫步",
    status: "pending_review",
    review_reason: null,
    submitted_at: "2026-08-22T09:00:00Z",
    author_display_name: "Voyage 旅行者",
    images: [{
      id: "image-1",
      image_url: "https://signed.example.test/short-lived",
      sort_order: 0,
      width: 800,
      height: 600,
    }],
    ...overrides,
  };
}

function pagePayload(items = [notePayload()], nextCursor = null) {
  return { items, next_cursor: nextCursor };
}

function baseFetch(call) {
  if (call.url === "/api/admin/community/review-queue?target_type=note&limit=20") {
    return jsonResponse(200, pagePayload());
  }
  if (call.url === "/api/admin/community/review-queue?target_type=comment&limit=20") {
    return jsonResponse(200, { items: [], next_cursor: null });
  }
  if (call.url === "/api/admin/community/review-queue?target_type=report&limit=20") {
    return jsonResponse(200, { items: [], next_cursor: null });
  }
  return jsonResponse(200, {});
}

test("admin page exposes the three queues and safe rendering boundaries", () => {
  assert.match(html, /id="admin-community-tab-notes"/);
  assert.match(html, /id="admin-community-tab-comments"/);
  assert.match(html, /id="admin-community-tab-reports"/);
  assert.match(script, /\/api\/admin\/community\//);
  assert.match(script, /notes/);
  assert.match(script, /comments/);
  assert.match(script, /reports/);
  assert.doesNotMatch(script, /location\.replace\(["']\/["']\)/);
  assert.doesNotMatch(script, /innerHTML\s*=/);
  assert.doesNotMatch(script, /storage_path/);
});

test("admin standalone keeps its route hidden until administrator access succeeds", async () => {
  const forbidden = createHarness({
    page: "community-admin",
    fetch: async () => jsonResponse(403, { detail: { code: "COMMUNITY_ADMIN_REQUIRED" } }),
  });
  await settle();
  assert.equal(forbidden.elements.get("admin-community-self-nav").hidden, true);

  const allowed = createHarness({ page: "community-admin", auth: new FakeSupabaseAuth({ initialSession: SESSION }), fetch: baseFetch });
  await settle();
  assert.equal(allowed.elements.get("admin-community-self-nav").hidden, false);
});
test("admin loads notes, switches to comments, and sends the authenticated Task 12 paths", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({ page: "community-admin", auth, fetch: baseFetch });

  await settle();
  assert.equal(harness.fetchCalls[0].url, "/api/admin/community/review-queue?target_type=note&limit=20");
  assert.equal(harness.fetchCalls[0].options.headers.Authorization, "Bearer admin-token");
  assert.match(harness.elements.get("admin-community-list").textContent, /安全标题/);
  assert.equal(harness.elements.get("admin-community-empty").hidden, true);

  await harness.elements.get("admin-community-tab-comments").dispatch("click");
  await settle();
  assert.equal(harness.fetchCalls[1].url, "/api/admin/community/review-queue?target_type=comment&limit=20");
  assert.equal(harness.elements.get("admin-community-empty").hidden, false);
});

test("403 clears the queue and stays in the administrator view with a permission message", async () => {
  const forbidden = createHarness({
    page: "community-admin",
    auth: new FakeSupabaseAuth({ initialSession: SESSION }),
    fetch: async () => jsonResponse(403, { detail: { code: "COMMUNITY_ADMIN_REQUIRED" } }),
  });
  await settle();
  assert.equal(forbidden.elements.get("admin-community-list").children.length, 0);
  assert.equal(forbidden.window.location.pathname, "/admin/community");
  assert.match(forbidden.elements.get("admin-community-forbidden").textContent, /没有社区管理员权限/);
  assert.equal(forbidden.elements.get("admin-community-forbidden").hidden, false);
  assert.equal(forbidden.elements.get("admin-community-return-explore").getAttribute("href"), "/");

  const signedOut = createHarness({ page: "community-admin" });
  await settle();
  assert.equal(signedOut.window.location.pathname, "/admin/community");
  assert.equal(signedOut.elements.get("admin-community-auth-required").hidden, false);
  assert.match(signedOut.elements.get("admin-community-auth-required").textContent, /登录/);
});

test("401 clears a stale session before redirecting to administrator sign-in", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "community-admin",
    auth,
    fetch: async () => jsonResponse(401, { detail: { code: "AUTH_REQUIRED" } }),
  });

  await settle();

  assert.equal(auth.signOutCalls, 1);
  assert.equal(auth.session, null);
  assert.equal(harness.window.location.pathname, "/admin/community");
  assert.equal(harness.elements.get("admin-community-auth-required").hidden, false);
  assert.match(harness.elements.get("admin-community-auth-required").textContent, /登录/);
});

test("reject dialog submits a reason and clears target state on close", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    page: "community-admin",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/admin/community/review-queue?target_type=note&limit=20") return jsonResponse(200, pagePayload());
      if (call.url === "/api/admin/community/reviews/note/note-1/reject") {
        assert.equal(call.options.method, "POST");
        assert.deepEqual(JSON.parse(call.options.body), {
          reason: "请补充来源",
        });
        return jsonResponse(200, notePayload({ status: "rejected", review_reason: "请补充来源" }));
      }
      return jsonResponse(200, {});
    },
  });
  await settle();

  const reject = descendants(harness.document.body).find((node) => node.className === "admin-community-reject");
  assert.ok(reject);
  await reject.dispatch("click");
  const dialog = harness.elements.get("admin-community-review-dialog");
  const reason = harness.elements.get("admin-community-review-reason");
  assert.equal(dialog.hidden, false);
  assert.equal(dialog.dataset.targetId, "note-1");
  reason.value = "请补充来源";
  await harness.elements.get("admin-community-review-submit").dispatch("click");
  await settle();

  assert.equal(dialog.hidden, true);
  assert.equal(reason.value, "");
  assert.equal(dialog.dataset.targetId, undefined);
  assert.equal(dialog.dataset.targetType, undefined);
});

test("report cards close through the explicit Task 12 resolve endpoint", async () => {
  const report = {
    id: "report-1",
    target_type: "comment",
    target_id: "comment-1",
    reason: "���ƹ��",
    status: "pending",
    created_at: "2026-08-22T09:00:00Z",
  };
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  let resolved = false;
  const harness = createHarness({
    page: "community-admin",
    auth,
    fetch: async (call) => {
      if (call.url === "/api/admin/community/review-queue?target_type=note&limit=20") return jsonResponse(200, pagePayload([]));
      if (call.url === "/api/admin/community/review-queue?target_type=report&limit=20") return jsonResponse(200, { items: resolved ? [] : [report], next_cursor: null });
      if (call.url === "/api/admin/community/reports/report-1/resolve") {
        assert.equal(call.options.method, "POST");
        assert.deepEqual(JSON.parse(call.options.body), { decision: "dismissed" });
        resolved = true;
        return jsonResponse(200, { ...report, status: "dismissed" });
      }
      return jsonResponse(200, {});
    },
  });
  await settle();
  await harness.elements.get("admin-community-tab-reports").dispatch("click");
  await settle();
  const dismiss = descendants(harness.document.body).find((node) => node.className === "admin-community-report-dismiss");
  assert.ok(dismiss);
  await dismiss.dispatch("click");
  await settle();
  assert.equal(harness.elements.get("admin-community-empty").hidden, false);
});

test("queue errors expose retry and cursor pagination appends through the same tab", async () => {
  let attempts = 0;
  const harness = createHarness({
    page: "community-admin",
    auth: new FakeSupabaseAuth({ initialSession: SESSION }),
    fetch: async (call) => {
      if (call.url === "/api/admin/community/review-queue?target_type=note&limit=20") {
        attempts += 1;
        return attempts === 1
          ? jsonResponse(503, { detail: { code: "COMMUNITY_MODERATION_UNAVAILABLE" } })
          : jsonResponse(200, pagePayload([notePayload()], "cursor-1"));
      }
      if (call.url === "/api/admin/community/review-queue?target_type=note&cursor=cursor-1&limit=20") {
        return jsonResponse(200, pagePayload([notePayload({ id: "note-2", title: "第二条" })]));
      }
      return jsonResponse(200, {});
    },
  });
  await settle();
  assert.equal(harness.elements.get("admin-community-retry").hidden, false);
  await harness.elements.get("admin-community-retry").dispatch("click");
  await settle();
  assert.equal(harness.elements.get("admin-community-load-more").hidden, false);
  await harness.elements.get("admin-community-load-more").dispatch("click");
  await settle();
  assert.equal(harness.fetchCalls.at(-1).url, "/api/admin/community/review-queue?target_type=note&cursor=cursor-1&limit=20");
  assert.match(harness.elements.get("admin-community-list").textContent, /第二条/);
});
