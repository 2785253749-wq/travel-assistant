# 实时查询分句判定重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复分句造成的实时价格请求漏拒，同时保留明确不查动态信息的正常行程规划。

**Architecture:** `app.agent.safety` 将消息分割为带位置的子句。opt-out 仅作用于自身子句；动态查询判定在相邻子句窗口内聚合时间、旅行对象和动态需求信号，避免单个请求被冒号或换行断开。

**Tech Stack:** Python 3.13、pytest、现有离线评估 runner。

## Global Constraints

- 不修改 `tests/evaluation/cases.jsonl`、`baseline.json`、评估期望值或门槛。
- 不修改 Task 11 的 CI、Render、README 或公开仓库文件。
- 保持普通旅行建议、实际安全措施与明确“无需查询”的请求可处理。
- 实时价格、库存、余票以及绝对安全保证必须确定性拒答；不得编造事实。

---

### Task 1: 子句局部 opt-out 与相邻窗口实时判定

**Files:**
- Modify: `app/agent/safety.py`
- Modify: `tests/unit/test_agent_routes.py`
- Modify: `.superpowers/sdd/2026-07-28-zero-cost-public-mvp-implementation/task-10-optimization-report.md`

**Interfaces:**
- Consumes: `_REQUEST_CLAUSE_SEPARATOR`, `_DYNAMIC_LOOKUP_OPT_OUT`, `_REALTIME_MARKERS`, `_DYNAMIC_TRAVEL_SUBJECTS`, `_DYNAMIC_REQUEST_TERMS`.
- Produces: `_requests_realtime_dynamic_data(message: str) -> bool` that recognizes a true dynamic request across adjacent clauses without globally suppressing separate requests.

- [ ] **Step 1: 写失败回归测试**

```python
@pytest.mark.parametrize("message", ["明天机票：价格多少", "明天机票\\n价格多少"])
def test_realtime_signals_across_adjacent_clauses_are_refused(message):
    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


def test_opt_out_only_applies_to_its_own_clause():
    assert assess_message("机票价格不用查：明天只帮我安排行程").code is None
    assert assess_message("机票价格不用查：明天酒店价格多少").code == "UNVERIFIABLE_REALTIME_REQUEST"
```

- [ ] **Step 2: 运行 RED 测试**

Run: `python -m pytest tests/unit/test_agent_routes.py -k "adjacent_clauses or opt_out_only" -q`

Expected: 前两项因当前硬分句返回 `None` 而失败。

- [ ] **Step 3: 实现最小判定重构**

```python
def _requests_realtime_dynamic_data(message: str) -> bool:
    clauses = _split_request_clauses(message)
    for index, clause in enumerate(clauses):
        if _DYNAMIC_LOOKUP_OPT_OUT.search(clause):
            continue
        window = " ".join(clauses[index : index + 2])
        if _has_realtime_marker(window) and _has_dynamic_subject(window) and _has_dynamic_demand(window):
            return True
    return False
```

窗口不得越过相邻子句；如果当前子句是 opt-out，不能抑制下一子句独立的动态查询。

- [ ] **Step 4: 运行 GREEN 与完整验证**

Run: `python -m pytest tests/unit/test_agent_routes.py -q`

Expected: PASS。

Run: `python -m pytest -q`

Expected: PASS。

Run: `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation`

Expected: exit 0、80/80、`failures={}`、`known_failures=[]`。

- [ ] **Step 5: 记录并提交**

```bash
git add app/agent/safety.py tests/unit/test_agent_routes.py docs/superpowers/specs/2026-08-01-realtime-clause-refactor-design.md docs/superpowers/plans/2026-08-01-realtime-clause-refactor-implementation.md
git commit -m "fix: preserve realtime safety across clauses"
```
