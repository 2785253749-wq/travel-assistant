# Voyage 中文前端改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变旅行规划安全流程的前提下，交付中文 Voyage 风格探索、我的行程和静态社区页面，并以福建/云南完成离线地图试点。

**Architecture:** 保持 `app/static/index.html` 为入口和 `app/static/app.js` 为既有 API/认证层，新增独立的本地数据、地图状态和页面渲染模块。探索、行程、社区只通过页面状态切换；真实行程继续只从现有受信任 trip API 读取和操作。

**Tech Stack:** 原生 ES modules、HTML、CSS、本地 SVG/JSON、现有 FastAPI 静态路由、Node 前端测试与 pytest。

## Global Constraints

- 只有当前安全发布收尾、全分支审查和发布前验证通过后才能执行。
- 中文为主；仅 `Voyage AI` 等品牌名保留英文。
- 第一版离线可运行：无地图 Key、地图瓦片、外部图片或新付费 API。
- 地图试点仅福建、云南、厦门、福州、大理、丽江；全国扩展另开任务。
- 点击省、市、景点自动追加中文助手推荐；没有可靠数据时沿用既有拒答/降级文案。
- 不改变 `collect -> confirm`；浏览器不得提交原始 itinerary 或密钥。
- 社区只做本地静态展示/筛选；不做真实发帖、评论、点赞持久化。
- 不修改评测 cases、baseline 或阈值。

---

### Task 1: 设计令牌、导航和页面壳

**Files:** Modify `app/static/index.html`, `app/static/styles.css`, `app/static/app.js`, `tests/frontend/app.test.js`.

- [ ] 先写导航 RED：点击 `[data-page-target="community"]` 后断言 `body.dataset.page === "community"`、社区链接有 `aria-current="page"`、探索根节点 `hidden`。
- [ ] 运行 `node --test tests/frontend/app.test.js`，确认因页面根节点/导航不存在而失败。
- [ ] 建立 `explore-page`、`trips-page`、`community-page`；实现 `setActivePage(page)`，循环更新页面 `hidden` 与导航 `aria-current`。
- [ ] 用 CSS variables 定义深蓝、浅灰、白卡片、20–28px 圆角、阴影、中文字体和桌面/窄屏断点；保持 skip link、认证和聊天可达。
- [ ] 运行 Node 前端测试并提交：`feat: add voyage page shell`。

### Task 2: 本地地图数据与省市缩放

**Files:** Create `app/static/data/explore-map.js`, `app/static/map-explorer.js`, `app/static/assets/maps/china.svg`, `fujian.svg`, `yunnan.svg`; modify `index.html`, `styles.css`; test `tests/frontend/map-explorer.test.js`.

**Interface:** `createMapExplorer(root, { onSelect })` 返回 `showNation()`, `showProvince(id)`, `showCity(id)`；`onSelect` 接收 `{kind, id, name, recommendation}`。

- [ ] 写 RED：点击 `[data-map-id="fujian"]` 后断言 `root.dataset.level === "province"` 且出现厦门针点；再覆盖云南、市级、返回、Enter 键与 reduced-motion。
- [ ] 运行 `node --test tests/frontend/map-explorer.test.js`，确认模块不存在而失败。
- [ ] 在冻结 `MAP_NODES` 中定义两省、四市和每市三景点的本地坐标、中文推荐和占位卡片；组件使用内联 SVG、`viewBox`、`data-level`。
- [ ] 使用 transform/opacity 动画；`prefers-reduced-motion` 关闭动画；实现返回全国/返回省份。
- [ ] 运行地图测试并提交：`feat: add offline province map explorer`。

### Task 3: 探索地图到 AI 自动推荐

**Files:** Modify `app/static/app.js`, `index.html`, `styles.css`; test `tests/frontend/app.test.js`.

**Interface:** `appendExploreRecommendation(selection)` 仅将本地推荐用 `textContent` 写入助手区。

- [ ] 写 RED：选择厦门后聊天出现“厦门”，且 mock `fetch` 未收到 `/api/chat` 调用。
- [ ] 运行前端测试，确认联动不存在而失败。
- [ ] 将 `createMapExplorer` 的 `onSelect` 接到 `appendExploreRecommendation`；渲染本地景点卡片和快捷问题。快捷问题只能填入 textarea，必须由用户发送才调用既有 collect API。
- [ ] 回归 collect/confirm、XSS 与 URL 测试，提交：`feat: link map selections to voyage assistant`。

### Task 4: 我的行程三栏视图

**Files:** Create `app/static/trip-view.js`; modify `index.html`, `app.js`, `styles.css`; test `tests/frontend/trip-view.test.js`.

**Interface:** `renderTripWorkspace(root, trip)` 只接受服务端 GET/copy 返回 trip。

- [ ] 写 RED：fixture trip 渲染“第一天”“同行人数”和离线天气占位。
- [ ] 运行 `node --test tests/frontend/trip-view.test.js`，确认模块不存在而失败。
- [ ] 实现左侧真实计划列表、中间日程时间线、右侧预算/城市/人数摘要；保留服务端复制、删除、分享与重命名入口。
- [ ] 使用 `textContent`；不向服务器提交 itinerary；运行 trip view 和 app 回归，提交：`feat: render voyage trip workspace`。

### Task 5: 离线静态社区瀑布流

**Files:** Create `app/static/data/community-posts.js`, `app/static/community-feed.js`, `app/static/assets/placeholders/*.svg`; modify `index.html`, `styles.css`; test `tests/frontend/community-feed.test.js`.

**Interface:** `renderCommunityFeed(root, posts)`、`setCommunityFilter(category)`；post 为 `{id, category, title, author, likes, comments, image}`。

- [ ] 写 RED：点击“美食地图”后只出现 `data-post-category="food"` 卡片。
- [ ] 运行社区测试，确认模块不存在而失败。
- [ ] 创建冻结的中文本地帖子与 SVG 占位图；用 CSS grid 实现瀑布流；发布按钮显示“演示模式，暂不发布”，不调用 API。
- [ ] 运行社区测试并提交：`feat: add offline community feed`。

### Task 6: 响应式、无障碍与发布验收

**Files:** Modify `README.md`, `docs/deployment/free-tier.md`, `app/static/styles.css`, `tests/frontend/app.test.js`.

- [ ] 写 RED：390px 视口下探索页仍有可见聊天输入、页面导航与焦点顺序。
- [ ] 实现小屏纵向/可切换布局、清晰焦点、离线数据提示和 reduced-motion。
- [ ] README 写明福建/云南试点、离线资源与全国扩展前提；部署文档说明无新增地图密钥。
- [ ] 运行 `node --test tests/frontend/*.test.js`、`pytest -q`、`python tests/evaluation/runner.py`；全部通过且评测数据/阈值无 diff 后提交：`docs: document voyage frontend trial`。

## 计划自检

- 探索试点由 Task 2–3 覆盖；真实行程页由 Task 4 覆盖；静态社区由 Task 5 覆盖；中文、离线、响应式和回归由 Task 1、6 覆盖。
- 计划没有引入在线地图、真实社区、真实图片或付费服务，也不改变确认/权限/额度边界。
- 跨任务接口固定为地图 selection、受信任 trip 和本地 post 数据，避免演示数据进入用户行程。
