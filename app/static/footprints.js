(function footprintsModule(globalScope) {
  "use strict";

  function clear(element) {
    if (!element) return;
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function createAbortController() {
    if (typeof AbortController === "function") return new AbortController();
    const signal = { aborted: false };
    return { signal, abort() { signal.aborted = true; } };
  }

  function validFootprint(value) {
    return value && typeof value.id === "string" && typeof value.city_adcode === "string";
  }

  function sortedFootprints(footprints) {
    return [...footprints].sort((left, right) => String(right.visited_at).localeCompare(String(left.visited_at)));
  }

  function createController({
    elements = {}, request = async () => [], createMap = null,
    today = () => new Date().toISOString().slice(0, 10),
    onAuthRequired = () => {}, onStatus = () => {},
  } = {}) {
    let identity = null;
    let mounted = false;
    let loadedIdentity = null;
    let generation = 0;
    let abortController = null;
    let map = null;
    let footprints = [];
    let boundaries = new Map();
    let selectedCity = null;
    let editingFootprint = null;
    let searchCandidates = new Map();
    let boundaryQueue = [];
    let queuedBoundaryAdcodes = new Set();
    let boundaryWorkers = 0;

    function report(error, fallback) {
      if (error && ["AUTH_REQUIRED", "AUTH_INVALID"].includes(error.code)) onAuthRequired();
      else onStatus(fallback || "足迹暂时无法同步，请稍后重试。", true);
    }

    function current(currentGeneration) {
      return mounted && identity && currentGeneration === generation && abortController && !abortController.signal.aborted;
    }

    function layers() {
      return footprints.map((footprint) => ({
        footprint,
        boundary: boundaries.get(footprint.city_adcode) || {
          status: "unavailable",
          rings: [],
          center: Array.isArray(footprint.center) ? footprint.center : [],
        },
      }));
    }

    function updateMap() {
      if (map && typeof map.update === "function") map.update(layers());
    }

    function renderSummary() {
      const cities = new Set(footprints.map((item) => item.city_adcode).filter(Boolean));
      const provinces = new Set(footprints.map((item) => item.province_adcode).filter(Boolean));
      if (elements.cityCount) elements.cityCount.textContent = String(cities.size);
      if (elements.provinceCount) elements.provinceCount.textContent = String(provinces.size);
      if (elements.latestCity) elements.latestCity.textContent = footprints[0] ? footprints[0].city_name : "尚未到访";
    }

    function openVisitDialog(city, footprint = null) {
      selectedCity = city;
      editingFootprint = footprint;
      if (elements.visitDialogTitle) elements.visitDialogTitle.textContent = footprint ? `修改${footprint.city_name}的到访日期` : `点亮${city.city_name}`;
      if (elements.visitDate) elements.visitDate.value = footprint ? footprint.visited_at : today();
      if (elements.visitDialog && typeof elements.visitDialog.showModal === "function") elements.visitDialog.showModal();
    }

    function renderList() {
      if (!elements.list) return;
      clear(elements.list);
      const empty = footprints.length === 0;
      if (elements.listEmpty) elements.listEmpty.hidden = !empty;
      if (elements.mapEmpty) elements.mapEmpty.hidden = !empty;
      for (const footprint of footprints) {
        const item = document.createElement("li");
        item.className = "footprint-list-item";
        const copy = document.createElement("div");
        const title = document.createElement("strong");
        title.textContent = footprint.city_name;
        const detail = document.createElement("span");
        detail.textContent = `${footprint.province_name} · ${footprint.visited_at}`;
        copy.append(title, detail);
        const actions = document.createElement("div");
        actions.className = "footprint-list-actions";
        const focus = document.createElement("button");
        focus.type = "button";
        focus.dataset.footprintAction = "focus";
        focus.dataset.footprintId = footprint.id;
        focus.textContent = "定位";
        const edit = document.createElement("button");
        edit.type = "button";
        edit.className = "secondary";
        edit.dataset.footprintAction = "edit";
        edit.dataset.footprintId = footprint.id;
        edit.textContent = "修改日期";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "secondary";
        remove.dataset.footprintAction = "remove";
        remove.dataset.footprintId = footprint.id;
        remove.textContent = "移除";
        actions.append(focus, edit, remove);
        item.append(copy, actions);
        elements.list.append(item);
      }
    }

    function render() {
      const signedIn = Boolean(identity);
      if (elements.authPrompt) elements.authPrompt.hidden = signedIn;
      if (elements.content) elements.content.hidden = !signedIn;
      renderSummary();
      renderList();
    }

    function beginMap() {
      if (map || !createMap || !elements.map) return;
      map = createMap(elements.map, { fallbackRoot: elements.staticMap, layers: layers() });
    }

    function stopMap() {
      if (!map) return;
      if (typeof map.destroy === "function") map.destroy();
      map = null;
    }

    function startBoundaryLoads(currentGeneration) {
      for (const footprint of footprints) {
        if (boundaries.has(footprint.city_adcode) || queuedBoundaryAdcodes.has(footprint.city_adcode)) continue;
        queuedBoundaryAdcodes.add(footprint.city_adcode);
        boundaryQueue.push({ footprint, generation: currentGeneration });
      }
      runBoundaryQueue();
    }

    function runBoundaryQueue() {
      while (boundaryWorkers < 3 && boundaryQueue.length) {
        const entry = boundaryQueue.shift();
        if (!current(entry.generation)) {
          queuedBoundaryAdcodes.delete(entry.footprint.city_adcode);
          continue;
        }
        boundaryWorkers += 1;
        loadBoundary(entry).finally(() => {
          boundaryWorkers -= 1;
          runBoundaryQueue();
        });
      }
    }

    async function loadBoundary({ footprint, generation: currentGeneration }) {
      try {
        const boundary = await request(`/api/map/districts/${encodeURIComponent(footprint.city_adcode)}`, { signal: abortController.signal });
        if (!current(currentGeneration)) return;
        boundaries.set(footprint.city_adcode, boundary && typeof boundary === "object" ? boundary : {
          status: "unavailable", rings: [], center: footprint.center,
        });
        updateMap();
      } catch (_) {
        if (!current(currentGeneration)) return;
        boundaries.set(footprint.city_adcode, { status: "unavailable", rings: [], center: footprint.center });
        updateMap();
      } finally {
        queuedBoundaryAdcodes.delete(footprint.city_adcode);
      }
    }

    function replaceFootprint(next) {
      const index = footprints.findIndex((item) => item.id === next.id || item.city_adcode === next.city_adcode);
      if (index >= 0) footprints.splice(index, 1, next);
      else footprints.push(next);
      footprints = sortedFootprints(footprints);
      render();
      updateMap();
    }

    async function searchCities(event) {
      if (event) event.preventDefault();
      const query = elements.search && typeof elements.search.value === "string" ? elements.search.value.trim() : "";
      if (query.length < 2) {
        if (elements.searchResults) elements.searchResults.textContent = "请输入至少两个字符搜索城市。";
        return;
      }
      try {
        const cities = await request(`/api/map/cities?q=${encodeURIComponent(query)}`, { signal: abortController && abortController.signal });
        if (!elements.searchResults) return;
        clear(elements.searchResults);
        searchCandidates = new Map();
        if (!Array.isArray(cities) || !cities.length) {
          elements.searchResults.textContent = "没有找到可添加的规范城市。";
          return;
        }
        for (const city of cities) {
          if (!city || typeof city.city_adcode !== "string") continue;
          searchCandidates.set(city.city_adcode, city);
          const button = document.createElement("button");
          button.type = "button";
          button.className = "secondary footprint-search-result";
          button.dataset.footprintCityAdcode = city.city_adcode;
          button.textContent = city.city_name;
          elements.searchResults.append(button);
        }
      } catch (error) {
        report(error, "城市搜索暂时不可用，请稍后重试。");
      }
    }

    async function submitVisit(event) {
      if (event) event.preventDefault();
      if (!identity || !selectedCity) {
        onAuthRequired();
        return;
      }
      const visitedAt = elements.visitDate && elements.visitDate.value ? elements.visitDate.value : today();
      try {
        if (editingFootprint) {
          const updated = await request(`/api/footprints/${encodeURIComponent(editingFootprint.id)}`, {
            method: "PATCH", body: { visited_at: visitedAt }, signal: abortController && abortController.signal,
          });
          replaceFootprint(updated);
        } else {
          await addCity({ cityAdcode: selectedCity.city_adcode, suggestedVisitedAt: visitedAt });
        }
        if (elements.visitDialog && typeof elements.visitDialog.close === "function") elements.visitDialog.close();
        selectedCity = null;
        editingFootprint = null;
      } catch (error) {
        report(error, "足迹暂时无法保存，请稍后重试。");
      }
    }

    function cancelVisit() {
      selectedCity = null;
      editingFootprint = null;
      if (elements.visitDialog && typeof elements.visitDialog.close === "function") elements.visitDialog.close();
    }

    async function addCity({ cityAdcode, cityName, suggestedVisitedAt } = {}) {
      if (!identity) {
        onAuthRequired();
        return null;
      }
      if ((typeof cityAdcode !== "string" || !/^\d{6}$/.test(cityAdcode)) && typeof cityName === "string" && cityName.trim().length >= 2) {
        const cities = await request(`/api/map/cities?q=${encodeURIComponent(cityName.trim())}`, {
          signal: abortController && abortController.signal,
        });
        const requestedName = cityName.trim();
        const city = Array.isArray(cities) && cities.find((item) => item && (
          item.city_name === requestedName || item.city_name.replace(/[市州]$/, "") === requestedName
        ));
        cityAdcode = city && city.city_adcode;
      }
      if (typeof cityAdcode !== "string" || !/^\d{6}$/.test(cityAdcode)) return null;
      const footprint = await request("/api/footprints", {
        method: "POST",
        body: { city_adcode: cityAdcode, visited_at: suggestedVisitedAt || today() },
        signal: abortController && abortController.signal,
      });
      if (!validFootprint(footprint)) throw new Error("FOOTPRINT_INVALID_RESPONSE");
      replaceFootprint(footprint);
      startBoundaryLoads(generation);
      return footprint;
    }

    async function removeFootprint(footprint) {
      const before = footprints;
      footprints = footprints.filter((item) => item.id !== footprint.id);
      boundaries.delete(footprint.city_adcode);
      render();
      updateMap();
      try {
        await request(`/api/footprints/${encodeURIComponent(footprint.id)}`, {
          method: "DELETE", signal: abortController && abortController.signal,
        });
      } catch (error) {
        if (current(generation)) {
          footprints = before;
          render();
          updateMap();
          startBoundaryLoads(generation);
          report(error, "移除足迹失败，已恢复原来的城市。 ");
        }
      }
    }

    async function handleListClick(event) {
      const target = event && event.target && typeof event.target.closest === "function"
        ? event.target.closest("[data-footprint-action]") : null;
      const action = target && target.dataset ? target.dataset.footprintAction : null;
      const footprint = target && footprints.find((item) => item.id === target.dataset.footprintId);
      if (!action || !footprint) return;
      if (action === "focus" && map && typeof map.focus === "function") map.focus(footprint.city_adcode);
      if (action === "edit") openVisitDialog(footprint, footprint);
      if (action === "remove") await removeFootprint(footprint);
    }

    function handleSearchResultClick(event) {
      const target = event && event.target && typeof event.target.closest === "function"
        ? event.target.closest("[data-footprint-city-adcode]") : null;
      const city = target && searchCandidates.get(target.dataset.footprintCityAdcode);
      if (city) openVisitDialog(city);
    }

    if (elements.searchForm) elements.searchForm.addEventListener("submit", searchCities);
    if (elements.visitForm) elements.visitForm.addEventListener("submit", submitVisit);
    if (elements.visitCancel) elements.visitCancel.addEventListener("click", cancelVisit);
    if (elements.list) elements.list.addEventListener("click", handleListClick);
    if (elements.searchResults) elements.searchResults.addEventListener("click", handleSearchResultClick);

    return {
      setIdentity(nextIdentity) {
        const normalized = typeof nextIdentity === "string" && nextIdentity.trim() ? nextIdentity : null;
        if (normalized === identity) return;
        generation += 1;
        if (abortController) abortController.abort();
        abortController = null;
        stopMap();
        identity = normalized;
        mounted = false;
        loadedIdentity = null;
        footprints = [];
        boundaries = new Map();
        boundaryQueue = [];
        queuedBoundaryAdcodes = new Set();
        selectedCity = null;
        editingFootprint = null;
        render();
      },
      async mount() {
        if (!identity) {
          render();
          return;
        }
        if (mounted && loadedIdentity === identity) return;
        mounted = true;
        loadedIdentity = identity;
        const currentGeneration = ++generation;
        abortController = createAbortController();
        try {
          const loaded = await request("/api/footprints", { signal: abortController.signal });
          if (!current(currentGeneration)) return;
          footprints = sortedFootprints(Array.isArray(loaded) ? loaded.filter(validFootprint) : []);
        boundaries = new Map();
        boundaryQueue = [];
        queuedBoundaryAdcodes = new Set();
          render();
          beginMap();
          updateMap();
          startBoundaryLoads(currentGeneration);
        } catch (error) {
          if (!current(currentGeneration)) return;
          loadedIdentity = null;
          report(error, "足迹暂时无法加载，请稍后重试。");
          render();
        }
      },
      unmount() {
        generation += 1;
        mounted = false;
        loadedIdentity = null;
        if (abortController) abortController.abort();
        abortController = null;
        boundaryQueue = [];
        queuedBoundaryAdcodes = new Set();
        stopMap();
      },
      addCity,
      isSaved(cityAdcode) { return footprints.some((item) => item.city_adcode === cityAdcode); },
    };
  }

  const exported = { createController };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (globalScope) globalScope.TravelFootprints = exported;
}(typeof window === "undefined" ? globalThis : window));
