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
      let script = null;
      let timer = null;
      let settled = false;
      const cleanup = () => {
        try {
          if (timer !== null) window.clearTimeout(timer);
        } catch (_) { /* best effort cleanup */ }
        try {
          if (script) {
            script.onload = null;
            script.onerror = null;
            if (script.parentNode) script.parentNode.removeChild(script);
          }
        } catch (_) { /* best effort cleanup */ }
      };
      finish = (value) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(value);
      };
      try {
        script = document.createElement("script");
        script.async = true;
        script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapKey)}`;
        script.onload = () => finish(window.AMap && typeof window.AMap.Map === "function" ? window.AMap : null);
        script.onerror = () => finish(null);
        timer = window.setTimeout(() => finish(null), 6000);
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

    function clearMarkers({ tolerateErrors = false } = {}) {
      const currentMarkers = markers;
      markers = [];
      let cleanupError = null;
      currentMarkers.forEach((marker) => {
        try {
          if (typeof marker.setMap === "function") marker.setMap(null);
        } catch (error) {
          if (!cleanupError) cleanupError = error;
        }
      });
      if (cleanupError && !tolerateErrors) throw cleanupError;
    }

    function markerItems() {
      if (activeLevel === "nation") return EXPLORE_TRIAL.provinces;
      if (activeLevel === "province") return citiesForProvince(activeId);
      return findCity(activeId).places;
    }

    function onlineView() {
      const item = activeLevel === "province" ? findProvince(activeId) : activeLevel === "city" ? findCity(activeId) : null;
      return item
        ? { center: item.coordinates, zoom: activeLevel === "city" ? 11 : 8 }
        : NATION_VIEW;
    }

    function renderMarkers() {
      clearMarkers();
      markerItems().forEach((item) => {
        const marker = new amap.Marker({ position: item.coordinates, title: item.name });
        markers.push(marker);
        marker.on("click", () => {
          if (activeLevel === "nation") showProvince(item.id);
          else if (activeLevel === "province") showCity(item.id);
          else emit("place", item);
        });
        marker.setMap(map);
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
        state.items.forEach((city) => {
          const button = createButton(city.name, () => showCity(city.id));
          button.id = `explore-city-${city.id}`;
          hotspots.append(button);
        });
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
      try {
        const view = onlineView();
        map.setZoomAndCenter(view.zoom, view.center);
        renderMarkers();
        canvas.hidden = false;
        offlineLayer.hidden = true;
        root.dataset.mapMode = "amap";
      } catch (_) {
        fallbackOffline();
      }
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
      clearMarkers({ tolerateErrors: true });
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
        const view = onlineView();
        canvas.hidden = false;
        offlineLayer.hidden = true;
        map = new amap.Map(canvas, { zoom: view.zoom, center: view.center, viewMode: "2D" });
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
        clearMarkers({ tolerateErrors: true });
        if (map && typeof map.destroy === "function") {
          try { map.destroy(); } catch (_) { /* best effort cleanup */ }
        }
        map = null;
        clear(root);
      },
    };
  }

  function createFootprintMap(root, {
    amapKey = null,
    securityJsCode = null,
    fallbackRoot = null,
    entries = [],
    layers = null,
  } = {}) {
    if (!root) throw new Error("footprint map root is required");
    let destroyed = false;
    let amap = null;
    let map = null;
    let currentLayers = Array.isArray(layers) ? layers : null;
    let currentEntries = Array.isArray(entries) ? entries : [];
    const overlays = new Map();
    const canvas = document.createElement("div");
    canvas.className = "amap-footprint-canvas";
    canvas.setAttribute("aria-label", "我的足迹地图");
    root.append(canvas);

    function removeOverlay(record) {
      if (!record) return;
      if (record.overlay && typeof record.overlay.off === "function") {
        record.handlers.forEach(({ event, handler }) => {
          try { record.overlay.off(event, handler); } catch (_) { /* best effort cleanup */ }
        });
      }
      try {
        if (record.overlay && typeof record.overlay.setMap === "function") record.overlay.setMap(null);
      } catch (_) { /* best effort cleanup */ }
    }

    function clearOverlays() {
      const currentOverlays = Array.from(overlays.values());
      overlays.clear();
      currentOverlays.forEach((record) => {
        try {
          removeOverlay(record);
        } catch (_) { /* best effort cleanup */ }
      });
    }

    function showFallback() {
      root.hidden = true;
      root.dataset.mapMode = "offline";
      if (fallbackRoot) fallbackRoot.hidden = false;
    }

    function showOnline() {
      root.hidden = false;
      root.dataset.mapMode = "amap";
      if (fallbackRoot) fallbackRoot.hidden = true;
    }

    function validCenter(center) {
      return Array.isArray(center) && center.length === 2 && center.every(Number.isFinite);
    }

    function validRings(rings) {
      return Array.isArray(rings) && rings.length > 0 && rings.every((ring) => Array.isArray(ring) && ring.length > 0);
    }

    function layerKey(layer) {
      return layer && layer.footprint && layer.footprint.city_adcode;
    }

    function layerSignature(layer) {
      return JSON.stringify(layer);
    }

    function bindOverlayEvents(record) {
      const overlay = record.overlay;
      if (!overlay || typeof overlay.on !== "function") return;
      if (record.kind !== "polygon") return;
      const enter = () => {
        if (typeof overlay.setOptions === "function") overlay.setOptions({ fillOpacity: 0.62 });
        else if (overlay.options) overlay.options.fillOpacity = 0.62;
      };
      const leave = () => {
        if (typeof overlay.setOptions === "function") overlay.setOptions({ fillOpacity: 0.38 });
        else if (overlay.options) overlay.options.fillOpacity = 0.38;
      };
      overlay.on("mouseover", enter);
      overlay.on("mouseout", leave);
      record.handlers.push({ event: "mouseover", handler: enter }, { event: "mouseout", handler: leave });
    }

    function createLayerOverlay(layer) {
      const footprint = layer && layer.footprint ? layer.footprint : {};
      const boundary = layer && layer.boundary ? layer.boundary : {};
      const key = layerKey(layer);
      if (!key || !amap) return null;
      const usePolygon = boundary.status !== "unavailable" && validRings(boundary.rings) && typeof amap.Polygon === "function";
      let overlay;
      let kind = "marker";
      if (usePolygon) {
        try {
          overlay = new amap.Polygon({
            path: boundary.rings,
            fillColor: "#27b8aa",
            fillOpacity: 0.38,
            strokeColor: "#087f76",
            strokeWeight: 2,
          });
          kind = "polygon";
        } catch (_) {
          overlay = null;
        }
      }
      if (!overlay && validCenter(boundary.center) && typeof amap.Marker === "function") {
        overlay = new amap.Marker({ position: boundary.center, title: footprint.city_name || "旅行足迹" });
      }
      if (!overlay) return null;
      let record = { key, kind, overlay, center: boundary.center, handlers: [], signature: layerSignature(layer) };
      try {
        overlay.setMap(map);
      } catch (error) {
        if (kind !== "polygon" || !validCenter(boundary.center) || typeof amap.Marker !== "function") throw error;
        overlay = new amap.Marker({ position: boundary.center, title: footprint.city_name || "旅行足迹" });
        record = { key, kind: "marker", overlay, center: boundary.center, handlers: [], signature: layerSignature(layer) };
        overlay.setMap(map);
      }
      bindOverlayEvents(record);
      return record;
    }

    function createLegacyOverlay(entry) {
      if (!entry || !validCenter(entry.coordinates) || typeof amap.Marker !== "function") return null;
      const overlay = new amap.Marker({ position: entry.coordinates, title: entry.name || "旅行足迹" });
      overlay.setMap(map);
      return { key: entry.id || JSON.stringify(entry.coordinates), kind: "marker", overlay, center: entry.coordinates, handlers: [], signature: layerSignature(entry) };
    }

    function renderLayers({ fit = false } = {}) {
      const desired = new Map((currentLayers || []).filter((layer) => layerKey(layer)).map((layer) => [layerKey(layer), layer]));
      overlays.forEach((record, key) => {
        const layer = desired.get(key);
        if (!layer || record.signature !== layerSignature(layer)) {
          removeOverlay(record);
          overlays.delete(key);
        }
      });
      desired.forEach((layer, key) => {
        if (overlays.has(key)) return;
        const record = createLayerOverlay(layer);
        if (record) overlays.set(key, record);
      });
      if (fit && map && typeof map.setFitView === "function" && overlays.size) map.setFitView(Array.from(overlays.values()).map((record) => record.overlay));
    }

    function renderEntries({ fit = false } = {}) {
      const desired = new Map(currentEntries.map((entry) => [entry.id || JSON.stringify(entry.coordinates), entry]));
      overlays.forEach((record, key) => {
        const entry = desired.get(key);
        if (!entry || record.signature !== layerSignature(entry)) {
          removeOverlay(record);
          overlays.delete(key);
        }
      });
      desired.forEach((entry, key) => {
        if (overlays.has(key)) return;
        const record = createLegacyOverlay(entry);
        if (record) overlays.set(key, record);
      });
      if (fit && map && typeof map.setFitView === "function" && overlays.size) map.setFitView(Array.from(overlays.values()).map((record) => record.overlay));
    }

    function renderCurrent({ fit = false } = {}) {
      if (currentLayers) renderLayers({ fit });
      else renderEntries({ fit });
    }

    function fallbackOnlineMap() {
      clearOverlays();
      if (map && typeof map.destroy === "function") {
        try { map.destroy(); } catch (_) { /* best effort cleanup */ }
      }
      map = null;
      amap = null;
      showFallback();
    }

    function initializeOnline(loaded) {
      if (destroyed || !loaded) return;
      amap = loaded;
      try {
        showOnline();
        map = new amap.Map(canvas, { zoom: 4, center: [104.2, 35.9], viewMode: "2D" });
        renderCurrent({ fit: true });
      } catch (_) {
        fallbackOnlineMap();
      }
    }

    showFallback();
    const amapLoading = loadAmap(amapKey, securityJsCode);
    amapLoading.then(initializeOnline).catch(() => fallbackOnlineMap());

    return {
      update(nextEntries) {
        if (Array.isArray(nextEntries) && (nextEntries.length === 0 || nextEntries[0].footprint)) {
          currentLayers = nextEntries;
        } else {
          currentLayers = null;
          currentEntries = Array.isArray(nextEntries) ? nextEntries : [];
        }
        if (!map || !amap || destroyed) return;
        try { renderCurrent({ fit: true }); } catch (_) { fallbackOnlineMap(); }
      },
      focus(cityAdcode) {
        if (!map || destroyed) return;
        const record = overlays.get(cityAdcode);
        if (!record) return;
        try {
          if (record.kind === "polygon" && typeof map.setFitView === "function") map.setFitView([record.overlay]);
          else if (validCenter(record.center) && typeof map.setZoomAndCenter === "function") map.setZoomAndCenter(11, record.center);
          else if (typeof map.setFitView === "function") map.setFitView([record.overlay]);
        } catch (_) { /* best effort focus */ }
      },
      destroy() {
        destroyed = true;
        amapLoading.cancel();
        clearOverlays();
        if (map && typeof map.destroy === "function") {
          try { map.destroy(); } catch (_) { /* best effort cleanup */ }
        }
        map = null;
        clear(root);
        root.hidden = true;
        if (fallbackRoot) fallbackRoot.hidden = false;
      },
    };
  }

  const exported = { EXPLORE_TRIAL, createMapExplorer, createFootprintMap, loadAmap };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (globalScope) globalScope.TravelMapExplorer = exported;
}(typeof window === "undefined" ? globalThis : window));
