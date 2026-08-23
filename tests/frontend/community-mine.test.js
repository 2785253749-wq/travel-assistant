const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { FakeElement, FakeSupabaseAuth, descendants, jsonResponse, settle } = require("./dom-harness");

const ROOT = path.resolve(__dirname, "..", "..");
const HTML_PATH = path.join(ROOT, "app", "static", "community-mine.html");
const CLIENT_PATH = path.join(ROOT, "app", "static", "community-client.js");
const MINE_PATH = path.join(ROOT, "app", "static", "community-mine.js");
const SESSION = {
  access_token: "access-one",
  refresh_token: "refresh-one",
  expires_at: 2000000000,
  user: { id: "user-a", email: "owner@example.test" },
};

function extractElements(html) {
  const elements = new Map();
  const pattern = /<([a-z0-9]+)\b([^>]*\bid="([^"]+)"[^>]*)>/gi;
  for (const match of html.matchAll(pattern)) {
    const element = new FakeElement(match[1], match[3]);
    element.hidden = /\bhidden\b/i.test(match[2]);
    const className = /\bclass="([^"]+)"/i.exec(match[2]);
    if (className) element.className = className[1];
    const href = /\bhref="([^"]+)"/i.exec(match[2]);
    if (href) {
      element.href = href[1];
      element.setAttribute("href", href[1]);
    }
    const type = /\btype="([^"]+)"/i.exec(match[2]);
    if (type) element.type = type[1];
    for (const dataAttribute of match[2].matchAll(/\bdata-([\w-]+)="([^"]*)"/gi)) {
      const key = dataAttribute[1].replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      element.dataset[key] = dataAttribute[2];
    }
    elements.set(element.id, element);
  }
  return elements;
}

function createLocation(initialHref) {
  let current = new URL(initialHref);
  return {
    get href() { return current.href; },
    set href(next) { current = new URL(String(next), current.href); },
    get origin() { return current.origin; },
    get pathname() { return current.pathname; },
    get search() { return current.search; },
    get hash() { return current.hash; },
    toString() { return current.href; },
    valueOf() { return current.href; },
  };
}

function ownerView(status, overrides = {}) {
  return {
    id: overrides.id || `${status}-11111111-1111-1111-1111-111111111111`,
    title: overrides.title || `${status} 标题`,
    body: "苍山脚下散步，傍晚去洱海看日落。",
    location_name: "云南·大理",
    category: "城市漫步",
    status,
    review_reason: status === "rejected" ? "图片需要重新调整顺序" : null,
    source_trip_id: null,
    submitted_at: status === "draft" ? null : "2026-08-22T09:00:00Z",
    published_at: status === "approved" ? "2026-08-22T10:00:00Z" : null,
    updated_at: "2026-08-22T09:00:00Z",
    deleted_at: null,
    cover_image_url: "https://images.example.test/cover.webp",
    author_display_name: "Voyage 旅行者",
    author_avatar_url: null,
    like_count: 0,
    comment_count: 0,
    images: [{
      id: `${status}-image`,
      storage_path: `user-a/${status}/cover.webp`,
      sort_order: 0,
      width: 1440,
      height: 1920,
    }],
    ...overrides,
  };
}

function createMineHarness(options = {}) {
  const html = fs.readFileSync(HTML_PATH, "utf8");
  const elements = extractElements(html);
  const body = new FakeElement("body", "body");
  const head = new FakeElement("head", "head");
  for (const element of elements.values()) body.append(element);

  const auth = options.auth || new FakeSupabaseAuth({ initialSession: SESSION });
  const fetchCalls = [];
  const location = createLocation("https://travel.example/community/mine");
  const historyCalls = [];

  const document = {
    body,
    head,
    title: "",
    getElementById(id) {
      return elements.get(id) || descendants(body).find((node) => node.id === id) || null;
    },
    createElement(tag) {
      return new FakeElement(tag);
    },
  };

  const fetchImpl = options.fetch || (async (call) => {
    if (call.url === "/api/me/travel-notes") {
      return jsonResponse(200, {
        items: [
          ownerView("draft", { id: "draft-note", title: "待完善的大理草稿" }),
          ownerView("pending_review", { id: "pending-note", title: "待审核的成都夜游" }),
          ownerView("approved", { id: "approved-note", title: "已经公开的阿勒泰" }),
          ownerView("rejected", { id: "rejected-note", title: "被驳回的厦门海边" }),
        ],
      });
    }
    if (call.url.endsWith("/draft-note") && call.options.method === "DELETE") {
      return { ok: true, status: 204, async json() { return {}; } };
    }
    return jsonResponse(500, { detail: { code: "UNEXPECTED" } });
  });

  const urlApi = Object.assign(URL, {
    createObjectURL() { return "blob:unused"; },
    revokeObjectURL() {},
  });
  const windowListeners = new Map();
  const window = {
    document,
    location,
    history: {
      replaceState(state, title, url) {
        const next = new URL(String(url), location.href);
        historyCalls.push({ state, title, url: next.href });
        location.href = next.href;
      },
    },
    addEventListener(type, listener) {
      const listeners = windowListeners.get(type) || [];
      listeners.push(listener);
      windowListeners.set(type, listeners);
    },
    async dispatch(type, properties = {}) {
      const event = { preventDefault() {}, target: window, currentTarget: window, ...properties };
      for (const listener of windowListeners.get(type) || []) await listener(event);
    },
    URL: urlApi,
    TRAVEL_ASSISTANT_CONFIG: {
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "public-anon-placeholder",
    },
    supabase: {
      createClient(url, key, clientOptions) {
        window.supabaseCreate = { url, key, clientOptions };
        return { auth };
      },
    },
  };
  window.location.assign = (next) => { location.href = new URL(String(next), location.href).href; };
  window.location.replace = (next) => { location.href = new URL(String(next), location.href).href; };

  const context = {
    window,
    document,
    navigator: {},
    URL: urlApi,
    URLSearchParams,
    encodeURIComponent,
    decodeURIComponent,
    setTimeout,
    clearTimeout,
    fetch: async (url, requestOptions = {}) => {
      const call = { url: String(url), options: requestOptions };
      fetchCalls.push(call);
      return fetchImpl(call, fetchCalls.length - 1);
    },
  };

  for (const sourcePath of [CLIENT_PATH, MINE_PATH]) {
    const source = fs.readFileSync(sourcePath, "utf8");
    vm.runInNewContext(source, context, { filename: path.basename(sourcePath) });
  }

  return {
    auth,
    elements,
    fetchCalls,
    window,
    async clickTab(status) {
      await elements.get(`community-mine-tab-${status}`).dispatch("click");
      await settle();
    },
    async clickDelete(noteId) {
      const button = descendants(elements.get("community-mine-list"))
        .find((node) => node.tagName === "BUTTON" && node.dataset.noteId === noteId && node.dataset.action === "delete");
      await button.dispatch("click");
      await settle();
    },
  };
}

test("my notes groups owner views by status and only shows rejection reason for rejected notes", async () => {
  const harness = createMineHarness();
  await settle();

  assert.equal(harness.fetchCalls[0].url, "/api/me/travel-notes");
  assert.match(harness.elements.get("community-mine-list").textContent, /待完善的大理草稿/);

  await harness.clickTab("rejected");

  assert.match(harness.elements.get("community-mine-list").textContent, /被驳回的厦门海边/);
  assert.match(harness.elements.get("community-mine-list").textContent, /图片需要重新调整顺序/);
  assert.doesNotMatch(harness.elements.get("community-mine-list").textContent, /待完善的大理草稿/);
});

test("draft cards expose edit and delete actions and remove the note after deletion", async () => {
  const harness = createMineHarness();
  await settle();

  await harness.clickTab("draft");

  assert.match(harness.elements.get("community-mine-list").textContent, /编辑/);
  assert.match(harness.elements.get("community-mine-list").textContent, /删除/);

  await harness.clickDelete("draft-note");

  assert.equal(harness.fetchCalls.at(-1).url, "/api/community/notes/draft-note");
  assert.equal(harness.fetchCalls.at(-1).options.method, "DELETE");
  assert.doesNotMatch(harness.elements.get("community-mine-list").textContent, /待完善的大理草稿/);
});
