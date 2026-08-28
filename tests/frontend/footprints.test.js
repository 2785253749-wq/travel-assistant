const assert = require("node:assert/strict");
const test = require("node:test");
const { FakeElement, descendants, findByText } = require("./dom-harness");

const XIAMEN = {
  id: "footprint-xiamen",
  city_adcode: "350200",
  city_name: "厦门市",
  province_adcode: "350000",
  province_name: "福建省",
  center: [118.09, 24.48],
  visited_at: "2026-08-20",
  created_at: "2026-08-20T00:00:00Z",
  updated_at: "2026-08-20T00:00:00Z",
};

const FUZHOU = { ...XIAMEN, id: "footprint-fuzhou", city_adcode: "350100", city_name: "福州市", visited_at: "2026-08-19" };
const DALI = { ...XIAMEN, id: "footprint-dali", city_adcode: "532900", city_name: "大理州", province_adcode: "530000", province_name: "云南省", visited_at: "2026-08-18" };
const LIJIANG = { ...XIAMEN, id: "footprint-lijiang", city_adcode: "530700", city_name: "丽江市", province_adcode: "530000", province_name: "云南省", visited_at: "2026-08-17" };

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((nextResolve, nextReject) => { resolve = nextResolve; reject = nextReject; });
  return { promise, resolve, reject };
}

function createElements() {
  const elements = {
    authPrompt: new FakeElement("div"), content: new FakeElement("div"),
    map: new FakeElement("div"), staticMap: new FakeElement("div"), mapGrid: new FakeElement("div"), mapEmpty: new FakeElement("p"),
    provinceCount: new FakeElement("strong"), cityCount: new FakeElement("strong"), latestCity: new FakeElement("strong"),
    list: new FakeElement("ul"), listEmpty: new FakeElement("p"),
    searchForm: new FakeElement("form"), search: new FakeElement("input"), searchResults: new FakeElement("div"),
    visitDialog: new FakeElement("dialog"), visitDialogTitle: new FakeElement("h2"), visitForm: new FakeElement("form"), visitDate: new FakeElement("input"), visitCancel: new FakeElement("button"),
  };
  elements.searchForm.append(elements.search);
  elements.visitForm.append(elements.visitDate);
  return elements;
}

function createMap() {
  return {
    updates: [], focusCalls: [], destroyCalls: 0,
    update(layers) { this.updates.push(layers); },
    focus(cityAdcode) { this.focusCalls.push(cityAdcode); },
    destroy() { this.destroyCalls += 1; },
  };
}

function controllerFixture({ request, createMap: createMapOption = () => createMap() } = {}) {
  const originalDocument = global.document;
  global.document = { createElement: (tag) => new FakeElement(tag) };
  const { createController } = require("../../app/static/footprints.js");
  const elements = createElements();
  const map = createMap();
  const requests = [];
  const controller = createController({
    elements,
    request: async (path, options = {}) => {
      requests.push({ path, options });
      return request ? request(path, options, requests) : [];
    },
    createMap: () => map,
    today: () => "2026-08-28",
  });
  return {
    controller, elements, map, requests,
    restore() { global.document = originalDocument; },
  };
}

test("signed-in mount loads cloud footprints once", async () => {
  const fixture = controllerFixture({ request: async (path) => path === "/api/footprints" ? [] : { status: "unavailable", rings: [], center: [118.09, 24.48] } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    await fixture.controller.mount();
    assert.equal(fixture.requests.filter((item) => item.path === "/api/footprints").length, 1);
  } finally {
    fixture.restore();
  }
});

test("account switch clears A and ignores A late response", async () => {
  const userA = deferred();
  let listCalls = 0;
  const fixture = controllerFixture({ request: async (path) => {
    if (path !== "/api/footprints") return { status: "unavailable", rings: [], center: [118.09, 24.48] };
    listCalls += 1;
    return listCalls === 1 ? userA.promise : [];
  } });
  try {
    fixture.controller.setIdentity("user-a");
    const staleMount = fixture.controller.mount();
    fixture.controller.setIdentity("user-b");
    await fixture.controller.mount();
    userA.resolve([XIAMEN]);
    await staleMount;
    assert.doesNotMatch(fixture.elements.list.textContent, /厦门/);
    assert.equal(fixture.elements.cityCount.textContent, "0");
  } finally {
    fixture.restore();
  }
});

test("unmount aborts boundary loads and destroys one map", async () => {
  let boundarySignal;
  const boundary = deferred();
  const fixture = controllerFixture({ request: async (path, options) => {
    if (path === "/api/footprints") return [XIAMEN];
    boundarySignal = options.signal;
    return boundary.promise;
  } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    fixture.controller.unmount();
    assert.equal(fixture.map.destroyCalls, 1);
    assert.equal(boundarySignal.aborted, true);
    boundary.resolve({ status: "unavailable", rings: [], center: [118.09, 24.48] });
  } finally {
    fixture.restore();
  }
});

test("boundary request concurrency never exceeds three", async () => {
  let active = 0;
  let maximum = 0;
  const fixture = controllerFixture({ request: async (path) => {
    if (path === "/api/footprints") return [XIAMEN, FUZHOU, DALI, LIJIANG];
    active += 1;
    maximum = Math.max(maximum, active);
    await new Promise((resolve) => setImmediate(resolve));
    active -= 1;
    return { status: "unavailable", rings: [], center: [118.09, 24.48] };
  } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    assert.equal(maximum, 3);
  } finally {
    fixture.restore();
  }
});

test("adding a city while boundaries load shares the three-request budget", async () => {
  const pendingBoundaries = [];
  let active = 0;
  let maximum = 0;
  const fixture = controllerFixture({ request: async (path, options) => {
    if (path === "/api/footprints" && !options.method) return [XIAMEN, FUZHOU, DALI];
    if (path === "/api/footprints" && options.method === "POST") return LIJIANG;
    active += 1;
    maximum = Math.max(maximum, active);
    const pending = deferred();
    pendingBoundaries.push(pending);
    return pending.promise.finally(() => { active -= 1; });
  } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    await fixture.controller.addCity({ cityAdcode: "530700", suggestedVisitedAt: "2026-08-28" });
    assert.equal(maximum, 3);
    fixture.controller.unmount();
    pendingBoundaries.forEach((pending) => pending.resolve({ status: "unavailable", rings: [], center: [118.09, 24.48] }));
  } finally {
    fixture.restore();
  }
});

test("canonical city result posts adcode and visit date", async () => {
  const fixture = controllerFixture({ request: async (path, options) => {
    if (path === "/api/footprints" && !options.method) return [];
    if (path === "/api/map/cities?q=%E5%8E%A6%E9%97%A8") return [{
      city_adcode: "350200", city_name: "厦门市", province_adcode: "350000", province_name: "福建省", center: [118.09, 24.48],
    }];
    if (path === "/api/footprints" && options.method === "POST") return XIAMEN;
    return { status: "unavailable", rings: [], center: [118.09, 24.48] };
  } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    fixture.elements.search.value = "厦门";
    await fixture.elements.searchForm.dispatch("submit");
    await findByText(fixture.elements.searchResults, "厦门市").dispatch("click");
    fixture.elements.visitDate.value = "2026-08-28";
    await fixture.elements.visitForm.dispatch("submit");
    const post = fixture.requests.find((item) => item.path === "/api/footprints" && item.options.method === "POST");
    assert.deepEqual(post.options.body, { city_adcode: "350200", visited_at: "2026-08-28" });
  } finally {
    fixture.restore();
  }
});

test("cancelling or failing a date dialog keeps the existing footprint summary unchanged", async () => {
  const fixture = controllerFixture({ request: async (path, options) => {
    if (path === "/api/footprints" && !options.method) return [XIAMEN];
    if (path === "/api/map/cities?q=%E7%A6%8F%E5%B7%9E") return [{
      city_adcode: "350100", city_name: "福州市", province_adcode: "350000", province_name: "福建省", center: [119.3, 26.08],
    }];
    if (path === "/api/footprints" && options.method === "POST") throw Object.assign(new Error("offline"), { code: "FOOTPRINT_UNAVAILABLE" });
    return { status: "unavailable", rings: [], center: [118.09, 24.48] };
  } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    assert.equal(fixture.elements.latestCity.textContent, "厦门市");
    fixture.elements.search.value = "福州";
    await fixture.elements.searchForm.dispatch("submit");
    await findByText(fixture.elements.searchResults, "福州市").dispatch("click");
    assert.equal(fixture.elements.latestCity.textContent, "厦门市");
    await fixture.elements.visitCancel.dispatch("click");
    assert.equal(fixture.elements.visitDialog.open, false);
    assert.equal(fixture.elements.cityCount.textContent, "1");
    await findByText(fixture.elements.searchResults, "福州市").dispatch("click");
    fixture.elements.visitDate.value = "2026-08-28";
    await fixture.elements.visitForm.dispatch("submit");
    assert.equal(fixture.elements.cityCount.textContent, "1");
    assert.equal(fixture.elements.latestCity.textContent, "厦门市");
    assert.doesNotMatch(fixture.elements.list.textContent, /福州/);
  } finally {
    fixture.restore();
  }
});

test("editing sorts by visit date, focusing delegates to map, and delete failure restores layers", async () => {
  let deleteAttempted = false;
  const fixture = controllerFixture({ request: async (path, options) => {
    if (path === "/api/footprints" && !options.method) return [XIAMEN, FUZHOU];
    if (path === "/api/footprints/footprint-fuzhou" && options.method === "PATCH") return { ...FUZHOU, visited_at: "2026-08-28" };
    if (path === "/api/footprints/footprint-fuzhou" && options.method === "DELETE") { deleteAttempted = true; throw Object.assign(new Error("offline"), { code: "FOOTPRINT_UNAVAILABLE" }); }
    return { status: "unavailable", rings: [], center: [118.09, 24.48] };
  } });
  try {
    fixture.controller.setIdentity("user-a");
    await fixture.controller.mount();
    await findByText(fixture.elements.list, "定位").dispatch("click");
    assert.deepEqual(fixture.map.focusCalls, ["350200"]);
    await descendants(fixture.elements.list.children[1]).find((node) => node.textContent === "修改日期").dispatch("click");
    fixture.elements.visitDate.value = "2026-08-28";
    await fixture.elements.visitForm.dispatch("submit");
    assert.match(fixture.elements.list.textContent, /福州市/);
    assert.equal(fixture.elements.list.children[0].textContent.includes("福州市"), true);
    await descendants(fixture.elements.list.children[0]).find((node) => node.textContent === "移除").dispatch("click");
    assert.equal(deleteAttempted, true);
    assert.match(fixture.elements.list.textContent, /厦门市/);
    assert.ok(fixture.map.updates.at(-1).some((layer) => layer.footprint.city_adcode === "350200"));
  } finally {
    fixture.restore();
  }
});
