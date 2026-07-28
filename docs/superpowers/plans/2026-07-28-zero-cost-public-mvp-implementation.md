# 旅行助手零成本公开 MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 8 周内把现有旅行规划原型建设为面向国内 2～7 天自由行用户的零平台月费公开 MVP，并达到安全拒答、持久化、量化评估和可部署要求。

**Architecture:** 保留 FastAPI + LangGraph + DeepSeek 的 Python 单体边界，在单体内部拆分 API、Agent、行程领域、外部 provider 与核心工程模块。生产数据和认证使用 Supabase 免费层，FastAPI 部署到 Render 免费 Web Service；所有收费模型调用通过服务端配额和熔断控制。

**Tech Stack:** Python 3.13、FastAPI、LangGraph、LangChain DeepSeek、Pydantic Settings、Supabase/Postgres、原生 HTML/CSS/JavaScript、Pytest、HTTPX、GitHub Actions、Render。

## Global Constraints

- 开发者为 1 人，每天投入至少 5 小时，计划周期为 8 周、约 220～240 小时。
- 托管、数据库、地图、天气等平台月费必须为 0 元，并接受冷启动、二级域名和免费额度限制。
- 保留维护者现有 DeepSeek Key，但 API Key 只能存在服务端 Secret 中，且必须有单用户、全站每日限额和总开关。
- 产品只处理国内 2～7 天、1～6 人的行前自由行规划，不做通用问答。
- 缺少关键信息必须追问；事实无法核实时必须标记待确认或拒答，不得编造。
- 不接入实时机票、酒店、铁路价格和库存；不执行支付、预订、出票或退改签。
- 评测集固定为 80 条，发布门槛以设计文档第 6.2 节为准。
- 生产数据不得写入 Render 本地文件系统；所有用户表启用 Row Level Security。
- 每个任务遵循测试先行；完成任务后运行相关测试并做独立提交。

---

## Planned File Structure

```text
app/
  main.py                         # FastAPI 组装、静态资源和生命周期
  api/
    auth.py                       # Bearer token 校验与当前用户依赖
    chat.py                       # 聊天 HTTP 接口
    trips.py                      # 行程 CRUD 和分享 HTTP 接口
  agent/
    graph.py                      # LangGraph 节点和状态路由
    intent.py                     # 五类意图模型与识别
    extraction.py                 # 旅行字段提取和合并
    planning.py                   # 结构化行程生成与修复
    safety.py                     # 拒答、事实来源和能力边界
  core/
    config.py                     # 分环境配置与启动校验
    errors.py                     # 稳定错误码和用户安全提示
    logging.py                    # JSON 日志和 request_id
    usage.py                      # DeepSeek 配额与总开关
  providers/
    base.py                       # 天气、地点、搜索链接接口
    free_weather.py               # 免费天气适配器
    places.py                     # 免费地点适配器与 query 重写
    booking_links.py              # 机票、酒店、铁路外链生成
  trips/
    models.py                     # 行程、消息、分享领域模型
    repository.py                 # 存储协议
    service.py                    # 行程与分享用例
  infrastructure/
    supabase.py                   # Supabase Auth/REST 网关
    repositories.py              # Supabase repository 实现
  static/
    index.html
    app.js
    styles.css
supabase/migrations/001_initial.sql
tests/
  unit/
  integration/
  evaluation/
    cases.jsonl
    runner.py
    test_metrics.py
  fixtures/
    providers.py
scripts/verify_public_repo.ps1
.github/workflows/ci.yml
render.yaml
```

---

### Task 1: 工程骨架、配置和结构化日志

**Files:**
- Create: `app/core/config.py`
- Create: `app/core/errors.py`
- Create: `app/core/logging.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_logging.py`

**Interfaces:**
- Produces: `Settings`, `get_settings() -> Settings`, `AppError`, `configure_logging() -> None`, `request_context(request: Request, call_next) -> Response`。
- Consumes: 环境变量，不读取调用者传入的字典。

- [ ] **Step 1: 编写配置失败测试**

```python
def test_production_requires_supabase(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    with pytest.raises(ValueError, match="SUPABASE_URL"):
        Settings(_env_file=None)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_config.py -v`

Expected: FAIL，因为 `app.core.config.Settings` 尚不存在。

- [ ] **Step 3: 实现分环境配置和安全默认值**

```python
class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    deepseek_api_key: SecretStr
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_base: AnyHttpUrl = "https://api.deepseek.com"
    supabase_url: AnyHttpUrl | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_key: SecretStr | None = None
    ai_enabled: bool = True
    ai_user_daily_limit: int = Field(default=5, ge=0, le=100)
    ai_global_daily_limit: int = Field(default=100, ge=0, le=10_000)
```

生产环境验证器同时要求三项 Supabase 配置存在；`.env.example` 只放示例占位值和注释，不放真实凭据。

- [ ] **Step 4: 编写并实现 request_id 日志测试**

```python
def test_request_log_contains_correlation_fields(caplog, client):
    response = client.get("/health", headers={"X-Request-ID": "req-fixed"})
    record = next(r for r in caplog.records if getattr(r, "request_id", None))
    assert response.headers["X-Request-ID"] == "req-fixed"
    assert record.request_id == "req-fixed"
    assert not hasattr(record, "deepseek_api_key")
```

实现 JSON formatter，并让中间件记录 `request_id`、method、path、status_code 和 duration_ms。

- [ ] **Step 5: 运行相关测试**

Run: `python -m pytest tests/unit/test_config.py tests/unit/test_logging.py tests/test_app.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/core app/config.py app/main.py .env.example tests/unit/test_config.py tests/unit/test_logging.py
git commit -m "feat: add validated config and request logging"
```

---

### Task 2: 垂直领域模型、意图识别和确定性路由

**Files:**
- Create: `app/agent/intent.py`
- Create: `app/agent/extraction.py`
- Modify: `app/schemas.py`
- Test: `tests/unit/test_intent.py`
- Test: `tests/unit/test_extraction.py`

**Interfaces:**
- Produces: `Intent = Literal["plan_trip", "modify_trip", "explain_trip", "smalltalk", "unsupported"]`。
- Produces: `classify_intent(message: str, has_trip: bool) -> IntentResult`。
- Produces: `merge_profile(current: TravelProfile, extracted: TravelProfile) -> TravelProfile`。
- Consumes: `TravelProfile`，不访问数据库。

- [ ] **Step 1: 编写路由与字段合并测试**

```python
@pytest.mark.parametrize(("message", "expected"), [
    ("从上海去杭州玩三天", "plan_trip"),
    ("把第二天改成西湖", "modify_trip"),
    ("为什么推荐灵隐寺", "explain_trip"),
    ("你好", "smalltalk"),
    ("帮我写 Java 作业", "unsupported"),
])
def test_intent_contract(fake_intent_model, message, expected):
    assert classify_intent(message, has_trip=True, model=fake_intent_model).intent == expected
```

```python
def test_empty_extraction_does_not_erase_confirmed_values():
    current = TravelProfile(origin="上海", travelers=2)
    extracted = TravelProfile(origin=None, travelers=None)
    assert merge_profile(current, extracted) == current
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_intent.py tests/unit/test_extraction.py -v`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现结构化意图输出和确定性前置规则**

```python
class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)

def route_intent(result: IntentResult, has_trip: bool) -> Intent:
    if result.intent in {"modify_trip", "explain_trip"} and not has_trip:
        return "plan_trip"
    if result.confidence < 0.55:
        return "unsupported"
    return result.intent
```

意图模型使用 JSON mode；提示词只包含五类定义和边界，不生成最终用户回复。

- [ ] **Step 4: 实现旅行字段合并与一致性错误**

增加 `ProfileIssue(code, field, message)`；日期逆序、人数超出 1～6、行程超过 7 天分别返回稳定错误码，不让模型自行修复。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_intent.py tests/unit/test_extraction.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/agent/intent.py app/agent/extraction.py app/schemas.py tests/unit/test_intent.py tests/unit/test_extraction.py
git commit -m "feat: add travel intent and profile contracts"
```

---

### Task 3: Supabase 数据库、认证与数据隔离

**Files:**
- Create: `supabase/migrations/001_initial.sql`
- Create: `app/infrastructure/supabase.py`
- Create: `app/api/auth.py`
- Modify: `requirements.txt`
- Test: `tests/unit/test_auth.py`
- Test: `tests/integration/test_rls_contract.py`

**Interfaces:**
- Produces: `AuthenticatedUser(id: UUID, email: str | None)`。
- Produces: `get_current_user(authorization: str) -> AuthenticatedUser`。
- Produces tables: `profiles`, `trips`, `conversation_messages`, `share_links`, `ai_usage`。
- Consumes: Supabase JWT；不得信任浏览器提交的 user_id。

- [ ] **Step 1: 编写认证失败和身份映射测试**

```python
def test_missing_bearer_token_is_401(client):
    response = client.get("/api/trips")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"

def test_user_id_comes_from_verified_token(fake_supabase, client):
    fake_supabase.user_id = "11111111-1111-1111-1111-111111111111"
    response = client.get("/api/me", headers={"Authorization": "Bearer valid"})
    assert response.json()["id"] == fake_supabase.user_id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_auth.py -v`

Expected: FAIL，认证依赖尚不存在。

- [ ] **Step 3: 创建数据库迁移和 RLS 策略**

```sql
create table public.trips (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null check (char_length(title) between 1 and 100),
  status text not null check (status in ('collecting', 'planned')),
  profile jsonb not null default '{}'::jsonb,
  itinerary jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.trips enable row level security;
create policy "users manage own trips" on public.trips
for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
```

为其余四张表建立相同所有权边界；`share_links.token_hash` 唯一且不保存明文 token。

- [ ] **Step 4: 实现认证网关和依赖注入**

通过 Supabase Auth 验证 Bearer token；将网关作为 FastAPI dependency 注入，测试使用 fake gateway，不连接真实服务。

- [ ] **Step 5: 验证迁移和隔离契约**

Run: `python -m pytest tests/unit/test_auth.py tests/integration/test_rls_contract.py -v`

Expected: PASS；RLS 合约测试确认所有用户表包含 `user_id` 策略，分享读取不复用宽松策略。

- [ ] **Step 6: 提交**

```bash
git add supabase/migrations/001_initial.sql app/infrastructure/supabase.py app/api/auth.py requirements.txt tests/unit/test_auth.py tests/integration/test_rls_contract.py
git commit -m "feat: add supabase auth and row security"
```

---

### Task 4: 行程仓库、CRUD 和只读分享

**Files:**
- Create: `app/trips/models.py`
- Create: `app/trips/repository.py`
- Create: `app/trips/service.py`
- Create: `app/infrastructure/repositories.py`
- Create: `app/api/trips.py`
- Test: `tests/unit/test_trip_service.py`
- Test: `tests/integration/test_trip_api.py`

**Interfaces:**
- Produces: `TripRepository` Protocol，方法为 `create`, `get`, `list_for_user`, `update`, `delete`, `append_message`。
- Produces: `TripService.create_trip(user_id: UUID, profile: TravelProfile) -> Trip`。
- Produces: `TripService.create_share_link(user_id: UUID, trip_id: UUID, expires_in_days: int = 30) -> str`。
- Consumes: 已验证 user_id；repository 必须同时用 `trip_id` 与 `user_id` 查询私有数据。

- [ ] **Step 1: 编写所有权和分享测试**

```python
def test_other_user_cannot_read_trip(service, trip):
    with pytest.raises(AppError) as error:
        service.get_trip(USER_B, trip.id)
    assert error.value.code == "TRIP_NOT_FOUND"

def test_share_token_is_stored_as_hash(service, repository, trip):
    token = service.create_share_link(USER_A, trip.id)
    stored = repository.last_share_link
    assert token not in stored.token_hash
    assert stored.expires_at > datetime.now(UTC)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_trip_service.py tests/integration/test_trip_api.py -v`

Expected: FAIL，行程服务尚不存在。

- [ ] **Step 3: 实现 repository Protocol 和内存 fake**

```python
class TripRepository(Protocol):
    def get(self, user_id: UUID, trip_id: UUID) -> Trip | None: ...
    def save(self, trip: Trip) -> Trip: ...
    def append_message(self, message: ConversationMessage) -> None: ...
```

单元测试只使用内存 fake；Supabase 实现负责 DTO 与领域模型转换。

- [ ] **Step 4: 实现 REST 接口**

提供 `POST /api/trips`、`GET /api/trips`、`GET/PATCH/DELETE /api/trips/{id}`、`POST /api/trips/{id}/share`、`DELETE /api/trips/{id}/share` 和匿名 `GET /api/shared/{token}`。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_trip_service.py tests/integration/test_trip_api.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/trips app/infrastructure/repositories.py app/api/trips.py tests/unit/test_trip_service.py tests/integration/test_trip_api.py
git commit -m "feat: add private trips and revocable sharing"
```

---

### Task 5: 安全 Agent 流程、追问与拒答

**Files:**
- Create: `app/agent/safety.py`
- Create: `app/agent/graph.py`
- Move logic from: `app/graph.py`
- Create: `app/api/chat.py`
- Modify: `app/main.py`
- Test: `tests/unit/test_agent_routes.py`
- Test: `tests/integration/test_chat_api.py`

**Interfaces:**
- Produces: `TravelState`，包含 `intent`, `profile`, `issues`, `sources`, `reply`, `stage`, `error_code`。
- Produces: `chat(user: AuthenticatedUser | None, trip_id: UUID | None, message: str) -> ChatResult`。
- Consumes: `IntentClassifier`, `TravelExtractor`, `TripRepository`, `Planner`, `UsageGuard`。

- [ ] **Step 1: 编写追问、拒答和无行程修改测试**

```python
def test_missing_fields_asks_without_calling_planner(agent, planner):
    result = agent.run("从上海出发", trip=None)
    assert result.stage == "collecting"
    assert "目的地" in result.reply
    planner.invoke.assert_not_called()

def test_live_inventory_question_is_refused(agent):
    result = agent.run("保证明天还有两张高铁票并帮我买", trip=None)
    assert result.error_code == "UNVERIFIABLE_REALTIME_REQUEST"
    assert "12306" in result.reply

def test_modify_without_trip_routes_to_creation(agent):
    result = agent.run("把第二天改成西湖", trip=None)
    assert result.stage == "collecting"
    assert "先告诉我" in result.reply
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_agent_routes.py -v`

Expected: FAIL，新 Agent 图尚不存在。

- [ ] **Step 3: 实现节点和条件边**

节点固定为 `load_context -> classify -> guard_scope -> extract -> validate -> ask_or_confirm -> enrich -> plan -> verify -> persist`。`unsupported`、缺字段和矛盾字段通过确定性条件边提前结束。

- [ ] **Step 4: 实现稳定拒答表**

```python
REFUSALS = {
    "UNVERIFIABLE_REALTIME_REQUEST": "我无法核实实时价格、库存或余票，请在官方预订平台确认。",
    "OUT_OF_SCOPE": "我目前只处理国内多日自由行的行前规划。",
    "HIGH_STAKES_ADVICE": "签证、医疗和人身安全结论需要向相关官方机构或专业人士确认。",
}
```

- [ ] **Step 5: 运行 API 和 Agent 测试**

Run: `python -m pytest tests/unit/test_agent_routes.py tests/integration/test_chat_api.py tests/test_app.py -v`

Expected: PASS，旧 `/api/chat` 契约在迁移期间保持兼容。

- [ ] **Step 6: 提交**

```bash
git add app/agent app/api/chat.py app/main.py app/graph.py tests/unit/test_agent_routes.py tests/integration/test_chat_api.py
git commit -m "feat: add safe travel agent workflow"
```

---

### Task 6: 免费外部数据 Provider、Query 重写和预订链接

**Files:**
- Create: `app/providers/base.py`
- Create: `app/providers/free_weather.py`
- Create: `app/providers/places.py`
- Create: `app/providers/booking_links.py`
- Test: `tests/fixtures/providers.py`
- Test: `tests/unit/test_providers.py`

**Interfaces:**
- Produces: `WeatherProvider.forecast(destination: str, start: date, end: date) -> ProviderResult[WeatherSummary]`。
- Produces: `PlacesProvider.search(city: str, query: str) -> ProviderResult[list[Place]]`。
- Produces: `BookingLinkBuilder.build(profile: TravelProfile) -> BookingLinks`。
- `ProviderResult` 必须包含 `data`, `source`, `fetched_at`, `degraded`, `error_code`。

- [ ] **Step 1: 编写超时降级和 Query 重写测试**

```python
def test_place_search_rewrites_once_after_empty_result(httpx_mock):
    httpx_mock.add_response(json={"features": []})
    httpx_mock.add_response(json={"features": [{"name": "西湖", "city": "杭州"}]})
    result = provider.search(city="杭州", query="西湖景区")
    assert result.data[0].name == "西湖"
    assert httpx_mock.get_requests()[1].url.params["q"] == "杭州 西湖"

def test_weather_timeout_returns_degraded_result(timeout_client):
    result = provider.forecast("杭州", date(2026, 8, 1), date(2026, 8, 3))
    assert result.degraded is True
    assert result.error_code == "WEATHER_TIMEOUT"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_providers.py -v`

Expected: FAIL，provider 接口尚不存在。

- [ ] **Step 3: 实现 ProviderResult、超时和单次重试**

每个 HTTP 请求连接超时 3 秒、总超时 6 秒；仅网络错误和 5xx 重试一次。空地点结果执行 `城市 + 归一化别名` query 重写一次，不无限重试。

- [ ] **Step 4: 实现安全外链构造**

只允许 `https` 且 hostname 在显式 allowlist；日期、城市和人数使用 URL encoder。响应文字始终包含“价格和库存以第三方平台为准”。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_providers.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/providers tests/fixtures/providers.py tests/unit/test_providers.py
git commit -m "feat: add degradable travel data providers"
```

---

### Task 7: 结构化行程、预算校验和事实约束

**Files:**
- Create: `app/agent/planning.py`
- Modify: `app/schemas.py`
- Test: `tests/unit/test_planning.py`

**Interfaces:**
- Produces: `Itinerary`, `ItineraryDay`, `Activity`, `BudgetBreakdown`, `SourceCitation`。
- Produces: `Planner.plan(profile, provider_results) -> Itinerary`。
- Produces: `validate_itinerary(itinerary, profile, sources) -> list[PlanIssue]`。

- [ ] **Step 1: 编写预算、日期和无来源事实测试**

```python
def test_budget_total_matches_profile():
    itinerary = itinerary_factory(budget={"transport": 1000, "hotel": 1200, "food": 600, "tickets": 200, "reserve": 500})
    assert validate_itinerary(itinerary, profile_factory(budget_cny=3500), []) == []

def test_unverified_price_is_rejected():
    itinerary = itinerary_factory(notes=["酒店实时价格为每晚 399 元"])
    issues = validate_itinerary(itinerary, profile_factory(), sources=[])
    assert {issue.code for issue in issues} == {"UNSOURCED_FACT"}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_planning.py -v`

Expected: FAIL，结构化规划模块尚不存在。

- [ ] **Step 3: 实现 Pydantic 行程 Schema**

`Itinerary.days` 数量必须与日期范围一致；每一天固定包含 morning、afternoon、evening；预算分类合计必须等于总预算，允许 reserve 吸收取整差额。

- [ ] **Step 4: 实现一次修复和安全失败**

首次 JSON 校验失败时，把精简后的 issue code 列表交给模型修复一次；第二次失败抛出 `PLAN_VALIDATION_FAILED`，不得返回半结构化文本。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_planning.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/agent/planning.py app/schemas.py tests/unit/test_planning.py
git commit -m "feat: validate itinerary budgets and facts"
```

---

### Task 8: DeepSeek 用量限制、全站熔断和错误映射

**Files:**
- Create: `app/core/usage.py`
- Modify: `app/core/errors.py`
- Modify: `app/api/chat.py`
- Test: `tests/unit/test_usage.py`
- Test: `tests/integration/test_error_mapping.py`

**Interfaces:**
- Produces: `UsageRepository.get_daily(user_key, day) -> UsageCount`。
- Produces: `UsageGuard.reserve(user_key: str) -> UsageReservation`，成功后才允许模型调用。
- Produces: `UsageReservation.commit(input_tokens: int, output_tokens: int)` 和 `rollback()`。

- [ ] **Step 1: 编写限额和回滚测试**

```python
def test_user_limit_blocks_before_model_call(guard, model):
    guard.repository.set_user_count("user-a", 5)
    with pytest.raises(AppError) as error:
        guard.reserve("user-a")
    assert error.value.code == "AI_DAILY_LIMIT_REACHED"
    model.invoke.assert_not_called()

def test_failed_call_rolls_back_reservation(guard):
    reservation = guard.reserve("user-a")
    reservation.rollback()
    assert guard.repository.get_daily("user-a", TODAY).pending == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/unit/test_usage.py -v`

Expected: FAIL，用量模块尚不存在。

- [ ] **Step 3: 实现原子预留与熔断**

调用模型前原子增加 pending；成功后转为 completed 并记录 token；失败则减少 pending。`AI_ENABLED=false`、用户限额和全站限额均在构造模型请求前检查。

- [ ] **Step 4: 实现安全错误响应**

`AI_DAILY_LIMIT_REACHED` 返回 429，`AI_DISABLED` 返回 503，供应商降级仍返回 200 并包含 warnings。所有响应包含 request_id，不包含供应商原始 body。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/unit/test_usage.py tests/integration/test_error_mapping.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/core/usage.py app/core/errors.py app/api/chat.py tests/unit/test_usage.py tests/integration/test_error_mapping.py
git commit -m "feat: enforce ai usage limits"
```

---

### Task 9: 响应式前端、认证、历史和分享

**Files:**
- Modify: `app/static/index.html`
- Create: `app/static/app.js`
- Create: `app/static/styles.css`
- Test: `tests/integration/test_frontend_assets.py`
- Test: `tests/integration/test_user_journey.py`

**Interfaces:**
- Consumes: Supabase 浏览器认证 session、`/api/chat`、`/api/trips`、`/api/shared/{token}`。
- Produces: 登录页、聊天工作区、资料确认卡、结构化行程卡、历史列表和只读分享视图。

- [ ] **Step 1: 编写静态资源和核心可访问性测试**

```python
def test_home_has_core_regions(client):
    html = client.get("/").text
    assert 'id="auth-panel"' in html
    assert 'id="chat-panel"' in html
    assert 'id="trip-history"' in html
    assert 'aria-live="polite"' in html

def test_javascript_is_external(client):
    html = client.get("/").text
    assert '<script src="/static/app.js"' in html
    assert "DEEPSEEK_API_KEY" not in client.get("/static/app.js").text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/integration/test_frontend_assets.py -v`

Expected: FAIL，现有单页没有目标区域且 JavaScript 尚未拆分。

- [ ] **Step 3: 实现页面状态机**

浏览器状态固定为 `signed_out`, `collecting`, `confirming`, `planning`, `planned`, `error`；按钮在请求期间禁用，错误提示使用 `aria-live`，不把服务端错误堆栈渲染给用户。

- [ ] **Step 4: 实现历史、分享和降级提示**

历史列表支持打开、重命名、复制、删除；分享弹窗显示过期时间和撤销按钮；provider 降级时显示黄色提示条和数据更新时间。

- [ ] **Step 5: 运行前端集成测试**

Run: `python -m pytest tests/integration/test_frontend_assets.py tests/integration/test_user_journey.py -v`

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add app/static tests/integration/test_frontend_assets.py tests/integration/test_user_journey.py
git commit -m "feat: add authenticated trip workspace"
```

---

### Task 10: 80 条评测集、指标报告和优化门禁

**Files:**
- Create: `tests/evaluation/cases.jsonl`
- Create: `tests/evaluation/runner.py`
- Create: `tests/evaluation/test_metrics.py`
- Create: `tests/evaluation/baseline.json`
- Create: `docs/evaluation/README.md`

**Interfaces:**
- Produces: `EvaluationCase(id, category, messages, expected_intent, expected_fields, expected_action, allowed_sources)`。
- Produces: `run_evaluation(cases, agent) -> EvaluationReport`。
- Produces metrics: intent_accuracy、slot_micro_f1、clarification_recall、refusal_precision、refusal_recall、schema_validity、unsupported_fact_rate、task_success_rate、fallback_success_rate。

- [ ] **Step 1: 定义 80 条固定用例矩阵**

`cases.jsonl` 使用以下不可变 ID 范围：

- `P001`～`P020`：完整规划，分别覆盖 2/3/5/7 天、1/2/4/6 人、低中高预算和不同偏好。
- `M001`～`M020`：缺字段与矛盾，包含缺目的地、缺日期、日期逆序、人数 0/7、预算缺失及多轮修改。
- `R001`～`R015`：必须拒答，包含实时票价、余票保证、代订、签证结论、医疗建议、安全保证和非旅行任务。
- `N001`～`N015`：口语、错别字、简称、相对日期、上下文“那里/第二天”和中英混合。
- `E001`～`E010`：天气超时、地点空结果、地点二次失败、模型 400/429/500、格式两次失败、用户限额、全站限额和数据库失败。

每个用例必须显式写出 `expected_action`，取值只允许 `ask`, `refuse`, `plan`, `modify`, `explain`, `degrade`。

- [ ] **Step 2: 编写指标公式测试**

```python
def test_metric_formulas():
    report = score([
        prediction(intent="plan_trip", action="plan", fields={"origin": "上海"})
    ], [
        expected(intent="plan_trip", action="plan", fields={"origin": "上海"})
    ])
    assert report.intent_accuracy == 1.0
    assert report.slot_micro_f1 == 1.0
    assert report.task_success_rate == 1.0
```

- [ ] **Step 3: 运行测试确认失败**

Run: `python -m pytest tests/evaluation/test_metrics.py -v`

Expected: FAIL，runner 尚不存在。

- [ ] **Step 4: 实现离线 runner 和报告**

runner 默认使用 fake model/provider fixture，保证 CI 不消耗 DeepSeek；`--live` 才允许真实模型，并要求显式设置 `ALLOW_PAID_EVAL=true`。输出 `evaluation-report.json` 和包含门槛对比表的 `evaluation-report.md`。

- [ ] **Step 5: 生成并校验基线**

Run: `python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation`

Expected: 读取恰好 80 条，用例 ID 唯一；全部指标字段存在；未达门槛时进程退出码为 1。

- [ ] **Step 6: 提交**

```bash
git add tests/evaluation docs/evaluation
git commit -m "test: add travel agent evaluation gate"
```

---

### Task 11: CI、免费部署、公开仓库验证与文档

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `render.yaml`
- Create: `scripts/verify_public_repo.ps1`
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `docs/deployment/free-tier.md`
- Test: `tests/integration/test_deployment_config.py`

**Interfaces:**
- CI produces: 单元/集成/评测测试结果和敏感信息扫描结果。
- Render consumes: `render.yaml` 和平台 Secret，不从仓库读取 `.env`。
- Verification script exits non-zero when tracked files contain forbidden paths or credential patterns。

- [ ] **Step 1: 编写部署配置测试**

```python
def test_render_uses_port_environment():
    config = yaml.safe_load(Path("render.yaml").read_text(encoding="utf-8"))
    command = config["services"][0]["startCommand"]
    assert "--port $PORT" in command
    assert config["services"][0]["plan"] == "free"
```

- [ ] **Step 2: 创建公开仓库验证脚本**

脚本读取 `git ls-files`，拒绝 `.env`、`.venv/`、`.pytest_cache/`、`__pycache__/`、`.agents/`、本地数据库和日志；再扫描 `DEEPSEEK_API_KEY=` 后的非占位值、GitHub token、Supabase service key 和通用私钥头。

- [ ] **Step 3: 创建 CI 工作流**

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements.txt
      - run: pytest -q
        env:
          APP_ENV: test
          DEEPSEEK_API_KEY: test-only-key
      - shell: pwsh
        run: ./scripts/verify_public_repo.ps1
```

- [ ] **Step 4: 完成 README 和免费部署说明**

README 必须包含：垂直场景、架构、功能边界、本地启动、配置表、测试命令、80 条评测指标、Render/Supabase 部署链接、冷启动说明、DeepSeek 费用警告和停止 AI 的方法。

- [ ] **Step 5: 运行全量验证**

Run: `python -m pytest -q`

Expected: 全部测试 PASS。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`

Expected: 输出 `Public repository check passed`，退出码 0。

- [ ] **Step 6: 提交**

```bash
git add .github/workflows/ci.yml render.yaml scripts/verify_public_repo.ps1 .gitignore README.md docs/deployment/free-tier.md tests/integration/test_deployment_config.py
git commit -m "chore: add free deployment and release checks"
```

---

## Release Sequence

- [ ] 在 GitHub 创建公开仓库 `2785253749-wq/travel-assistant`，默认分支 `main`。
- [ ] 推送当前文档和原型代码；确认 `.env`、`.venv`、`.agents`、缓存和真实凭据未被跟踪。
- [ ] 按 Task 1～11 顺序实现，每个任务通过测试并独立提交。
- [ ] 第 7 周保存首次 80 条离线评测基线；根据失败指标执行一次有记录的优化循环。
- [ ] 第 8 周运行全量测试、公开仓库验证和线上冒烟测试。
- [ ] 创建版本标签 `v0.1.0`，发布说明包含已知免费层限制和不支持能力。
