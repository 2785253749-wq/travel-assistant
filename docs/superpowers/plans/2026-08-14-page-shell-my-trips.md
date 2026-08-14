# 页面框架与我的行程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Voyage 单页静态界面升级为可切换的探索、我的行程、社区三视图，并完成已登录用户的行程列表闭环。

**Architecture:** 保留现有原生 HTML/CSS/JavaScript，不引入前端框架和 URL 路由。使用 `activeView` 管理三个视图，复用现有认证、`/api/trips`、行程详情、分享和重命名逻辑；社区只实现真实空状态，不调用不存在的接口。

**Tech Stack:** 原生 HTML、CSS、JavaScript、Node `node:test` 前端测试、现有 DOM harness。

## Global Constraints

- 不改变后端认证、行程、分享接口和现有 API 响应结构。
- 不重写地图、天气、AI 助手和行程生成逻辑。
- 不实现社区帖子、评论、点赞、审核或搜索。
- 未登录用户不能看到私有行程数据；接口请求必须继续使用现有授权流程。
- 所有隐藏视图必须不可被键盘焦点访问；导航必须更新 `aria-current`。
- 只修改本功能需要的文件，不暂存 `docs/work-log-*` 和用户已有计划文件。
- 每个任务遵循：先写失败测试，再写最小实现，再运行相关测试，再提交一个小 commit。

---

### Task 1: 建立三视图 DOM 和导航状态

**Files:**
- Modify: `app/static/index.html:16-20,94-102` — 给导航按钮增加稳定 ID 和 `data-view`，将行程历史包入 `#trips-page`，新增 `#community-page` 空状态。
- Modify: `app/static/app.js:28-44,1069-1095` — 增加页面元素引用、`activeView` 和导航绑定。
- Test: `tests/frontend/app.test.js` — 增加导航视图切换测试。
- Modify: `tests/frontend/dom-harness.js:85-111` — 仅在测试需要时补齐新父子关系或属性解析。

**Interfaces:**
- Consumes: 现有 `elements.explorePage`、`elements.history`、`initializeApp()`。
- Produces: `switchView(view)`，支持 `explore`、`trips`、`community` 三个值；默认视图为 `explore`。

- [ ] **Step 1: Write the failing test**

在 `tests/frontend/app.test.js` 增加一个测试，使用新增的导航按钮 ID，验证点击后只有目标视图可见：

```js
test("navigation switches between explore, trips, and community without a reload", async () => {
  const harness = createHarness();
  await settle();

  const tripsNav = harness.elements.get("trips-nav-button");
  const communityNav = harness.elements.get("community-nav-button");
  const explore = harness.elements.get("explore-page");
  const trips = harness.elements.get("trips-page");
  const community = harness.elements.get("community-page");

  await tripsNav.dispatch("click");
  assert.equal(explore.hidden, true);
  assert.equal(trips.hidden, false);
  assert.equal(community.hidden, true);
  assert.equal(tripsNav.getAttribute("aria-current"), "page");

  await communityNav.dispatch("click");
  assert.equal(trips.hidden, true);
  assert.equal(community.hidden, false);
  assert.equal(harness.elements.get("explore-nav-button").getAttribute("aria-current"), null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/frontend/app.test.js --test-name-pattern="navigation switches"`

Expected: FAIL because the new navigation IDs and view elements do not exist.

- [ ] **Step 3: Write minimal DOM and state implementation**

In `app/static/index.html`:

```html
<button id="explore-nav-button" class="navigation-item is-active" data-view="explore" aria-current="page" type="button">探索</button>
<button id="trips-nav-button" class="navigation-item" data-view="trips" type="button">我的行程</button>
<button id="community-nav-button" class="navigation-item" data-view="community" type="button">社区</button>
```

Keep `#explore-page` as the first view. Wrap the existing history region in:

```html
<section id="trips-page" hidden aria-labelledby="trips-page-title">
  <div id="trips-auth-prompt" class="empty-state" hidden></div>
  <h1 id="trips-page-title">我的行程</h1>
  <!-- existing private history list remains inside this view -->
</section>
<section id="community-page" hidden aria-labelledby="community-page-title">
  <h1 id="community-page-title">社区</h1>
  <p class="empty-state">社区功能即将开放。</p>
</section>
```

In `app/static/app.js`, add a finite view set and a single switch function:

```js
const VIEWS = new Set(["explore", "trips", "community"]);

function switchView(view) {
  if (!VIEWS.has(view)) return;
  state.activeView = view;
  for (const [name, element] of [["explore", elements.explorePage], ["trips", elements.tripsPage], ["community", elements.communityPage]]) {
    element.hidden = name !== view;
  }
  for (const button of elements.navigation) {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}
```

Bind each navigation button once during initialization and call `switchView("explore")` before existing explore/auth initialization.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/frontend/app.test.js --test-name-pattern="navigation switches"`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html app/static/app.js tests/frontend/app.test.js tests/frontend/dom-harness.js
git commit -m "feat: add single-page navigation views"
```

---

### Task 2: Complete the “我的行程” authentication and data states

**Files:**
- Modify: `app/static/index.html` — add login prompt, loading text, empty state, and retry target inside `#trips-page`.
- Modify: `app/static/app.js:789-900` — make session changes update the trips view and reuse `refreshHistory()`.
- Test: `tests/frontend/app.test.js` — add signed-out, empty, populated, and request-failure cases.

**Interfaces:**
- Consumes: `state.session`, `applySession()`, `clearSession()`, `requestJson("/api/trips")`, `historyItem()`.
- Produces: `renderTripsPage()` and `renderTripsState(stateName)`; no new backend API.

- [ ] **Step 1: Write the failing tests**

Add tests covering the public behavior:

```js
test("signed-out trips view shows a login prompt instead of requesting private trips", async () => {
  const harness = createHarness();
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  assert.equal(harness.elements.get("trips-auth-prompt").hidden, false);
  assert.equal(harness.fetchCalls.some((call) => call.url === "/api/trips"), false);
});

test("signed-in trips view renders the empty and populated states", async () => {
  const auth = new FakeSupabaseAuth({ initialSession: SESSION });
  const harness = createHarness({
    auth,
    fetch: async (call) => call.url === "/api/trips"
      ? jsonResponse(200, [{ id: "trip-1", title: "厦门三日游", updated_at: "2026-08-14T00:00:00Z" }])
      : jsonResponse(200, {}),
  });
  await settle();
  await harness.elements.get("trips-nav-button").dispatch("click");

  assert.match(harness.elements.get("trip-history-list").textContent, /厦门三日游/);
  assert.equal(harness.elements.get("trips-auth-prompt").hidden, true);
});
```

Also add a 200-empty response assertion for `还没有保存的行程。` and a failed `/api/trips` response assertion for a retryable error message.

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/frontend/app.test.js --test-name-pattern="trips view|signed-in trips"`

Expected: FAIL because the trips view prompt and state renderer do not exist.

- [ ] **Step 3: Implement the state flow**

Implement these rules in `app/static/app.js`:

1. `switchView("trips")` calls `renderTripsPage()`.
2. If `state.session` is absent, show `#trips-auth-prompt`, hide the private history region, and do not call `/api/trips`.
3. If `state.session` exists, hide the prompt and call the existing `refreshHistory()`.
4. `refreshHistory()` shows loading text before the request, renders the existing list on success, keeps the empty text for an empty array, and renders a retry button on failure without hiding the whole application.
5. `applySession()` refreshes the trips view only when it is active; `clearSession()` clears list contents, hides private history, and returns the trips view to the signed-out prompt if it is active.
6. The prompt’s login action opens the existing `account-menu` summary and focuses the email field.

- [ ] **Step 4: Run tests to verify behavior**

Run: `node --test tests/frontend/app.test.js --test-name-pattern="trips view|signed-in trips|logout clears"`

Expected: PASS with existing logout and private-data cleanup tests still green.

- [ ] **Step 5: Commit**

```bash
git add app/static/index.html app/static/app.js tests/frontend/app.test.js
git commit -m "feat: complete my trips page states"
```

---

### Task 3: Add responsive page and empty-state styling

**Files:**
- Modify: `app/static/styles.css:55-110,160-184` — style the three page containers, trips cards, auth prompt, retry state, and community placeholder.
- Test: `tests/frontend/app.test.js` — keep behavior tests structural; use manual browser verification for visual layout.

**Interfaces:**
- Consumes: the class names and IDs added in Tasks 1–2.
- Produces: consistent desktop and mobile layouts without changing JavaScript behavior.

- [ ] **Step 1: Add the styling contract**

Use shared container styles for `#explore-page`, `#trips-page`, and `#community-page`; use a single-column layout below `620px`; ensure `.empty-state` has readable contrast and no disabled-looking fake action.

- [ ] **Step 2: Run the frontend test suite**

Run: `node --test tests/frontend/*.test.js`

Expected: all existing and new tests pass.

- [ ] **Step 3: Perform manual visual checks**

Check at desktop and mobile widths:

- active navigation underline follows the current view;
- hidden views do not occupy layout space;
- trip cards remain readable in one column;
- AI assistant remains fixed and usable above the view content;
- keyboard focus is visible on navigation and login prompt.

- [ ] **Step 4: Commit**

```bash
git add app/static/styles.css
git commit -m "style: polish page views and trip states"
```

---

### Task 4: Run the full regression and prepare the handoff

**Files:**
- Modify: `docs/handoff-2026-08-14.md` only if the final verification results or branch name need updating.

- [ ] **Step 1: Run frontend regression**

Run: `node --test tests/frontend/*.test.js`

Expected: 0 failures.

- [ ] **Step 2: Run relevant backend regression**

Run: `& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest tests/unit/test_chat_api.py tests/unit/test_trips.py -q`

Expected: 0 failures; no backend files should be changed by this feature.

- [ ] **Step 3: Run the public repository safety check**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`

Expected: pass with no exposed secret findings.

- [ ] **Step 4: Inspect the diff and working tree**

Run: `git diff --check` and `git status -sb`.

Expected: only the intended page, test, style, and plan/spec changes are tracked; existing user-owned untracked documents remain untouched.

- [ ] **Step 5: Commit any handoff-only update**

```bash
git add docs/handoff-2026-08-14.md
git commit -m "docs: record page shell verification"
```

Do not stage the existing untracked work logs or user plan files.
