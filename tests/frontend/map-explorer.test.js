const assert = require("node:assert/strict");
const test = require("node:test");
const { FakeElement, descendants, findByText } = require("./dom-harness");

function withBrowser(testBody) {
  return async () => {
    const originalWindow = global.window;
    const originalDocument = global.document;
    const originalFetch = global.fetch;
    const head = new FakeElement("head");
    global.window = { setTimeout, clearTimeout };
    global.document = {
      head,
      createElement: (tag) => new FakeElement(tag),
    };
    try {
      await testBody({ head });
    } finally {
      global.window = originalWindow;
      global.document = originalDocument;
      global.fetch = originalFetch;
    }
  };
}

function withControlledBrowser(testBody) {
  return async () => {
    const originalWindow = global.window;
    const originalDocument = global.document;
    const timers = [];
    const clearedTimers = [];
    const head = new FakeElement("head");
    global.window = {
      setTimeout(callback) { timers.push(callback); return timers.length - 1; },
      clearTimeout(timer) { clearedTimers.push(timer); },
    };
    global.document = { head, createElement: (tag) => new FakeElement(tag) };
    try {
      await testBody({ head, timers, clearedTimers, window: global.window });
    } finally {
      global.window = originalWindow;
      global.document = originalDocument;
    }
  };
}

test("offline explorer drills from Fujian to Xiamen places without a network request", withBrowser(async () => {
  const { createMapExplorer } = require("../../app/static/map-explorer.js");
  const root = new FakeElement("section");
  const selections = [];
  global.fetch = () => { throw new Error("offline explorer must not fetch"); };
  const explorer = createMapExplorer(root, { amapKey: null, onSelect: (value) => selections.push(value) });

  explorer.showProvince("fujian");
  assert.equal(root.dataset.mapLevel, "province");
  explorer.showCity("xiamen");
  assert.equal(root.dataset.mapLevel, "city");
  assert.match(root.textContent, /鼓浪屿/);
  assert.equal(selections.at(-1).kind, "city");
}));

test("trial data includes both provinces, all trial cities, and three coordinate places each", withBrowser(async () => {
  const { EXPLORE_TRIAL } = require("../../app/static/map-explorer.js");
  assert.deepEqual(EXPLORE_TRIAL.provinces.map((province) => province.id), ["fujian", "yunnan"]);
  assert.deepEqual(EXPLORE_TRIAL.cities.map((city) => city.id), ["xiamen", "fuzhou", "dali", "lijiang"]);
  for (const city of EXPLORE_TRIAL.cities) {
    assert.equal(city.places.length, 3);
    for (const place of city.places) {
      assert.equal(typeof place.name, "string");
      assert.equal(place.coordinates.length, 2);
      assert.ok(place.coordinates.every(Number.isFinite));
    }
  }
  assert.equal(Object.isFrozen(EXPLORE_TRIAL), true);
}));

test("offline controls emit the same structured city and place selections", withBrowser(async () => {
  const { createMapExplorer } = require("../../app/static/map-explorer.js");
  const root = new FakeElement("section");
  const selections = [];
  const explorer = createMapExplorer(root, { amapKey: "", onSelect: (value) => selections.push(value) });

  explorer.showProvince("yunnan");
  await findByText(root, "大理").dispatch("click");
  await findByText(root, "洱海").dispatch("click");

  assert.deepEqual(selections.map((value) => value.kind), ["province", "city", "place"]);
  assert.deepEqual(selections.at(-1), {
    kind: "place", id: "erhai-lake", name: "洱海", recommendation: "推荐在洱海安排环湖慢游，预留拍照和休息时间。",
  });
}));

test("failed AMap script loading keeps the offline drill controls available", withBrowser(async ({ head }) => {
  const { createMapExplorer } = require("../../app/static/map-explorer.js");
  let loadedScript;
  head.append = (node) => { loadedScript = node; FakeElement.prototype.append.call(head, node); node.onerror(); };
  const root = new FakeElement("section");
  const explorer = createMapExplorer(root, { amapKey: "test-browser-key", onSelect() {} });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(root.dataset.mapMode, "offline");
  assert.match(root.textContent, /离线地图/);
  explorer.showNation();
  assert.ok(findByText(root, "福建"));
  assert.match(loadedScript.src, /key=test-browser-key/);
  assert.equal(head.children.length, 0);
}));

test("AMap loader encodes the key and cleans its script and handlers once after timeout", withControlledBrowser(async ({ head, timers, clearedTimers }) => {
  const { loadAmap } = require("../../app/static/map-explorer.js");
  let removals = 0;
  const removeChild = head.removeChild.bind(head);
  head.removeChild = (node) => { removals += 1; return removeChild(node); };

  const loading = loadAmap("key with & symbol");
  const script = head.firstChild;
  assert.match(script.src, /key=key%20with%20%26%20symbol/);
  timers[0]();

  assert.equal(await loading, null);
  assert.equal(head.children.length, 0);
  assert.equal(script.onload, null);
  assert.equal(script.onerror, null);
  assert.deepEqual(clearedTimers, [0]);
  const staleError = script.onerror;
  assert.equal(staleError, null);
  assert.equal(removals, 1);
}));

test("AMap loader accepts an existing map API without injecting a script", withControlledBrowser(async ({ head, window }) => {
  const { loadAmap } = require("../../app/static/map-explorer.js");
  const existing = { Map() {} };
  window.AMap = existing;

  assert.equal(await loadAmap("unused"), existing);
  assert.equal(head.children.length, 0);
}));

test("AMap loader cleans script once after a successful script event", withControlledBrowser(async ({ head, window }) => {
  const { loadAmap } = require("../../app/static/map-explorer.js");
  let removals = 0;
  const removeChild = head.removeChild.bind(head);
  head.removeChild = (node) => { removals += 1; return removeChild(node); };
  const loading = loadAmap("safe-key");
  const script = head.firstChild;
  window.AMap = { Map() {} };
  script.onload();

  assert.equal(await loading, window.AMap);
  assert.equal(head.children.length, 0);
  assert.equal(script.onerror, null);
  assert.equal(removals, 1);
}));

test("immediate AMap script errors clear the timer that is already armed", withControlledBrowser(async ({ head, clearedTimers }) => {
  const { loadAmap } = require("../../app/static/map-explorer.js");
  head.append = (node) => { FakeElement.prototype.append.call(head, node); node.onerror(); };

  assert.equal(await loadAmap("safe-key"), null);
  assert.deepEqual(clearedTimers, [0]);
  assert.equal(head.children.length, 0);
}));

test("AMap initialization failure restores only the offline renderer", withBrowser(async () => {
  const { createMapExplorer } = require("../../app/static/map-explorer.js");
  global.window.AMap = { Map() { throw new Error("initialization failed"); } };
  const root = new FakeElement("section");
  const explorer = createMapExplorer(root, { amapKey: "safe-key", onSelect() {} });
  await new Promise((resolve) => setImmediate(resolve));
  explorer.showCity("xiamen");

  assert.equal(root.dataset.mapMode, "offline");
  assert.equal(descendants(root).some((node) => node.className === "amap-explorer-canvas"), false);
  assert.match(root.textContent, /离线地图/);
}));

test("AMap markers reuse selection callbacks and destroy releases the map", withBrowser(async () => {
  const { createMapExplorer } = require("../../app/static/map-explorer.js");
  const markers = [];
  let destroyedMaps = 0;
  class Map {
    setZoomAndCenter(zoom, center) { this.zoom = zoom; this.center = center; }
    destroy() { destroyedMaps += 1; }
  }
  class Marker {
    constructor(options) { this.options = options; markers.push(this); }
    on(event, listener) { if (event === "click") this.click = listener; }
    setMap(map) { this.map = map; }
  }
  global.window.AMap = { Map, Marker };
  const root = new FakeElement("section");
  const selections = [];
  const explorer = createMapExplorer(root, { amapKey: "safe-key", onSelect: (value) => selections.push(value) });
  await new Promise((resolve) => setImmediate(resolve));
  explorer.showCity("dali");
  markers[0].click();

  assert.equal(root.dataset.mapMode, "amap");
  assert.equal(selections.at(-1).kind, "place");
  assert.equal(selections.at(-1).name, "洱海");
  explorer.destroy();
  assert.equal(destroyedMaps, 1);
  assert.equal(root.textContent, "");
}));
