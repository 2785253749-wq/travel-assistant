(function mapExplorerModule(globalScope) {
  const EXPLORE_TRIAL = globalScope.TRAVEL_EXPLORE_DATA || (typeof require === "function" ? require("./data/explore-data.js").EXPLORE_TRIAL : null);

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function createButton(label, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", onClick);
    return button;
  }

  function selection(kind, item) {
    return { kind, id: item.id, name: item.name, recommendation: item.recommendation };
  }

  function loadAmap(amapKey) {
    if (!amapKey || typeof document === "undefined" || typeof window === "undefined") return Promise.resolve(null);
    if (window.AMap && typeof window.AMap.Map === "function") return Promise.resolve(window.AMap);
    return new Promise((resolve) => {
      const script = document.createElement("script");
      const timer = window.setTimeout(() => finish(null), 6000);
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        resolve(value);
      };
      script.async = true;
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapKey)}`;
      script.onload = () => finish(window.AMap && typeof window.AMap.Map === "function" ? window.AMap : null);
      script.onerror = () => finish(null);
      document.head.append(script);
    });
  }

  function createMapExplorer(root, { amapKey = null, onSelect = () => {} } = {}) {
    if (!root) throw new Error("map explorer root is required");
    let activeLevel = "nation";
    let activeId = null;
    let destroyed = false;
    let amap = null;
    let map = null;

    function citiesForProvince(provinceId) {
      return EXPLORE_TRIAL.cities.filter((city) => city.provinceId === provinceId);
    }
    function findProvince(id) { return EXPLORE_TRIAL.provinces.find((province) => province.id === id); }
    function findCity(id) { return EXPLORE_TRIAL.cities.find((city) => city.id === id); }

    function emit(kind, item) { onSelect(selection(kind, item)); }

    function renderOffline() {
      clear(root);
      root.dataset.mapLevel = activeLevel;
      root.dataset.mapMode = "offline";
      const notice = document.createElement("p");
      notice.textContent = "当前使用离线地图试点，可继续浏览福建和云南的景点。";
      const image = document.createElement("img");
      image.src = "/static/assets/maps/china-trial.svg";
      image.alt = "中国地图离线试点";
      root.append(notice, image);

      const controls = document.createElement("div");
      if (activeLevel === "nation") {
        EXPLORE_TRIAL.provinces.forEach((province) => controls.append(createButton(province.name, () => showProvince(province.id))));
      } else if (activeLevel === "province") {
        const province = findProvince(activeId);
        controls.append(createButton("返回全国", showNation));
        citiesForProvince(activeId).forEach((city) => controls.append(createButton(city.name, () => showCity(city.id))));
        if (province) controls.append(createButton(`推荐 ${province.name}`, () => emit("province", province)));
      } else {
        const city = findCity(activeId);
        controls.append(createButton("返回省份", () => showProvince(city.provinceId)));
        city.places.forEach((place) => controls.append(createButton(place.name, () => emit("place", place))));
      }
      root.append(controls);
    }

    function enhanceWithAmap() {
      if (!amap || destroyed) return;
      const item = activeLevel === "province" ? findProvince(activeId) : activeLevel === "city" ? findCity(activeId) : null;
      if (!item) return;
      const host = document.createElement("div");
      host.className = "amap-explorer-canvas";
      host.setAttribute("aria-label", `${item.name}地图`);
      root.append(host);
      try {
        map = new amap.Map(host, { zoom: activeLevel === "city" ? 11 : 8, center: item.coordinates });
        map.setZoomAndCenter(activeLevel === "city" ? 11 : 8, item.coordinates);
        const markerItems = activeLevel === "province" ? citiesForProvince(item.id) : item.places;
        markerItems.forEach((markerItem) => {
          const marker = new amap.Marker({ position: markerItem.coordinates, title: markerItem.name });
          marker.on("click", () => activeLevel === "province" ? showCity(markerItem.id) : emit("place", markerItem));
          marker.setMap(map);
        });
        root.dataset.mapMode = "amap";
      } catch (_) {
        root.dataset.mapMode = "offline";
      }
    }

    function render() {
      if (destroyed) return;
      renderOffline();
      enhanceWithAmap();
    }
    function showNation() { activeLevel = "nation"; activeId = null; render(); }
    function showProvince(id) {
      const province = findProvince(id);
      if (!province) return;
      activeLevel = "province"; activeId = id; emit("province", province); render();
    }
    function showCity(id) {
      const city = findCity(id);
      if (!city) return;
      activeLevel = "city"; activeId = id; emit("city", city); render();
    }

    render();
    loadAmap(amapKey).then((loaded) => { amap = loaded; enhanceWithAmap(); });
    return { showNation, showProvince, showCity, destroy() { destroyed = true; clear(root); } };
  }

  const exported = { EXPLORE_TRIAL, createMapExplorer, loadAmap };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (globalScope) globalScope.TravelMapExplorer = exported;
}(typeof window === "undefined" ? globalThis : window));
