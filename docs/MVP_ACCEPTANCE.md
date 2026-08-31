# MVP Acceptance

审计日期：2026-08-31。本文记录 Stage 9B 在当前 worktree 的本地验收证据；没有把本地 TestClient 结果当作线上部署证据，也没有执行线上 Render 部署或真实外部 E2E。

## Scope

本轮范围是 Stage 9B MVP 重新验收：重新执行 Unit/Integration/E2E default/Full、启动与离线 Hotel Nearby HTTP smoke，并检查 Render Blueprint 与 secret 安全。Stage 9A 的测试隔离和 Render 配置变更保留；本轮没有修改 `app/*` 业务代码或新增功能。

## Verified Core Flow

离线 Unit/Integration 覆盖了：

```text
hotel_nearby intent
  -> HotelNearbyQueryExtractor
  -> SafeTravelAgent
  -> HotelNearbyApplication
  -> LocationService / BaiduLocationProvider
  -> HotelService / BaiduHotelProvider
  -> HotelNearbyReplyRenderer
  -> POST /api/chat
```

本轮启动 Smoke：`app.main:app` 可导入，TestClient startup 正常，`GET /health` 返回 `200` 和 `{"status":"ok"}`。`tests/integration/test_chat_hotel_nearby.py` 本轮 `5 passed`：fake application 场景确认“帮我找厦门大学附近的酒店”返回 `200`，以及“鼓浪屿附近酒店”返回城市澄清，未访问真实百度。

“帮我找厦门大学附近的酒店”的 Chat Hotel Nearby 真实链历史开发验证已报告为：HTTP 200、`hotel_nearby`、真实返回 10 家酒店、Baidu 请求 2 次（region/around）、无 detail、无 DeepSeek。本轮没有重复调用百度。

## Feature Matrix

| Feature | Implementation | Unit | Integration | Real E2E | Status |
|---|---|---:|---:|---|---|
| Hotel city search | Domain/provider/service/REST | yes | yes | E2E test exists; not rerun | PARTIAL |
| Hotel nearby search | Domain/provider/service/REST | yes | yes | E2E test exists; not rerun | PARTIAL |
| Hotel detail | Domain/provider/service/REST | yes | yes | E2E test exists; not rerun | PARTIAL |
| Location resolve | Domain/provider/service/composition | yes | yes | E2E test exists; not rerun | PARTIAL |
| Hotel Nearby Application | Location to hotel application | yes | yes | E2E test exists; not rerun | PARTIAL |
| Hotel Nearby Chat | Intent/extractor/agent/renderer/HTTP | yes | yes | Historical Stage 7D run reported | VERIFIED with deployment caveat |
| Train Chat | Provider/service/extractor/recommendation/agent | yes | partial | No Train E2E file | PARTIAL |
| Weather | AMap provider/service/chat/API | yes | yes | No Weather E2E file | PARTIAL |
| Knowledge/RAG | Local corpus, embedding/repository seams, guarded chat path | yes | yes | No real RAG E2E | PARTIAL |
| Trip planning | Agent/planner/repository/chat/API | yes | yes | No online deployment E2E | PARTIAL |

`PlacesProvider` remains travel evidence search and is not a Location Resolver.

## Test Baseline

Fresh commands in Stage 9B used the existing `.venv` and no `RUN_*_E2E=1` variables:

| Suite | Result |
|---|---|
| `pytest tests/unit -q` | 948 passed, 0 failed, 1 warning |
| `pytest tests/integration -q` | 279 passed, 0 failed, 1 warning |
| `pytest tests/e2e -q -rs` | 7 skipped, 1 warning |
| `pytest -q` | 1288 passed, 0 failed, 7 skipped, 1 warning |

Stage 9A 的测试隔离修复后，上述两个历史 baseline failure 也不再复现；没有修改它们的业务断言。测试域默认禁用项目 `.env`，需要 dotenv 的测试必须显式标记。当前 Full pytest 无失败，且默认 E2E 全部跳过，因此没有新的 regression。

The warning is `StarletteDeprecationWarning`: installed Starlette TestClient uses the deprecated httpx integration. It does not prevent startup, but should be handled as dependency maintenance.

## Real E2E Evidence

- Hotel: `tests/e2e/test_baidu_hotel_e2e.py` exists and is opt-in through `RUN_BAIDU_HOTEL_E2E`; this audit did not rerun it and no run artifact is stored here.
- Location: `tests/e2e/test_baidu_location_e2e.py` exists and is opt-in through `RUN_BAIDU_LOCATION_E2E`; this audit did not rerun it and no run artifact is stored here.
- HotelNearby Application: `tests/e2e/test_hotel_nearby_application_e2e.py` exists and is opt-in through `RUN_HOTEL_NEARBY_E2E`; this audit did not rerun it and no run artifact is stored here.
- HotelNearby Chat: `tests/e2e/test_chat_hotel_nearby_e2e.py` exists and is opt-in through `RUN_CHAT_HOTEL_NEARBY_E2E`; the prior Stage 7D report recorded one successful real run with HTTP 200 and two Baidu requests. It was not rerun here to avoid duplicate external calls.
- Train: no real Train E2E file was found; real Train E2E is not verified.

All seven E2E tests skipped by default in this audit. The guards require explicit feature-specific `RUN_*_E2E=1` and, for Baidu cases, a configured `BAIDU_MAP_AK`.

## Configuration

`app/core/config.py` and `.env.example` define separate server-side credentials for DeepSeek, Jina, AMap Web Service, Juhe Train, Baidu Map, Supabase and the anonymous-session signer. `BAIDU_MAP_AK` is shared by Hotel and Location; `HOTEL_TIMEOUT_SECONDS` defaults to 10 seconds. Production startup requires Supabase URL, anon key, service key and a valid session signing secret. DeepSeek, Juhe, Jina, AMap Web Service and Baidu credentials are feature-level dependencies rather than general startup requirements; AMap browser credentials are optional for the map fallback.

`.env.example` contains placeholders or blank declarations and the tracked-file public repository scanner passed. The local `.env` is ignored and not tracked; its non-empty credential variables were not printed or copied into this document.

Configuration gaps found:

- `README.md` does not document `BAIDU_MAP_AK` or `HOTEL_TIMEOUT_SECONDS`.
- `README.md` and deployment docs do not document the four feature-specific E2E opt-in variables.
- `.env.example` omits seven declared Settings fields: RAG embedding/threshold/daily limit, weather daily/cache/timeout settings, and `TRUSTED_CLIENT_IP_HEADER`.
- Render Blueprint now declares `BAIDU_MAP_AK` as `sync: false` and `HOTEL_TIMEOUT_SECONDS` as `10.0`; the deployment config test passes. `BAIDU_MAP_AK` still must be entered securely in Render Dashboard after Blueprint setup.

Render configuration categories:

- Core startup/deployment: `APP_ENV=production`, platform `PORT`, and production Supabase/session settings required by the current `Settings` validator.
- Hotel Nearby: `BAIDU_MAP_AK` and `HOTEL_TIMEOUT_SECONDS`.
- Optional feature-specific: DeepSeek, Juhe Train, Jina, AMap Web Service, and AMap browser credentials.
- Deployment/database-specific: Supabase URL, anon key, service key, and anonymous-session signing secret.

The anonymous Hotel Nearby Chat path does not itself use Supabase; Supabase remains a production Settings/startup requirement for the deployed application as a whole.

## Public API Inventory

The current FastAPI OpenAPI inventory contains:

| Method | Path | Status |
|---|---|---|
| GET | `/health` | active |
| POST | `/api/chat` | active |
| GET | `/api/hotels/search` | active |
| GET | `/api/hotels/nearby` | active |
| GET | `/api/hotels/{hotel_id}` | active |
| GET | `/api/weather/cities/{city_id}` | active |
| GET/POST | `/api/trips` | active |
| GET/DELETE/PATCH | `/api/trips/{trip_id}` | active |
| POST | `/api/trips/{trip_id}/copy` | active |
| POST/DELETE | `/api/trips/{trip_id}/share` | active |
| POST | `/api/shared/resolve` | active |
| GET/POST | `/api/footprints` | active |
| PATCH/DELETE | `/api/footprints/{footprint_id}` | active |
| GET | `/api/map/cities`, `/api/map/districts/{city_adcode}` | active |
| GET/POST | `/api/community/posts` | active |
| GET/DELETE | `/api/community/posts/{post_id}` | active |
| GET/POST | `/api/community/notes` | active |
| GET | `/api/community/creators/{creator_slug}`, `/api/me/travel-notes` | active |
| GET/PUT/DELETE | `/api/community/notes/{note_id}` | active |
| POST | `/api/community/notes/{note_id}/submit` | active |
| PUT/DELETE | `/api/community/notes/{note_id}/like`, `/api/community/notes/{note_id}/bookmark` | active |
| GET/POST | `/api/community/notes/{note_id}/comments` | active |
| POST | `/api/community/notes/{note_id}/reports` | active |
| GET | `/api/admin/community/review-queue` | active |
| POST | `/api/admin/community/reviews/{target_type}/{target_id}/{decision}`, `/api/admin/community/hide/{target_type}/{target_id}` | active |
| POST | `/api/admin/community/reports/{report_id}/resolve` | active |
| GET/PUT | `/api/profile` | active |

No Train REST endpoint is registered; Train is exposed through Chat.

## Known Non-Blocking Issues

- The two named historical baseline failures are no longer reproduced after test-only `.env` isolation; their assertions were not changed.
- The Starlette/httpx deprecation warning is dependency maintenance.
- README and `.env.example` omissions are documentation gaps.
- `app/graph.py::chat()` is a legacy compatibility facade. No production import of `app.graph` was found; current public Chat uses `POST /api/chat` through `TravelChatApplication` and `SafeTravelAgent`. Deletion is out of scope for this audit.
- Real Train, Weather and RAG E2E evidence is absent.

## Not Yet Real-E2E-Verified

Train, Weather, Knowledge/RAG, Trip planning and online Render/Supabase deployment are not verified by real external E2E in this audit. The existing release evidence remains `BLOCKED` because public URL, deploy ID, applied migration state and online smoke evidence are absent.

## Out of Current MVP Scope

Hotel detail Chat, city-wide hotel Chat, price/rating filters, booking/payment, new weather/places/route capabilities, Agent tool calling, LangGraph, new caching/database features and frontend feature expansion remain out of scope.

## Recommended Next Phase

1. Configure `BAIDU_MAP_AK` securely in Render Dashboard and perform the online `/health` and Hotel Nearby smoke.
2. Keep Train, Weather, RAG and Trip Planning real E2E verification as follow-up work.
3. After deployment evidence is available, choose Train real E2E or Hotel V2 as the next product phase.

## MVP Decision

**MVP ACCEPTED WITH KNOWN ISSUES** for the local MVP re-acceptance.

Reasons:

1. Local Unit, Integration and Full pytest are green; default E2E remains safely skipped.
2. Stage 7D provides prior real local Hotel Nearby Chat evidence, and Stage 9B offline HTTP/startup smoke passed.
3. Render Blueprint declares the required Hotel Nearby variables and contains no hard-coded Baidu AK.
4. Online Render deployment and online Hotel Nearby smoke were not executed because this environment has no Render CLI/API authorization; therefore production/online verification remains open.
5. Train, Weather, RAG and Trip Planning do not have real online E2E evidence.
