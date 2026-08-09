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
  head.append = (node) => { FakeElement.prototype.append.call(head, node); node.onerror(); };
  const root = new FakeElement("section");
  const explorer = createMapExplorer(root, { amapKey: "test-browser-key", onSelect() {} });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(root.dataset.mapMode, "offline");
  assert.match(root.textContent, /离线地图/);
  explorer.showNation();
  assert.ok(findByText(root, "福建"));
  assert.match(head.children[0].src, /key=test-browser-key/);
}));
