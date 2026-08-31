# 可拖动 AI 助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Voyage AI 助手改为默认收起、按钮切换开关、可在浏览器视口内自由拖动的悬浮窗。

**Architecture:** 保持现有 `assistant-panel`、`assistant-toggle` 与聊天 API。由 `app.js` 集中维护可见性和拖动坐标；地图选择只追加本地消息。CSS 只负责悬浮样式、拖动手柄和响应式尺寸。

**Tech Stack:** 原生 HTML、CSS、JavaScript、Node 内置测试运行器与现有 DOM harness。

## Global Constraints

- 初始状态只显示右下角“打开 AI 助手”按钮；地图、省份、城市、景点选择不得自动打开助手。
- 同一开关按钮在打开时关闭面板；`Esc` 关闭且恢复开关焦点；不保留单独关闭叉号。
- 仅顶部标题栏可拖动，鼠标和触屏均使用 Pointer Events；面板四边始终保留至少 12px 可见边距。
- 不保存拖动坐标，刷新回到右下角；不新增依赖，不调用 `/api/chat` 以响应本地地图选择。
- 不修改 AI、行程、鉴权、配额、数据库、AMap 配置或密钥处理。
- 不暂存或提交既有未跟踪的用户工作文档。

---

### Task 1: 助手开关与非阻塞地图推荐

**Files:**
- Modify: `index.html`：移除独立关闭按钮；为顶部标题区域提供 `id="assistant-drag-handle"` 和可访问提示。
- Modify: `app/static/app.js`：以 `setAssistantOpen(open, options)` 集中更新隐藏状态、`aria-expanded` 与焦点；地图推荐只追加消息。
- Test: `tests/frontend/app.test.js`：覆盖初始关闭、开关切换、Esc 和地图选择不弹窗。

**Interfaces:**
- Produces: `setAssistantOpen(open, { focusInput, restoreFocus })`，只由用户手动点击路径聚焦输入框。
- Consumes: `assistantPanel`、`assistantToggle`、`message` 与 `appendExploreRecommendation(selection)`。

- [ ] **Step 1: 写失败测试**

```js
test('assistant toggle opens and closes the panel without a dedicated close button', () => {
  const page = loadApp();
  assert.equal(page.assistantPanel.hidden, true);
  page.assistantToggle.click();
  assert.equal(page.assistantPanel.hidden, false);
  page.assistantToggle.click();
  assert.equal(page.assistantPanel.hidden, true);
});

test('map selections append recommendations without opening a closed assistant', () => {
  const page = loadAppWithMap();
  page.selectPlace('gulangyu');
  assert.equal(page.assistantPanel.hidden, true);
  assert.equal(page.fetchCalls.some((url) => url === '/api/chat'), false);
});
```

- [ ] **Step 2: 验证 RED**

Run: `node --test tests/frontend/app.test.js`

Expected: FAIL，因为旧代码会由地图选择调用 `openAssistant()`，且仍依赖独立关闭按钮。

- [ ] **Step 3: 最小实现**

```js
function setAssistantOpen(open, { focusInput = false, restoreFocus = false } = {}) {
  elements.assistantPanel.hidden = !open;
  elements.assistantToggle.setAttribute('aria-expanded', String(open));
  if (open && focusInput) elements.message.focus();
  if (!open && restoreFocus) elements.assistantToggle.focus();
}

function appendExploreRecommendation(selection) {
  addMessage(selection.recommendation, 'assistant');
}
```

- [ ] **Step 4: 验证 GREEN**

Run: `node --test tests/frontend/app.test.js`

Expected: PASS，既有登录、行程与分享测试保持通过。

- [ ] **Step 5: 提交**

```bash
git add index.html app/static/app.js tests/frontend/app.test.js
git commit -m "feat: make assistant opt-in toggle"
```

### Task 2: 标题栏拖动与视口边界

**Files:**
- Modify: `app/static/app.js`：添加 `initializeAssistantDrag()`，在标题栏的 Pointer Events 中更新面板内联坐标。
- Modify: `app/static/styles.css`：拖动手柄的 `cursor`、`touch-action` 和窄屏悬浮尺寸。
- Test: `tests/frontend/app.test.js`：覆盖鼠标/触屏拖动和 12px 边界。

**Interfaces:**
- Produces: `initializeAssistantDrag()` 和 `clamp(value, min, max)`。
- Consumes: `assistant-drag-handle`、面板的 `getBoundingClientRect()`、`window.innerWidth`、`window.innerHeight`。

- [ ] **Step 1: 写失败测试**

```js
test('drag handle moves the open assistant and clamps it within viewport margins', () => {
  const page = loadApp({ viewport: { width: 900, height: 700 } });
  page.assistantToggle.click();
  page.dragHandle.dispatchEvent(pointer('pointerdown', { pointerId: 4, clientX: 760, clientY: 420 }));
  page.dragHandle.dispatchEvent(pointer('pointermove', { pointerId: 4, clientX: -400, clientY: 2000 }));
  page.dragHandle.dispatchEvent(pointer('pointerup', { pointerId: 4, clientX: -400, clientY: 2000 }));
  assert.equal(page.assistantPanel.style.left, '12px');
  assert.equal(page.assistantPanel.hidden, false);
});
```

- [ ] **Step 2: 验证 RED**

Run: `node --test tests/frontend/app.test.js`

Expected: FAIL，因为旧代码没有 pointer 拖动监听器或面板坐标。

- [ ] **Step 3: 最小实现**

```js
function clamp(value, min, max) { return Math.min(Math.max(value, min), max); }

function initializeAssistantDrag() {
  const handle = document.getElementById('assistant-drag-handle');
  let drag = null;
  handle.addEventListener('pointerdown', (event) => {
    const rect = elements.assistantPanel.getBoundingClientRect();
    drag = { pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top };
    handle.setPointerCapture(event.pointerId);
  });
  handle.addEventListener('pointermove', (event) => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = elements.assistantPanel.getBoundingClientRect();
    const left = clamp(event.clientX - drag.offsetX, 12, window.innerWidth - rect.width - 12);
    const top = clamp(event.clientY - drag.offsetY, 12, window.innerHeight - rect.height - 12);
    Object.assign(elements.assistantPanel.style, { left: `${left}px`, top: `${top}px`, right: 'auto', bottom: 'auto' });
  });
}
```

- [ ] **Step 4: 验证 GREEN 与完整前端回归**

Run: `node --test tests/frontend/app.test.js && node --test tests/frontend/*.test.js`

Expected: PASS，拖动不改变面板开关状态，不新增网络请求。

- [ ] **Step 5: 相关 Python 资源契约、扫描与提交**

```bash
C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m pytest tests/unit/test_config.py tests/integration/test_frontend_assets.py -q
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
git add app/static/app.js app/static/styles.css tests/frontend/app.test.js
git commit -m "feat: add draggable AI assistant panel"
```

Expected: 资源契约与公开仓库扫描通过；提交不含密钥或用户未跟踪文档。

## 验收总门禁

- [ ] `node --test tests/frontend/*.test.js`
- [ ] `C:\tmp\travel-assistant-verify-py\Scripts\python.exe -m pytest tests/unit/test_config.py tests/integration/test_frontend_assets.py -q`
- [ ] `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`
- [ ] `git diff --check`
