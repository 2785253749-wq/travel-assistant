# RAG 试点景点别名识别实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让唯一归属试点地区的景点问答在未写城市时安全进入 RAG 检索，同时保留未知或歧义地点的拒答边界。

**Architecture:** 在 `app.agent.graph` 增加纯本地、可测试的景点别名解析器。仅在 `travel_knowledge` 意图且显式地区缺失时使用其返回值作为 `KnowledgeAnswerer.answer()` 的 `region` 参数；不修改用户原始问题，不新增网络调用。

**Tech Stack:** Python 3.13、FastAPI、pytest。

## Global Constraints

- 仅支持知识库已覆盖且唯一归属厦门/福建/云南试点的景点；未知或歧义地点必须拒答，不得全库检索或猜测城市。
- 天气问答、行程收集、修改行程和解释行程的既有意图优先级不得改变。
- 测试必须执行原始用户问题；不得在评测代码中为问题拼接地区名称。
- 不访问外部 API，不执行 Supabase 迁移，不修改 Render 配置，不写入或输出任何密钥。
- 不暂存或提交既有未跟踪计划、工作日志和 SDD 报告。

---

### Task 1: 试点景点别名解析与真实聊天接线

**Files:**
- Modify: `app/agent/graph.py`
- Modify: `tests/unit/test_agent_graph.py`
- Modify: `tests/evaluation/runner.py`
- Test: `tests/unit/test_agent_graph.py`
- Test: `tests/evaluation/test_runner.py`

**Interfaces:**
- Produces: `_knowledge_region(message: str) -> str | None`，显式地区优先；无显式地区时仅返回唯一试点景点的地区；未知/歧义为 `None`。
- Consumes: `KnowledgeAnswerer.answer(question: str, region: str | None = None) -> RagAnswer`。
- Produces: `SafeTravelAgent._special_intent_result()` 将原始 `message` 与解析后的 `region` 一起交给知识问答服务。

- [ ] **Step 1: 写失败的唯一别名聊天测试**

```python
def test_unique_trial_place_alias_routes_raw_question_to_xiamen_knowledge() -> None:
    knowledge = RecordingKnowledgeAnswerer.grounded("厦门鼓浪屿避坑资料")
    result = SafeTravelAgent(
        classifier=RuleIntentClassifier(), knowledge=knowledge
    ).run("鼓浪屿游玩前需要怎样安排？", trip=None)

    assert knowledge.calls == [("鼓浪屿游玩前需要怎样安排？", "厦门")]
    assert result.intent == "travel_knowledge"
    assert result.sources
```

- [ ] **Step 2: 运行测试确认 RED**

Run: `python -m pytest tests/unit/test_agent_graph.py -k unique_trial_place_alias -q`

Expected: FAIL，因为现有 `_knowledge_region()` 对未含城市名的问题返回 `None`。

- [ ] **Step 3: 写失败的未知/歧义拒答与原始评测测试**

```python
def test_unknown_place_refuses_without_retrieval() -> None:
    knowledge = RecordingKnowledgeAnswerer.fail_if_called()
    result = SafeTravelAgent(
        classifier=RuleIntentClassifier(), knowledge=knowledge
    ).run("海边古城游玩前怎么安排？", trip=None)

    assert knowledge.calls == []
    assert result.error_code == "KNOWLEDGE_UNAVAILABLE"
    assert "补充目的地城市" in result.reply

def test_rag_evaluation_uses_case_question_without_region_prefix() -> None:
    captured = run_one_rag_case(case_with_region_and_raw_question()).question
    assert captured == "鼓浪屿游玩前需要怎样安排？"
```

- [ ] **Step 4: 运行测试确认 RED**

Run: `python -m pytest tests/unit/test_agent_graph.py tests/evaluation/test_runner.py -k 'unknown_place or without_region_prefix' -q`

Expected: FAIL，因为未知问题目前仍会调用知识服务，评测运行器会向问题添加地区前缀。

- [ ] **Step 5: 编写最小实现**

```python
_TRIAL_PLACE_REGIONS: dict[str, str] = {
    "鼓浪屿": "厦门",
    # 仅加入现有 YAML 知识库中唯一归属试点地区的景点。
}

def _knowledge_region(message: str) -> str | None:
    explicit = _explicit_trial_region(message)
    if explicit is not None:
        return explicit
    matches = {region for alias, region in _TRIAL_PLACE_REGIONS.items() if alias in message}
    return next(iter(matches)) if len(matches) == 1 else None
```

在 `travel_knowledge` 分支中先解析地区：若 `None`，直接返回固定中文“请补充目的地城市”的拒答结果，不调用 `KnowledgeAnswerer`；否则按原始消息和该地区调用服务。修改评测运行器，删除所有把 `case.region` 拼入问题文本的逻辑，并让评测断言真实解析后的调用地区。

- [ ] **Step 6: 运行目标测试确认 GREEN**

Run: `python -m pytest tests/unit/test_agent_graph.py tests/evaluation/test_runner.py -k 'unique_trial_place_alias or unknown_place or without_region_prefix' -q`

Expected: PASS。

- [ ] **Step 7: 运行回归、公开扫描并提交**

Run:

```powershell
python -m pytest tests/unit/test_agent_graph.py tests/evaluation -q
python -m tests.evaluation.runner --cases tests/evaluation/rag_weather_cases.jsonl --output build/evaluation-place-alias
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
git diff --check
```

Expected: 所有目标测试通过；评测使用原始问题，门禁仍达标；扫描与 diff 检查通过。

```powershell
git add app/agent/graph.py tests/unit/test_agent_graph.py tests/evaluation/runner.py tests/evaluation/test_runner.py
git commit -m "fix: resolve unique RAG place aliases safely"
```
