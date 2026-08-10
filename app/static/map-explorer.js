(function mapExplorerModule(globalScope) {
  const EXPLORE_TRIAL = globalScope.TRAVEL_EXPLORE_DATA || (typeof require === "function" ? require("./data/explore-data.js").EXPLORE_TRIAL : null);
  const NATION_VIEW = Object.freeze({ center: [104.2, 35.9], zoom: 4 });

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

  function loadAmap(amapKey, securityJsCode) {
    const unavailable = Promise.resolve(null);
    unavailable.cancel = () => {};
    if (!amapKey || !securityJsCode || typeof document === "undefined" || typeof window === "undefined") return unavailable;
    window._AMapSecurityConfig = { securityJsCode };
    if (window.AMap && typeof window.AMap.Map === "function") {
      const existing = Promise.resolve(window.AMap);
      existing.cancel = () => {};
      return existing;
    }

    let finish = () => {};
    const loading = new Promise((resolve) => {
      const script = document.createElement("script");
      let timer = null;
      let settled = false;
      const cleanup = () => {
        if (timer !== null) window.clearTimeout(timer);
        script.onload = null;
        script.onerror = null;
        if (script.parentNode) script.parentNode.removeChild(script);
      };
      finish = (value) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(value);
      };
      script.async = true;
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapKey)}`;
      script.onload = () => finish(window.AMap && typeof window.AMap.Map === "function" ? window.AMap : null);
      script.onerror = () => finish(null);
      timer = window.setTimeout(() => finish(null), 6000);
      try {
        document.head.append(script);
      } catch (_) {
        finish(null);
      }
    });
    loading.cancel = () => finish(null);
    return loading;
  }

  function createMapExplorer(root, {
    amapKey = null,
    securityJsCode = null,
    onSelect = () => {},
    onStateChange = () => {},
  } = {}) {
    if (!root) throw new Error("map explorer root is required");
    let activeLevel = "nation";
    let activeId = null;
    let destroyed = false;
    let amap = null;
    let map = null;
    let markers = [];

    clear(root);
    const canvas = document.createElement("div");
    canvas.className = "amap-explorer-canvas";
    canvas.setAttribute("aria-label", "中国目的地地图");
    canvas.hidden = true;
    const offlineLayer = document.createElement("div");
    offlineLayer.className = "offline-map-layer";
    const navigation = document.createElement("nav");
    navigation.className = "map-navigation-overlay";
    navigation.setAttribute("aria-label", "地图层级导航");
    root.append(canvas, offlineLayer, navigation);

    function citiesForProvince(provinceId) {
      return EXPLORE_TRIAL.cities.filter((city) => city.provinceId === provinceId);
    }
    function findProvince(id) { return EXPLORE_TRIAL.provinces.find((province) => province.id === id); }
    function findCity(id) { return EXPLORE_TRIAL.cities.find((city) => city.id === id); }

    function currentState() {
      const province = activeLevel === "province"
        ? findProvince(activeId)
        : activeLevel === "city"
          ? findProvince(findCity(activeId).provinceId)
          : null;
      const city = activeLevel === "city" ? findCity(activeId) : null;
      const items = activeLevel === "nation"
        ? EXPLORE_TRIAL.provinces
        : activeLevel === "province"
          ? citiesForProvince(activeId)
          : city.places;
      return {
        level: activeLevel,
        activeId,
        province,
        city,
        items,
        breadcrumb: ["中国", province && province.name, city && city.name].filter(Boolean),
        title: activeLevel === "nation" ? "试点省份" : activeLevel === "province" ? `${province.name}热门城市` : `${city.name}热门景点`,
      };
    }

    function emit(kind, item) { onSelect(selection(kind, item)); }

    function clearMarkers() {
      markers.forEach((marker) => {
        if (typeof marker.setMap === "function") marker.setMap(null);
      });
      markers = [];
    }

    function markerItems() {
      if (activeLevel === "nation") return EXPLORE_TRIAL.provinces;
      if (activeLevel === "province") return citiesForProvince(activeId);
      return findCity(activeId).places;
    }

    function renderMarkers() {
      clearMarkers();
      markerItems().forEach((item) => {
        const marker = new amap.Marker({ position: item.coordinates, title: item.name });
        marker.on("click", () => {
          if (activeLevel === "nation") showProvince(item.id);
          else if (activeLevel === "province") showCity(item.id);
          else emit("place", item);
        });
        marker.setMap(map);
        markers.push(marker);
      });
    }

    function renderNavigation(state) {
      clear(navigation);
      const trail = document.createElement("p");
      trail.textContent = state.breadcrumb.join(" › ");
      navigation.append(trail);
      if (activeLevel === "province") navigation.append(createButton("返回全国", showNation));
      if (activeLevel === "city") navigation.append(createButton("返回省份", () => showProvince(state.city.provinceId)));
    }

    function renderOffline(state) {
      clear(offlineLayer);
      const notice = document.createElement("p");
      notice.textContent = "当前使用离线地图试点，可继续浏览福建和云南的景点。";
      const image = document.createElement("img");
      image.src = "/static/assets/maps/china-trial.svg";
      image.alt = `${state.breadcrumb.join(" ")}离线地图`;
      const hotspots = document.createElement("div");
      hotspots.className = "offline-map-hotspots";
      hotspots.setAttribute("aria-label", `${state.title}地图标记`);
      if (activeLevel === "nation") {
        state.items.forEach((province) => hotspots.append(createButton(province.name, () => showProvince(province.id))));
      } else if (activeLevel === "province") {
        state.items.forEach((city) => hotspots.append(createButton(city.name, () => showCity(city.id))));
        hotspots.append(createButton(`推荐 ${state.province.name}`, () => emit("province", state.province)));
      } else {
        state.items.forEach((place) => hotspots.append(createButton(place.name, () => emit("place", place))));
      }
      offlineLayer.append(notice, image, hotspots);
    }

    function showOffline() {
      canvas.hidden = true;
      offlineLayer.hidden = false;
      root.dataset.mapMode = "offline";
    }

    function syncOnlineView() {
      if (!map || !amap) return;
      const item = activeLevel === "province" ? findProvince(activeId) : activeLevel === "city" ? findCity(activeId) : null;
      const view = item
        ? { center: item.coordinates, zoom: activeLevel === "city" ? 11 : 8 }
        : NATION_VIEW;
      map.setZoomAndCenter(view.zoom, view.center);
      renderMarkers();
      canvas.hidden = false;
      offlineLayer.hidden = true;
      root.dataset.mapMode = "amap";
    }

    function render({ transitionOnline = true } = {}) {
      if (destroyed) return;
      const state = currentState();
      root.dataset.mapLevel = activeLevel;
      renderNavigation(state);
      renderOffline(state);
      if (map && transitionOnline) syncOnlineView();
      else if (!map) showOffline();
      onStateChange(state);
    }

    function fallbackOffline() {
      clearMarkers();
      if (map && typeof map.destroy === "function") {
        try { map.destroy(); } catch (_) { /* best effort cleanup */ }
      }
      map = null;
      amap = null;
      showOffline();
    }

    function initializeOnline(loaded) {
      if (destroyed || !loaded) return;
      amap = loaded;
      try {
        canvas.hidden = false;
        offlineLayer.hidden = true;
        map = new amap.Map(canvas, { zoom: NATION_VIEW.zoom, center: NATION_VIEW.center, viewMode: "2D" });
        renderMarkers();
        root.dataset.mapMode = "amap";
      } catch (_) {
        fallbackOffline();
      }
    }

    function showNation() { activeLevel = "nation"; activeId = null; render(); }
    function showProvince(id) {
      const province = findProvince(id);
      if (!province) return;
      activeLevel = "province";
      activeId = id;
      emit("province", province);
      render();
    }
    function showCity(id) {
      const city = findCity(id);
      if (!city) return;
      activeLevel = "city";
      activeId = id;
      emit("city", city);
      render();
    }

    render({ transitionOnline: false });
    const amapLoading = loadAmap(amapKey, securityJsCode);
    amapLoading.then(initializeOnline).catch(() => fallbackOffline());

    return {
      showNation,
      showProvince,
      showCity,
      destroy() {
        destroyed = true;
        amapLoading.cancel();
        clearMarkers();
        if (map && typeof map.destroy === "function") {
          try { map.destroy(); } catch (_) { /* best effort cleanup */ }
        }
        map = null;
        clear(root);
      },
    };
  }

  const exported = { EXPLORE_TRIAL, createMapExplorer, loadAmap };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (globalScope) globalScope.TravelMapExplorer = exported;
}(typeof window === "undefined" ? globalThis : window));
