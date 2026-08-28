# 城市足迹云端同步与区域点亮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有浏览器本地足迹升级为账户云端城市足迹，并在高德地图上按城市行政边界点亮。

**Architecture:** Supabase `user_footprints` 是私人足迹唯一事实来源；`FootprintModule` 负责账户级 CRUD，`DistrictBoundaryService` 负责城市目录、高德边界、缓存和降级。前端以独立 `TravelFootprints` 控制器管理挂载、请求、迁移和列表，现有地图适配器只负责 Polygon/Marker 生命周期。

**Tech Stack:** Python 3.13、FastAPI、Pydantic v2、Supabase/PostgreSQL RLS、httpx、高德 Web Service 与 JavaScript API 2.0、原生 JavaScript、Node `node:test`、pytest。

**Spec:** `docs/superpowers/specs/2026-08-28-city-footprints-design.md`

## Global Constraints

- 足迹粒度固定为地级市或同级行政区，以六位 `city_adcode` 唯一标识。
- Supabase 是用户足迹唯一事实来源；Local Storage 仅用于一次性迁移，不能作为云端失败时的写入降级。
- `(user_id, city_adcode)` 必须唯一，所有私人读写必须满足 `auth.uid() = user_id`。
- 足迹、城市搜索和边界接口都要求 Bearer Token，并沿用现有一次刷新重试规则。
- `visited_at` 不能晚于中国时区当天。
- 高德 Web 服务 Key 只存在于服务端配置。
- 城市搜索返回至多 10 项，查询长度 2 至 40 个字符。
- 前端边界请求并发上限为 3；服务端成功缓存 30 天、失败缓存 5 分钟，同一 `adcode` 并发未命中只请求一次。
- 不记录 GPS 轨迹、实时位置或精确路线；景点入口归并到所属城市。
- 不公开邮箱、完整 UUID、Storage 路径、访问令牌、服务端 Key 或私人足迹列表。
- 不修改社区 API 契约、管理员角色模型或生产凭据。
- 每个任务先写失败测试，再写最小实现；只精确暂存任务列出的文件。

## File Responsibility Map

- `app/footprints/models.py`：公开请求/响应模型和内部存储模型。
- `app/footprints/repositories.py`：内存与 Supabase 足迹仓库。
- `app/footprints/service.py`：账户所有权、城市校验、日期校验和 CRUD。
- `app/footprints/districts.py`：城市目录、边界缓存、并发合并与降级。
- `app/providers/amap_district.py`：高德行政区域 HTTP 适配器。
- `app/api/footprints.py`：足迹、城市搜索和边界路由。
- `app/static/footprints.js`：云端足迹控制器、城市搜索和旧数据迁移。
- `app/static/map-explorer.js`：Polygon/Marker 渲染、聚焦与销毁。
- `supabase/migrations/013_city_footprints.sql`：表、索引、授权和 RLS。

---

### Task 1: 建立城市足迹领域模型与纯应用服务

**Files:**
- Create: `app/footprints/__init__.py`
- Create: `app/footprints/models.py`
- Create: `app/footprints/service.py`
- Create: `tests/unit/test_footprint_models.py`
- Create: `tests/unit/test_footprint_service.py`

**Interfaces:**
- Consumes: 已认证用户 UUID、`CityDirectory.resolve(adcode)`、`FootprintRepository`。
- Produces: `FootprintCreate`、`FootprintUpdate`、`FootprintView`、`StoredFootprint`、`CityRecord`、`DistrictBoundary`、`DistrictBoundaryView`、`StaticCityDirectory`、`FootprintModule.list/add/update/remove`。

- [ ] **Step 1: 写模型失败测试**

```python
def test_create_accepts_canonical_city_and_date():
    request = FootprintCreate(city_adcode="350200", visited_at=date(2026, 8, 28))
    assert request.city_adcode == "350200"

@pytest.mark.parametrize("adcode", ["", "35020", "3502000", "xiamen", "3502 00"])
def test_create_rejects_noncanonical_adcode(adcode):
    with pytest.raises(ValidationError):
        FootprintCreate(city_adcode=adcode, visited_at=date(2026, 8, 28))
```

- [ ] **Step 2: 运行模型测试，确认因模块不存在而失败**

Run: `python -m pytest tests/unit/test_footprint_models.py -q`

- [ ] **Step 3: 实现严格模型**

```python
class FootprintCreate(StrictSchema):
    city_adcode: str = Field(pattern=r"^\d{6}$")
    visited_at: date

class FootprintUpdate(StrictSchema):
    visited_at: date

class CityRecord(StrictSchema):
    city_adcode: str = Field(pattern=r"^\d{6}$")
    city_name: str = Field(min_length=1, max_length=40)
    province_adcode: str = Field(pattern=r"^\d{6}$")
    province_name: str = Field(min_length=1, max_length=40)
    center: tuple[float, float]

class DistrictBoundary(StrictSchema):
    city: CityRecord
    rings: list[list[tuple[float, float]]]
    fetched_at: datetime

class DistrictBoundaryView(StrictSchema):
    city: CityRecord
    rings: list[list[tuple[float, float]]]
    status: Literal["fresh", "stale", "unavailable"]
```

内部 `StoredFootprint` 包含 `user_id`；公开 `FootprintView` 只返回足迹 ID、城市、省份、中心点、到访日期和时间戳，不包含 `user_id`。`StaticCityDirectory` 在 `service.py` 中保存厦门、福州、大理州、丽江四个规范城市，用于没有高德 Key 时仍能完成试点城市 CRUD。

- [ ] **Step 4: 写服务所有权、去重和日期失败测试**

```python
def test_add_uses_authenticated_owner_and_server_city(module, repository):
    result = module.add(USER_A, FootprintCreate(city_adcode="350200", visited_at=TODAY))
    assert result.city_name == "厦门市"
    assert repository.rows[(USER_A, "350200")].user_id == USER_A

def test_repeated_city_is_idempotent(module):
    assert module.add(USER_A, request()).id == module.add(USER_A, request()).id

def test_future_visit_is_rejected(module):
    with pytest.raises(AppError, match="FOOTPRINT_VALIDATION_FAILED"):
        module.add(USER_A, FootprintCreate(city_adcode="350200", visited_at=TODAY + timedelta(days=1)))

def test_other_account_cannot_update_or_delete(module):
    stored = module.add(USER_A, request())
    with pytest.raises(AppError, match="FOOTPRINT_NOT_FOUND"):
        module.update(USER_B, stored.id, FootprintUpdate(visited_at=TODAY))
```

- [ ] **Step 5: 运行服务测试，确认失败**

Run: `python -m pytest tests/unit/test_footprint_service.py -q`

- [ ] **Step 6: 实现服务协议**

```python
class CityDirectory(Protocol):
    def resolve(self, city_adcode: str) -> CityRecord | None:
        raise NotImplementedError

class FootprintRepository(Protocol):
    def list_owned(self, user_id: UUID) -> list[StoredFootprint]:
        raise NotImplementedError
    def upsert_owned(self, user_id: UUID, city: CityRecord, visited_at: date) -> StoredFootprint:
        raise NotImplementedError
    def update_visited_at(self, user_id: UUID, footprint_id: UUID, visited_at: date) -> StoredFootprint | None:
        raise NotImplementedError
    def delete_owned(self, user_id: UUID, footprint_id: UUID) -> bool:
        raise NotImplementedError
```

`FootprintModule` 注入 `today: Callable[[], date]`，并稳定产生 `FOOTPRINT_VALIDATION_FAILED`、`FOOTPRINT_CITY_NOT_FOUND`、`FOOTPRINT_NOT_FOUND`、`FOOTPRINT_UNAVAILABLE`。

- [ ] **Step 7: 运行 Task 1 测试**

Run: `python -m pytest tests/unit/test_footprint_models.py tests/unit/test_footprint_service.py -q`
Expected: PASS。

- [ ] **Step 8: 精确提交**

```powershell
git add -- app/footprints/__init__.py app/footprints/models.py app/footprints/service.py tests/unit/test_footprint_models.py tests/unit/test_footprint_service.py
git commit -m "feat: add city footprint domain"
```

---

### Task 2: 增加 Supabase 表、RLS 与账户仓库

**Files:**
- Create: `supabase/migrations/013_city_footprints.sql`
- Create: `app/footprints/repositories.py`
- Create: `tests/integration/test_city_footprints_sql_contract.py`
- Create: `tests/unit/test_footprint_repositories.py`
- Modify: `tests/integration/test_rls_contract.py`

**Interfaces:**
- Consumes: Task 1 模型和仓库协议。
- Produces: `InMemoryFootprintRepository`、`SupabaseFootprintRepository`、`create_user_scoped_footprint_repository`。

- [ ] **Step 1: 写迁移和 RLS 失败测试**

```python
def test_table_is_owner_scoped_and_city_unique():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "create table public.user_footprints" in sql
    assert "unique (user_id, city_adcode)" in sql
    assert "alter table public.user_footprints enable row level security" in sql
    assert sql.count("auth.uid() = user_id") >= 5
```

同时把 `"user_footprints": "user_id"` 加入 `OWNER_SCOPED_TABLES`，复用后续迁移安全回归扫描。

- [ ] **Step 2: 运行迁移契约，确认失败**

Run: `python -m pytest tests/integration/test_city_footprints_sql_contract.py tests/integration/test_rls_contract.py -q`

- [ ] **Step 3: 编写迁移**

```sql
create table public.user_footprints (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  city_adcode text not null check (city_adcode ~ '^[0-9]{6}$'),
  city_name text not null check (char_length(btrim(city_name)) between 1 and 40),
  province_adcode text not null check (province_adcode ~ '^[0-9]{6}$'),
  province_name text not null check (char_length(btrim(province_name)) between 1 and 40),
  center_lng double precision not null check (center_lng between 73 and 136),
  center_lat double precision not null check (center_lat between 3 and 54),
  visited_at date not null check (visited_at <= current_date),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, city_adcode),
  unique (id, user_id)
);
```

为 select/insert/update/delete 分别建立所有者策略；update 同时包含 `using` 和 `with check`。撤销 public/anon/authenticated 默认权限，只向 authenticated 授予四种数据操作；增加账户和日期排序索引及 `updated_at` 触发器。

- [ ] **Step 4: 写仓库查询形状和所有者过滤失败测试**

```python
def test_upsert_uses_verified_owner_and_composite_conflict(repository, client):
    repository.upsert_owned(USER_A, XIAMEN, TODAY)
    assert client.query.payload["user_id"] == str(USER_A)
    assert client.query.on_conflict == "user_id,city_adcode"

def test_update_filters_id_and_owner(repository, client):
    repository.update_visited_at(USER_A, FOOTPRINT_ID, TODAY)
    assert ("id", str(FOOTPRINT_ID)) in client.query.filters
    assert ("user_id", str(USER_A)) in client.query.filters
```

- [ ] **Step 5: 运行仓库测试，确认失败**

Run: `python -m pytest tests/unit/test_footprint_repositories.py -q`

- [ ] **Step 6: 实现内存与 Supabase 仓库**

Supabase 客户端必须绑定用户 access token；每个查询显式过滤 `user_id`。数据库或响应异常统一包装为 `FOOTPRINT_UNAVAILABLE`，无行 update/delete 返回 `None/False`。

- [ ] **Step 7: 运行 Task 2 测试**

Run: `python -m pytest tests/unit/test_footprint_repositories.py tests/integration/test_city_footprints_sql_contract.py tests/integration/test_rls_contract.py -q`
Expected: PASS。

- [ ] **Step 8: 精确提交**

```powershell
git add -- supabase/migrations/013_city_footprints.sql app/footprints/repositories.py tests/unit/test_footprint_repositories.py tests/integration/test_city_footprints_sql_contract.py tests/integration/test_rls_contract.py
git commit -m "feat: persist private city footprints"
```

---

### Task 3: 暴露足迹 CRUD API 并完成生产组装

**Files:**
- Create: `app/api/footprints.py`
- Create: `tests/integration/test_footprints_api.py`
- Create: `tests/unit/test_footprint_production_wiring.py`
- Modify: `app/composition.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: Tasks 1–2。
- Produces: `get_footprint_module(user)` 和 `GET/POST/PATCH/DELETE /api/footprints`。

- [ ] **Step 1: 写认证、响应隐私和错误映射失败测试**

```python
def test_list_requires_bearer(client):
    assert client.get("/api/footprints").status_code == 401

def test_list_omits_owner_identity(client, monkeypatch):
    response = client.get("/api/footprints", headers=auth_header())
    assert response.status_code == 200
    assert "user_id" not in response.text
    assert "email" not in response.text

@pytest.mark.parametrize("code,status", [
    ("FOOTPRINT_VALIDATION_FAILED", 422),
    ("FOOTPRINT_CITY_NOT_FOUND", 404),
    ("FOOTPRINT_NOT_FOUND", 404),
    ("FOOTPRINT_UNAVAILABLE", 503),
])
def test_api_maps_stable_errors(client, code, status):
    client.app.dependency_overrides[get_footprint_module] = lambda: RaisingModule(code)
    response = client.get("/api/footprints", headers=auth_header())
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
```

覆盖 POST 201、PATCH 200、DELETE 204。

- [ ] **Step 2: 运行 API 测试，确认路由不存在**

Run: `python -m pytest tests/integration/test_footprints_api.py -q`

- [ ] **Step 3: 实现路由**

```python
@router.get("/api/footprints", response_model=list[FootprintView])
def list_footprints(user: CurrentUser, module: FootprintModule = Depends(get_footprint_module)):
    return module.list(user.id)

@router.post("/api/footprints", response_model=FootprintView, status_code=201)
def add_footprint(request: FootprintCreate, user: CurrentUser, module=Depends(get_footprint_module)):
    return module.add(user.id, request)
```

PATCH/DELETE 使用相同依赖；错误只返回稳定 code 和通用 message。

- [ ] **Step 4: 写开发/生产组装失败测试**

生产测试断言 `create_user_scoped_footprint_repository` 收到 verified JWT；开发测试断言共享内存仓库但服务按不同用户 UUID 隔离。

- [ ] **Step 5: 实现组装并注册路由**

开发使用缓存的 `InMemoryFootprintRepository`。生产强制 `CurrentUser.access_token`，使用用户令牌绑定 Supabase 仓库。在 `app/main.py` 注册 `footprints_router`。此任务先使用探索试点城市 `StaticCityDirectory`。

- [ ] **Step 6: 运行 Task 3 测试**

Run: `python -m pytest tests/integration/test_footprints_api.py tests/unit/test_footprint_production_wiring.py -q`
Expected: PASS。

- [ ] **Step 7: 精确提交**

```powershell
git add -- app/api/footprints.py app/composition.py app/main.py tests/integration/test_footprints_api.py tests/unit/test_footprint_production_wiring.py
git commit -m "feat: expose private footprint api"
```

---

### Task 4: 实现高德城市目录与边界 Provider

**Files:**
- Create: `app/providers/amap_district.py`
- Create: `tests/unit/test_amap_district.py`
- Modify: `app/core/config.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `AMAP_WEB_SERVICE_KEY` 和高德 `/v3/config/district`。
- Produces: `AmapDistrictProvider.search(query)`、`AmapDistrictProvider.boundary(adcode)`。

- [ ] **Step 1: 写配置边界失败测试**

```python
@pytest.mark.parametrize("field,value", [
    ("district_cache_seconds", 0),
    ("district_failure_cache_seconds", 0),
    ("district_timeout_seconds", 0),
    ("district_max_points", 0),
])
def test_district_limits_are_positive(field, value):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field: value})
```

默认值固定为 2592000、300、5.0、50000。

- [ ] **Step 2: 写 HTTP 参数、解析和密钥保密失败测试**

```python
def test_boundary_uses_server_key_and_all_extensions(httpx_mock):
    provider.boundary("350200")
    request = httpx_mock.get_request()
    assert request.url.params["keywords"] == "350200"
    assert request.url.params["subdistrict"] == "0"
    assert request.url.params["extensions"] == "all"
    assert request.url.params["key"] == "server-secret"

def test_polyline_becomes_closed_numeric_rings():
    boundary = provider.boundary("350200").data
    assert boundary.rings[0][0] == boundary.rings[0][-1]
```

覆盖空 district、坏 center、非数字 ring、超过 50000 点、HTTP 错误和高德非成功状态。直辖市 province 级结果规范为城市候选，其他普通省级结果过滤。

- [ ] **Step 3: 运行测试，确认失败**

Run: `python -m pytest tests/unit/test_amap_district.py tests/unit/test_config.py -q`

- [ ] **Step 4: 实现严格 Provider**

```python
AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"

class AmapDistrictProvider:
    def search(self, query: str) -> list[CityRecord]:
        return self._search_request(query)
    def boundary(self, adcode: str) -> ProviderResult[DistrictBoundary]:
        return self._boundary_request(adcode)
```

检查 HTTP 状态、`status == "1"`、`infocode == "10000"`。polyline 以 `|` 分 ring、`;` 分点、`,` 分经纬度并闭合。错误结果不得包含 Key 或原始响应。

- [ ] **Step 5: 运行 Task 4 测试**

Run: `python -m pytest tests/unit/test_amap_district.py tests/unit/test_config.py -q`
Expected: PASS。

- [ ] **Step 6: 精确提交**

```powershell
git add -- app/providers/amap_district.py app/core/config.py tests/unit/test_amap_district.py tests/unit/test_config.py
git commit -m "feat: add amap district provider"
```

---

### Task 5: 增加缓存服务、城市搜索和边界 API

**Files:**
- Create: `app/footprints/districts.py`
- Create: `tests/unit/test_district_service.py`
- Modify: `app/api/footprints.py`
- Modify: `app/composition.py`
- Modify: `tests/integration/test_footprints_api.py`
- Modify: `tests/unit/test_footprint_production_wiring.py`

**Interfaces:**
- Consumes: Task 4 Provider。
- Produces: `DistrictBoundaryService.search/resolve/get_boundary`、`GET /api/map/cities`、`GET /api/map/districts/{adcode}`。

- [ ] **Step 1: 写缓存、并发合并和 stale 降级失败测试**

```python
def test_fresh_cache_avoids_second_call(service, provider):
    service.get_boundary("350200")
    service.get_boundary("350200")
    assert provider.calls == ["350200"]

def test_concurrent_miss_is_single_flight(service, provider):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.get_boundary("350200"), range(4)))
    assert provider.calls == ["350200"]
    assert all(item.status == "fresh" for item in results)

def test_expired_success_becomes_stale_when_provider_fails(service, provider, clock):
    assert service.get_boundary("350200").status == "fresh"
    clock.advance(2592001)
    provider.fail = True
    assert service.get_boundary("350200").status == "stale"

def test_failure_cache_suppresses_retries_for_300_seconds(service, provider, clock):
    provider.fail = True
    assert service.get_boundary("350200").status == "unavailable"
    assert service.get_boundary("350200").status == "unavailable"
    assert provider.calls == ["350200"]
    clock.advance(301)
    service.get_boundary("350200")
    assert provider.calls == ["350200", "350200"]
```

- [ ] **Step 2: 运行服务测试，确认失败**

Run: `python -m pytest tests/unit/test_district_service.py -q`

- [ ] **Step 3: 实现有界缓存和组合目录**

`DistrictBoundaryService` 使用 Lock + 每键 Event 合并请求，缓存最大 256 项。试点城市先命中静态目录，再调用 Provider。没有高德 Key 时使用 `UnavailableDistrictBoundaryService`，试点城市 CRUD 仍可工作，边界返回 unavailable + center。

- [ ] **Step 4: 写城市搜索和边界 API 失败测试**

```python
def test_city_search_requires_auth_and_valid_query(client):
    assert client.get("/api/map/cities?q=厦门").status_code == 401
    assert client.get("/api/map/cities?q=x", headers=auth_header()).status_code == 422

def test_boundary_unavailable_never_leaks_secret(client):
    response = client.get("/api/map/districts/350200", headers=auth_header())
    assert response.json()["status"] == "unavailable"
    assert "server-secret" not in response.text
```

- [ ] **Step 5: 实现路由和组装**

搜索使用 `Query(min_length=2, max_length=40)`，边界路径校验六位数字。两个接口都声明 `user: CurrentUser`。`get_footprint_module` 与地图 API 复用同一个缓存的 District 服务作为 CityDirectory。

- [ ] **Step 6: 运行 Task 5 测试**

Run: `python -m pytest tests/unit/test_district_service.py tests/integration/test_footprints_api.py tests/unit/test_footprint_production_wiring.py -q`
Expected: PASS。

- [ ] **Step 7: 精确提交**

```powershell
git add -- app/footprints/districts.py app/api/footprints.py app/composition.py tests/unit/test_district_service.py tests/integration/test_footprints_api.py tests/unit/test_footprint_production_wiring.py
git commit -m "feat: serve cached city boundaries"
```

---

### Task 6: 将足迹地图升级为 Polygon 点亮适配器

**Files:**
- Modify: `app/static/map-explorer.js`
- Modify: `tests/frontend/map-explorer.test.js`

**Interfaces:**
- Consumes: `{ footprint, boundary }` 图层。
- Produces: `createFootprintMap(root, options).update(layers)`、`focus(cityAdcode)`、`destroy()`。

- [ ] **Step 1: 写 Polygon、Marker 降级和清理失败测试**

```javascript
test("saved city renders one polygon and fits bounds", withBrowser(async () => {
  const layers = [{
    footprint: { city_adcode: "350200", city_name: "厦门市" },
    boundary: { status: "fresh", rings: [[[118, 24], [119, 24], [118, 24]]], center: [118.1, 24.5] },
  }];
  const view = createFootprintMap(root, { amapKey: "key", securityJsCode: "code", layers });
  await immediate();
  assert.equal(polygons.length, 1);
  assert.deepEqual(polygons[0].options.path, layers[0].boundary.rings);
  assert.equal(maps[0].setFitViewCalls.length, 1);
});
```

另测 unavailable 使用一个中心 Marker、`focus` 不重建地图、`update` 移除旧图层、`destroy` 解绑全部 overlay 和事件。

- [ ] **Step 2: 运行地图测试，确认当前只有 Marker**

Run: `node --test tests/frontend/map-explorer.test.js`

- [ ] **Step 3: 实现生命周期**

```javascript
return {
  update(nextLayers) { /* 按 city_adcode diff，复用 map */ },
  focus(cityAdcode) { /* 聚焦匹配 overlay */ },
  destroy() { /* overlay.setMap(null)、取消 loader、destroy map */ },
};
```

默认 Polygon：`fillColor "#27b8aa"`、`fillOpacity 0.38`、`strokeColor "#087f76"`、`strokeWeight 2`；hover/focus 提升到 0.62。单城市 Polygon 失败只降级该城市 Marker。

- [ ] **Step 4: 运行地图测试**

Run: `node --test tests/frontend/map-explorer.test.js`
Expected: PASS。

- [ ] **Step 5: 精确提交**

```powershell
git add -- app/static/map-explorer.js tests/frontend/map-explorer.test.js
git commit -m "feat: light city boundaries on footprint map"
```

---

### Task 7: 建立云端足迹控制器与完整页面交互

**Files:**
- Create: `app/static/footprints.js`
- Create: `tests/frontend/footprints.test.js`
- Modify: `app/static/index.html`
- Modify: `app/static/styles.css`
- Modify: `app/static/app.js`
- Modify: `tests/frontend/dom-harness.js`
- Modify: `tests/frontend/app.test.js`
- Modify: `tests/integration/test_frontend_assets.py`

**Interfaces:**
- Consumes: Tasks 3/5 API、Task 6 地图、`app.js` 注入的 `requestJson`。
- Produces: `TravelFootprints.createController(options)`，公开 `setIdentity`、`mount`、`unmount`、`addCity`、`isSaved`。

- [ ] **Step 1: 写加载代次和生命周期失败测试**

```javascript
test("signed-in mount loads cloud footprints once", async () => {
  controller.setIdentity("user-a");
  await controller.mount();
  await controller.mount();
  assert.equal(requests.filter((item) => item.path === "/api/footprints").length, 1);
});

test("account switch clears A and ignores A late response", async () => {
  const userA = deferred();
  request.when("/api/footprints", userA.promise);
  controller.setIdentity("user-a");
  const staleMount = controller.mount();
  controller.setIdentity("user-b");
  request.respond("/api/footprints", []);
  await controller.mount();
  userA.resolve([XIAMEN]);
  await staleMount;
  assert.doesNotMatch(elements.list.textContent, /厦门/);
});

test("unmount aborts boundary loads and destroys one map", async () => {
  controller.setIdentity("user-a");
  await controller.mount();
  controller.unmount();
  assert.equal(map.destroyCalls, 1);
  assert.equal(boundarySignal.aborted, true);
});

test("boundary request concurrency never exceeds three", async () => {
  request.trackConcurrency("/api/map/districts/");
  controller.setIdentity("user-a");
  await controller.mountWith(FOUR_CITIES);
  assert.equal(request.maxConcurrency, 3);
});
```

- [ ] **Step 2: 写搜索、添加、修改、定位和删除失败测试**

```javascript
test("canonical city result posts adcode and visit date", async () => {
  elements.search.value = "厦门";
  await elements.searchForm.dispatch("submit");
  await findByText(elements.results, "厦门市").dispatch("click");
  elements.visitDate.value = "2026-08-28";
  await elements.addForm.dispatch("submit");
  assert.deepEqual(lastRequest().body, { city_adcode: "350200", visited_at: "2026-08-28" });
});
```

另测 PATCH 后排序、定位调用 `map.focus("350200")`、DELETE 乐观更新失败时恢复列表和 Polygon。

- [ ] **Step 3: 运行新前端测试，确认失败**

Run: `node --test tests/frontend/footprints.test.js tests/frontend/app.test.js`

- [ ] **Step 4: 增加页面结构和样式**

增加 `footprint-city-search-form`、搜索结果 `aria-live`、到访日期 dialog、`footprint-latest-city`。列表按钮固定“定位”“修改日期”“移除”；窄屏单列且按钮不换行。

- [ ] **Step 5: 实现控制器**

```javascript
const controller = TravelFootprints.createController({
  elements,
  request: requestJson,
  createMap: TravelMapExplorer.createFootprintMap,
  localStorage: window.localStorage,
  resolveLegacyEntry,
  today: () => new Date().toISOString().slice(0, 10),
  onAuthRequired: () => navigateToAuth("signin"),
  onStatus: setStatus,
});
```

构造时只绑定一次事件；`mount` 增加载代次、加载云端列表并创建地图；`unmount` abort 请求并销毁地图。边界由三个 worker 增量加载。

- [ ] **Step 6: 替换 app.js 本地事实来源**

移除 `readFootprints/saveFootprints/toggleFootprint` 的 Local Storage CRUD。进入足迹页调用 `mount`，离开调用 `unmount`；登录、退出、账户切换调用 `setIdentity`。所有请求复用现有 401 刷新一次逻辑。

- [ ] **Step 7: 加载资源并扩展 Harness**

在 `app.js` 前加载 `footprints.js`。Harness 先执行该脚本，并为 dialog、日期 input、事件委托增加最小 DOM 行为。静态资源测试断言脚本可访问且不包含服务端 Key。

- [ ] **Step 8: 运行全前端测试**

Run: `node --test tests/frontend/*.test.js`
Expected: PASS。

- [ ] **Step 9: 精确提交**

```powershell
git add -- app/static/footprints.js app/static/index.html app/static/styles.css app/static/app.js tests/frontend/footprints.test.js tests/frontend/dom-harness.js tests/frontend/app.test.js tests/integration/test_frontend_assets.py
git commit -m "feat: sync footprint page with cloud data"
```

---

### Task 8: 接入探索页城市归并与 Local Storage 幂等迁移

**Files:**
- Modify: `app/static/data/explore-data.js`
- Modify: `app/static/footprints.js`
- Modify: `app/static/app.js`
- Modify: `tests/frontend/footprints.test.js`
- Modify: `tests/frontend/app.test.js`
- Modify: `tests/frontend/map-explorer.test.js`

**Interfaces:**
- Consumes: 现有试点城市和景点。
- Produces: 稳定 `cityAdcode/provinceAdcode`；迁移键 `voyage:footprints-cloud-migration:v1:<stableUserId>`。

- [ ] **Step 1: 写 adcode 完整性和景点归并失败测试**

```javascript
test("every trial place resolves to its city adcode", () => {
  for (const city of EXPLORE_TRIAL.cities) {
    assert.match(city.adcode, /^\d{6}$/);
    for (const place of city.places) assert.equal(place.cityAdcode, city.adcode);
  }
});

test("two Xiamen places create one city footprint", async () => {
  await addExplorePlace("gulangyu");
  await addExplorePlace("nanputuo");
  assert.equal(postCalls("/api/footprints").length, 1);
});
```

固定城市映射：厦门 350200、福州 350100、大理州 532900、丽江 530700；福建 350000、云南 530000。

- [ ] **Step 2: 写迁移成功、失败重试和账户隔离失败测试**

```javascript
test("legacy places migrate once after first cloud load", async () => {
  storage.setItem("voyage:footprints:user-a", JSON.stringify([
    { id: "gulangyu", visitedAt: "2026-08-20T10:00:00Z" },
    { id: "nanputuo", visitedAt: "2026-08-21T10:00:00Z" },
  ]));
  await mountAs("user-a");
  assert.equal(postCalls().length, 1);
  assert.equal(postCalls()[0].body.city_adcode, "350200");
  assert.equal(storage.getItem("voyage:footprints-cloud-migration:v1:user-a"), "complete");
  assert.ok(storage.getItem("voyage:footprints:user-a"));
});
```

另测任一 POST 失败不写 marker、下次 mount 重试、账户 A marker 不影响 B、未知旧 ID 不上传自由文本。

- [ ] **Step 3: 运行迁移与探索测试，确认失败**

Run: `node --test tests/frontend/footprints.test.js tests/frontend/app.test.js tests/frontend/map-explorer.test.js`

- [ ] **Step 4: 增加规范城市字段并实现迁移**

迁移只在云端 GET 成功后运行；按 `cityAdcode` 去重。旧时间转中国日期，非法或未来日期使用当天。全部可识别条目成功后才写 marker；保留旧键一个版本周期。

- [ ] **Step 5: 接入探索入口**

城市和景点都打开到访日期 dialog，再调用：

```javascript
footprintsController.addCity({
  cityAdcode: city.adcode,
  suggestedVisitedAt: today,
});
```

按钮文案为“点亮这座城市”或“已点亮”；再次点击已点亮按钮不得直接删除。

- [ ] **Step 6: 运行全部前端测试**

Run: `node --test tests/frontend/*.test.js`
Expected: PASS，页面往返无重复地图、事件或请求。

- [ ] **Step 7: 精确提交**

```powershell
git add -- app/static/data/explore-data.js app/static/footprints.js app/static/app.js tests/frontend/footprints.test.js tests/frontend/app.test.js tests/frontend/map-explorer.test.js
git commit -m "feat: migrate and merge city footprints"
```

---

### Task 9: 全量验证、部署说明与 PR 准备

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Test: 足迹专项、全量 pytest、全部前端与静态脚本。

**Interfaces:**
- Consumes: Tasks 1–8。
- Produces: 可部署配置、迁移顺序和验收证据。

- [ ] **Step 1: 更新非密钥配置示例**

```dotenv
DISTRICT_CACHE_SECONDS=2592000
DISTRICT_FAILURE_CACHE_SECONDS=300
DISTRICT_TIMEOUT_SECONDS=5
DISTRICT_MAX_POINTS=50000
```

README 说明复用服务端 `AMAP_WEB_SERVICE_KEY`，先执行 `013_city_footprints.sql` 再部署；不写真实 Key。

- [ ] **Step 2: 运行足迹专项测试**

```powershell
python -m pytest tests/unit/test_footprint_models.py tests/unit/test_footprint_service.py tests/unit/test_footprint_repositories.py tests/unit/test_footprint_production_wiring.py tests/unit/test_amap_district.py tests/unit/test_district_service.py tests/integration/test_footprints_api.py tests/integration/test_city_footprints_sql_contract.py tests/integration/test_rls_contract.py -q
node --test tests/frontend/footprints.test.js tests/frontend/map-explorer.test.js tests/frontend/app.test.js
```

- [ ] **Step 3: 运行全量验证**

```powershell
python -m pytest
node --test tests/frontend/*.test.js
Get-ChildItem app/static -Filter *.js | ForEach-Object { node --check $_.FullName }
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
git diff --check HEAD
```

Expected: 全部 PASS。

- [ ] **Step 4: 浏览器验收**

1. 未登录不请求私人足迹。
2. 账户 A 添加厦门后显示厦门 Polygon，刷新和换浏览器仍存在。
3. 账户 B 看不到 A 的足迹。
4. 添加福州后城市数 2、省份数 1。
5. 修改日期后排序和最近到访同步。
6. 边界接口失败时列表仍可修改删除，地图降级 Marker/静态视图。
7. 退出登录后列表、统计、Polygon 和 Marker 立即清空。
8. 前进后退和页面切换无重复实例或请求。

- [ ] **Step 5: 精确提交部署文档**

```powershell
git add -- README.md .env.example
git commit -m "docs: document city footprint deployment"
```

不得暂存既有 `CONTEXT.md`、工作日志、其他计划、旅行笔记或任何 `.env`。

- [ ] **Step 6: 审查与 PR**

使用 `superpowers:requesting-code-review` 对照规格和计划审查。修复高优先级问题并重跑 Step 2–3 后，推送 `codex/city-footprints`，创建目标为 `main` 的 PR。
