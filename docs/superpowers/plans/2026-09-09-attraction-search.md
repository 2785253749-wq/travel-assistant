# Attraction Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 AI 旅行助手增加城市景点搜索和附近景点搜索，同时保持 RAG V2 知识问答边界不变。

**Architecture:** 使用独立 Attraction domain/provider/service/application/query/renderer，沿用 Hotel Nearby 的分层模式。City 使用 Baidu Place region；Nearby 先通过 LocationService resolve，再调用 Baidu Place around。Attraction Search 与 RAG V2 明确分流。

**Tech Stack:** Python 3.13, Pydantic, httpx, pytest, FastAPI/current chat architecture, Baidu Place API V3

**Spec:** docs/superpowers/specs/2026-09-09-attraction-search-design.md

## Global Constraints

- 毕设规模，YAGNI。
- 不做通用 POI abstraction。
- 不重构 Hotel。
- 不修改 Train 行为。
- 不修改 Weather 行为。
- 不修改 RAG V2 行为。
- 不新增数据库。
- 不新增 RAG 数据。
- 不做 ticket / booking / images / OTA。
- 不做 price sorting。
- 不做 Planner 深度集成。
- 不做本地二次排序。
- Attraction provider 失败不 fallback 到 RAG。
- city search 不调用 LocationService。
- nearby search 才调用 LocationService。
- 缺 city 使用 `missing_fields` 澄清，不猜城市。
- 不维护地点到城市 alias。
- `distance` 必须是 `int | None`。
- `AttractionSearchResult.total` 必须是 `int | None`。
- city sort 只允许 `None`/`rating`。
- nearby sort 允许 `None`/`rating`/`distance`。
- city + distance 不得成为有效 request。
- `PendingAttractionNearbySelection` 必须保存和恢复 `sort_by`。
- Hotel pending 与 Attraction pending 独立。
- 不写死未经真实 API 验证的景点 category filter。
- 不写死未经真实 API 验证的 rating sort field。
- Provider HTTP client 使用 `trust_env=False`。
- 禁止 `verify=False`。
- 禁止泄漏 `BAIDU_MAP_AK`、完整凭据 URL 或 raw provider body。

## Existing file map

当前实现以这些接口为边界，不改变其既有行为：

- `app/hotels/models.py`: Hotel request/result models and `HotelSortBy`。
- `app/hotels/provider.py`: `HotelProvider.search()` protocol。
- `app/hotels/service.py`: `HotelService.search_city()` and `search_nearby()` delegation。
- `app/providers/baidu_hotel.py`: region/around request construction, response parsing, and returned-order preservation。
- `app/agent/hotel_nearby_query.py`: deterministic nearby extraction and `missing_fields`/`invalid_fields` convention。
- `app/application/hotel_nearby.py`: location resolution followed by nearby service search。
- `app/application/hotel_nearby_reply.py`: bounded text rendering。
- `app/agent/intent.py`: `Intent`, `IntentResult`, model prompt, and intent routing types。
- `app/agent/graph.py`: `RuleIntentClassifier`, `SafeTravelAgent`, `ChatResult`, and Hotel pending state。
- `app/application/chat.py`: `ConfirmationStore` and cross-request Hotel pending persistence。
- `app/api/chat.py`: `_KNOWN_INTENTS`, public HTTP boundary, safe error mapping。
- `app/composition.py`: optional provider composition and `SafeTravelAgent` injection。
- `app/locations/models.py`, `app/locations/provider.py`, `app/locations/service.py`: location resolution and `LOCATION_NOT_FOUND`/`LOCATION_AMBIGUOUS` behavior。

The existing `app/providers/places.py` Photon evidence provider is not changed or reused as the Attraction Search provider.

---

### Task 1: Attraction Domain Models

**Files:**
- Create: `app/attractions/__init__.py`
- Create: `app/attractions/models.py`
- Test: `tests/unit/test_attraction_models.py`

**Interfaces:**
- Consumes: Pydantic conventions from `app/hotels/models.py`, with no Hotel model imports.
- Produces: `AttractionSortBy`, `AttractionSearchMode`, `AttractionSearchRequest`, `AttractionNearbySearchRequest`, `AttractionSummary`, and `AttractionSearchResult`.

- [ ] **Step 1: Write the failing model tests**

Add tests for the exact public fields and constraints:

```python
def test_attraction_summary_accepts_optional_live_fields() -> None:
    item = AttractionSummary(
        id="baidu-attraction-1",
        name="厦门大学",
        address="厦门市思明区",
        latitude=24.44,
        longitude=118.09,
        rating=4.8,
        comment_num=123,
        distance=320,
        tags=("校园景观",),
        provider="baidu",
    )
    assert item.distance == 320

def test_city_request_rejects_distance_sort() -> None:
    with pytest.raises(ValidationError):
        AttractionSearchRequest(city="厦门", sort_by="distance")

def test_result_allows_missing_total() -> None:
    result = AttractionSearchResult(
        items=[], total=None, page=1, page_size=10,
        provider="baidu", status="success", warning=None,
        fetched_at=datetime.now(timezone.utc),
    )
    assert result.total is None
```

Also cover negative rating, negative comment count, negative distance, tag cleaning, page/page-size boundaries, nearby rating/distance acceptance, and absent optional values.

- [ ] **Step 2: Run the model tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_models.py -q
```

Expected: collection fails because `app.attractions.models` does not exist.

- [ ] **Step 3: Implement the minimal independent models**

Define the type aliases and models with the following contract:

```python
AttractionSortBy = Literal["rating", "distance"]
AttractionSearchMode = Literal["city", "nearby"]

class AttractionSearchRequest(...):
    city: str
    keyword: str = "景点"
    page: int = 1
    page_size: int = 10
    sort_by: Literal["rating"] | None = None

class AttractionNearbySearchRequest(...):
    latitude: float
    longitude: float
    radius: int = 2000
    keyword: str = "景点"
    page: int = 1
    page_size: int = 10
    sort_by: AttractionSortBy | None = None

class AttractionSummary(...):
    id: str | None
    name: str
    address: str | None
    latitude: float | None
    longitude: float | None
    rating: float | None
    comment_num: int | None
    distance: int | None
    tags: tuple[str, ...]
    provider: str

class AttractionSearchResult(...):
    items: list[AttractionSummary]
    total: int | None
    page: int
    page_size: int
    provider: str
    status: Literal["success", "unavailable"]
    warning: str | None
    fetched_at: datetime
```

Validate rating/comment count/distance as non-negative when present, radius in `[500, 20000]`, positive page/page size, and normalize tags without creating fake values.

- [ ] **Step 4: Run the model tests and verify GREEN**

Run the same command. Expected: all model tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/attractions/__init__.py app/attractions/models.py tests/unit/test_attraction_models.py
git commit -m "feat: add attraction domain models"
```

### Task 2: Provider Protocol and AttractionService

**Files:**
- Create: `app/attractions/provider.py`
- Create: `app/attractions/service.py`
- Test: `tests/unit/test_attraction_service.py`

**Interfaces:**
- Consumes: Task 1 request/result models and the existing Hotel provider/service delegation pattern.
- Produces: `AttractionProvider.search_city(request)`, `AttractionProvider.search_nearby(request)`, `AttractionService.search_city(request)`, and `AttractionService.search_nearby(request)`.

- [ ] **Step 1: Write the failing delegation tests**

Use a recording fake provider. The tests must assert identity-preserving delegation:

```python
def test_search_city_delegates_without_sorting() -> None:
    request = AttractionSearchRequest(city="厦门", sort_by="rating")
    provider = RecordingAttractionProvider()
    result = AttractionService(provider=provider).search_city(request)
    assert provider.city_requests == [request]
    assert result is provider.city_result

def test_search_nearby_delegates_without_location_resolution() -> None:
    request = AttractionNearbySearchRequest(
        latitude=24.44, longitude=118.09, sort_by="distance"
    )
    provider = RecordingAttractionProvider()
    result = AttractionService(provider=provider).search_nearby(request)
    assert provider.nearby_requests == [request]
    assert result is provider.nearby_result
```

- [ ] **Step 2: Run the delegation tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_service.py -q
```

Expected: collection fails because `app.attractions.service` does not exist.

- [ ] **Step 3: Implement the narrow protocol and service**

Use this interface shape:

```python
class AttractionProvider(Protocol):
    def search_city(self, request: AttractionSearchRequest) -> AttractionSearchResult: ...
    def search_nearby(self, request: AttractionNearbySearchRequest) -> AttractionSearchResult: ...

class AttractionService:
    def __init__(self, *, provider: AttractionProvider) -> None: ...
    def search_city(self, request: AttractionSearchRequest) -> AttractionSearchResult: ...
    def search_nearby(self, request: AttractionNearbySearchRequest) -> AttractionSearchResult: ...
```

The service must only delegate. It must not call `LocationService`, parse intent, render replies, sort results, or call RAG V2.

- [ ] **Step 4: Run the delegation tests and verify GREEN**

Run the same command. Expected: all delegation tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/attractions/provider.py app/attractions/service.py tests/unit/test_attraction_service.py
git commit -m "feat: add attraction service boundary"
```

### Task 3: Deterministic Attraction Query Extraction

**Files:**
- Create: `app/agent/attraction_search_query.py`
- Test: `tests/unit/test_attraction_search_query.py`

**Interfaces:**
- Consumes: Task 1 sort/mode types and the current `HotelNearbyQueryExtractor` regex/value conventions.
- Produces: `AttractionSearchQueryExtraction` and `AttractionSearchQueryExtractor.extract(message)`.

- [ ] **Step 1: Write the failing extraction tests**

Lock the exact extraction contract:

```python
@pytest.mark.parametrize(
    ("message", "mode", "city", "location", "sort_by"),
    [
        ("厦门有哪些景点", "city", "厦门", None, None),
        ("厦门有什么好玩的景点", "city", "厦门", None, None),
        ("厦门评分最高的景点", "city", "厦门", None, "rating"),
        ("厦门大学附近有什么景点", "nearby", "厦门", "厦门大学", None),
        ("厦门鼓浪屿附近景点推荐", "nearby", "厦门", "鼓浪屿", None),
        ("厦门大学附近评分最高的景点", "nearby", "厦门", "厦门大学", "rating"),
        ("厦门大学附近最近的景点", "nearby", "厦门", "厦门大学", "distance"),
    ],
)
def test_extracts_frozen_attraction_queries(...): ...
```

Add tests for `厦门大学附近3公里评分最高的景点` -> `radius=3000`, default radius 2000, boundaries 500/20000, invalid radius, no sort-term contamination, `鼓浪屿附近有什么景点` -> `missing_fields=("city",)`, and `厦门附近有什么景点` -> city mode.

The city-distance case must be explicit:

```python
def test_city_distance_sort_is_invalid_before_request_construction() -> None:
    result = AttractionSearchQueryExtractor().extract("厦门最近的景点")
    assert result.mode == "city"
    assert "sort_by" in result.invalid_fields
```

- [ ] **Step 2: Run extraction tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_search_query.py -q
```

Expected: collection fails because the Attraction extraction module does not exist.

- [ ] **Step 3: Implement deterministic extraction**

Implement the following public shape:

```python
@dataclass(frozen=True)
class AttractionSearchQueryExtraction:
    mode: AttractionSearchMode | None = None
    city: str | None = None
    location_query: str | None = None
    radius: int | None = None
    sort_by: AttractionSortBy | None = None
    invalid_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()

class AttractionSearchQueryExtractor:
    def extract(self, message: str) -> AttractionSearchQueryExtraction: ...
```

Use explicit attraction-list signals and nearby markers. Do not route knowledge phrases merely because they contain `景点`. Strip search prefixes and sorting words from `location_query`. Do not infer city or maintain a location-to-city alias table. For a city-mode distance phrase, preserve the detected preference only as an invalid extraction condition; the application will refuse to build a city request.

- [ ] **Step 4: Run extraction tests and verify GREEN**

Run the same command. Expected: all extraction tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/agent/attraction_search_query.py tests/unit/test_attraction_search_query.py
git commit -m "feat: add attraction query extraction"
```

### Task 4: Intent Contract and RAG Regression Protection

**Files:**
- Modify: `app/agent/intent.py`
- Modify: `app/agent/graph.py`
- Modify: `app/api/chat.py`
- Test: `tests/unit/test_intent.py`, `tests/unit/test_agent_routes.py`, `tests/unit/test_agent_graph.py`

**Interfaces:**
- Consumes: current `Intent`, `IntentResult`, `RuleIntentClassifier`, `SafeTravelAgent`, and API `_KNOWN_INTENTS` behavior.
- Produces: the new `Intent` literal `attraction_search`, deterministic routing, and model-prompt support without changing existing branch priority.

- [ ] **Step 1: Add failing routing assertions**

Add focused tests:

```python
@pytest.mark.parametrize("message", [
    "厦门有哪些景点",
    "厦门大学附近有什么景点",
])
def test_rule_classifier_routes_attraction_lists_to_attraction_search(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "attraction_search"

@pytest.mark.parametrize("message", [
    "鼓浪屿有什么特点",
    "鼓浪屿有哪些值得了解的特点",
    "鼓浪屿怎么去",
    "介绍一下鼓浪屿",
])
def test_rule_classifier_keeps_attraction_knowledge_on_rag_route(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "travel_knowledge"
```

Also freeze `厦门有什么值得去的地方` and `厦门大学附近有什么好玩的` as `travel_knowledge`, and retain explicit Train, Weather, Hotel, Planner, smalltalk, and unsupported assertions.

- [ ] **Step 2: Run routing tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_intent.py tests/unit/test_agent_routes.py tests/unit/test_agent_graph.py -q -x
```

Expected: the new attraction assertions fail because current code returns `travel_knowledge` or the old fallback intent.

- [ ] **Step 3: Implement the minimum route contract**

Update the literal and model prompt:

```python
Intent = Literal[
    "plan_trip", "modify_trip", "explain_trip", "travel_knowledge",
    "weather_query", "train_query", "hotel_nearby", "attraction_search",
    "smalltalk", "unsupported",
]
```

In `RuleIntentClassifier.classify`, evaluate attraction-list signals after Hotel nearby and before generic travel knowledge. Keep complete planning, Train, planning context, Weather, and unsupported Hotel-transit checks ahead of it. Add `attraction_search` to the API `_KNOWN_INTENTS` set and update the model prompt to distinguish live attraction lists from knowledge/advice questions.

- [ ] **Step 4: Run routing tests and verify GREEN**

Run the same command. Expected: new assertions and all existing routing tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/agent/intent.py app/agent/graph.py app/api/chat.py tests/unit/test_intent.py tests/unit/test_agent_routes.py tests/unit/test_agent_graph.py
git commit -m "feat: route attraction search intent"
```

### Task 5: Baidu Attraction Provider Contract

**Files:**
- Create: `app/providers/baidu_attraction.py`
- Test: `tests/unit/test_baidu_attraction.py`

**Interfaces:**
- Consumes: Task 1 request/result models, Task 2 provider protocol, existing `BaiduHotelProvider` HTTP/error conventions, and `httpx` fake-client patterns.
- Produces: `BaiduAttractionProvider.search_city()` and `search_nearby()` with safe parsing and returned-order preservation.

- [ ] **Step 1: Write mock-HTTP RED tests**

Cover both endpoint shapes and safe parsing:

```python
def test_city_search_uses_region_endpoint_and_controlled_filter(): ...
def test_nearby_search_uses_around_endpoint_and_coordinates(): ...
def test_provider_preserves_response_order_without_local_sort(): ...
def test_provider_parses_rating_comment_distance_and_tags(): ...
def test_provider_keeps_missing_optional_fields_as_none(): ...
def test_provider_normalizes_negative_or_malformed_optional_values(): ...
def test_provider_uses_trust_env_false_when_it_creates_client(): ...
def test_unverified_provider_returns_safe_unavailable_without_http_call(): ...
```

Assert safe common parameters exactly: `scope=2`, `ret_coordtype=gcj02ll`, `output=json`, pagination, `region`/`region_limit` for city, and `location`/`radius`/`radius_limit`/`coord_type` for nearby. Assert that the request includes one provider-controlled category filter and never a user-supplied raw filter.

The tests must not freeze an unverified category literal or rating sort field. Use a constructor-injected internal contract seam for the controlled filter and sort mapping, and assert that the provider uses those values without exposing them to request callers. Also assert the unconfigured state: with both values omitted, the provider reports `contract_state="unverified"`, returns `AttractionSearchResult(items=[], total=None, status="unavailable", warning="BAIDU_ATTRACTION_NOT_CONFIGURED", ...)`, and makes no HTTP call.

- [ ] **Step 2: Run provider tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_baidu_attraction.py -q
```

Expected: collection fails because `app.providers.baidu_attraction` does not exist.

- [ ] **Step 3: Implement the provider behind the controlled seam**

Use this shape:

```python
class BaiduAttractionProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
        category_filter: str | None = None,
        sort_filters: Mapping[AttractionSortBy, str] | None = None,
    ) -> None: ...

    def search_city(self, request: AttractionSearchRequest) -> AttractionSearchResult: ...
    def search_nearby(self, request: AttractionNearbySearchRequest) -> AttractionSearchResult: ...
```

`category_filter` and `sort_filters` are provider/composition-controlled values, not fields on user requests. They are optional during this pre-live-contract stage: omitted or incomplete values produce the minimal `contract_state="unverified"`; a non-empty category filter plus both `rating` and `distance` mappings produce `contract_state="configured"`. The unverified state returns the safe unavailable result described above without an HTTP request. The HTTP client created by a configured provider must use `trust_env=False`; no `verify=False` or debug output is allowed. The provider maps payload status/errors to safe codes, skips or safely rejects malformed POIs, converts optional numeric fields to `None`, accepts `total=None`, and returns items in the exact response order.

Do not invent the production category or rating field in this task. The real API gate in Task 15 will freeze those values and Task 16 will update only this controlled seam.

- [ ] **Step 4: Run provider tests and verify GREEN**

Run the same command. Expected: all mock-HTTP provider tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/providers/baidu_attraction.py tests/unit/test_baidu_attraction.py
git commit -m "feat: add Baidu attraction provider contract"
```

### Task 6: AttractionSearchApplication

**Files:**
- Create: `app/application/attraction_search.py`
- Test: `tests/unit/test_attraction_search_application.py`

**Interfaces:**
- Consumes: Task 1 domain requests/results, Task 2 `AttractionService`, existing `LocationService.resolve()` and `LocationServiceError`.
- Produces: `AttractionSearchApplication`, `AttractionCityApplicationRequest`, `AttractionNearbyApplicationRequest`, and `AttractionSearchApplicationResult`.

- [ ] **Step 1: Write failing application tests**

Use fakes to prove the boundary:

```python
def test_search_city_does_not_call_location_service(): ...
def test_search_nearby_resolves_location_then_forwards_coordinates(): ...
def test_search_nearby_forwards_radius_and_sort_by(): ...
def test_location_service_errors_are_propagated(): ...
def test_empty_result_and_total_none_are_preserved(): ...
def test_invalid_city_distance_never_reaches_attraction_service(): ...
```

The application-facing requests must carry extracted user context:

```python
@dataclass(frozen=True)
class AttractionCityApplicationRequest:
    city: str
    sort_by: Literal["rating"] | None = None
    page: int = 1
    page_size: int = 10

@dataclass(frozen=True)
class AttractionNearbyApplicationRequest:
    location_query: str
    city: str
    radius: int = 2000
    sort_by: AttractionSortBy | None = None
    page: int = 1
    page_size: int = 10
```

- [ ] **Step 2: Run application tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_search_application.py -q
```

Expected: collection fails because the Attraction application module does not exist.

- [ ] **Step 3: Implement the two explicit application entry points**

Implement:

```python
class AttractionSearchApplication:
    def __init__(self, *, location_service: LocationService,
                 attraction_service: AttractionService) -> None: ...

    def search_city(
        self, request: AttractionCityApplicationRequest
    ) -> AttractionSearchApplicationResult: ...

    def search_nearby(
        self, request: AttractionNearbyApplicationRequest
    ) -> AttractionSearchApplicationResult: ...
```

`search_city()` calls only `AttractionService.search_city()` and sets `location_query=None`, `radius=None`. `search_nearby()` calls `LocationService.resolve(LocationQuery(query=..., city=...))`, then creates an `AttractionNearbySearchRequest` from the resolved coordinates and delegates once. It does not sort or call RAG V2. A city-distance invalid condition is rejected before a domain city request is built.

Use a result wrapper with exact fields:

```python
@dataclass(frozen=True)
class AttractionSearchApplicationResult:
    attractions: AttractionSearchResult
    mode: AttractionSearchMode
    city: str
    location_query: str | None
    radius: int | None
```

- [ ] **Step 4: Run application tests and verify GREEN**

Run the same command. Expected: all application tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/application/attraction_search.py tests/unit/test_attraction_search_application.py
git commit -m "feat: add attraction search application"
```

### Task 7: Attraction Reply Renderer

**Files:**
- Create: `app/application/attraction_search_reply.py`
- Test: `tests/unit/test_attraction_search_reply.py`

**Interfaces:**
- Consumes: Task 1 `AttractionSearchResult` and Task 6 `AttractionSearchApplicationResult`.
- Produces: `AttractionReplyRenderer.render(result) -> str`.

- [ ] **Step 1: Write failing renderer tests**

Cover city and nearby headings, first three results, optional fields, missing values, `total=None`, and distance formatting:

```python
def test_city_renderer_omits_distance_and_uses_total(): ...
def test_nearby_renderer_shows_distance_when_present(): ...
def test_renderer_uses_len_items_when_total_is_none(): ...
def test_renderer_omits_missing_optional_fields_without_fake_values(): ...
def test_renderer_never_exposes_internal_ids_or_price_fields(): ...
```

- [ ] **Step 2: Run renderer tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_search_reply.py -q
```

Expected: collection fails because the renderer module does not exist.

- [ ] **Step 3: Implement bounded rendering**

Use this public shape:

```python
class AttractionReplyRenderer:
    def render(self, result: AttractionSearchApplicationResult) -> str: ...
```

Render at most three items in provider order. Use `total` when non-`None`, otherwise `len(items)` for the heading. Omit absent rating/comment/distance/tags/address values. Format `320` as `320 米` and `1500` as `1.5 公里`. City output never includes distance. Do not sort, call a provider, call RAG, fabricate values, or print price/ticket/booking/internal IDs.

- [ ] **Step 4: Run renderer tests and verify GREEN**

Run the same command. Expected: all renderer tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/application/attraction_search_reply.py tests/unit/test_attraction_search_reply.py
git commit -m "feat: add attraction search renderer"
```

### Task 8: SafeTravelAgent City Search Branch

**Files:**
- Modify: `app/agent/graph.py`
- Test: `tests/unit/test_attraction_agent.py`

**Interfaces:**
- Consumes: Task 3 extractor, Task 4 `attraction_search` intent, Task 6 city application request/result, Task 7 renderer.
- Produces: SafeTravelAgent city branch with `ChatResult(intent="attraction_search")`.

- [ ] **Step 1: Write failing city-agent tests**

Add tests proving:

```python
def test_city_attraction_query_calls_attraction_application_not_rag(): ...
def test_city_attraction_query_does_not_start_profile_collection(): ...
def test_city_attraction_provider_unavailable_is_safe(): ...
```

Use a classifier returning `attraction_search`, a recording application, a recording renderer, and a RAG fake that raises if called.

- [ ] **Step 2: Run the city-agent tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_agent.py -q -x
```

Expected: the constructor has no Attraction injection/branch and the recording application is not called.

- [ ] **Step 3: Add only the city branch and injection seams**

Extend the `SafeTravelAgent` constructor with:

```python
attraction_search_extractor: Any | None = None
attraction_search_application: Any | None = None
attraction_search_renderer: Any | None = None
```

In `collect()` and `run()` handle `attraction_search` after Hotel and before generic profile extraction. Validate extraction fields, build `AttractionCityApplicationRequest` for city mode, call the application, render the result, and return a `ChatResult` with `intent="attraction_search"`. Missing fields become deterministic clarification; provider failures become safe Attraction unavailable responses. Do not alter RAG, Hotel, Train, Weather, or Planner branches.

- [ ] **Step 4: Run city-agent tests and existing route tests**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_agent.py tests/unit/test_agent_routes.py tests/unit/test_agent_graph.py -q
```

Expected: all targeted tests pass and existing route isolation remains green.

- [ ] **Step 5: Commit**

```powershell
git add app/agent/graph.py tests/unit/test_attraction_agent.py
git commit -m "feat: add attraction city agent branch"
```

### Task 9: SafeTravelAgent Nearby Search Branch

**Files:**
- Modify: `app/agent/graph.py`
- Test: `tests/unit/test_attraction_agent.py`

**Interfaces:**
- Consumes: Task 3 nearby extraction, Task 6 nearby application request/result, Task 7 renderer, and Task 8 Agent injection seams.
- Produces: normal nearby execution with explicit radius/sort forwarding and no pending persistence yet.

- [ ] **Step 1: Write failing nearby-agent tests**

Add focused tests:

```python
def test_nearby_attraction_query_forwards_rating(): ...
def test_nearby_attraction_query_forwards_distance(): ...
def test_nearby_attraction_query_maps_location_not_found(): ...
def test_nearby_attraction_without_city_clarifies_without_location_call(): ...
def test_nearby_attraction_provider_unavailable_does_not_call_rag(): ...
```

- [ ] **Step 2: Run nearby tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_agent.py -q -x
```

Expected: the city-only branch does not construct nearby requests or map the nearby application errors.

- [ ] **Step 3: Implement the nearby branch**

When extraction returns `mode="nearby"` and all required fields are valid, construct:

```python
AttractionNearbyApplicationRequest(
    location_query=extracted.location_query,
    city=extracted.city,
    radius=extracted.radius or 2000,
    sort_by=extracted.sort_by,
)
```

Call `search_nearby()`, render the result, and return `ChatResult(intent="attraction_search")`. If `city` or `location_query` is missing, return clarification and do not call `LocationService`. Preserve `LOCATION_NOT_FOUND` and `LOCATION_AMBIGUOUS` for the next pending-state task.

- [ ] **Step 4: Run nearby and existing route tests**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_attraction_agent.py tests/unit/test_agent_routes.py tests/unit/test_intent.py -q
```

Expected: all Attraction nearby tests and existing routing tests pass.

- [ ] **Step 5: Commit**

```powershell
git add app/agent/graph.py tests/unit/test_attraction_agent.py
git commit -m "feat: add attraction nearby agent branch"
```

### Task 10: Attraction LOCATION_AMBIGUOUS State

**Files:**
- Modify: `app/agent/graph.py`
- Modify: `app/application/chat.py`
- Test: `tests/integration/test_chat_attraction_search.py`, `tests/unit/test_attraction_agent.py`

**Interfaces:**
- Consumes: Task 9 nearby branch, `LocationServiceError.candidates`, current `ConfirmationStore` Hotel-specific methods, and `TravelChatApplication.collect()`.
- Produces: `PendingAttractionNearbySelection`, independent cross-request persistence, and `collect_attraction_nearby_selection()`.

- [ ] **Step 1: Write failing two-turn tests**

Define the expected state and HTTP behavior:

```python
def test_attraction_ambiguous_selection_preserves_sort_by_and_does_not_call_rag():
    first = post_collect("厦门鼓浪屿附近评分最高的景点")
    assert first.json()["error_code"] == "LOCATION_AMBIGUOUS"

    second = post_collect("鼓浪屿风景名胜区", same_thread=True)
    assert fake_attraction_application.requests[1].sort_by == "rating"
    assert rag_answerer.calls == []
```

Add an isolation test that stores Hotel and Attraction pending entries under the same subject/thread and asserts both remain independently retrievable.

- [ ] **Step 2: Run the state tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_chat_attraction_search.py tests/unit/test_attraction_agent.py -q -x
```

Expected: the attraction pending type/store methods do not exist and the second request is reclassified instead of restoring the Attraction request.

- [ ] **Step 3: Implement independent pending state**

Add:

```python
@dataclass(frozen=True)
class PendingAttractionNearbySelection:
    city: str
    radius: int
    candidate_names: tuple[str, ...]
    sort_by: AttractionSortBy | None = None

    def matches(self, message: str) -> bool: ...
```

Add separate `ConfirmationStore` methods:

```python
def get_attraction_nearby_pending(...): ...
def put_attraction_nearby_pending(..., pending: PendingAttractionNearbySelection) -> None: ...
def discard_attraction_nearby_pending(...): ...
```

In `TravelChatApplication.collect()`, check an attraction pending candidate before normal classifier routing, and preserve the existing Hotel pending path separately. Save the Agent’s current Attraction pending object after each collect request. In `SafeTravelAgent`, save `sort_by` on ambiguity and restore `location_query`, `city`, `radius`, and `sort_by` when the candidate is selected. Do not create `PendingPOISelection`.

- [ ] **Step 4: Run the two-turn and isolation tests**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_chat_attraction_search.py tests/integration/test_chat_hotel_nearby.py tests/unit/test_attraction_agent.py -q
```

Expected: attraction two-turn restoration, Hotel regression, and pending isolation all pass.

- [ ] **Step 5: Commit**

```powershell
git add app/agent/graph.py app/application/chat.py tests/integration/test_chat_attraction_search.py tests/unit/test_attraction_agent.py
git commit -m "feat: persist attraction nearby selection state"
```

### Task 11: Composition Root and Optional Provider

**Files:**
- Modify: `app/composition.py`
- Test: `tests/integration/test_attraction_composition.py`

**Interfaces:**
- Consumes: Task 5 optional provider contract seam (`contract_state="unverified"` until live values are frozen), Task 6 application, Task 7 renderer, Task 8/9 Agent injection names, `Settings.baidu_map_ak`, and existing optional Hotel composition behavior.
- Produces: cached/build functions for Attraction and non-blocking chat composition.

- [ ] **Step 1: Write failing composition tests**

Add tests for configured wiring and missing configuration:

```python
def test_build_attraction_search_application_wires_location_and_provider(): ...
def test_missing_baidu_configuration_does_not_block_chat_composition(): ...
def test_attraction_unavailable_does_not_disable_rag_hotel_train_or_weather(): ...
def test_get_attraction_search_application_is_cached(): ...
def test_unverified_attraction_provider_keeps_chat_composition_available(): ...
```

Record constructor arguments with fakes. Verify that no provider network call occurs during construction.

- [ ] **Step 2: Run composition tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_attraction_composition.py -q
```

Expected: Attraction build/get functions and Agent injection do not exist.

- [ ] **Step 3: Add the minimal optional composition chain**

Add narrowly scoped functions:

```python
def build_attraction_service(*, settings: Settings | None = None) -> AttractionService: ...
def get_attraction_service() -> AttractionService: ...
def build_attraction_search_application(*, settings: Settings | None = None) -> AttractionSearchApplication: ...
def get_attraction_search_application() -> AttractionSearchApplication: ...
```

Build the existing `LocationService` only for nearby resolution. Before Task 15, construct `BaiduAttractionProvider` with `category_filter=None` and `sort_filters=None`, so its explicit `contract_state="unverified"` returns safe Attraction unavailable results and never sends a guessed production request. Catch only the established Baidu configuration exception at chat composition so non-attraction requests still build. Inject `AttractionSearchQueryExtractor`, application, and renderer into `SafeTravelAgent` without changing RAG V2, Hotel, Train, Weather, or Planner construction. Task 16 is the only task that supplies live-evidence-backed production constants.

- [ ] **Step 4: Run composition and existing composition tests**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_attraction_composition.py tests/integration/test_hotel_nearby_composition.py tests/integration/test_rag_composition.py tests/integration/test_location_composition.py -q
```

Expected: Attraction wiring and all existing composition behavior pass.

- [ ] **Step 5: Commit**

```powershell
git add app/composition.py tests/integration/test_attraction_composition.py
git commit -m "feat: compose attraction search service"
```

### Task 12: Public Chat Integration

**Files:**
- Modify: `app/api/chat.py` only if `_KNOWN_INTENTS` or fallback handling is incomplete after Task 4.
- Modify: `app/application/chat.py` only for the Attraction pending methods from Task 10.
- Test: `tests/integration/test_chat_attraction_search.py`

**Interfaces:**
- Consumes: Task 4 intent contract, Task 8/9 Agent branches, Task 10 pending persistence, Task 11 composition.
- Produces: unchanged public `ChatRequest`/`ChatResponse` behavior with Attraction replies delivered through `/api/chat`.

- [ ] **Step 1: Write failing public HTTP tests**

Cover:

```python
def test_chat_city_attraction_search_uses_attraction_application(): ...
def test_chat_nearby_attraction_search_uses_location_and_attraction_application(): ...
def test_chat_missing_city_clarifies_without_provider_call(): ...
def test_chat_location_not_found_is_safe(): ...
def test_chat_location_ambiguous_restores_rating_across_collect_requests(): ...
def test_chat_attraction_search_preserves_rag_boundary(): ...
def test_chat_attraction_search_accepts_rating_and_distance_requests(): ...
```

Use existing API fixtures and fakes. Assert no public schema expansion: no raw provider fields, internal IDs, prices, or RAG citations are added to the Attraction response.

- [ ] **Step 2: Run public chat tests and verify RED**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_chat_attraction_search.py -q -x
```

Expected: the current public composition has no Attraction wiring or pending slot, so the new endpoint-path assertions fail.

- [ ] **Step 3: Complete only the required public wiring**

Ensure `app/api/chat.py` recognizes `attraction_search` in `_KNOWN_INTENTS` and fallback selection. Ensure `TravelChatApplication.collect()` uses the separate Attraction pending slot. Do not add a new endpoint or public response field.

- [ ] **Step 4: Run public chat and existing API regression tests**

Run:

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/integration/test_chat_attraction_search.py tests/integration/test_chat_api.py tests/integration/test_chat_hotel_nearby.py tests/integration/test_hotel_api.py -q
```

Expected: Attraction HTTP flows pass and existing chat/Hotel API behavior remains green.

- [ ] **Step 5: Commit**

```powershell
git add app/api/chat.py app/application/chat.py tests/integration/test_chat_attraction_search.py
git commit -m "feat: expose attraction search through chat"
```

### Task 13: Focused Regression Gate

**Files:**
- Create: none.
- Modify: none.
- Test: existing Attraction, Intent, Location, Hotel, RAG V2, Chat, and Composition test files.

**Interfaces:**
- Consumes: all completed Tasks 1–12.
- Produces: evidence that the new module has not changed existing domain boundaries.

**Commit message:** none; this is a verification-only gate and must not create an empty commit.

- [ ] **Step 1: Run the exact focused regression**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest `
  tests/unit/test_attraction_models.py `
  tests/unit/test_attraction_service.py `
  tests/unit/test_attraction_search_query.py `
  tests/unit/test_baidu_attraction.py `
  tests/unit/test_attraction_search_application.py `
  tests/unit/test_attraction_search_reply.py `
  tests/unit/test_attraction_agent.py `
  tests/unit/test_intent.py `
  tests/unit/test_agent_routes.py `
  tests/unit/test_train_extraction.py `
  tests/unit/test_train_service.py `
  tests/unit/test_train_application.py `
  tests/unit/test_weather_service.py `
  tests/unit/test_planning.py `
  tests/unit/test_location_service.py `
  tests/unit/test_hotel_nearby_query.py `
  tests/unit/test_hotel_nearby_application.py `
  tests/unit/test_hotel_nearby_reply.py `
  tests/integration/test_chat_attraction_search.py `
  tests/integration/test_chat_hotel_nearby.py `
  tests/integration/test_attraction_composition.py `
  tests/integration/test_hotel_nearby_composition.py `
  tests/integration/test_rag_composition.py `
  tests/integration/test_structured_planner_production_seam.py `
  -q
```

These are the existing repository tests selected for Train, Weather, and Planner isolation; no nonexistent module is assumed. Acceptance: all listed tests pass; Attraction Search does not call RAG V2 for list queries; RAG V2 knowledge queries remain on the knowledge route; Hotel, Train, Weather, Planner, and existing location behavior remain green.

- [ ] **Step 2: Review the diff without changing behavior**

Run `git diff --check`, inspect only Attraction-related changes, and verify no Hotel model/provider refactor, local sorting, secret output, or public schema expansion was introduced.

- [ ] **Step 3: Commit**

No repository commit is created. Retain the preceding task commits and record the focused regression result in the review log.

### Task 14: Full Regression Gate

**Files:**
- Create: none.
- Modify: none.
- Test: full repository test suite.

**Interfaces:**
- Consumes: all implemented Attraction behavior and all existing test modules.
- Produces: full-regression evidence.

**Commit message:** none; this is a verification-only gate and must not create an empty commit.

- [ ] **Step 1: Run the full suite**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

Acceptance:

- every existing test passes;
- every new Attraction test passes;
- only already-known pre-existing warnings are allowed;
- no failure is attributed to changed Train, Hotel, Weather, or RAG V2 behavior.

- [ ] **Step 2: Review the full-suite result**

If the suite fails, classify the first failure by module before editing. Do not weaken existing assertions or skip unrelated tests.

- [ ] **Step 3: Commit**

No repository commit is created.

### Task 15: Real Baidu Diagnostic Gate

**Files:**
- Create: none in the repository; use a temporary UTF-8 script under the user temp directory and delete it after execution.
- Modify: none before evidence review.
- Test: operator-run live diagnostic only.

**Interfaces:**
- Consumes: the provider request contract from Task 5 and local `.env` `BAIDU_MAP_AK`.
- Produces: evidence freezing the category filter, display fields, rating sort field, and distance sort field before production constants are finalized.

- [ ] **Step 1: Prepare the safe diagnostic command**

The operator creates a UTF-8 temporary script and runs it with the repository venv. The script uses `httpx.Client(trust_env=False)`, loads `.env`, and never prints the key, full URL, headers, or raw response.

The script sends identical requests except for endpoint, filter, and the frozen legal sort matrix:

- `REGION_DEFAULT`: region endpoint, no sort filter;
- `REGION_RATING`: region endpoint, rating sort filter;
- `AROUND_DEFAULT`: around endpoint, no sort filter;
- `AROUND_RATING`: around endpoint, rating sort filter;
- `AROUND_DISTANCE`: around endpoint, distance sort filter.

It must not send a region/city distance request because city + distance is invalid. For each of the five responses, print only the endpoint-specific status, a safe filter label, and the first ten rows containing name, display rating, comment count, distance, and tag/type. Compute endpoint-specific non-increasing rating and non-decreasing distance over non-`None` values and record missing-rating positions.

Required safe output fields:

```text
REGION_DEFAULT_STATUS=
REGION_RATING_STATUS=
AROUND_DEFAULT_STATUS=
AROUND_RATING_STATUS=
AROUND_DISTANCE_STATUS=
REGION_DEFAULT_ROW_1_NAME=
REGION_RATING_ROW_1_NAME=
AROUND_DEFAULT_ROW_1_NAME=
AROUND_RATING_ROW_1_NAME=
AROUND_DISTANCE_ROW_1_NAME=
REGION_RATING_NONINCREASING=PASS|FAIL|INCONCLUSIVE
AROUND_RATING_NONINCREASING=PASS|FAIL|INCONCLUSIVE
AROUND_DISTANCE_NONDECREASING=PASS|FAIL|INCONCLUSIVE
REGION_RATING_MISSING_POSITION=FRONT|BACK|MIXED|ALL_NONE|NONE
AROUND_RATING_MISSING_POSITION=FRONT|BACK|MIXED|ALL_NONE|NONE
```

- [ ] **Step 2: Compare the live evidence with the unverified provider contract**

Freeze only values supported by the evidence:

1. category filter returns actual attraction POIs;
2. display rating field is identified separately for region and around;
3. rating server-side sort field is identified separately for region and around;
4. distance sort behavior is identified for around only;
5. missing rating placement is recorded per sorted endpoint.

Status `0` alone is insufficient for an ordering pass.

- [ ] **Step 3: Record evidence and stop before any repository change**

Record the exact category filter, display-field mapping, rating filter mapping, and distance filter mapping supported by the five responses. Do not modify provider constants, tests, or any other repository file in this task, and do not commit. Hand the evidence to Task 16 for the mandatory TDD RED → GREEN production freeze.

### Task 16: Live API Contract Freeze and Fix Gate

**Files:**
- Modify: `app/providers/baidu_attraction.py` to replace the unverified provider contract with the exact Task 15 evidence-backed production contract.
- Test: `tests/unit/test_baidu_attraction.py`.

**Interfaces:**
- Consumes: the exact Task 15 diagnostic evidence.
- Produces: the first evidence-backed production category/sort/parser contract, or the smallest provider-only correction when Task 15 exposed a mismatch.

- [ ] **Step 1: Convert the Task 15 evidence into focused failing tests**

Write deterministic fake-HTTP assertions for every provider-owned value that Task 15 froze: category filter, region/around rating mapping, around distance mapping, and any parser field mapping required by the evidence. Because the current production provider is intentionally unverified, these tests must fail against `contract_state="unverified"` or the old mapping for the exact observed reason. Do not encode a value that Task 15 did not establish.

- [ ] **Step 2: Run the focused RED test**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_baidu_attraction.py -q -x
```

Expected: focused assertion failures for the evidence-backed production contract, with no live dependency.

- [ ] **Step 3: Freeze only the evidence-backed provider contract**

Change only the provider-owned category filter constant, rating sort mapping, distance sort mapping, and parser mapping supported by the diagnostic. Set the provider to `contract_state="configured"` only with those evidence-backed values. Do not add application-side sorting, renderer sorting, guessed fields, or fallback logic.

- [ ] **Step 4: Run GREEN and focused regression**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest tests/unit/test_baidu_attraction.py tests/unit/test_attraction_search_application.py tests/unit/test_attraction_search_reply.py tests/integration/test_chat_attraction_search.py -q
```

Expected: all provider and Attraction focused tests pass, including the no-local-sort and no-RAG-boundary assertions.

- [ ] **Step 5: Commit**

```powershell
git add app/providers/baidu_attraction.py tests/unit/test_baidu_attraction.py
git commit -m "fix: align attraction provider with live Baidu behavior"
```

### Task 17: Local Real `/api/chat` E2E

**Files:**
- Create: none.
- Modify: none unless a prior focused regression identifies a separately approved fix.
- Test: operator-run local E2E using the existing local server and `.env`.

**Interfaces:**
- Consumes: composed production chain, local Baidu AK, anonymous session cookie, and public `/api/chat`.
- Produces: evidence for city, nearby, sorting, RAG boundary, and multi-turn state behavior.

**Commit message:** none; this is an operator-only verification gate.

- [ ] **Step 1: Run the local real requests**

Use the same anonymous session cookie for:

- `厦门有哪些景点`;
- `厦门大学附近有什么景点`;
- `厦门大学附近评分最高的景点`;
- `厦门大学附近最近的景点`;
- `鼓浪屿有什么特点`;
- first request `厦门鼓浪屿附近评分最高的景点`;
- candidate selection using the same thread and cookie.

- [ ] **Step 2: Verify routing and safe output**

Acceptance:

- city and nearby requests return Attraction Search text;
- rating/distance requests retain the requested mode and sort;
- `鼓浪屿有什么特点` remains RAG V2;
- ambiguous selection continues Attraction nearby and restores `sort_by="rating"`;
- no raw AK, provider body, internal IDs, ticket data, or RAG misrouting appears.

- [ ] **Step 3: Record the local E2E result**

This is a live verification gate and creates no repository commit.

### Task 18: Review and PR Gate

**Files:**
- Create: none.
- Modify: none.
- Test: final review commands and existing regression suites.

**Interfaces:**
- Consumes: the complete implementation and all prior test evidence.
- Produces: a clean, reviewable branch ready for a human PR decision.

**Commit message:** none; this gate creates no empty review commit.

- [ ] **Step 1: Run final static checks**

```powershell
git diff --check
git status --short
git diff --stat
```

The worktree must contain no untracked secret, cookie, temporary diagnostic, or unrelated module change.

- [ ] **Step 2: Run final tests**

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
```

The result must include all Attraction tests and all pre-existing tests.

- [ ] **Step 3: Review the branch**

Inspect every Attraction diff, verify no RAG/Train/Weather/Hotel behavior changed, and run the `requesting-code-review` skill before integration. A PASS is required before handoff. Do not modify or merge the base branch during this review.

- [ ] **Step 4: Commit only if review requested a bounded correction**

Use a focused commit for any approved correction. Do not create an empty review commit.

- [ ] **Step 5: Push and create the PR after review PASS**

Confirm the worktree is clean and refresh the base reference:

```powershell
git diff --check
git status --short
git fetch origin
git branch -vv
git merge-base HEAD origin/main
git log --oneline --decorate --graph --max-count=15 HEAD origin/main
```

If `origin/main` contains commits absent from the feature branch, rebase the feature branch onto `origin/main` using the repository's existing workflow; never force-push. After a successful rebase, rerun the focused regression from Task 13 and the full suite from Task 14 before continuing. Then push the current feature branch and create a PR with base `main` and the current feature branch as head. Record the PR number, URL, head SHA, and base/head; do not merge automatically.

### Task 19: Online Smoke

**Files:**
- Create: none.
- Modify: none.
- Test: post-deployment operator smoke only.

**Interfaces:**
- Consumes: deployed branch, production Baidu configuration, and public `/api/chat`.
- Produces: online smoke evidence for Attraction and non-regression routes.

**Commit message:** none; this is a post-deployment verification gate.

- [ ] **Step 1: Confirm the human deployment gate**

Do not run this task until the PR from Task 18 has been manually approved and merged into `main`, and the resulting deployment has completed successfully. This plan never merges the PR or triggers deployment automatically.

- [ ] **Step 2: Run the online smoke cases**

Verify:

- city search;
- nearby search;
- rating search;
- distance search;
- RAG boundary query;
- Hotel nearby regression;
- Train regression;
- Weather regression.

- [ ] **Step 3: Check safety and routing**

Confirm Attraction failures do not block RAG V2, Hotel, Train, or Weather and that no raw provider data or credentials appear in responses/logs.

- [ ] **Step 4: Record the online result**

This gate creates no code commit and does not authorize rollback or unrelated production changes.

### Task 20: Cross-Region E2E

**Files:**
- Create: none.
- Modify: none.
- Test: operator-run cross-region E2E only.

**Interfaces:**
- Consumes: deployed Attraction Search city/nearby flow and selected test regions.
- Produces: evidence that the MVP does not depend on Xiamen-only parsing or data.

**Commit message:** none; this is an acceptance-only gate.

- [ ] **Step 1: Select two or three regions**

Use cities from Beijing, Hangzhou, and Chengdu. Do not add static city data or a location-to-city alias table.

- [ ] **Step 2: Exercise city and nearby searches**

For each selected region verify:

- city region search;
- nearby POI search;
- missing-city clarification;
- attraction-only filtering;
- optional field handling;
- rating/distance provider order when the API supplies those values.

- [ ] **Step 3: Record the cross-region result**

This is an acceptance gate with no repository change and no empty commit.

## Plan Self-Review

Before implementation begins, verify the following:

1. Every requirement in the approved spec maps to one or more numbered tasks.
2. Every implementation task has exact Files, Interfaces, RED, GREEN, regression, and commit steps.
3. Domain types consistently use `distance: int | None` and `total: int | None`.
4. City requests accept only `None` or `rating`; nearby requests accept `None`, `rating`, or `distance`.
5. City distance is rejected before a valid request reaches the service/provider.
6. City search never calls `LocationService`.
7. Missing city is a structured clarification and never a guessed alias or provider exception.
8. Attraction Provider failures never fall back to RAG V2.
9. Category and rating filter literals remain behind a controlled seam until Task 15 evidence.
10. Nearby pending state saves/restores `sort_by` and is isolated from Hotel pending state.
11. Optional Attraction configuration cannot block chat composition.
12. Existing RAG V2, Train, Weather, Hotel, Planner, public schema, and session behavior are protected by regression tasks.
13. The full regression and real API gates occur before local/live smoke.
14. Every repository-root Python/pytest command uses the local `.venv\Scripts\python.exe` path; no parent-directory venv path remains.
15. Task 5 controlled overrides, Task 11 `contract_state="unverified"`, Task 15 read-only evidence, and Task 16 evidence-backed freeze are one consistent provider contract.
16. Task 15 has no repository edits or commit step; Task 16 has mandatory RED → GREEN before production constants are frozen.
17. Task 13 lists the actual Train, Weather, and Planner test files, and Task 18 contains full verification, review, rebase, push, and PR handoff without auto-merge.
18. Task 19 requires manual PR approval, merge, and completed deployment before online smoke.
19. This plan contains no production implementation, test file, diagnostic file, or live command execution.

## Completion Check

Run only after the plan is written:

```powershell
git diff --check
git diff -- docs/superpowers/plans/2026-09-09-attraction-search.md
```

Confirm that only this plan file is new or modified, then commit:

```powershell
git add docs/superpowers/plans/2026-09-09-attraction-search.md
git commit -m "docs: plan attraction search implementation"
```
