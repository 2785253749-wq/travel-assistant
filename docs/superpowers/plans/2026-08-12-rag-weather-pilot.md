# RAG 旅行知识库与天气试点 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为福建、云南和厦门建立可引用、可拒答的 RAG 旅行知识试点，并把独立的高德天气安全地显示在问答、地图城市卡片和生成行程中。

**Architecture:** 知识资料以版本化中文文件保存在仓库中；导入器使用 Jina 生成向量、以 service role 写入 Supabase pgvector，检索器只向聊天用例提供已经过阈值过滤的证据。高德天气作为单独的后端适配器，带缓存、超时和日限额；行程服务只合并它返回的结构化天气，绝不让语言模型虚构天气。

**Tech Stack:** FastAPI、Pydantic v2、httpx、Supabase PostgreSQL/pgvector、Jina Embeddings API、AMap Web Service Weather API、现有原生 JavaScript 前端、pytest、Node test runner。

## Global Constraints

- 试点资料只覆盖福建、云南和厦门；不实现上传、网页抓取或全国扩展。
- `JINA_API_KEY`、`AMAP_WEB_SERVICE_KEY` 只能存在于后端环境变量，绝不进入 runtime-config、浏览器、日志、测试快照或 Git。
- RAG 相关事实必须来自阈值内的检索段落，并在每个事实段落末尾显示中文来源标签；没有足够证据、Embedding 失败或检索失败时返回固定中文拒答。
- 高德天气按 adcode 查询；实时预报最多显示当天、第二天和第三天，超出窗口只允许显示明确标注“非实时天气”的本地季节建议。
- 天气或 RAG 上游失败不得阻塞既有行程生成、保存、分享、鉴权、配额和可拖动助手行为。
- 新增依赖必须有明确用途；所有网络请求必须配置超时，所有服务端限额默认安全且可通过 Settings 配置。
- 每项任务先 RED、后最小 GREEN、再运行其回归集；每项只暂存列出的文件并单独提交。

---

## 文件结构与职责

- `app/core/config.py`：声明、验证且仅在后端读取 RAG/天气密钥、模型、超时、缓存和日限额。
- `app/rag/models.py`：定义 `KnowledgeDocument`、`KnowledgeChunk`、`RetrievedChunk` 和不可用结果，不依赖 FastAPI 或 Supabase SDK。
- `app/rag/embedding.py`：封装 Jina HTTP Embedding 调用及严格的响应维度验证。
- `app/rag/repository.py`：定义知识库协议及 Supabase service-role 实现，只暴露导入和相似度检索。
- `app/rag/service.py`：切块、幂等导入、阈值检索、中文拒答与可显示来源标签的应用服务。
- `app/rag/content/*.yaml`：试点的版本化中文资料；每项具备标题、地区、主题、正文、来源标签、核对日期和版本。
- `app/scripts/import_knowledge.py`：显式运行的资料导入命令，不在应用启动时导入。
- `app/providers/amap_weather.py`：高德 Web Service 的 adcode、缓存、超时、限额和结构化天气适配器。
- `app/application/weather.py`：将天气映射为地图卡片和行程每日展示项；处理 3 日窗口外的季节建议。
- `app/api/weather.py`：只提供公开、已脱敏的城市天气摘要 API。
- `app/schemas.py`：定义严格的 `WeatherCard`、`ItineraryWeather` 与 RAG 引用展示模型。
- `app/agent/graph.py`、`app/application/chat.py`、`app/composition.py`：在明确的应用边界连接 RAG、天气和现有行程工作流。
- `supabase/migrations/008_rag_knowledge.sql`：启用 pgvector、创建私有知识表、RPC 相似度检索和拒绝匿名直读写的 RLS。
- `app/static/app.js`、`app/static/styles.css`、`app/static/data/explore-data.js`：在城市卡片和已生成的每日行程中展示服务器返回的天气，不把密钥或检索文本放入前端。
- `tests/unit/*`、`tests/integration/*`、`tests/frontend/*`、`tests/evaluation/*`：分别验证边界、数据库/HTTP 合同、浏览器渲染及 80 条离线评测。

## Task 1: 后端配置与严格的领域模型

**Files:**
- Create: `app/rag/__init__.py`
- Create: `app/rag/models.py`
- Modify: `app/core/config.py`
- Modify: `app/schemas.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_rag_models.py`

**Interfaces:**
- Produces `Settings.jina_api_key: SecretStr | None`、`Settings.amap_web_service_key: SecretStr | None`、`Settings.rag_embedding_model: str`、`Settings.rag_similarity_threshold: float`、`Settings.rag_daily_embedding_limit: int`、`Settings.weather_daily_limit: int`、`Settings.weather_cache_seconds: int`、`Settings.weather_timeout_seconds: float`。
- Produces `KnowledgeDocument`、`KnowledgeChunk`、`RetrievedChunk`、`WeatherCard` 和 `ItineraryWeather`；后续任务只能通过这些模型传递 RAG/天气数据。

- [ ] **Step 1: 写入配置与模型失败测试**

```python
def test_production_accepts_missing_optional_rag_and_weather_keys():
    settings = Settings(
        app_env="production",
        supabase_url="https://project.supabase.co",
        supabase_anon_key="anon",
        supabase_service_key="your_supabase_service_key_here",
        anon_session_signing_secret="your_anon_session_signing_secret_here",
    )
    assert settings.jina_api_key is None
    assert settings.amap_web_service_key is None


def test_retrieved_chunk_requires_chinese_source_label():
    with pytest.raises(ValidationError):
        RetrievedChunk(chunk_id="x", content="事实", source_label="", score=0.9)
```

- [ ] **Step 2: 运行失败测试，确认新字段和模型尚不存在**

Run: `pytest tests/unit/test_config.py tests/unit/test_rag_models.py -q`
Expected: FAIL，错误指出未知 `Settings` 字段或无法导入 `app.rag.models`。

- [ ] **Step 3: 实现最小配置和模型**

```python
class RetrievedChunk(BaseModel):
    chunk_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=6000)
    source_label: str = Field(min_length=1, max_length=120)
    score: float = Field(ge=0.0, le=1.0)


class WeatherCard(StrictSchema):
    city: str = Field(min_length=1, max_length=80)
    status: Literal["available", "unavailable", "seasonal"]
    summary: str = Field(min_length=1, max_length=500)
    report_time: datetime | None = None
```

将所有 Key 使用 `SecretStr`，并给限额和超时增加正数范围；生产环境不要求两个可选能力的 Key 存在，以便安全降级。

- [ ] **Step 4: 运行配置和模型回归**

Run: `pytest tests/unit/test_config.py tests/unit/test_rag_models.py -q`
Expected: PASS。

- [ ] **Step 5: 提交配置与模型边界**

```bash
git add app/core/config.py app/rag/__init__.py app/rag/models.py app/schemas.py tests/unit/test_config.py tests/unit/test_rag_models.py
git commit -m "feat: add RAG and weather domain configuration"
```

## Task 2: 私有 pgvector 知识库与可重复导入

**Files:**
- Create: `supabase/migrations/008_rag_knowledge.sql`
- Create: `app/rag/repository.py`
- Create: `app/rag/content/fujian.yaml`
- Create: `app/rag/content/yunnan.yaml`
- Create: `app/rag/content/xiamen.yaml`
- Create: `app/scripts/import_knowledge.py`
- Test: `tests/integration/test_rag_migration_contract.py`
- Test: `tests/unit/test_knowledge_import.py`

**Interfaces:**
- Consumes `KnowledgeDocument`、`KnowledgeChunk` 和 Settings 的 service-role Supabase 凭据。
- Produces `KnowledgeRepository.upsert_document(document, chunks) -> int` 与 `KnowledgeRepository.search(query_vector, region, limit) -> list[RetrievedChunk]`。
- Produces CLI：`python -m app.scripts.import_knowledge --content-dir app/rag/content`，相同版本重复执行返回零新增段落。

- [ ] **Step 1: 写迁移合同与幂等导入失败测试**

```python
def test_rag_migration_keeps_knowledge_table_private_and_enables_vector():
    sql = Path("supabase/migrations/008_rag_knowledge.sql").read_text(encoding="utf-8")
    assert "create extension if not exists vector" in sql.lower()
    assert "alter table public.knowledge_chunks enable row level security" in sql.lower()
    assert "to anon" not in sql.lower()


def test_reimport_same_document_version_does_not_write_chunks_twice():
    repository = FakeKnowledgeRepository()
    service = KnowledgeImportService(repository, FakeEmbedder())
    assert service.import_documents([sample_document()]) == 2
    assert service.import_documents([sample_document()]) == 0
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/integration/test_rag_migration_contract.py tests/unit/test_knowledge_import.py -q`
Expected: FAIL，迁移、仓库和导入服务尚不存在。

- [ ] **Step 3: 实现迁移、仓库与试点资料**

```sql
create extension if not exists vector;
create table public.knowledge_chunks (
  chunk_id text primary key,
  document_id text not null,
  document_version text not null,
  region text not null,
  topic text not null,
  content text not null,
  source_label text not null,
  reviewed_on date not null,
  embedding vector(1024) not null,
  imported_at timestamptz not null default now(),
  unique (document_id, document_version, chunk_id)
);
alter table public.knowledge_chunks enable row level security;
```

在 YAML 中各提供福建、云南、厦门的景点、交通、餐饮、季节/避坑资料；每条资料须有 `source_label` 与 `reviewed_on`。导入器仅用 service role，按稳定 chunk id upsert，不记录文档正文或 Key。

- [ ] **Step 4: 运行导入与迁移回归**

Run: `pytest tests/integration/test_rag_migration_contract.py tests/unit/test_knowledge_import.py -q`
Expected: PASS。

- [ ] **Step 5: 提交私有资料库**

```bash
git add supabase/migrations/008_rag_knowledge.sql app/rag/repository.py app/rag/content app/scripts/import_knowledge.py tests/integration/test_rag_migration_contract.py tests/unit/test_knowledge_import.py
git commit -m "feat: add private pgvector travel knowledge store"
```

## Task 3: Jina Embedding、检索阈值与可引用中文拒答

**Files:**
- Create: `app/rag/embedding.py`
- Create: `app/rag/service.py`
- Modify: `app/composition.py`
- Test: `tests/unit/test_jina_embedding.py`
- Test: `tests/unit/test_rag_service.py`
- Test: `tests/integration/test_rag_composition.py`

**Interfaces:**
- Consumes `Settings.jina_api_key`、`KnowledgeRepository.search()` 和 `RetrievedChunk`。
- Produces `JinaEmbedder.embed(texts: list[str]) -> list[list[float]]`，`KnowledgeAnswerService.answer(question: str, region: str | None) -> RagAnswer`。
- `RagAnswer` 只有两种结果：`grounded`（含 1–4 个段落和中文来源标签）或 `refused`（固定中文拒答）；不得返回无证据的事实。

- [ ] **Step 1: 写 Jina、阈值和拒答失败测试**

```python
def test_lowest_ranked_chunk_below_threshold_is_not_returned():
    answer = KnowledgeAnswerService(FakeRepository(scores=[0.94, 0.62]), FakeEmbedder()).answer("厦门怎么去鼓浪屿")
    assert answer.status == "grounded"
    assert [chunk.chunk_id for chunk in answer.chunks] == ["high-score"]


def test_embedding_failure_returns_fixed_chinese_refusal_without_model_call():
    answer = KnowledgeAnswerService(FakeRepository(), FailingEmbedder()).answer("云南雨季")
    assert answer.status == "refused"
    assert answer.reply == "资料库没有足够依据，无法可靠回答。"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/unit/test_jina_embedding.py tests/unit/test_rag_service.py tests/integration/test_rag_composition.py -q`
Expected: FAIL，Embedding 与检索服务尚未实现。

- [ ] **Step 3: 实现安全检索应用服务**

```python
class KnowledgeAnswerService:
    def answer(self, question: str, region: str | None) -> RagAnswer:
        try:
            vector = self._embedder.embed([question])[0]
            chunks = self._repository.search(vector, region, limit=4)
        except RagUnavailable:
            return RagAnswer.refused()
        grounded = tuple(item for item in chunks if item.score >= self._threshold)
        return RagAnswer.grounded(grounded) if grounded else RagAnswer.refused()
```

Jina 请求使用 `Authorization: Bearer`、Settings 超时、响应维度验证和每日配额。将服务以依赖注入方式接入 composition；没有 Key 时构造 `UnavailableKnowledgeAnswerService`，而不是访问网络。

- [ ] **Step 4: 运行检索、组合与现有聊天回归**

Run: `pytest tests/unit/test_jina_embedding.py tests/unit/test_rag_service.py tests/integration/test_rag_composition.py tests/integration/test_chat_api.py -q`
Expected: PASS。

- [ ] **Step 5: 提交检索服务**

```bash
git add app/rag/embedding.py app/rag/service.py app/composition.py tests/unit/test_jina_embedding.py tests/unit/test_rag_service.py tests/integration/test_rag_composition.py
git commit -m "feat: add grounded travel knowledge retrieval"
```

## Task 4: 高德天气适配器、缓存和公开摘要接口

**Files:**
- Create: `app/providers/amap_weather.py`
- Create: `app/application/weather.py`
- Create: `app/api/weather.py`
- Modify: `app/main.py`
- Modify: `app/composition.py`
- Test: `tests/unit/test_amap_weather.py`
- Test: `tests/unit/test_weather_service.py`
- Test: `tests/integration/test_weather_api.py`

**Interfaces:**
- Produces `AmapWeatherProvider.weather(adcode: str, extensions: Literal["base", "all"]) -> ProviderResult[AmapWeatherPayload]`。
- Produces `WeatherService.city_card(city_id: str) -> WeatherCard` 和 `WeatherService.daily_weather(destination: str, travel_date: date) -> ItineraryWeather | None`。
- Produces `GET /api/weather/cities/{city_id}`，只返回 `WeatherCard`，无任何 provider Key、原始上游 payload 或完整错误文本。

- [ ] **Step 1: 写高德请求、缓存和降级失败测试**

```python
def test_weather_request_uses_server_key_and_adcode_not_browser_key(httpx_mock):
    provider = AmapWeatherProvider(settings=weather_settings())
    provider.weather("350200", "base")
    request = httpx_mock.get_request()
    assert request.url.params["city"] == "350200"
    assert request.url.params["key"] == "web-service-secret"


def test_weather_api_returns_unavailable_card_on_timeout(client, monkeypatch):
    monkeypatch.setattr("app.api.weather.get_weather_service", lambda: TimeoutWeatherService())
    response = client.get("/api/weather/cities/xiamen")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/unit/test_amap_weather.py tests/unit/test_weather_service.py tests/integration/test_weather_api.py -q`
Expected: FAIL，模块、路由和缓存尚不存在。

- [ ] **Step 3: 实现独立天气服务**

```python
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"

def weather(self, adcode: str, extensions: Literal["base", "all"]) -> ProviderResult[AmapWeatherPayload]:
    return self._request_json(
        AMAP_WEATHER_URL,
        {"key": self._key, "city": adcode, "extensions": extensions, "output": "JSON"},
    )
```

将城市 id 映射为试点 adcode；按 Settings 做 TTL 缓存、每日调用计数和超时。解析上游 `reporttime`，处理高德非成功状态、HTTP 错误和格式错误为 `WeatherCard(status="unavailable")`。不得复用 `amap_js_key`，不得把失败传播到聊天或行程请求。

- [ ] **Step 4: 运行天气 API 及提供商回归**

Run: `pytest tests/unit/test_amap_weather.py tests/unit/test_weather_service.py tests/unit/test_providers.py tests/integration/test_weather_api.py -q`
Expected: PASS。

- [ ] **Step 5: 提交高德天气边界**

```bash
git add app/providers/amap_weather.py app/application/weather.py app/api/weather.py app/main.py app/composition.py tests/unit/test_amap_weather.py tests/unit/test_weather_service.py tests/integration/test_weather_api.py
git commit -m "feat: add guarded AMap weather service"
```

## Task 5: 在聊天与行程中合并证据和每日天气

**Files:**
- Modify: `app/agent/intent.py`
- Modify: `app/agent/graph.py`
- Modify: `app/application/chat.py`
- Modify: `app/schemas.py`
- Modify: `app/trips/models.py`
- Test: `tests/unit/test_intent.py`
- Test: `tests/unit/test_planning.py`
- Test: `tests/integration/test_chat_api.py`
- Test: `tests/integration/test_user_journey.py`

**Interfaces:**
- Consumes `KnowledgeAnswerService.answer()`、`WeatherService.daily_weather()`、现有 `ChatResult` 与 `Itinerary`。
- Produces intent `travel_knowledge` 和 `weather_query`；产生的 `ChatResult.sources` 必须是现有 `SourceCitation` 合同可验证的来源；`ItineraryDay.weather: ItineraryWeather | None`。
- `travel_knowledge` 永不调用计划语言模型；`weather_query` 永不伪装为 RAG 资料。

- [ ] **Step 1: 写问答路由和 3 日边界失败测试**

```python
def test_knowledge_question_returns_chinese_source_labels_without_planner_call():
    result = agent.run("厦门去鼓浪屿怎么安排", trip=None, user_id=None)
    assert result.intent == "travel_knowledge"
    assert "来源：厦门出行资料" in result.reply
    planner.invoke.assert_not_called()


def test_fourth_itinerary_day_uses_marked_seasonal_advice_not_live_forecast():
    itinerary = enrich_itinerary(sample_itinerary(days=4), fake_weather, fake_rag)
    assert itinerary.days[3].weather.status == "seasonal"
    assert "非实时天气" in itinerary.days[3].weather.summary
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/unit/test_intent.py tests/unit/test_planning.py tests/integration/test_chat_api.py tests/integration/test_user_journey.py -q`
Expected: FAIL，新意图、来源文案和每日天气字段尚不存在。

- [ ] **Step 3: 在明确的分支中合并 RAG 与天气**

```python
if intent == "travel_knowledge":
    answer = self._knowledge.answer(message, region=None)
    return ChatResult(answer.reply, "collecting", {}, sources=answer.citations, intent=intent)

if intent == "weather_query":
    card = self._weather.city_card(extract_city(message))
    return ChatResult(card.summary, "collecting", {}, warnings=card.warnings, intent=intent)
```

行程生成后逐日调用 `WeatherService.daily_weather()`；仅高德三日窗口返回 `available`，第四天及以后只能调用检索服务取得季节建议并标记 `seasonal`。任一天气调用失败留空或 `unavailable`，但仍返回原行程。

- [ ] **Step 4: 运行聊天、规划与用户旅程回归**

Run: `pytest tests/unit/test_intent.py tests/unit/test_planning.py tests/integration/test_chat_api.py tests/integration/test_user_journey.py -q`
Expected: PASS。

- [ ] **Step 5: 提交行程天气与问答整合**

```bash
git add app/agent/intent.py app/agent/graph.py app/application/chat.py app/schemas.py app/trips/models.py tests/unit/test_intent.py tests/unit/test_planning.py tests/integration/test_chat_api.py tests/integration/test_user_journey.py
git commit -m "feat: show grounded answers and daily weather"
```

## Task 6: 地图天气卡片与行程前端展示

**Files:**
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `app/static/data/explore-data.js`
- Test: `tests/frontend/app.test.js`
- Test: `tests/frontend/map-explorer.test.js`

**Interfaces:**
- Consumes `GET /api/weather/cities/{city_id}` 的 `WeatherCard` JSON；只在试点城市选择后请求一次。
- Produces `.city-weather-card` 和 `.itinerary-weather`；无 Key、无 `AMAP_WEB_SERVICE_KEY`、无 RAG 原文、无自动打开 AI 助手。

- [ ] **Step 1: 写地图卡片、失败降级和行程天气渲染失败测试**

```javascript
test("selecting Xiamen renders one weather card and does not call chat", async () => {
  fetchStub.resolves(weatherResponse("xiamen", "晴 26℃"));
  click(findById(document, "explore-city-xiamen"));
  await flushPromises();
  assert.equal(textOf(document, ".city-weather-card"), "厦门：晴 26℃");
  assert.equal(fetchStub.calls.some((call) => call[0] === "/api/chat"), false);
});

test("weather endpoint failure shows unavailable copy while map remains usable", async () => {
  fetchStub.rejects(new Error("offline"));
  click(findById(document, "explore-city-xiamen"));
  await flushPromises();
  assert.match(textOf(document, ".city-weather-card"), /天气暂不可用/);
});
```

- [ ] **Step 2: 运行失败测试**

Run: `node --test tests/frontend/app.test.js tests/frontend/map-explorer.test.js`
Expected: FAIL，天气卡片和每日天气展示尚不存在。

- [ ] **Step 3: 实现最小前端展示**

```javascript
async function renderCityWeather(cityId) {
  try {
    const response = await api(`/api/weather/cities/${encodeURIComponent(cityId)}`);
    renderWeatherCard(response);
  } catch (_) {
    renderWeatherCard({ city: cityId, status: "unavailable", summary: "天气暂不可用" });
  }
}
```

在 `handleExploreSelection` 的城市分支调用该函数；用 `textContent` 渲染，避免 HTML 注入。行程卡从已验证的 `itinerary.days[n].weather` 读取，不解析聊天 markdown。保留默认关闭的助手与所有既有地图层级、键盘和拖动行为。

- [ ] **Step 4: 运行完整前端回归**

Run: `node --test tests/frontend/*.test.js`
Expected: PASS。

- [ ] **Step 5: 提交浏览器展示**

```bash
git add app/static/app.js app/static/styles.css app/static/data/explore-data.js tests/frontend/app.test.js tests/frontend/map-explorer.test.js
git commit -m "feat: render city and itinerary weather"
```

## Task 7: 80 条离线评测、发布门禁与运维文档

**Files:**
- Modify: `tests/evaluation/cases.jsonl`
- Modify: `tests/evaluation/runner.py`
- Modify: `tests/evaluation/test_metrics.py`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `scripts/verify_public_repo.ps1`
- Test: `tests/evaluation/test_metrics.py`
- Test: `tests/integration/test_deployment_config.py`

**Interfaces:**
- Produces 80 个带稳定 id 的离线 RAG/天气用例：45 grounded、15 refusal、10 citation-safety、10 weather-boundary。
- Produces评测指标 `grounded_source_rate`、`refusal_accuracy`、`citation_completeness`、`weather_boundary_accuracy`；任何低于 1.0 的目标值都使 runner 返回非零。

- [ ] **Step 1: 写离线评测分布和秘密扫描失败测试**

```python
def test_rag_weather_case_distribution_is_exact():
    cases = load_cases()
    assert len(cases) == 80
    assert Counter(case.category for case in cases) == {
        "grounded": 45, "refusal": 15, "citation_safety": 10, "weather_boundary": 10,
    }


def test_example_environment_lists_key_names_but_not_secret_values():
    content = Path(".env.example").read_text(encoding="utf-8")
    assert "JINA_API_KEY=" in content
    assert "AMAP_WEB_SERVICE_KEY=" in content
    assert "sk-" not in content
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/evaluation/test_metrics.py tests/integration/test_deployment_config.py -q`
Expected: FAIL，80 条分类、指标和部署配置说明尚未完成。

- [ ] **Step 3: 实现离线门禁和用户部署说明**

```python
REQUIRED_METRICS = {
    "grounded_source_rate": 1.0,
    "refusal_accuracy": 1.0,
    "citation_completeness": 1.0,
    "weather_boundary_accuracy": 1.0,
}

if any(metrics[name] < threshold for name, threshold in REQUIRED_METRICS.items()):
    raise SystemExit("RAG/weather evaluation threshold failed")
```

更新 README：说明先执行 `008_rag_knowledge.sql`，再在受控环境运行导入命令；Render 仅填写两项新 Key，未配置时 RAG 拒答、天气显示不可用但行程照常运行。公开扫描器继续拒绝实际 Key，只允许 `.env.example` 中的空变量名。

- [ ] **Step 4: 运行评测、发布门禁与全量回归**

Run: `pytest tests/evaluation/test_metrics.py tests/integration/test_deployment_config.py -q`
Expected: PASS。

Run: `python -m tests.evaluation.runner`
Expected: exit 0，80 条用例及四项指标均达到 1.0。

Run: `pytest -q`
Expected: PASS，只有已知的 Starlette/httpx 弃用警告可以保留。

Run: `node --test tests/frontend/*.test.js`
Expected: PASS。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`
Expected: `Public repository check passed`。

- [ ] **Step 5: 提交评测与发布说明**

```bash
git add tests/evaluation/cases.jsonl tests/evaluation/runner.py tests/evaluation/test_metrics.py README.md .env.example scripts/verify_public_repo.ps1 tests/integration/test_deployment_config.py
git commit -m "test: gate RAG weather pilot release"
```

## Task 8: Supabase 执行、Render 配置与人工验收

**Files:**
- Modify: `README.md`
- Test: `tests/integration/test_rag_migration_contract.py`
- Test: `tests/integration/test_weather_api.py`

**Interfaces:**
- Consumes已提交的 `008_rag_knowledge.sql`、Render 私有环境变量与三个试点城市。
- Produces一份不含秘密的部署验收记录：迁移成功、资料导入计数、天气可用/降级、RAG 来源/拒答和行程 3 日边界结果。

- [ ] **Step 1: 写人工部署检查清单测试**

```python
def test_readme_requires_server_only_render_variables_and_manual_smoke_steps():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "AMAP_WEB_SERVICE_KEY" in readme
    assert "JINA_API_KEY" in readme
    assert "不得填入浏览器" in readme
```

- [ ] **Step 2: 运行失败测试**

Run: `pytest tests/integration/test_rag_migration_contract.py tests/integration/test_weather_api.py -q`
Expected: PASS；这一步只验证此前提交的合同，README 清单若未补齐则 FAIL。

- [ ] **Step 3: 执行受控部署步骤**

```text
1. 在 Supabase SQL Editor 执行 008_rag_knowledge.sql，确认成功且无匿名 grant。
2. 在安全本机或一次性 Render Shell 运行 import_knowledge，记录导入数量但不记录 Key 或正文。
3. 在 Render 添加 JINA_API_KEY 和 AMAP_WEB_SERVICE_KEY，触发一次部署。
4. 验收厦门天气、资料内问答带中文来源、资料外问答拒答、四日行程的“非实时天气”边界和天气失败时的行程生成。
```

将这些无秘密步骤加入 README；人工验收结论仅记录状态码、用例 id 和可公开摘要。

- [ ] **Step 4: 复跑合同和发布检查**

Run: `pytest tests/integration/test_rag_migration_contract.py tests/integration/test_weather_api.py -q`
Expected: PASS。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`
Expected: `Public repository check passed`。

- [ ] **Step 5: 提交部署清单**

```bash
git add README.md tests/integration/test_rag_migration_contract.py tests/integration/test_weather_api.py
git commit -m "docs: add RAG weather deployment acceptance"
```

## 自检清单

- 规格中的资料范围、pgvector、Jina、中文来源、拒答、独立高德天气、三日边界、季节建议、缓存/超时/限额、日志脱敏、80 条评测和部署步骤均分别由 Task 1–8 覆盖。
- 所有跨任务名称均由前一任务的 Interfaces 区定义：`KnowledgeRepository`、`KnowledgeAnswerService`、`AmapWeatherProvider`、`WeatherService`、`WeatherCard`、`ItineraryWeather`。
- 已扫描本计划：没有占位标记、空白实现步骤或含糊的错误处理要求。
- 本计划保留现有免费天气适配器的既有回归；是否完全替换其规划职责由 Task 5 的契约测试决定，不能在没有证据时删除它。
