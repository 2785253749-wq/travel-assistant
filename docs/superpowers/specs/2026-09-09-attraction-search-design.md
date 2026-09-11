# Attraction Search / Nearby Attractions Design

## 1. Status and decision

This document freezes the Stage 1 architecture for the Attraction Search / Nearby Attractions MVP.

The module supports two modes:

- city attraction search;
- nearby attraction search.

The module is independent from Hotel, RAG V2, Train, Weather, and Planner. It reuses established application patterns, but it does not reuse Hotel domain types, Hotel providers, or Hotel service methods.

The implementation is intentionally bounded for a dissertation-scale project. A generic POI abstraction is explicitly deferred until both Attraction and Restaurant modules exist and their actual common behavior can be compared.

## 2. Product scope

### 2.1 Supported MVP expressions

City search:

- `厦门有哪些景点`
- `厦门有什么好玩的景点`

Nearby search:

- `厦门大学附近有什么景点`
- `厦门鼓浪屿附近景点推荐`
- `厦门大学附近评分最高的景点`
- `厦门大学附近最近的景点`

### 2.2 Returned information

The user-facing attraction result may contain:

- attraction name;
- address;
- rating, when supplied by the provider;
- comment count, when supplied by the provider;
- distance for nearby results, when supplied by the provider;
- tags or attraction type, when supplied by the provider.

Missing optional values are omitted. The system must not fabricate zero ratings, zero comment counts, prices, or other fallback values.

### 2.3 Supported sorting

Only these values are supported:

- default: `None`;
- rating descending: `rating`;
- distance ascending: `distance`.

There is no attraction price sorting.

### 2.4 Explicit non-goals

This module does not include:

- live ticket prices;
- ticket reservation or purchase;
- images;
- OTA links or booking;
- a new database or new RAG data;
- deep Planner integration;
- complex recommendation algorithms;
- a nationwide static attraction dataset;
- a generic POI framework;
- a Restaurant module;
- local application-side ranking intended to compensate for an unverified provider sort.

## 3. Existing architecture and boundaries

The current Hotel implementation establishes the reusable layering pattern:

```text
domain models
  -> provider protocol
  -> concrete provider
  -> domain service
  -> application orchestration
  -> reply renderer
  -> SafeTravelAgent branch
  -> composition root
```

The Attraction module follows the same shape with independent types.

The current `app/locations` package remains the location-resolution boundary:

- `LocationService.search()` delegates location search;
- `LocationService.resolve()` returns one `ResolvedLocation`;
- zero results become `LOCATION_NOT_FOUND`;
- non-unique results become `LOCATION_AMBIGUOUS`;
- an exact or unique result becomes `ResolvedLocation`.

The existing `app/providers/places.py` Photon provider is not the Attraction Search provider. It returns only the legacy `Place` shape and is used by existing evidence behavior. It must remain unchanged.

RAG V2 remains the knowledge-answering path. Attraction Search returns a list of live POIs and never falls back to RAG V2 when the attraction provider is unavailable.

## 4. Recommended directory structure

New files:

```text
app/attractions/
    __init__.py
    models.py
    provider.py
    service.py

app/providers/
    baidu_attraction.py

app/agent/
    attraction_search_query.py

app/application/
    attraction_search.py
    attraction_search_reply.py
```

The following existing files are the minimum expected integration points:

- `app/agent/intent.py`;
- `app/agent/graph.py`;
- `app/application/chat.py`;
- `app/api/chat.py`;
- `app/composition.py`.

Hotel is not refactored as part of this work.

## 5. Domain contract

### 5.1 Types

The Attraction domain owns these types:

```text
AttractionSortBy = Literal["rating", "distance"]
AttractionSearchMode = Literal["city", "nearby"]
```

There is no `price` value in `AttractionSortBy`.

### 5.2 Requests

City and nearby requests are separate models. A universal request with optional coordinate fields is not allowed.

`AttractionSearchRequest` contains the city-search contract, including:

- city;
- keyword, fixed by the application to an attraction-search term;
- page;
- page size;
- `sort_by: Literal["rating"] | None`; city search permits default order or rating order only.

`AttractionNearbySearchRequest` contains the nearby-search contract, including:

- latitude;
- longitude;
- radius;
- keyword;
- page;
- page size;
- `sort_by: AttractionSortBy | None`; nearby search permits default, rating, or distance order.

This mode-specific contract is intentional. The shared `AttractionSortBy` union remains useful for extraction and pending state, but a city request cannot contain `distance`.

The models validate their own bounded values. The radius contract is:

- default: 2000 meters;
- minimum: 500 meters;
- maximum: 20000 meters.

### 5.3 AttractionSummary

`AttractionSummary` is independent from `HotelSummary` and contains exactly the minimum live-listing surface:

- `id: str | None`;
- `name: str`;
- `address: str | None`;
- `latitude: float | None`;
- `longitude: float | None`;
- `rating: float | None`;
- `comment_num: int | None`;
- `distance: int | None`;
- `tags: tuple[str, ...]`;
- `provider: str`.

`distance` is frozen as `int | None`, not `float`.

The model does not contain ticket price, booking data, image URLs, raw provider fields, or internal provider response bodies.

### 5.4 Search result

`AttractionSearchResult` follows the bounded result pattern already used by Hotel:

- `items: list[AttractionSummary]` in provider order;
- `total: int | None`; a missing third-party total does not invalidate valid `items`;
- `page: int`;
- `page_size: int`;
- `provider: str`;
- `status: Literal["success", "unavailable"]`;
- `warning: str | None` containing only a safe application code;
- `fetched_at: datetime`.

Provider order is preserved throughout the service, application, agent, and renderer layers.

## 6. Query extraction contract

`AttractionSearchQueryExtraction` contains:

- `mode: "city" | "nearby" | None`;
- `city: str | None`;
- `location_query: str | None`;
- `radius: int | None`;
- `sort_by: "rating" | "distance" | None`;
- `invalid_fields: tuple[str, ...]`;
- `missing_fields: tuple[str, ...]`.

The extractor is deterministic and provider-independent.

Mode-specific sorting is frozen as follows:

| mode | valid `sort_by` values |
| --- | --- |
| `city` | `None`, `rating` |
| `nearby` | `None`, `rating`, `distance` |

If a city query contains a distance preference, the extractor records `sort_by` as an invalid field and the application must not construct or send a city request carrying `distance`. It returns deterministic clarification instead. For example, `厦门最近的景点` must not produce a valid `city + distance` request.

### 6.1 Frozen examples

```text
厦门有哪些景点
mode=city
city=厦门

厦门有什么好玩的景点
mode=city
city=厦门

厦门大学附近有什么景点
mode=nearby
city=厦门
location_query=厦门大学

厦门大学附近3公里评分最高的景点
mode=nearby
city=厦门
location_query=厦门大学
radius=3000
sort_by=rating

厦门大学附近最近的景点
mode=nearby
sort_by=distance

厦门评分最高的景点
mode=city
city=厦门
sort_by=rating

厦门最近的景点
mode=city
sort_by=distance is invalid
no valid city request is constructed
```

Sorting terms must not remain in `location_query`. For example:

```text
厦门大学附近评分最高的景点
location_query=厦门大学
sort_by=rating
```

### 6.2 Missing-city policy

The extractor must not guess a city, maintain a location-to-city alias table, or ask RAG V2 to infer a city.

For:

```text
鼓浪屿附近有什么景点
```

the frozen extraction is:

```text
mode=nearby
location_query=鼓浪屿
city=None
missing_fields=("city",)
```

The application must not call `LocationService` when required extraction fields are missing. It returns deterministic clarification through the normal collection path. There is no `ATTRACTION_CITY_REQUIRED` provider exception.

The first version treats:

```text
厦门附近有什么景点
```

as city search:

```text
mode=city
city=厦门
```

Missing location and invalid radius are likewise represented through `missing_fields` and `invalid_fields`, not provider exceptions.

## 7. Intent and RAG V2 boundary

The only new intent is:

```text
attraction_search
```

Do not split it into `attraction_city` and `attraction_nearby`. The extraction mode provides that distinction after routing.

The deterministic routing priority is:

1. existing-trip explain or modify;
2. explicit complete trip planning;
3. train;
4. planner context;
5. weather;
6. hotel nearby;
7. attraction search;
8. travel knowledge;
9. existing remaining branches.

Attraction Search must be evaluated before generic `travel_knowledge` matching.

### 7.1 Search examples

These route to `attraction_search`:

- `厦门有哪些景点`;
- `厦门有什么好玩的景点`;
- `厦门大学附近有什么景点`;
- `厦门大学附近评分最高的景点`;
- `厦门大学附近最近的景点`.

### 7.2 Knowledge examples

These remain `travel_knowledge` and therefore continue through RAG V2 first:

- `鼓浪屿有什么特点`;
- `鼓浪屿有哪些值得了解的特点`;
- `鼓浪屿怎么去`;
- `介绍一下鼓浪屿`.

The classifier must distinguish list/search intent from knowledge/advice intent. It must not route every message containing `景点` to Attraction Search.

The following deliberately remain conservative in the first version:

- `厦门有什么值得去的地方` remains `travel_knowledge`;
- `厦门大学附近有什么好玩的` is not forced into Attraction Search without an explicit attraction-list signal.

The model intent prompt must add `attraction_search` and narrow the `travel_knowledge` description so the two routes have the same boundary as the deterministic classifier.

An Attraction Provider failure must not fall back to RAG V2.

## 8. Application architecture

There is one `AttractionSearchApplication` with two explicit entry points:

- `search_city()`;
- `search_nearby()`.

Do not expose a single `search(mode=...)` method. Separate methods make the city/location-resolution boundary explicit and prevent invalid request combinations.

### 8.1 City flow

```text
/api/chat
  -> SafeTravelAgent.collect()
  -> attraction_search
  -> AttractionSearchQueryExtractor
  -> AttractionSearchApplication.search_city()
  -> AttractionService.search_city()
  -> BaiduAttractionProvider
  -> /place/v3/region
  -> AttractionReplyRenderer
```

City search does not call `LocationService.resolve()`.

### 8.2 Nearby flow

```text
/api/chat
  -> SafeTravelAgent.collect()
  -> attraction_search
  -> AttractionSearchQueryExtractor
  -> AttractionSearchApplication.search_nearby()
  -> LocationService.resolve(...)
  -> ResolvedLocation
  -> AttractionService.search_nearby()
  -> BaiduAttractionProvider
  -> /place/v3/around
  -> AttractionReplyRenderer
```

The application resolves the nearby reference point and forwards coordinates. It validates the mode-specific request contract, but does not reinterpret the meaning of a valid `sort_by` and does not sort results.

### 8.3 Service and provider sharing

One `AttractionService` exposes `search_city()` and `search_nearby()` and delegates to one `AttractionProvider` implementation. The provider dispatches to the region or around endpoint based on the request type.

The service and provider own Attraction request/result types only. They do not accept `HotelSearchRequest`, `HotelNearbySearchRequest`, `HotelSummary`, or Hotel filter values.

## 9. Location ambiguity and pending state

Nearby search reuses the existing `LocationService` algorithm without rewriting it:

- zero results -> `LOCATION_NOT_FOUND`;
- multiple non-unique candidates -> `LOCATION_AMBIGUOUS`;
- unique or exact candidate -> `ResolvedLocation`.

Add an independent:

```text
PendingAttractionNearbySelection
```

with exactly:

- `city: str`;
- `radius: int`;
- `candidate_names: tuple[str, ...]`;
- `sort_by: AttractionSortBy | None`.

Do not create `PendingPOISelection` yet. Hotel and Attraction pending states must remain independent.

### 9.1 Two-turn flow

First request:

```text
厦门鼓浪屿附近评分最高的景点
```

On `LOCATION_AMBIGUOUS`, save:

```text
city=厦门
radius=2000
sort_by=rating
candidate_names=(...)
```

Second request:

```text
鼓浪屿风景名胜区
```

The chat application checks the attraction pending store before normal intent routing. If the message matches a candidate, it reconstructs the nearby extraction with the selected location and restores city, radius, and sort preference.

The selection must not be routed through RAG V2 or reclassified as generic travel knowledge.

`ConfirmationStore` gains a separate attraction pending collection and methods, parallel to the existing Hotel-specific collection. It must not serialize attraction state into the travel profile and must not overwrite Hotel pending state.

## 10. Baidu provider contract

Add `BaiduAttractionProvider` with one provider surface for both request types:

- `AttractionSearchRequest` -> `/place/v3/region`;
- `AttractionNearbySearchRequest` -> `/place/v3/around`.

Required safe request behavior:

- `scope=2`;
- `ret_coordtype=gcj02ll`;
- `output=json`;
- `trust_env=False` on the HTTP client;
- no `verify=False`;
- no debug printing;
- API key is never printed;
- user input is never inserted directly into the Baidu `filter` string.

The provider, service, application, and renderer do not sort locally. They preserve the order returned by Baidu.

### 10.1 Controlled attraction filter

The provider must use a fixed, controlled attraction-category filter so non-attraction POIs are not treated as attractions. The exact Baidu category-filter literal is intentionally not frozen in this design document because it has not yet passed the required real API diagnostic.

The implementation must keep the value provider-owned and constant. The literal is frozen only after the real API gate verifies that it returns tourism-attraction POIs and does not rely on user input.

This is a validation gate, not permission to add a user-configurable filter or a general category system.

### 10.2 Sorting-field validation gate

The provider must map `rating` and `distance` to controlled Baidu filter values, but the actual Baidu field names must not be assumed from Hotel behavior or from display-field names.

Before production use, a real API diagnostic must compare the first ten raw results for:

- default order;
- rating order;
- distance order.

The gate must verify:

- response status is zero;
- returned POIs are attractions;
- rating output is actually non-increasing for the rating request, when values are present;
- distance output is actually non-decreasing for the distance request;
- missing rating placement is recorded;
- the sorted response differs from default when the API claims a different sort.

The verified filter field must then be covered by a deterministic provider contract test. If the live API does not honor the requested order, the implementation returns to a separate RED -> GREEN correction cycle. Application-side sorting must not silently mask the provider defect.

### 10.3 Parsing

The provider parses only the approved public fields:

- name;
- address;
- coordinates;
- display rating;
- comment count;
- nearby distance;
- provider-documented attraction tags/type.

Missing or malformed optional numeric values become `None`, not zero. Unknown raw fields are not exposed.

## 11. Renderer contract

Add `AttractionReplyRenderer` with a maximum of three displayed attractions.

City response shape:

```text
在“厦门”找到 X 个景点，先为你展示前 3 个：

1. xxx，评分：4.9，评论数：123
   地址：xxx
```

Nearby response shape:

```text
“厦门大学”附近 2 公里内找到 X 个景点，先为你展示前 3 个：

1. xxx，评分：4.8，评论数：123，距离：420 米
   地址：xxx
```

Formatting rules:

- optional fields are shown only when present;
- the displayed result count uses `total` when it is not `None`, otherwise `len(items)`;
- do not print `评分：暂无`;
- do not print `评论数：暂无`;
- distance is rendered as meters up to 1000 meters and as kilometers beyond that;
- city results do not show distance;
- nearby results show distance when present;
- name, address, and provider order remain compatible with the result model;
- no price, ticket, booking, image, internal ID, or raw Baidu field is printed;
- the renderer does not sort.

## 12. Error handling

### 12.1 Query clarification

Missing city, missing location, and invalid radius are normal extraction outcomes:

- represent them in `missing_fields` or `invalid_fields`;
- return deterministic clarification;
- do not convert them into provider exceptions;
- do not define `ATTRACTION_CITY_REQUIRED`.

### 12.2 Location errors

Reuse:

- `LOCATION_NOT_FOUND`;
- `LOCATION_AMBIGUOUS`.

Candidate names and ordering are safe user-facing data. Internal provider response bodies are not exposed.

### 12.3 Provider errors

The provider may normalize stable failures to:

- `BAIDU_ATTRACTION_NOT_CONFIGURED`;
- `BAIDU_ATTRACTION_INVALID_REQUEST`;
- `BAIDU_ATTRACTION_TIMEOUT`;
- `BAIDU_ATTRACTION_NETWORK_ERROR`;
- `BAIDU_ATTRACTION_HTTP_ERROR`;
- `BAIDU_ATTRACTION_INVALID_RESPONSE`;
- `BAIDU_ATTRACTION_PROVIDER_ERROR`.

The user receives safe unavailable text. The system never exposes:

- API keys;
- raw HTTP bodies;
- provider exception bodies;
- full URLs containing credentials.

An unavailable Attraction provider must not prevent chat composition or block RAG V2, Train, Weather, or Hotel.

## 13. Composition and graph integration

The composition root adds independent Attraction construction and injects the resulting application, extractor, and renderer into `SafeTravelAgent`.

If the Baidu attraction configuration is absent, composition must inject an unavailable attraction dependency or `None` according to the existing optional-provider convention. It must not make non-attraction chat initialization fail.

`SafeTravelAgent` adds only the attraction branch and pending state needed by this feature. It does not alter:

- RAG V2 answer generation or fallback behavior;
- Train routing;
- Weather routing;
- Planner behavior;
- Hotel routing or Hotel pending state.

The public `ChatResponse` schema remains unchanged. Attraction results are represented in the existing reply and normal chat result fields. Attraction POI fields must not be converted into RAG `SourceCitation` objects merely because the chat API supports citations for RAG answers.

`app/api/chat.py` must recognize `attraction_search` anywhere it validates or falls back to known intents. No new endpoint is required.

## 14. Test matrix

The implementation must eventually cover the following without creating tests during the design stage.

### 14.1 Domain

- city and nearby request validation;
- independent request types;
- `AttractionSortBy` accepts only `rating` and `distance`;
- radius default, minimum, and maximum;
- optional summary fields remain `None` when absent;
- distance remains an integer.
- `AttractionSearchResult.total` may be `None` without invalidating `items`;
- city requests accept only `None` or `rating`;
- nearby requests accept `None`, `rating`, or `distance`;
- a city distance query does not construct a valid city request.

### 14.2 Query extraction

- all six frozen MVP expressions;
- city/nearby mode;
- rating and distance extraction;
- city rating is valid;
- nearby rating is valid;
- nearby distance is valid;
- city distance is rejected before request construction;
- ordinary search with `sort_by=None`;
- sorting words do not contaminate `location_query`;
- radius parsing;
- missing city/location and invalid radius represented structurally.

### 14.3 Intent and RAG regression

Lock these routing assertions:

- `厦门有哪些景点` -> `attraction_search`;
- `厦门大学附近有什么景点` -> `attraction_search`;
- `鼓浪屿有什么特点` -> `travel_knowledge`;
- `鼓浪屿有哪些值得了解的特点` -> `travel_knowledge`;
- `鼓浪屿怎么去` -> `travel_knowledge`.

Also preserve existing Train, Weather, Hotel, Planner, smalltalk, and unsupported routing tests.

### 14.4 Provider contract

- region endpoint and parameters;
- around endpoint and parameters;
- controlled attraction filter;
- controlled rating and distance filters;
- detail-field parsing;
- missing/invalid optional fields;
- no local reordering;
- `trust_env=False`;
- no `verify=False`;
- no secret or raw-response output.

The exact category and ranking literals must be asserted only after the real API gate freezes them.

### 14.5 Application

- city search does not call `LocationService`;
- nearby search resolves through `LocationService`;
- resolved coordinates are forwarded unchanged;
- `sort_by` is forwarded unchanged;
- location errors propagate safely;
- empty result remains a normal business response.

### 14.6 Renderer

- city heading and first three results;
- nearby heading, radius, and first three results;
- optional rating/comment/distance/tags;
- no fabricated missing values;
- distance formatting;
- no restricted fields.

### 14.7 Agent and chat

- Attraction Search does not call RAG V2;
- RAG knowledge queries still call RAG V2 first;
- Hotel, Train, Weather, and Planner branches remain isolated;
- Attraction missing fields do not start profile collection;
- Attraction provider failure does not call RAG fallback.

### 14.8 Location ambiguity and state isolation

The HTTP integration test must cover:

1. first request returns `LOCATION_AMBIGUOUS`;
2. pending state includes `sort_by="rating"`;
3. second candidate-selection request continues Attraction nearby;
4. second request does not enter RAG V2;
5. `PendingAttractionNearbySelection` and `PendingHotelNearbySelection` do not overwrite each other.

### 14.9 Composition and public chat

- composition wires the attraction chain;
- missing Baidu configuration does not block chat composition;
- unavailable Attraction service produces safe unavailable behavior;
- public chat response remains within the existing schema;
- no Attraction fields leak as private IDs or RAG citations.

### 14.10 Regression and live gates

The eventual verification sequence includes:

- focused Attraction regression;
- full regression;
- real Baidu diagnostic;
- local real `/api/chat` E2E;
- online smoke;
- cross-region E2E for two or three cities such as Beijing, Hangzhou, and Chengdu.

## 15. Implementation stages

The approved implementation sequence is:

1. Design spec.
2. Domain Models TDD.
3. Query Extraction and Intent TDD.
4. Provider Contract TDD with mocked HTTP.
5. Service and Application TDD.
6. Renderer TDD.
7. Agent and City branch TDD.
8. Nearby and `LOCATION_AMBIGUOUS` TDD.
9. Composition Root TDD.
10. Focused regression.
11. Full regression.
12. Real Baidu diagnostic.
13. If real API behavior differs, a separate RED -> GREEN correction cycle.
14. Local real `/api/chat` E2E.
15. Push, PR, and review.
16. Online smoke.
17. Cross-region E2E.

Production implementation must not begin before this spec is reviewed. The real API gate must pass before production provider filter and sort literals are treated as frozen.

## 16. Compatibility and non-regression requirements

The change must preserve existing behavior for:

- RAG V2 knowledge routing and evidence handling;
- Train queries and train result rendering;
- Weather queries;
- Planner collection, confirmation, and itinerary generation;
- Hotel nearby queries, sorting, and pending location selection;
- existing `LOCATION_AMBIGUOUS` behavior for Hotel;
- public chat request and response schemas;
- existing safety, rate-limit, logging, and session behavior.

No existing module receives Attraction-specific fields. No Hotel model is widened for Attraction data.

## 17. Design self-review checklist

The following consistency checks pass for this specification:

1. No unresolved or incomplete implementation text is required.
2. `distance` is consistently `int | None`.
3. `total` is consistently `int | None`, and renderer fallback to `len(items)` is explicit.
4. City requests allow only `None` or `rating`; nearby requests allow `None`, `rating`, or `distance`.
5. City distance queries cannot construct valid city requests.
6. Missing city is represented by extraction fields and clarification, not `ATTRACTION_CITY_REQUIRED`.
7. No location-to-city hard-coded alias is introduced.
8. No unverified Baidu attraction filter literal is frozen.
9. No unverified Baidu rating sort field is assumed.
10. Attraction Provider failure never falls back to RAG V2.
11. City Search does not call `LocationService`.
12. No generic POI abstraction is introduced.
13. `sort_by` is explicitly saved and restored in nearby pending state.
14. Hotel and Attraction pending state are explicitly isolated.
15. Non-goals are explicit.
16. The component boundaries, data flows, error handling, tests, and implementation stages are specified for a subsequent implementation plan.
