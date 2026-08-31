# 高德地图 Voyage 探索页试点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付接近 Voyage 参考图的探索页，并以高德地图为主、本地 SVG 为兜底完成福建/云南到城市和景点的交互试点。

**Architecture:** FastAPI 只把可公开的 `AMAP_JS_KEY` 以运行时配置提供给浏览器；浏览器使用独立的 `map-explorer` 加载高德或离线 SVG。探索页模块只消费结构化选择事件并写入本地助手推荐，既有聊天、认证、行程和额度边界保持不变。

**Tech Stack:** FastAPI、Pydantic Settings、原生 HTML/CSS/JavaScript、高德 JavaScript API 2.0、Node DOM harness、pytest。

## Global Constraints

- 不把实际高德 Key、DeepSeek Key、Supabase service key 或用户输入写入代码、测试、文档、日志或 Git。
- 仅 `AMAP_JS_KEY` 可作为浏览器公开配置；本次不增加高德 Web 服务 API 调用。
- 没有 Key、脚本失败或初始化失败时，必须使用本地离线地图完成福建/云南试点。
- 地图选择只能追加本地中文推荐，不得调用 `/api/chat`；用户主动提交表单才沿用既有 `collect -> confirm`。
- 本次不覆盖全国、不增加真实图片、路线搜索、支付、真实社区或伪造行程数据。
- 不修改评测题库、基线、阈值、认证、RLS、行程保存和 AI 额度逻辑。

---

### Task 1: 安全公开地图运行时配置

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/main.py`
- Modify: `.env.example`
- Modify: `tests/unit/test_config.py`
- Modify: `tests/integration/test_frontend_assets.py`

**Interfaces:**
- Consumes: 环境变量 `AMAP_JS_KEY`。
- Produces: `Settings.amap_js_key: SecretStr | None`，以及 `window.TRAVEL_ASSISTANT_CONFIG.amapJsKey`（不存在时为 `null`）。

- [ ] **Step 1: 写失败的配置测试**

```python
def test_runtime_config_exposes_only_configured_amap_browser_key(client, monkeypatch):
    monkeypatch.setenv("AMAP_JS_KEY", "amap-browser-test-key")
    get_settings.cache_clear()
    response = client.get("/runtime-config.js")
    assert '"amapJsKey":"amap-browser-test-key"' in response.text
    assert "service" not in response.text.lower()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m pytest tests/integration/test_frontend_assets.py -q`

Expected: FAIL，因为运行时配置没有 `amapJsKey`。

- [ ] **Step 3: 实现最小配置边界**

```python
# app/core/config.py
amap_js_key: SecretStr | None = None

# app/main.py runtime_config()
"amapJsKey": settings.amap_js_key.get_secret_value() if settings.amap_js_key else None,
```

`.env.example` 仅增加 `AMAP_JS_KEY=your_amap_javascript_key_here`，不增加真实值。

- [ ] **Step 4: 验证通过并提交**

Run: `C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m pytest tests/unit/test_config.py tests/integration/test_frontend_assets.py -q`

Expected: PASS。

```powershell
git add app/core/config.py app/main.py .env.example tests/unit/test_config.py tests/integration/test_frontend_assets.py
git commit -m "feat: expose optional amap browser config"
```

### Task 2: 冻结探索数据与可降级地图模块

**Files:**
- Create: `app/static/data/explore-data.js`
- Create: `app/static/map-explorer.js`
- Create: `app/static/assets/maps/china-trial.svg`
- Modify: `tests/frontend/dom-harness.js`
- Create: `tests/frontend/map-explorer.test.js`

**Interfaces:**
- Produces: `createMapExplorer(root, { amapKey, onSelect })`，返回 `{ showNation(), showProvince(id), showCity(id), destroy() }`。
- Emits: `onSelect({ kind: "province" | "city" | "place", id, name, recommendation })`。
- Data: `EXPLORE_TRIAL` 包含福建/云南、厦门/福州/大理/丽江以及每市三个带经纬度的中文景点。

- [ ] **Step 1: 写失败的地图模块测试**

```js
test("offline explorer drills from Fujian to Xiamen places without a network request", async () => {
  const { createMapExplorer } = require("../../app/static/map-explorer.js");
  const root = new FakeElement("section");
  const selections = [];
  const explorer = createMapExplorer(root, { amapKey: null, onSelect: (value) => selections.push(value) });
  explorer.showProvince("fujian");
  assert.equal(root.dataset.mapLevel, "province");
  explorer.showCity("xiamen");
  assert.equal(root.dataset.mapLevel, "city");
  assert.match(root.textContent, /鼓浪屿/);
  assert.equal(selections.at(-1).kind, "city");
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/frontend/map-explorer.test.js`

Expected: FAIL，因为数据和模块不存在。

- [ ] **Step 3: 实现冻结数据与离线 SVG 渲染**

`explore-data.js` 导出冻结对象；`map-explorer.js` 只用 `textContent` 创建可点击按钮、城市和景点控件。无 Key 时插入 `china-trial.svg`，使用 `data-map-level` 与中文降级提示，不使用 `fetch`。

- [ ] **Step 4: 加入高德加载适配器**

```js
async function loadAmap(amapKey) {
  if (!amapKey) return null;
  // 插入 https://webapi.amap.com/maps?v=2.0&key=<encoded key> 的 script。
  // 仅在 window.AMap.Map 存在后返回 window.AMap；超时或 error 返回 null。
}
```

高德可用时使用 `new AMap.Map`、`setZoomAndCenter` 和 `new AMap.Marker`；失败时调用同一个离线渲染器。每个高德标记的点击回调必须调用同一 `onSelect`。

- [ ] **Step 5: 验证通过并提交**

Run: `node --test tests/frontend/map-explorer.test.js`

Expected: PASS，覆盖福建、云南、城市、景点、无 Key 降级和脚本失败降级。

```powershell
git add app/static/data/explore-data.js app/static/map-explorer.js app/static/assets/maps/china-trial.svg tests/frontend/dom-harness.js tests/frontend/map-explorer.test.js
git commit -m "feat: add amap-backed offline map explorer"
```

### Task 3: Voyage 高保真探索页和助手联动

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/styles.css`
- Modify: `app/static/app.js`
- Modify: `tests/frontend/app.test.js`

**Interfaces:**
- Consumes: `createMapExplorer` 和 `window.TRAVEL_ASSISTANT_CONFIG.amapJsKey`。
- Produces: `#explore-page`、`#explore-map`、`#explore-recommendations`、顶部品牌导航和 `appendExploreRecommendation(selection)`。

- [ ] **Step 1: 写失败的探索页联动测试**

```js
test("map selection opens the assistant with a Chinese local recommendation and no chat request", async () => {
  const harness = createHarness();
  await harness.settle();
  await harness.elements.get("explore-city-xiamen").dispatch("click");
  assert.equal(harness.elements.get("assistant-panel").hidden, false);
  assert.match(harness.elements.get("chat-messages").textContent, /厦门适合慢节奏/);
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/chat"), false);
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/frontend/app.test.js`

Expected: FAIL，因为探索页和本地推荐桥接不存在。

- [ ] **Step 3: 重建探索页面结构与视觉样式**

`index.html` 添加独立的 `#explore-page`，含品牌栏、搜索外观控件、地图容器、层级面包屑、热门卡片区和无障碍状态文本。离线可访问的城市控件必须使用固定 ID，例如 `#explore-city-xiamen`。保留现有聊天、登录、确认与行程节点 ID。

`styles.css` 使用浅灰蓝背景、白色圆角主卡、蓝色强调色、桌面地图/卡片双栏；在 `max-width: 900px` 下改为单列，保留悬浮助手底部抽屉。禁止依赖外部图片，景点卡只使用本地 SVG 占位图和渐变。

- [ ] **Step 4: 实现无模型的助手桥接**

```js
function appendExploreRecommendation(selection) {
  openAssistant();
  addMessage(selection.recommendation, "assistant");
}
```

初始化时将 `createMapExplorer(..., { amapKey: window.TRAVEL_ASSISTANT_CONFIG?.amapJsKey || null, onSelect: appendExploreRecommendation })` 挂到探索容器。此函数不得调用 `requestJson`、`fetch` 或 `sendMessage`。

- [ ] **Step 5: 验证通过并提交**

Run: `node --test tests/frontend/app.test.js tests/frontend/map-explorer.test.js`

Expected: PASS，既有 collect/confirm、焦点恢复和共享视图均不回归。

```powershell
git add app/static/index.html app/static/styles.css app/static/app.js tests/frontend/app.test.js
git commit -m "feat: build voyage explore page trial"
```

### Task 4: 部署说明、全量验证和人工验收

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment/free-tier.md`
- Modify: `.env.example`

**Interfaces:**
- Documents: Render 变量名 `AMAP_JS_KEY`、高德域名白名单、无 Key 离线兜底和人工验收步骤。

- [ ] **Step 1: 写失败的部署文档断言**

```python
def test_deployment_document_describes_amap_key_and_offline_fallback():
    text = Path("docs/deployment/free-tier.md").read_text(encoding="utf-8")
    assert "AMAP_JS_KEY" in text
    assert "离线" in text
    assert "travel-assistant-2cbd.onrender.com" in text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m pytest tests/integration/test_deployment_config.py -q`

Expected: FAIL，因为部署文档未说明高德配置。

- [ ] **Step 3: 写入部署与本地运行说明**

README 说明试点范围和不支持全国；部署文档说明在高德控制台限制生产域名和 `http://127.0.0.1`，在 Render 增加 `AMAP_JS_KEY` 后重新部署；明确该 Key 不提交 Git，未配置 Key 时会回退离线 SVG。

- [ ] **Step 4: 全量验证**

Run: `node --test tests/frontend/*.test.js`

Run: `C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m pytest -q`

Run: `C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation`

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`

Expected: 前端与 Python 测试通过；80 条离线评测无阈值失败；公开仓库扫描通过。

- [ ] **Step 5: 人工验收与提交**

在 Render 添加轮换后的 `AMAP_JS_KEY`，确认高德控制台已限制生产域名后访问探索页：依次点击福建→厦门→景点；再临时移除变量或阻止脚本加载，确认离线 SVG 仍可点击。不得把 Key 截图、复制到终端或写入 PR。

```powershell
git add README.md docs/deployment/free-tier.md .env.example tests/integration/test_deployment_config.py
git commit -m "docs: explain amap explore trial deployment"
```

## 计划自检

- 高德运行时配置由任务 1 覆盖；地图层级与降级由任务 2 覆盖；参考图视觉和助手联动由任务 3 覆盖；部署与回归由任务 4 覆盖。
- 所有数据接口、函数名与测试目标在任务中明确；无实际 Key、无占位实现、无评测数据改动。
