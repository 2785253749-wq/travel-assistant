# 中文行程字段显示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将行程预算分类与每日时段的固定字段统一显示为中文。

**Architecture:** 在 `app/static/app.js` 的渲染层定义两份固定键名映射。预算卡片和每日活动卡片读取映射后的标签；未映射字段回退为原键名，接口和行程 JSON 不变。

**Tech Stack:** 原生 JavaScript、Node DOM 测试、现有 DOM harness。

## Global Constraints

- 只改浏览器展示层，不改 API、数据库、AI 提示词或行程 JSON。
- 保留金额数值和 `CNY` 币种显示。
- 未定义字段必须显示原键名，不能留空或报错。

---

### Task 1: 中文标签渲染

**Files:**
- Modify: `app/static/app.js:4-8, 240-273`
- Test: `tests/frontend/app.test.js`

**Interfaces:**
- Consumes: itinerary `budget` 键和每日 `morning`、`afternoon`、`evening` 活动键。
- Produces: 页面文本“交通、住宿、餐饮、门票、预留、其他、合计、上午、下午、晚上”。

- [x] **Step 1: 写失败的前端 DOM 测试**

在现有行程渲染测试中断言预算和每日活动使用中文标签：

```js
assert.match(document.body.textContent, /交通：2000 CNY/);
assert.match(document.body.textContent, /合计：4000 CNY/);
assert.match(document.body.textContent, /上午：抵达厦门/);
assert.doesNotMatch(document.body.textContent, /transport:/);
assert.doesNotMatch(document.body.textContent, /morning:/);
```

- [x] **Step 2: 运行测试确认失败**

Run: `node --test tests/frontend/app.test.js`

Expected: FAIL，因为当前页面仍显示 `transport` 和 `morning`。

- [x] **Step 3: 添加最小显示映射**

在 `app/static/app.js` 中新增：

```js
const BUDGET_LABELS = Object.freeze({ transport: "交通", hotel: "住宿", food: "餐饮", tickets: "门票", reserve: "预留", other: "其他", total: "合计" });
const ACTIVITY_SLOT_LABELS = Object.freeze({ morning: "上午", afternoon: "下午", evening: "晚上" });
```

将预算和活动标题中的键名替换为 `BUDGET_LABELS[key] || key` 与 `ACTIVITY_SLOT_LABELS[slot] || slot`。

- [x] **Step 4: 运行前端测试确认通过**

Run: `node --test tests/frontend/app.test.js`

Expected: PASS，测试中的中文标签出现，英文固定字段不出现。

- [x] **Step 5: 检查差异并提交**

Run: `git diff --check`

Expected: exit 0。

Commit:

```bash
git add app/static/app.js tests/frontend/app.test.js
git commit -m "fix: localize itinerary field labels"
```
