const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { FakeElement, FakeSupabaseAuth, descendants, jsonResponse, settle } = require("./dom-harness");

const ROOT = path.resolve(__dirname, "..", "..");
const HTML_PATH = path.join(ROOT, "app", "static", "community-editor.html");
const CLIENT_PATH = path.join(ROOT, "app", "static", "community-client.js");
const EDITOR_PATH = path.join(ROOT, "app", "static", "community-editor.js");
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

function ownerView(overrides = {}) {
  const noteId = overrides.id || "11111111-1111-1111-1111-111111111111";
  const images = overrides.images || [{
    id: "22222222-2222-2222-2222-222222222222",
    storage_path: "user-a/draft-1/cover.webp",
    sort_order: 0,
    width: 1440,
    height: 1920,
  }];
  return {
    id: noteId,
    title: "大理四天三夜",
    body: "苍山脚下散步，傍晚去洱海看日落。",
    location_name: "云南·大理",
    category: "城市漫步",
    status: "draft",
    review_reason: null,
    source_trip_id: null,
    submitted_at: null,
    published_at: null,
    updated_at: "2026-08-22T09:00:00Z",
    deleted_at: null,
    cover_image_url: "https://images.example.test/cover.webp",
    author_display_name: "Voyage 旅行者",
    author_avatar_url: null,
    like_count: 0,
    comment_count: 0,
    images,
    ...overrides,
  };
}

function createCanvas() {
  return {
    width: 0,
    height: 0,
    getContext() {
      return { drawImage() {} };
    },
    toBlob(callback, type) {
      callback(new Blob(["image"], { type: type || "image/webp" }));
    },
  };
}

function makeImageFile(name, type, size, width = 1440, height = 1920) {
  const content = "x".repeat(Math.max(1, Math.min(size, 128)));
  const file = new File([content], name, { type });
  Object.defineProperty(file, "size", { value: size });
  file._width = width;
  file._height = height;
  return file;
}

function createEditorHarness(options = {}) {
  const html = fs.readFileSync(HTML_PATH, "utf8");
  const elements = extractElements(html);
  const body = new FakeElement("body", "body");
  const head = new FakeElement("head", "head");
  for (const element of elements.values()) body.append(element);

  const auth = options.auth || new FakeSupabaseAuth({ initialSession: SESSION });
  const fetchCalls = [];
  const storageUploads = [];
  const storageRemovals = [];
  const objectUrls = [];
  const revokedObjectUrls = [];
  const createImageBitmap = options.createImageBitmap || (async (file) => ({
    width: file._width || 1440,
    height: file._height || 1920,
    close() {},
  }));
  const location = createLocation(`https://travel.example${options.path || "/community/notes/new"}`);
  const historyCalls = [];
  let uuidCounter = 0;

  const document = {
    body,
    head,
    title: "",
    getElementById(id) {
      return elements.get(id) || descendants(body).find((node) => node.id === id) || null;
    },
    createElement(tag) {
      if (String(tag).toLowerCase() === "canvas") return createCanvas();
      return new FakeElement(tag);
    },
  };

  const storageApi = {
    from(bucket) {
      return {
        async upload(pathname, file, uploadOptions = {}) {
          storageUploads.push({ bucket, path: pathname, file, options: uploadOptions });
          if (typeof options.onUpload === "function") return options.onUpload(pathname, file, uploadOptions, storageUploads.length - 1);
          return { data: { path: pathname }, error: null };
        },
        async remove(paths) {
          storageRemovals.push({ bucket, paths });
          return { data: paths, error: null };
        },
      };
    },
  };

  const urlApi = Object.assign(URL, {
    createObjectURL(blob) {
      const next = `blob:preview-${objectUrls.length + 1}`;
      objectUrls.push({ url: next, blob });
      return next;
    },
    revokeObjectURL(url) {
      revokedObjectUrls.push(String(url));
    },
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
      return event;
    },
    crypto: {
      randomUUID() {
        uuidCounter += 1;
        return `00000000-0000-0000-0000-${String(uuidCounter).padStart(12, "0")}`;
      },
    },
    URL: urlApi,
    createImageBitmap,
    TRAVEL_ASSISTANT_CONFIG: {
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "public-anon-placeholder",
    },
    supabase: {
      createClient(url, key, clientOptions) {
        window.supabaseCreate = { url, key, clientOptions };
        return { auth, storage: storageApi };
      },
    },
  };
  window.location.assign = (next) => { location.href = new URL(String(next), location.href).href; };
  window.location.replace = (next) => { location.href = new URL(String(next), location.href).href; };

  const fetchImpl = options.fetch || (async (call, index) => {
    if (call.url === "/api/community/notes" && call.options.method === "POST") {
      const bodyPayload = JSON.parse(call.options.body);
      return jsonResponse(201, ownerView({
        id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        images: bodyPayload.images.map((image, indexValue) => ({
          id: `img-${indexValue}`,
          storage_path: image.storage_path,
          sort_order: image.sort_order,
          width: image.width,
          height: image.height,
        })),
      }));
    }
    if (call.url.startsWith("/api/community/notes/") && call.options.method === "PUT") {
      const bodyPayload = JSON.parse(call.options.body);
      return jsonResponse(200, ownerView({
        id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        images: bodyPayload.images.map((image, indexValue) => ({
          id: `img-${indexValue}`,
          storage_path: image.storage_path,
          sort_order: image.sort_order,
          width: image.width,
          height: image.height,
        })),
      }));
    }
    if (call.url.endsWith("/submit") && call.options.method === "POST") {
      return jsonResponse(200, ownerView({ status: "pending_review" }));
    }
    if (call.url === "/api/me/travel-notes" && (!call.options.method || call.options.method === "GET")) {
      return jsonResponse(200, { items: [] });
    }
    return jsonResponse(500, { detail: { code: `UNEXPECTED_${index}` } });
  });

  const context = {
    window,
    document,
    navigator: {},
    URL: urlApi,
    URLSearchParams,
    Blob,
    File: globalThis.File,
    encodeURIComponent,
    decodeURIComponent,
    setTimeout,
    clearTimeout,
    createImageBitmap,
    fetch: async (url, requestOptions = {}) => {
      const call = { url: String(url), options: requestOptions };
      fetchCalls.push(call);
      return fetchImpl(call, fetchCalls.length - 1);
    },
  };

  for (const sourcePath of [CLIENT_PATH, EDITOR_PATH]) {
    const source = fs.readFileSync(sourcePath, "utf8");
    vm.runInNewContext(source, context, { filename: path.basename(sourcePath) });
  }

  return {
    auth,
    document,
    elements,
    fetchCalls,
    storageUploads,
    storageRemovals,
    objectUrls,
    revokedObjectUrls,
    historyCalls,
    window,
    async addFiles(files) {
      const input = elements.get("community-editor-file-input");
      input.files = files;
      await input.dispatch("change");
      await settle();
    },
    async fillValidDraft() {
      elements.get("community-editor-title").value = "大理四天三夜";
      elements.get("community-editor-location").value = "云南·大理";
      elements.get("community-editor-category").value = "城市漫步";
      elements.get("community-editor-body").value = "苍山脚下散步，傍晚去洱海看日落。";
      await this.addFiles([makeImageFile("cover.png", "image/png", 1024 * 1024)]);
    },
    async saveDraft() {
      await elements.get("community-editor-save").dispatch("click");
      await settle();
    },
    async submitDraft() {
      await elements.get("community-editor-submit").dispatch("click");
      await settle();
    },
  };
}

test("editor rejects unsupported or tenth image before upload", async () => {
  const harness = createEditorHarness();
  const files = Array.from({ length: 10 }, (_value, index) =>
    makeImageFile(`image-${index + 1}.png`, "image/png", 512000),
  );

  await harness.addFiles(files);

  assert.match(harness.elements.get("community-editor-errors").textContent, /最多上传 9 张/);
  assert.equal(harness.storageUploads.length, 0);
});

test("save creates an empty draft before uploading with the real note id and submit transitions to pending review", async () => {
  const harness = createEditorHarness();

  await harness.fillValidDraft();
  await harness.saveDraft();
  await harness.submitDraft();

  assert.equal(harness.fetchCalls[0].url, "/api/community/notes");
  assert.equal(harness.fetchCalls[0].options.method, "POST");
  const createdBody = JSON.parse(harness.fetchCalls[0].options.body);
  assert.deepEqual(createdBody.images, []);
  assert.match(harness.storageUploads[0].path, /^user-a\/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\/[0-9a-f-]+[.]webp$/);
  assert.equal(harness.fetchCalls[1].options.method, "PUT");
  const manifest = JSON.parse(harness.fetchCalls[1].options.body).images;
  assert.deepEqual(manifest.map((image) => image.sort_order), [0]);
  assert.match(manifest[0].storage_path, /^user-a\/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa\/[0-9a-f-]+[.]webp$/);
  assert.equal(harness.fetchCalls[2].options.method, "POST");
  assert.match(harness.fetchCalls[2].url, /\/submit$/);
  assert.match(harness.elements.get("community-editor-status").textContent, /待审核|审核/);
});

test("save removes newly uploaded objects when the draft update fails", async () => {
  const harness = createEditorHarness({
    fetch: async (call, index) => {
      if (call.url === "/api/community/notes" && call.options.method === "POST") {
        return jsonResponse(201, ownerView({
          id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
          images: [],
        }));
      }
      if (call.url.startsWith("/api/community/notes/") && call.options.method === "PUT") {
        return jsonResponse(503, { detail: { code: "TRAVEL_NOTE_UNAVAILABLE" } });
      }
      return jsonResponse(500, { detail: { code: `UNEXPECTED_${index}` } });
    },
  });

  await harness.fillValidDraft();
  await harness.saveDraft();

  assert.equal(harness.storageUploads.length, 1);
  assert.equal(harness.storageRemovals.length, 1);
  assert.deepEqual(Array.from(harness.storageRemovals[0].paths), [harness.storageUploads[0].path]);
  assert.match(harness.elements.get("community-editor-errors").textContent, /保存失败/);
  assert.match(harness.elements.get("community-editor-status").textContent, /保存失败/);
});
test("save redirects to sign in when the auth session expires before upload starts", async () => {
  const harness = createEditorHarness();

  await harness.fillValidDraft();
  harness.auth.session = null;
  harness.auth.emit("SIGNED_OUT", null);
  await settle();
  await harness.saveDraft();

  assert.equal(harness.storageUploads.length, 0);
  assert.equal(harness.window.location.pathname, "/auth");
  assert.match(harness.window.location.search, /return_to=%2Fcommunity%2Fnotes%2Fnew/);
});
