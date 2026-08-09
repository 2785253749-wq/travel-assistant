# 悬浮 AI 助手与响应式页面壳 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以不占据主内容宽度的右下角悬浮助手替代长侧栏，并为后续 Voyage 页面提供响应式壳。

**Architecture:** 保留现有聊天 API、认证和 `collect -> confirm` 流程。浏览器新增独立的浮层开关状态：桌面固定小窗，窄屏使用底部抽屉；原聊天节点移动到浮层内部，主内容预留页面导航区域。

**Tech Stack:** HTML、CSS、原生 JavaScript、Node DOM harness。

## Global Constraints

- 不改 API、Supabase 数据模型、额度逻辑、AI 提示词或保存行程的服务端边界。
- 仅用户发送文本时调用既有 `/api/chat`；本地地图推荐不得发起模型调用。
- 中文为主；助手可用 Escape 和关闭按钮关闭，关闭后焦点回到打开按钮。
- 窄屏为底部抽屉；`prefers-reduced-motion: reduce` 禁用弹出动画。

---

### Task 1: 悬浮助手 DOM 与开关状态

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Test: `tests/frontend/app.test.js`

**Interfaces:**
- Consumes: 现有聊天表单、`setBusy` 状态及 `state`。
- Produces: `openAssistant()` 与 `closeAssistant()`，并将焦点稳定地移回 `#assistant-toggle`。

- [ ] **Step 1: 写失败的 DOM 测试**

```js
const assistant = harness.elements.get("assistant-panel");
const toggle = harness.elements.get("assistant-toggle");
assert.equal(assistant.hidden, true);
await toggle.dispatch("click");
assert.equal(assistant.hidden, false);
await harness.window.dispatch("keydown", { key: "Escape" });
assert.equal(assistant.hidden, true);
assert.equal(toggle.focused, true);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/frontend/app.test.js`

Expected: FAIL，因为浮层、开关按钮和关闭行为尚不存在。

- [ ] **Step 3: 实现最小浮层行为**

在 `index.html` 添加 `#assistant-toggle`、`#assistant-panel` 和 `#assistant-close`；将现有聊天区域放入 `#assistant-panel`。在 `app.js` 中实现：

```js
function openAssistant() { elements.assistantPanel.hidden = false; elements.message.focus(); }
function closeAssistant() { elements.assistantPanel.hidden = true; elements.assistantToggle.focus(); }
```

为关闭按钮和全局 Escape 绑定 `closeAssistant`；初始化保持 `hidden`。

- [ ] **Step 4: 添加桌面与移动端样式**

桌面样式将 `#assistant-panel` 固定在右下角，最大宽度 380px、最大高度 520px。`@media (max-width: 900px)` 将其改为底部抽屉，宽度 `calc(100% - 24px)`、最大高度 `78vh`。`prefers-reduced-motion: reduce` 取消 transform 和 transition。

- [ ] **Step 5: 运行测试确认通过并提交**

Run: `node --test tests/frontend/app.test.js`

Expected: PASS，初始关闭、点击打开、Escape 关闭和焦点恢复均通过。

Commit:

```bash
git add app/static/index.html app/static/app.js app/static/styles.css tests/frontend/app.test.js
git commit -m "feat: add responsive floating assistant"
```

### Task 2: 页面主内容壳与不遮挡验证

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/styles.css`
- Test: `tests/frontend/app.test.js`

**Interfaces:**
- Consumes: `#trip-view`、`#trip-content` 与浮层助手。
- Produces: 包含主导航及主内容的页面壳，助手不再作为右侧长栏。

- [ ] **Step 1: 写失败的结构测试**

```js
assert.equal(harness.elements.get("main-navigation").hidden, false);
assert.equal(harness.elements.get("trip-view").hidden, true);
assert.equal(harness.elements.get("assistant-panel").hidden, true);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `node --test tests/frontend/app.test.js`

Expected: FAIL，因为页面导航壳尚不存在。

- [ ] **Step 3: 添加最小中文导航壳**

在 `index.html` 添加语义化 `nav#main-navigation`，包含“探索”“我的行程”“社区”按钮。样式使用单行可横向滚动的窄屏导航；此任务不实现地图、行程工作台或社区数据。

- [ ] **Step 4: 运行全量验证**

Run: `node --test tests/frontend/app.test.js`

Run: `python -m pytest -q`

Expected: 前端测试和 Python 测试通过。

- [ ] **Step 5: 提交**

```bash
git add app/static/index.html app/static/styles.css tests/frontend/app.test.js
git commit -m "feat: add responsive voyage page shell"
```
