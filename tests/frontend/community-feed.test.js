const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { FakeElement, FakeSupabaseAuth, descendants, jsonResponse, settle } = require("./dom-harness");

const ROOT = path.resolve(__dirname, "..", "..");
const HTML_PATH = path.join(ROOT, "app", "static", "community.html");
const CLIENT_PATH = path.join(ROOT, "app", "static", "community-client.js");
const FEED_PATH = path.join(ROOT, "app", "static", "community-feed.js");
const COMMUNITY_CATEGORIES = ["全部", "摄影控", "美食地图", "独自旅行", "城市漫步", "自然风光", "亲子游"];
const SIGNED_IN_SESSION = {
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
    const value = /\bvalue="([^"]*)"/i.exec(match[2]);
    if (value) element.value = value[1];
    const name = /\bname="([^"]+)"/i.exec(match[2]);
    if (name) {
      element.name = name[1];
      element.setAttribute("name", name[1]);
    }
    const placeholder = /\bplaceholder="([^"]*)"/i.exec(match[2]);
    if (placeholder) element.setAttribute("placeholder", placeholder[1]);
    for (const dataAttribute of match[2].matchAll(/\bdata-([\w-]+)="([^"]*)"/gi)) {
      const key = dataAttribute[1].replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      element.dataset[key] = dataAttribute[2];
    }
    for (const attribute of match[2].matchAll(/\b(aria-[\w-]+)="([^"]*)"/gi)) {
      element.setAttribute(attribute[1], attribute[2]);
    }
    elements.set(element.id, element);
  }
  return elements;
}

function connectCommunityParents(elements) {
  const body = new FakeElement("body", "body");
  const head = new FakeElement("head", "head");
  const groups = [
    ["community-skip-link", "community-header", "community-search-form", "community-search-input", "community-search-submit", "community-auth-link"],
    ["community-main", "community-hero-title", "community-hero-copy", "community-filter-label", "community-filters", "community-search-label", "community-status", "community-grid", "community-empty", "community-error", "community-error-message", "community-retry", "community-load-more", "community-create-link"],
  ];
  for (const ids of groups) {
    const [parentId, ...childIds] = ids;
    const parent = elements.get(parentId);
    if (!parent) continue;
    for (const childId of childIds) {
      const child = elements.get(childId);
      if (child) parent.append(child);
    }
  }
  const filters = elements.get("community-filters");
  for (const category of COMMUNITY_CATEGORIES) {
    const button = elements.get(`community-filter-${category === "全部" ? "all" : category}`);
    if (filters && button) filters.append(button);
  }
  for (const element of elements.values()) {
    if (!element.parentNode) body.append(element);
  }
  return { body, head };
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

function createCommunityHarness(options = {}) {
  const html = fs.readFileSync(HTML_PATH, "utf8");
  const elements = extractElements(html);
  const { body, head } = connectCommunityParents(elements);
  const created = [];
  const innerHtmlWrites = [];
  const auth = options.auth || new FakeSupabaseAuth();
  const fetchCalls = [];
  const fetchImpl = options.fetch || (async () => jsonResponse(200, { items: [], next_cursor: null }));
  const location = createLocation(`https://travel.example${options.path || "/community"}${options.search || ""}`);
  const historyCalls = [];

  for (const element of elements.values()) {
    let innerHtmlValue = "";
    Object.defineProperty(element, "innerHTML", {
      configurable: true,
      get() { return innerHtmlValue; },
      set(next) {
        innerHtmlValue = String(next ?? "");
        innerHtmlWrites.push({ id: element.id, value: innerHtmlValue });
      },
    });
  }

  const document = {
    body,
    head,
    title: "",
    getElementById(id) {
      return elements.get(id) || descendants(body).find((node) => node.id === id) || null;
    },
    createElement(tag) {
      const element = new FakeElement(tag);
      let innerHtmlValue = "";
      Object.defineProperty(element, "innerHTML", {
        configurable: true,
        get() { return innerHtmlValue; },
        set(next) {
          innerHtmlValue = String(next ?? "");
          innerHtmlWrites.push({ id: element.id, value: innerHtmlValue });
        },
      });
      created.push(element);
      return element;
    },
    querySelectorAll(selector) {
      const tags = new Set(selector.split(",").map((value) => value.trim().toUpperCase()));
      return [body, ...descendants(body)].filter((node) => tags.has(node.tagName));
    },
  };

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
    crypto: { randomUUID() { return "client-uuid"; } },
    URL: urlApi,
    TRAVEL_ASSISTANT_CONFIG: {
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "public-anon-placeholder",
      ...options.runtimeConfig,
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
    URL: urlApi,
    URLSearchParams,
    navigator: {},
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

  for (const sourcePath of [CLIENT_PATH, FEED_PATH]) {
    const source = fs.readFileSync(sourcePath, "utf8");
    vm.runInNewContext(source, context, { filename: path.basename(sourcePath) });
  }

  return {
    auth,
    created,
    document,
    elements,
    fetchCalls,
    historyCalls,
    innerHtmlWrites,
    window,
  };
}

function communityNote(overrides = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    creator_slug: "voyage-traveler",
    title: "成都夏夜散步",
    location_name: "四川·成都",
    category: "城市漫步",
    excerpt: "傍晚从宽窄巷子一路走到玉林，最后在小店吃了冰粉。",
    cover_image_url: "https://images.example.test/chengdu-cover.webp",
    cover_image_alt: "成都夜色",
    author_display_name: "Voyage 旅行者",
    author_avatar_url: "https://images.example.test/avatar.webp",
    published_at: "2026-08-21T10:00:00Z",
    like_count: 18,
    comment_count: 4,
    viewer_has_liked: false,
    ...overrides,
  };
}

function feedPage(items, nextCursor = null) {
  return { items, next_cursor: nextCursor };
}

function buttonByText(root, text) {
  return descendants(root).find((node) => node.tagName === "BUTTON" && node.textContent === text) || null;
}

test("feed renders approved notes as safe masonry cards for anonymous readers", async () => {
  const first = communityNote({
    title: "<img src=x onerror=alert(1)>城市散步",
    cover_image_url: "javascript:alert(1)",
    author_avatar_url: "javascript:alert(2)",
  });
  const second = communityNote({
    id: "22222222-2222-2222-2222-222222222222",
    creator_slug: "mountain-walker",
    title: "阿勒泰日出",
    category: "自然风光",
    cover_image_url: "https://images.example.test/altay-cover.webp",
    author_avatar_url: null,
    like_count: 26,
    comment_count: 7,
  });
  const harness = createCommunityHarness({
    fetch: async () => jsonResponse(200, feedPage([first, second])),
  });
  await settle();

  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/community/notes?limit=20");
  assert.equal(harness.fetchCalls[0].options.headers && harness.fetchCalls[0].options.headers.Authorization, undefined);
  assert.equal(harness.elements.get("community-grid").children.length, 2);
  assert.match(harness.elements.get("community-grid").textContent, /城市散步/);
  assert.match(harness.elements.get("community-grid").textContent, /阿勒泰日出/);
  assert.match(harness.elements.get("community-grid").textContent, /18/);
  assert.match(harness.elements.get("community-grid").textContent, /7/);
  assert.equal(harness.innerHtmlWrites.length, 0);

  const images = descendants(harness.elements.get("community-grid")).filter((node) => node.tagName === "IMG");
  assert.equal(images.some((image) => image.src === "javascript:alert(1)"), false);
  assert.equal(images.some((image) => image.src === "javascript:alert(2)"), false);
  assert.equal(images.some((image) => image.src === "https://images.example.test/altay-cover.webp"), true);
  assert.equal(images.every((image) => image.getAttribute("loading") === "lazy"), true);

  const detailLinks = descendants(harness.elements.get("community-grid"))
    .filter((node) => node.tagName === "A" && node.href.includes("/community/notes/"));
  assert.equal(detailLinks.length, 2);
  assert.equal(detailLinks[0].href, "/community/notes/11111111-1111-1111-1111-111111111111");
  assert.equal(harness.elements.get("community-create-link").href, "/community/notes/new");
});

test("category and search changes reset cursor state and issue filtered requests", async () => {
  const harness = createCommunityHarness({
    fetch: async (call, index) => {
      if (index === 0) return jsonResponse(200, feedPage([communityNote()], "cursor-a"));
      if (call.url.includes("q=%E5%A4%A7%E7%90%86")) {
        return jsonResponse(200, feedPage([communityNote({ title: "搜索结果" })]));
      }
      if (call.url.includes("category=%E5%9F%8E%E5%B8%82%E6%BC%AB%E6%AD%A5")) {
        return jsonResponse(200, feedPage([communityNote({ title: "分类结果" })], "cursor-b"));
      }
      return jsonResponse(500, { detail: { code: "UNEXPECTED" } });
    },
  });
  await settle();

  await harness.elements.get("community-filter-城市漫步").dispatch("click");
  await settle();

  assert.equal(
    harness.fetchCalls[1].url,
    "/api/community/notes?limit=20&category=%E5%9F%8E%E5%B8%82%E6%BC%AB%E6%AD%A5",
  );
  assert.equal(harness.elements.get("community-load-more").hidden, false);

  harness.elements.get("community-search-input").value = "  大理  ";
  await harness.elements.get("community-search-form").dispatch("submit");
  await settle();

  assert.equal(
    harness.fetchCalls[2].url,
    "/api/community/notes?limit=20&category=%E5%9F%8E%E5%B8%82%E6%BC%AB%E6%AD%A5&q=%E5%A4%A7%E7%90%86",
  );
  assert.match(harness.elements.get("community-grid").textContent, /搜索结果/);
  assert.equal(harness.elements.get("community-load-more").hidden, true);
  assert.equal(new URL(harness.window.location.href).searchParams.get("q"), "大理");
});

test("load more appends older pages without dropping newer cards", async () => {
  const newer = communityNote({ title: "较新的游记" });
  const older = communityNote({
    id: "33333333-3333-3333-3333-333333333333",
    creator_slug: "older-note",
    title: "较早的游记",
  });
  const harness = createCommunityHarness({
    fetch: async (call) => {
      if (call.url === "/api/community/notes?limit=20") return jsonResponse(200, feedPage([newer], "cursor-1"));
      if (call.url === "/api/community/notes?limit=20&cursor=cursor-1") return jsonResponse(200, feedPage([older]));
      return jsonResponse(500, { detail: { code: "UNEXPECTED" } });
    },
  });
  await settle();

  await harness.elements.get("community-load-more").dispatch("click");
  await settle();

  assert.equal(harness.fetchCalls[1].url, "/api/community/notes?limit=20&cursor=cursor-1");
  assert.match(harness.elements.get("community-grid").textContent, /较新的游记/);
  assert.match(harness.elements.get("community-grid").textContent, /较早的游记/);
  assert.equal(harness.elements.get("community-grid").children.length, 2);
  assert.equal(harness.elements.get("community-load-more").hidden, true);
});

test("stale request generations cannot overwrite a newer filtered result", async () => {
  let resolveInitial;
  let resolveFiltered;
  const initialResponse = new Promise((resolve) => { resolveInitial = resolve; });
  const filteredResponse = new Promise((resolve) => { resolveFiltered = resolve; });
  const harness = createCommunityHarness({
    fetch: async (call, index) => {
      if (index === 0) return initialResponse;
      if (call.url.includes("category=%E8%87%AA%E7%84%B6%E9%A3%8E%E5%85%89")) return filteredResponse;
      return jsonResponse(500, { detail: { code: "UNEXPECTED" } });
    },
  });
  await settle(1);

  const filtering = harness.elements.get("community-filter-自然风光").dispatch("click");
  await settle(1);
  resolveFiltered(jsonResponse(200, feedPage([communityNote({ title: "新的筛选结果", category: "自然风光" })])));
  await filtering;
  await settle();
  assert.match(harness.elements.get("community-grid").textContent, /新的筛选结果/);

  resolveInitial(jsonResponse(200, feedPage([communityNote({ title: "过时的旧结果" })])));
  await settle();

  assert.match(harness.elements.get("community-grid").textContent, /新的筛选结果/);
  assert.doesNotMatch(harness.elements.get("community-grid").textContent, /过时的旧结果/);
});

test("error and empty states are retryable, and signed-out interactions redirect to sign-in", async () => {
  let attempts = 0;
  const harness = createCommunityHarness({
    fetch: async () => {
      attempts += 1;
      if (attempts === 1) return jsonResponse(503, { detail: { code: "COMMUNITY_UNAVAILABLE" } });
      return jsonResponse(200, feedPage([communityNote({ title: "可互动的卡片" })]));
    },
  });
  await settle();

  assert.match(harness.elements.get("community-error-message").textContent, /暂不可用|加载失败/);
  assert.equal(harness.elements.get("community-retry").hidden, false);

  await harness.elements.get("community-retry").dispatch("click");
  await settle();

  assert.equal(attempts, 2);
  assert.match(harness.elements.get("community-grid").textContent, /可互动的卡片/);

  const likeButton = buttonByText(harness.elements.get("community-grid"), "点赞");
  assert.ok(likeButton, "card like button should be present");
  await likeButton.dispatch("click");

  assert.equal(harness.window.location.pathname, "/auth");
  const params = new URLSearchParams(harness.window.location.search);
  assert.equal(params.get("mode"), "signin");
  assert.equal(params.get("return_to"), "/community");
});

test("signed-in readers keep create and interaction links inside the standalone community flow", async () => {
  const harness = createCommunityHarness({
    auth: new FakeSupabaseAuth({ initialSession: SIGNED_IN_SESSION }),
    fetch: async () => jsonResponse(200, feedPage([communityNote()])),
  });
  await settle();

  await harness.elements.get("community-create-link").dispatch("click");
  assert.equal(harness.window.location.pathname, "/community/notes/new");

  harness.window.location.href = "https://travel.example/community";
  const likeButton = buttonByText(harness.elements.get("community-grid"), "点赞");
  await likeButton.dispatch("click");

  assert.equal(harness.window.location.pathname, "/community/notes/11111111-1111-1111-1111-111111111111");
});
