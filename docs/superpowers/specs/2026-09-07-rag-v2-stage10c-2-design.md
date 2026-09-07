# RAG V2 Stage 10C-2 Planner/Runtime Integration Design

## 1. Purpose and Goal

Stage 10C-2 connects the already-validated RAG V2 retrieval path to the real
runtime knowledge-request path. It adds V2-backed answers for the existing
`travel_knowledge` intent while preserving the current Train, Hotel, Weather,
normal Planner, and legacy RAG behavior.

Stage 10C-1 already proved the live RAG V2 corpus and retrieval boundary:

- production dataset: `rag-v2-production`;
- active corpus: `d30105e3-4244-5a34-90ae-125fe0d4c64c`;
- version: `stage10c1-production-v1`;
- all 10 required and 2 optional retrieval cases passed;
- post-activation smoke passed; and
- `RetrievalService` uses the approved default threshold `0.60`.

Stage 10C-1 deliberately did not modify the request runtime, Planner, API, or
frontend. This document defines the bounded Stage 10C-2 runtime design. It is
not an implementation plan and does not authorize code changes by itself.

## 2. Current Runtime Baseline

The public request path is:

```text
POST /api/chat
→ app.api.chat.api_chat()
→ app.composition.execute_chat_request()
→ build_chat_application()
→ TravelChatApplication.collect() or confirm()
→ SafeTravelAgent.collect() or plan_confirmed()
→ ChatResult
→ ChatResponse validation and JSON response
```

`api_chat()` performs a lightweight rule-based intent classification for safe
logging and delegates the actual request to the composition root. The
application has two relevant behavioral paths.

### Existing travel-knowledge path

```text
travel_knowledge
→ SafeTravelAgent._special_intent_result()
→ KnowledgeAnswerService.answer()
→ legacy KnowledgeRepository + legacy JinaEmbedder
→ RagAnswer
→ _rag_citations()
→ ChatResult.reply and ChatResult.sources
```

This path returns a direct answer and does not call the Planner. It resolves a
broad region such as `厦门`, `福建`, or `云南`; it does not currently resolve a
reliable six-digit destination code.

### Existing normal-planning path

```text
profile collection
→ confirmation
→ SafeTravelAgent.plan_confirmed()
→ ProviderEvidenceAggregator.fetch()
→ ModelStructuredPlanner.plan()
→ itinerary validation and enrichment
→ rendered itinerary response
```

The normal Planner receives provider evidence through its existing structured
planning boundary. This stage does not replace that path with RAG V2.

## 3. Architectural Decision

Stage 10C-2 selects router/intent-triggered integration, Approach B. RAG V2
runs only for existing `travel_knowledge` requests.

This approach is selected because the current runtime already has a stable
knowledge routing seam, and the V2 contract is already a query-to-evidence
service. It avoids adding a second agent framework and avoids changing the
normal itinerary workflow.

### Rejected global pre-Planner retrieval

Global retrieval would run for weather, train, hotel, smalltalk, unsupported,
and unrelated requests. It would add Jina and Supabase latency to requests
that do not need knowledge retrieval and would often lack destination metadata.
It is broader than the graduation-project requirement.

### Rejected Planner-tool integration

The current Planner is a structured model call with pre-supplied evidence. It
has no tool registry or tool-execution loop. Adding one would create a new
agent framework and a larger failure and validation surface. Stage 10C-2 keeps
V2 travel knowledge outside `ModelStructuredPlanner`.

## 4. Target Runtime Flow

```text
POST /api/chat
→ existing intent routing
→ travel_knowledge?
    → V2 knowledge adapter
        → RetrievalService
            → Jina retrieval.query embedding
            → active-only RAG V2 RPC
        → V2 evidence
        → deterministic grounded answer and citations
        → if empty or unavailable: existing legacy RAG fallback
→ non-knowledge paths unchanged
```

V2 uses dataset key `rag-v2-production`. It does not import, activate, mutate,
or query a staging corpus during a normal request.

## 5. Components and Responsibilities

### Composition root

Responsibility: construct the production V2 repository, query provider,
query embedder, retrieval service, and knowledge adapter using the existing
composition patterns.

Input: application settings and existing runtime dependencies.

Output: an injected V2 knowledge dependency for `SafeTravelAgent`.

Dependencies: `Settings`, `RagV2Repository`, `JinaQueryProvider`,
`JinaQueryEmbedder`, and `RetrievalService`.

Must not add a DI framework, global service locator, import/activation flow, or
production-grade cache subsystem.

### V2 knowledge adapter

Responsibility: accept a knowledge query and optional reliable destination
metadata, invoke the existing `RetrievalService`, and map its evidence into a
deterministic grounded knowledge response.

Input: query text and optional destination code/level/province/attraction
metadata.

Output: a grounded result containing answer text and preserved source metadata,
or an empty/unavailable result that permits legacy fallback.

Dependencies: only the existing `RetrievalService` and its result types.

Must not duplicate thresholding, deduplication, diversity, embedding, RPC, or
database logic. It must not generate a second free-form LLM answer.

### RetrievalService

Responsibility: own query embedding, active-only repository retrieval, score
thresholding, deduplication, diversity, final-k selection, and
`RetrievalEvidence` construction.

Input: query, production dataset key, and trusted optional filters.

Output: `RetrievalResult`.

Must not format public answers, invoke the Planner, or implement legacy
fallbacks.

### Legacy fallback

Responsibility: preserve the existing `KnowledgeAnswerService` behavior when
V2 returns no evidence or cannot safely retrieve.

Input: the original question and existing broad region value.

Output: the existing `RagAnswer` and legacy public citation mapping.

Must not be removed or silently bypassed during rollout.

### SafeTravelAgent routing seam

Responsibility: invoke the V2 adapter only from the existing
`travel_knowledge` branch and select the fallback result when necessary.

Must not route Train, Hotel, Weather, planning, smalltalk, or unsupported
requests through V2.

### Response and citation mapping

Responsibility: convert grounded V2 evidence into the existing `ChatResult`
and `SourceCitation`-compatible representation.

Must preserve real V2 `source_label` and `source_url`, validate the public
source type contract, and keep `attraction_id` and `chunk_key` internal.

## 6. V2 Adapter Contract

The adapter is a runtime-owned knowledge boundary, not a second retrieval
implementation. Conceptually it accepts:

- the user query;
- an optional reliable six-digit destination code;
- optional destination level, province code, or attraction identity when those
  values already exist in trusted runtime context.

For a successful result, it returns deterministic grounded content derived
only from the ordered `RetrievalEvidence` values. Each evidence item retains
its content, source label, source URL, source type, score, attraction identity,
and chunk identity internally.

For empty evidence, it returns an empty result that triggers legacy fallback.
For V2 unavailable conditions, it returns an unavailable result with a stable
internal error classification and triggers legacy fallback. Raw provider,
database, or exception text never crosses the runtime response boundary.

## 7. Destination Policy

Stage 10C-2 does not add a destination resolver.

- It does not call AMap solely to resolve a RAG destination code.
- It does not add a frozen hard-coded city-code mapping.
- It passes `destination_code` only when a reliable six-digit code is already
  available from existing runtime context.
- Otherwise it passes no destination filter (`None`).
- It never guesses an administrative code from free-form text.

Destination-less V2 retrieval is allowed because the active production corpus
and the evaluator already enforce the approved corpus boundary. An invalid or
unresolved filter must be omitted when safe; otherwise the adapter falls back
to legacy RAG.

## 8. Grounded Answer Policy

Stage 10C-2 uses deterministic evidence-only formatting for V2 knowledge
answers.

- No new LLM generation step is introduced.
- Every factual sentence in the V2 answer must be copied from returned evidence
  or omitted.
- Unsupported claims, live availability claims, prices, opening hours, and
  booking claims are not added.
- Real V2 `source_label` and `source_url` are preserved.
- `attraction_id` and `chunk_key` remain internal evidence identity and are not
  added to the public schema.
- The existing public citation structure is reused where its source-type and
  URL validation permits.

V2 must not fabricate a provenance URL when a real V2 source URL exists.

## 9. Fallback and Error Handling

For `travel_knowledge` requests:

| V2 result | Required behavior |
| --- | --- |
| Relevant evidence | Return the deterministic V2 grounded answer. |
| Empty evidence | Invoke the existing legacy RAG fallback. |
| Jina unavailable | Invoke the existing legacy RAG fallback. |
| Supabase/RPC unavailable | Invoke the existing legacy RAG fallback. |
| No active V2 corpus | Invoke the existing legacy RAG fallback. |
| Invalid or unresolved filter | Omit the untrusted filter when safe; otherwise use legacy fallback. |
| Malformed V2 result | Treat as unavailable and use legacy fallback. |

Failures must use existing safe operational logging patterns. Raw provider,
database, headers, URLs containing credentials, and exception bodies are never
returned to the user.

No retry, circuit breaker, rollback, activation, or corpus repair mechanism is
added by this stage.

## 10. Isolation Guarantees

The following remain unchanged:

- Train routing and Train API behavior;
- Hotel routing and Hotel API behavior;
- Weather routing and Weather API behavior;
- the normal profile, provider, and Planner path;
- legacy RAG behavior outside the V2 knowledge route; and
- non-`travel_knowledge` intent behavior.

Non-knowledge requests must not invoke V2 retrieval. Composition may naturally
construct the cached dependency as part of application construction, but no
V2 embedding or repository retrieval call may occur for those requests.

## 11. Provenance and Public Response

V2 `source_label` maps to the existing public `SourceCitation.source_label`.
V2 `source_url` maps to `SourceCitation.source_url` and must remain an HTTPS
URL accepted by the existing public validation.

The V2 `source_type` must map to one of the existing public source types or the
evidence must be treated as unavailable rather than weakening public
validation.

No public schema expansion is expected. `attraction_id` and `chunk_key` remain
internal and may be retained in internal evidence identity or safe logs only.

## 12. Dependency Construction

The existing composition root constructs the V2 dependencies alongside the
legacy knowledge dependency:

```text
Settings
→ RagV2Repository
→ JinaQueryProvider
→ JinaQueryEmbedder
→ RetrievalService
→ V2 knowledge adapter
→ SafeTravelAgent injection
```

Construction follows existing application factory and cache patterns. No new
DI framework, service locator, or production-grade cache subsystem is added.
The adapter uses the fixed production dataset key `rag-v2-production` and the
existing active-only RPC path.

## 13. Expected Production Files

The expected minimal implementation surface is:

- `app/rag_v2/knowledge.py` — new thin runtime adapter, if this is the clearly
  justified module-local owner;
- `app/composition.py` — construct and inject V2 runtime dependencies;
- `app/agent/graph.py` — add the V2 knowledge seam and route the existing
  `travel_knowledge` branch through V2-first fallback behavior;
- unit and integration test files for the adapter, routing, and public response
  behavior.

`app/application/chat.py` should remain unchanged because it already owns the
collect/confirm lifecycle and does not own knowledge-provider selection.

`app/api/chat.py` should remain unchanged because the existing public response
contract already supports citations and safe error handling.

`app/schemas.py` should remain unchanged because no public attraction/chunk
fields are being added.

No migration, authoring content, acceptance CLI, root export, Planner-tool,
Train, Hotel, or Weather file should be required for the minimal design.

## 14. Testing Strategy

Implementation must follow TDD and keep ordinary tests offline.

### Adapter unit tests

Cover:

- relevant V2 evidence produces a deterministic grounded answer;
- empty evidence produces the fallback signal;
- V2 unavailable produces the fallback signal;
- real source label and URL propagation;
- deterministic evidence ordering and content grounding;
- no unsupported text generation.

### Routing tests

Cover:

- `travel_knowledge` invokes V2 first;
- V2 empty/unavailable invokes legacy fallback;
- non-knowledge intents do not call V2;
- no destination code is guessed;
- reliable destination metadata is forwarded unchanged.

### Regression tests

Cover unchanged Train, Hotel, and Weather paths, normal Planner behavior, API
response validation, and legacy knowledge behavior outside the V2 route.

### RAG regression

The existing RAG V2 unit and contract suite remains green. Ordinary tests do
not require live Supabase, live Jina, or production credentials.

## 15. Stage 10C-2 Acceptance Criteria

Stage 10C-2 implementation is acceptable only when all criteria below pass:

1. A real runtime `travel_knowledge` request can use RAG V2.
2. V2 uses dataset key `rag-v2-production`.
3. Active-only retrieval remains delegated to the existing V2 RPC.
4. The approved `RetrievalService` default threshold remains `0.60`.
5. The V2 answer is grounded only in returned evidence.
6. Real V2 source URLs and labels are preserved.
7. Empty or unavailable V2 retrieval falls back to legacy RAG.
8. A destination-less query is allowed without a guessed code.
9. Non-knowledge requests do not invoke V2 retrieval.
10. Train behavior is unchanged.
11. Hotel behavior is unchanged.
12. Weather behavior is unchanged.
13. The normal Planner path is unchanged.
14. No migration or corpus-data changes are introduced.
15. The existing RAG V2 test suite remains green.
16. Live Stage 10C-2 acceptance verifies at least one real `/api/chat`
    `travel_knowledge` request after implementation.

## 16. Explicit Non-Goals

Stage 10C-2 does not add:

- a reranker;
- a new vector database;
- a new embedding model;
- a fourth city or national corpus expansion;
- corpus import, activation, or migration behavior;
- a new agent framework;
- a Planner tool loop;
- frontend, streaming, or public endpoint redesign;
- an analytics platform;
- a destination ontology, geocoder workflow, or AMap resolver;
- Hotel, Train, or Weather API changes;
- legacy RAG removal; or
- production-grade retry, cache, or circuit-breaker infrastructure.

## 17. Rollout and Live Verification

Verification remains graduation-project-sized:

1. Run the new adapter, routing, fallback, and response tests locally.
2. Run the existing Train, Hotel, Weather, Planner, legacy RAG, and RAG V2
   regression suites.
3. Run a controlled real runtime/API knowledge smoke against the active
   production V2 corpus.
4. Verify empty and unavailable V2 fallback through deterministic fakes and
   safe logs.
5. Verify that non-knowledge requests do not call V2.

No automatic rollback or corpus lifecycle mutation is part of runtime
verification. Existing Stage 10C-1 corpus activation remains an operator-owned
acceptance boundary.

## 18. Implementation Handoff

After human approval of this design, implementation must be planned separately
as a sequence of small TDD tasks. The implementation plan must preserve the
router-triggered V2-first strategy, deterministic evidence formatting, legacy
fallback, destination policy, public schema boundary, and Train/Hotel/Weather/
Planner isolation defined here.

This document does not create implementation code, tests, migrations, runtime
flags, corpus data, or live acceptance evidence.
