const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeElement {
  constructor(tagName = "div", id = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this.value = "";
    this.className = "";
    this.classList = {
      toggle: (name, force) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        if (force) classes.add(name);
        else classes.delete(name);
        this.className = [...classes].join(" ");
        return force;
      },
      contains: (name) => this.className.split(/\s+/).filter(Boolean).includes(name),
    };
    this.type = "";
    this.name = "";
    this.href = "";
    this.rel = "";
    this.target = "";
    this.src = "";
    this.async = false;
    this.alt = "";
    this.checked = false;
    this.files = [];
    this.required = false;
    this.readOnly = false;
    this._text = "";
  }

  set textContent(value) {
    this._text = String(value ?? "");
    this.children = [];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] || null; }

  append(...nodes) {
    for (const node of nodes) {
      const child = node instanceof FakeElement ? node : Object.assign(new FakeElement("#text"), { textContent: node });
      child.parentNode = this;
      this.children.push(child);
    }
  }

  removeChild(node) {
    const index = this.children.indexOf(node);
    if (index >= 0) this.children.splice(index, 1);
    node.parentNode = null;
    return node;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) || null; }
  removeAttribute(name) { this.attributes.delete(name); }

  async dispatch(type) {
    if (type === "click" && this.disabled) return;
    const event = { preventDefault() {}, target: this, currentTarget: this };
    for (const listener of this.listeners.get(type) || []) await listener(event);
  }

  focus() { this.focused = true; }
  select() { this.selected = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }

  closest(selector) {
    let node = this;
    while (node) {
      if (selector === "section" && node.tagName === "SECTION") return node;
      node = node.parentNode;
    }
    return null;
  }

  querySelectorAll(selector) {
    const tags = new Set(selector.split(",").map((value) => value.trim().toUpperCase()));
    return descendants(this).filter((node) => tags.has(node.tagName));
  }
}

function descendants(root) {
  const output = [];
  for (const child of root.children) {
    output.push(child, ...descendants(child));
  }
  return output;
}

function buildDocument(html) {
  const elements = new Map();
  const created = [];
  const pattern = /<([a-z0-9]+)\b([^>]*\bid="([^"]+)"[^>]*)>/gi;
  for (const match of html.matchAll(pattern)) {
    const element = new FakeElement(match[1], match[3]);
    element.hidden = /\bhidden\b/i.test(match[2]);
    const className = /\bclass="([^"]+)"/i.exec(match[2]);
    if (className) element.className = className[1];
    for (const dataAttribute of match[2].matchAll(/\bdata-([\w-]+)="([^"]*)"/gi)) {
      const name = dataAttribute[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      element.dataset[name] = dataAttribute[2];
    }
    for (const attribute of match[2].matchAll(/\b(aria-[\w-]+)="([^"]*)"/gi)) element.setAttribute(attribute[1], attribute[2]);
    for (const attributeName of ["href", "tabindex", "name", "role", "for"]) {
      const attribute = new RegExp(`\\b${attributeName}="([^"]*)"`, "i").exec(match[2]);
      if (attribute) {
        element.setAttribute(attributeName, attribute[1]);
        if (attributeName === "href") element.href = attribute[1];
        if (attributeName === "name") element.name = attribute[1];
      }
    }
    const type = /\btype="([^"]+)"/i.exec(match[2]);
    if (type) element.type = type[1];
    const value = /\bvalue="([^"]*)"/i.exec(match[2]);
    if (value) element.value = value[1];
    element.required = /\brequired\b/i.test(match[2]);
    element.readOnly = /\breadonly\b/i.test(match[2]);
    element.disabled = /\bdisabled\b/i.test(match[2]);
    element.checked = /\bchecked\b/i.test(match[2]);
    elements.set(element.id, element);
    created.push(element);
  }
  const body = new FakeElement("body", "body");
  const head = new FakeElement("head", "head");
  const parents = {
    "email": "auth-form", "password": "auth-form", "sign-in-button": "auth-form", "sign-up-button": "auth-form",
    "auth-page-email": "auth-page-form", "auth-page-password": "auth-page-form", "auth-page-submit": "auth-page-form", "auth-page-alternate": "auth-page-form",
    "account-email": "account-summary", "profile-page-link": "account-summary", "sign-out-button": "account-summary",
    "chat-messages": "chat-panel", "chat-form": "chat-panel", "trip-history-list": "trip-history", "trip-history": "trips-page",
    "profile-confirmation": "explore-output", "trip-view": "explore-output", "profile-fields": "profile-confirmation",
    "trip-content": "trip-view", "trip-actions": "trip-view",
    "share-link": "share-dialog", "share-expiry": "share-dialog", "rename-input": "rename-dialog",
    "profile-avatar-image": "profile-avatar-preview", "profile-avatar-fallback": "profile-avatar-preview",
    "profile-avatar-input": "profile-form", "profile-email": "profile-form", "profile-display-name": "profile-form", "profile-bio": "profile-form",
    "profile-home-city": "profile-form", "travel-style-food": "profile-form", "travel-style-culture": "profile-form",
    "travel-style-nature": "profile-form", "travel-style-family": "profile-form", "travel-style-outdoor": "profile-form",
    "travel-style-leisure": "profile-form", "profile-errors": "profile-form", "profile-updated-at": "profile-form",
    "profile-save-button": "profile-form", "profile-retry-button": "profile-error",
    "community-page-title": "community-page", "community-feed-list": "community-page", "community-feed-status": "community-page",
    "community-retry-button": "community-page", "community-load-more-button": "community-page", "community-signin-button": "community-page",
    "community-publish-form": "community-page", "community-trip-select": "community-publish-form", "community-summary": "community-publish-form",
    "community-summary-help": "community-publish-form", "community-publish-button": "community-publish-form",
    "community-publish-feedback": "community-publish-form", "community-publish-empty": "community-publish-form",
    "community-publish-retry": "community-publish-form", "community-detail-title": "community-page",
    "community-detail-empty": "community-page", "community-detail-loading": "community-page", "community-detail-error": "community-page",
    "community-detail-card": "community-page", "community-detail-author": "community-detail-card",
    "community-detail-post-title": "community-detail-card",
    "community-detail-meta": "community-detail-card", "community-detail-summary": "community-detail-card",
    "community-detail-itinerary": "community-detail-card", "community-detail-back": "community-detail-card",
    "community-withdraw-button": "community-detail-card",
  };
  for (const [childId, parentId] of Object.entries(parents)) {
    const parent = elements.get(parentId);
    const child = elements.get(childId);
    if (parent && child) parent.append(child);
  }
  for (const element of elements.values()) if (!element.parentNode) body.append(element);
  const document = {
    body, head,
    title: "",
    getElementById(id) {
      return elements.get(id) || descendants(body).find((node) => node.id === id) || null;
    },
    createElement(tag) { const element = new FakeElement(tag); created.push(element); return element; },
    querySelectorAll(selector) {
      const tags = new Set(selector.split(",").map((value) => value.trim().toUpperCase()));
      return created.filter((node) => tags.has(node.tagName));
    },
  };
  return { document, elements, created };
}

class FakeSupabaseAuth {
  constructor({ initialSession = null, loginSession = null, refreshedSession = null } = {}) {
    this.session = initialSession;
    this.loginSession = loginSession;
    this.refreshedSession = refreshedSession;
    this.listeners = [];
    this.refreshCalls = 0;
    this.signOutCalls = 0;
  }

  async getSession() { return { data: { session: this.session }, error: null }; }
  onAuthStateChange(listener) {
    this.listeners.push(listener);
    return { data: { subscription: { unsubscribe() {} } } };
  }
  async signInWithPassword() {
    this.session = this.loginSession;
    return this.session ? { data: { session: this.session, user: this.session.user }, error: null } : { data: {}, error: new Error("invalid") };
  }
  async signUp() { return this.signInWithPassword(); }
  async signOut() { this.signOutCalls += 1; this.session = null; return { error: null }; }
  async refreshSession() {
    this.refreshCalls += 1;
    if (!this.refreshedSession) return { data: { session: null }, error: new Error("refresh failed") };
    this.session = this.refreshedSession;
    return { data: { session: this.session }, error: null };
  }
  emit(event, session) { for (const listener of this.listeners) listener(event, session); }
}

function jsonResponse(status, payload) {
  return { ok: status >= 200 && status < 300, status, async json() { return payload; } };
}

async function settle(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) await new Promise((resolve) => setImmediate(resolve));
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

function createHarness(options = {}) {
  const root = path.resolve(__dirname, "..", "..");
  const page = options.page || "app";
  const pageConfigs = {
    app: {
      defaultPath: "/",
      html: path.join(root, "app", "static", "index.html"),
      scripts: [
        path.join(root, "app", "static", "data", "explore-data.js"),
        path.join(root, "app", "static", "map-explorer.js"),
        path.join(root, "app", "static", "app.js"),
      ],
    },
    auth: {
      defaultPath: "/auth",
      html: path.join(root, "app", "static", "auth.html"),
      scripts: [path.join(root, "app", "static", "auth.js")],
    },
    profile: {
      defaultPath: "/profile",
      html: path.join(root, "app", "static", "profile.html"),
      scripts: [path.join(root, "app", "static", "profile.js")],
    },
    community: {
      defaultPath: "/community",
      html: path.join(root, "app", "static", "community.html"),
      scripts: [
        path.join(root, "app", "static", "community-client.js"),
        path.join(root, "app", "static", "community-feed.js"),
      ],
    },
    "community-note": {
      defaultPath: "/community/notes/note-1",
      html: path.join(root, "app", "static", "community-note.html"),
      scripts: [
        path.join(root, "app", "static", "community-client.js"),
        path.join(root, "app", "static", "community-note.js"),
      ],
    },
    "community-admin": {
      defaultPath: "/admin/community",
      html: path.join(root, "app", "static", "admin-community.html"),
      scripts: [
        path.join(root, "app", "static", "community-client.js"),
        path.join(root, "app", "static", "admin-community.js"),
      ],
    },
  };
  const pageConfig = pageConfigs[page];
  if (!pageConfig) throw new Error(`Unknown harness page: ${page}`);
  const html = fs.readFileSync(pageConfig.html, "utf8");
  const sources = pageConfig.scripts.map((sourcePath) => ({
    filename: path.basename(sourcePath),
    source: fs.readFileSync(sourcePath, "utf8"),
  }));
  const { document, elements, created } = buildDocument(html);
  const auth = options.auth || new FakeSupabaseAuth();
  const fetchCalls = [];
  const fetchImpl = options.fetch || (async () => jsonResponse(200, {}));
  const objectUrls = [];
  const revokedObjectUrls = [];
  const storage = options.storage || {
    from_() {
      return {
        async upload() { throw new Error("unexpected storage upload"); },
        async remove() { return { data: [], error: null }; },
      };
    },
  };
  const confirmCalls = [];
  const confirmImpl = typeof options.confirm === "function" ? options.confirm : () => true;
  let uuid = 0;
  const location = createLocation(`https://travel.example${options.path || pageConfig.defaultPath}${options.search || ""}${options.hash || ""}`);
  const historyCalls = [];
  const urlApi = Object.assign(URL, {
    createObjectURL(blob) {
      const next = `blob:mock-${objectUrls.length + 1}`;
      objectUrls.push({ url: next, blob });
      return next;
    },
    revokeObjectURL(url) {
      revokedObjectUrls.push(String(url));
    },
  });
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
    crypto: { randomUUID() { uuid += 1; return `thread-${uuid}`; } },
    confirm: (message) => {
      confirmCalls.push(String(message));
      return confirmImpl(message);
    },
    TRAVEL_ASSISTANT_CONFIG: {
      amapJsKey: null, amapSecurityJsCode: null,
      supabaseUrl: "https://project.supabase.co", supabaseAnonKey: "public-anon-placeholder",
    },
    supabase: {
      createClient(url, key, clientOptions) {
        window.supabaseCreate = { url, key, clientOptions };
        return { auth, storage };
      },
    },
  };
  window.URL = urlApi;
  window.createImageBitmap = options.createImageBitmap || (async () => {
    throw new Error("createImageBitmap not configured");
  });
  window.location.assign = (next) => { location.href = new URL(String(next), location.href).href; };
  window.location.replace = (next) => { location.href = new URL(String(next), location.href).href; };
  const windowListeners = new Map();
  window.addEventListener = (type, listener) => {
    const listeners = windowListeners.get(type) || [];
    listeners.push(listener);
    windowListeners.set(type, listeners);
  };
  window.dispatch = async (type, properties = {}) => {
    const event = { preventDefault() {}, target: window, currentTarget: window, ...properties };
    for (const listener of windowListeners.get(type) || []) await listener(event);
  };
  const originalCreateElement = document.createElement.bind(document);
  document.createElement = (tag) => {
    if (String(tag).toLowerCase() === "canvas" && typeof options.createCanvas === "function") {
      return options.createCanvas();
    }
    return originalCreateElement(tag);
  };
  const context = {
    window, document, navigator: { clipboard: { async writeText() {} } }, URL: urlApi, URLSearchParams, Blob, File: globalThis.File, Date, Math, JSON,
    encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
    createImageBitmap: window.createImageBitmap,
    fetch: async (url, requestOptions = {}) => {
      const call = { url: String(url), options: requestOptions };
      fetchCalls.push(call);
      return fetchImpl(call, fetchCalls.length - 1);
    },
  };
  for (const { source, filename } of sources) {
    vm.runInNewContext(source, context, { filename });
  }
  return {
    auth,
    document,
    elements,
    created,
    fetchCalls,
    historyCalls,
    window,
    settle,
    jsonResponse,
    confirmCalls,
    objectUrls,
    revokedObjectUrls,
    storage,
  };
}

function findByText(root, text) {
  return descendants(root).find((node) => node.textContent === text);
}

module.exports = { FakeElement, FakeSupabaseAuth, createHarness, descendants, findByText, jsonResponse, settle };
