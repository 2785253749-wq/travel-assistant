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
    };
    this.type = "";
    this.href = "";
    this.rel = "";
    this.target = "";
    this.src = "";
    this.async = false;
    this.alt = "";
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
    for (let node = this; node; node = node.parentNode) {
      event.currentTarget = node;
      for (const listener of node.listeners.get(type) || []) await listener(event);
    }
  }

  requestSubmit() { return this.dispatch("submit"); }

  focus() { this.focused = true; }
  select() { this.selected = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }

  closest(selector) {
    let node = this;
    while (node) {
      if (selector === "section" && node.tagName === "SECTION") return node;
      const dataSelector = /^\[data-([\w-]+)\]$/.exec(selector);
      if (dataSelector) {
        const key = dataSelector[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        if (Object.prototype.hasOwnProperty.call(node.dataset, key)) return node;
      }
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
    for (const attributeName of ["href", "tabindex"]) {
      const attribute = new RegExp(`\\b${attributeName}="([^"]*)"`, "i").exec(match[2]);
      if (attribute) element.setAttribute(attributeName, attribute[1]);
    }
    const type = /\btype="([^"]+)"/i.exec(match[2]);
    if (type) element.type = type[1];
    elements.set(element.id, element);
    created.push(element);
  }
  const body = new FakeElement("body", "body");
  const head = new FakeElement("head", "head");
  const parents = {
    "chat-messages": "chat-panel", "chat-form": "chat-panel", "trip-history-list": "trip-history", "trip-history": "trips-page",
    "profile-confirmation": "explore-output", "trip-view": "explore-output", "profile-fields": "profile-confirmation",
    "trip-content": "trip-view", "trip-actions": "trip-view",
    "share-link": "share-dialog", "share-expiry": "share-dialog", "rename-input": "rename-dialog",
  };
  for (const [childId, parentId] of Object.entries(parents)) elements.get(parentId).append(elements.get(childId));
  for (const element of elements.values()) if (!element.parentNode) body.append(element);
  const document = {
    body, head,
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

function createHarness(options = {}) {
  const root = path.resolve(__dirname, "..", "..");
  const html = fs.readFileSync(path.join(root, "app", "static", "index.html"), "utf8");
  const exploreDataSource = fs.readFileSync(path.join(root, "app", "static", "data", "explore-data.js"), "utf8");
  const mapExplorerSource = fs.readFileSync(path.join(root, "app", "static", "map-explorer.js"), "utf8");
  const footprintsSource = fs.readFileSync(path.join(root, "app", "static", "footprints.js"), "utf8");
  const source = fs.readFileSync(path.join(root, "app", "static", "app.js"), "utf8");
  const { document, elements, created } = buildDocument(html);
  const auth = options.auth || new FakeSupabaseAuth();
  const fetchCalls = [];
  const fetchImpl = options.fetch || (async () => jsonResponse(200, {}));
  let uuid = 0;
  const location = new URL(`https://travel.example/${options.hash || ""}`);
  const historyCalls = [];
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
    confirm: () => true,
    TRAVEL_ASSISTANT_CONFIG: {
      amapJsKey: null, amapSecurityJsCode: null,
      supabaseUrl: "https://project.supabase.co", supabaseAnonKey: "public-anon-placeholder",
    },
    supabase: {
      createClient(url, key, clientOptions) {
        window.supabaseCreate = { url, key, clientOptions };
        return { auth };
      },
    },
  };
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
  const context = {
    window, document, navigator: { clipboard: { async writeText() {} } }, URL, Date, Math, JSON,
    encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout, AbortController,
    fetch: async (url, requestOptions = {}) => {
      const call = { url: String(url), options: requestOptions };
      fetchCalls.push(call);
      return fetchImpl(call, fetchCalls.length - 1);
    },
  };
  vm.runInNewContext(exploreDataSource, context, { filename: "explore-data.js" });
  vm.runInNewContext(mapExplorerSource, context, { filename: "map-explorer.js" });
  vm.runInNewContext(footprintsSource, context, { filename: "footprints.js" });
  vm.runInNewContext(source, context, { filename: "app.js" });
  return { auth, document, elements, created, fetchCalls, historyCalls, window, settle, jsonResponse };
}

function findByText(root, text) {
  return descendants(root).find((node) => node.textContent === text);
}

module.exports = { FakeElement, FakeSupabaseAuth, createHarness, descendants, findByText, jsonResponse, settle };
