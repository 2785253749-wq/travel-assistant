# RAG V2 Stage 10B-3 Retrieval Design Specification

Date: 2026-09-02
Status: design specification; implementation is not started

## 1. Goal

Stage 10B-3 adds the query-time RAG V2 retrieval orchestration boundary on
top of the completed Stage 10B-2 persistence and candidate-RPC layer. It
turns a validated user query into a temporary Jina query vector, obtains
raw RagV2Candidate rows from the existing repository, applies the frozen
deterministic selection policy, and returns source-backed
RetrievalEvidence values.

The complete flow is:

    user query
        -> query normalization
        -> Jina retrieval.query embedding
        -> 1024-dimensional query vector
        -> RagV2Repository.match_chunks(...)
        -> raw RagV2Candidate tuple
        -> score threshold
        -> content_hash deduplication
        -> two-round attraction diversity
        -> final_k
        -> RetrievalEvidence tuple
        -> RetrievalResult

This stage defines a stable seam for later consumers. It does not connect
the seam to Planner, chat runtime, or a live deployment.

## 2. Scope

### In scope

- A thin V2 query-embedding adapter owned by
  app/rag_v2/embedding.py.
- A retrieval orchestration module owned by
  app/rag_v2/retrieval.py.
- The fixed Jina query profile: jina-embeddings-v3,
  retrieval.query, 1024 dimensions.
- Strict query normalization and provider-output validation.
- Calling the existing typed RagV2Repository.match_chunks(...) seam.
- Python-side thresholding, content-hash deduplication, stable attraction
  diversity, final result limiting, and evidence assembly.
- Unit tests using fake providers, fake repositories, and deterministic
  candidate fixtures.

### Out of scope

Stage 10B-3 does not change the Stage 10B-2 repository, SQL migration, RPC
signatures, database indexes, database filtering semantics, or package-root
export surface. It also does not add an online consumer.

The existing legacy app/rag/embedding.py transport is an available
provider-infrastructure dependency. If the implementation reuses it, the
legacy module's current request, timeout, retry-compatible behavior, response
validation, logging redaction, and RagUnavailable semantics remain
unchanged. The V2 adapter owns the V2 query-facing error boundary; it does
not rewrite legacy callers. Legacy transport may be reused only as provider
infrastructure; its legacy-facing public error taxonomy is not exposed through
the new V2 query retrieval API.

## 3. Architecture

The approved architecture is:

    A — Retrieval Service + thin query embedding layer + pure deterministic post-processing

The three responsibilities are deliberately separated:

| Boundary | Responsibility | Must not own |
| --- | --- | --- |
| Query embedding layer | Invoke the Jina query profile through a provider seam, validate one 1024-value finite vector, and map provider failures | Query normalization, destination resolution, repository filters, ranking policy, evidence formatting |
| Repository layer | Send the query vector and exact metadata filters to match_rag_v2_chunks through RagV2Repository.match_chunks(...); return ordered RagV2Candidate values | Thresholding, deduplication, diversity, final_k, query embedding, Planner behavior |
| Retrieval layer | Validate retrieval parameters, call the embedder and repository, apply the deterministic policy, and assemble evidence | API keys, HTTP details, provider response parsing, SQL, persistence mutation |

The conceptual dependency direction is:

    retrieval.py
        -> query embedding interface in embedding.py
        -> RagV2Repository.match_chunks(...)
        -> pure candidate-selection helpers

The V2 embedding module is a new module-level seam for this stage. The
current baseline does not contain app/rag_v2/embedding.py; its future
implementation may adapt the existing legacy Jina transport without modifying
app/rag/embedding.py. This preserves the additive Stage 10B boundary and
keeps provider-specific details below the retrieval module.

New Stage 10B-3 symbols are initially importable from their owning modules.
The frozen 44-name app.rag_v2.__all__ surface remains unchanged. No
Stage 10B-3 implementation may add retrieval or query-embedding names to the
package root without a separate human export-surface decision.

## 4. Public data contracts

### 4.1 RetrievalEvidence

RagV2Candidate is the persistence/RPC representation. RetrievalEvidence is
the upper-layer consumable representation. The latter is intentionally
smaller and must not leak storage lifecycle identity.

    @dataclass(frozen=True)
    class RetrievalEvidence:
        attraction_id: UUID
        chunk_key: str
        chunk_type: ChunkType
        content: str
        content_hash: str
        source_label: str
        source_url: str
        source_type: str
        reviewed_on: date
        score: float

The field order above is part of the contract. The value is immutable. The
following fields are deliberately absent:

- corpus_version_id
- summaries or rewritten content
- LLM-generated citations
- rank, explanation, or debug metadata

Mapping copies content, score, and provenance without normalization,
rewriting, truncation, or recomputation.

### 4.2 RetrievalResult

    @dataclass(frozen=True)
    class RetrievalResult:
        query: str
        evidence: tuple[RetrievalEvidence, ...]

query is the normalized query actually sent to the query embedding provider.
evidence preserves the final deterministic selection order. There are no
additional fields in this stage.

### 4.3 Provider seam

The retrieval layer depends on a small query-embedding interface. The
Retrieval Service owns query normalization; the Query Embedder receives an
already-normalized, non-empty string. Its conceptual public operation is:

    embed_query(query: str) -> tuple[float, ...]

The concrete representation of the provider adapter may be a class with an
embed_query method or a narrow callable seam, provided the observable
contract remains the same. The Query Embedder must not strip, lowercase,
collapse whitespace, rewrite, expand, or append destination metadata. It
only invokes the provider, applies the fixed query profile, decodes and
validates the provider response, and maps embedding failures. Retrieval code
receives a validated tuple and never receives an API key, HTTP client,
provider response object, retry state, or raw provider body.

## 5. Query embedding contract

### 5.1 Fixed profile

The query profile is fixed to:

    model = jina-embeddings-v3
    task = retrieval.query
    dimensions = 1024

It is intentionally distinct from stored document/chunk embeddings:

    document/chunk task = retrieval.passage
    query task         = retrieval.query
    vector dimensions  = 1024

Query vectors are request-time values. They are not written into
rag_attraction_chunks, are not part of a manifest, and are not candidates
for Stage 10B-4 vector reuse.

### 5.2 Query normalization owned by the Retrieval Service

The Retrieval Service is the one and only query-normalization owner. The
exact flow is:

    retrieve(...)
        -> validate query is str
        -> strip leading/trailing whitespace exactly once
        -> preserve internal whitespace
        -> normalized_query
        -> embed_query(normalized_query)
        -> RetrievalResult.query = normalized_query

The Query Embedder receives an already-normalized, non-empty query string and
must never normalize it again. The Retrieval Service accepts only a Python
str query. It strips leading and trailing whitespace and preserves all
internal whitespace exactly.

    "   厦门适合看日落的地方   " -> "厦门适合看日落的地方"
    "厦门   日落"               -> "厦门   日落"

It does not lowercase, remove punctuation, collapse internal whitespace,
tokenize, rewrite, expand, append destination metadata, or call an LLM.

If the input is not a string, or the stripped result is empty, the public
caller error from the Retrieval Service is:

    ValueError("query must be a non-empty string")

This validation occurs before the embedder or repository is called. An empty
query therefore makes zero provider calls and zero repository calls. The
Query Embedder itself does not repeat this stripping or ownership decision;
its input contract is an already-normalized, non-empty string.

### 5.3 Successful output

The Query Embedder accepts only a successful provider result with exactly 1024
numeric values. It converts every value to float, rejects booleans and
non-numeric values, verifies that every converted value is finite, and
returns an immutable tuple[float, ...] of length 1024.

The adapter rejects all of the following as provider failures:

- too-short or too-long vectors;
- missing embedding data;
- malformed provider response shape;
- non-numeric values or booleans;
- NaN, positive infinity, or negative infinity;
- provider HTTP 4xx/5xx responses;
- network, timeout, JSON, and other provider/client failures.

The adapter must not expose an API key, raw provider response body, or
secret-bearing exception text in the raised application error or public
result.

### 5.4 Embedding-specific error boundary

At the V2 Query Embedder boundary, every embedding/provider dependency
failure uses a distinct public AppError:

    code    = RAG_V2_EMBEDDING_UNAVAILABLE
    message = RAG V2 embedding is unavailable

The exact stable message is public. If the V2 Query Embedder encounters a
legacy Jina RagUnavailable, provider HTTP failure, network failure, timeout,
provider/client exception, malformed provider response, missing embedding,
wrong dimensions, non-numeric or boolean embedding values, or NaN/positive
infinity/negative infinity, its public result is exactly this V2 embedding
error. Raw provider and legacy exception text must not escape.

If an error is already exactly the V2 embedding error with code
RAG_V2_EMBEDDING_UNAVAILABLE, the Query Embedder may re-raise it unchanged
and must not wrap it again. Repository AppError values are outside the Query
Embedder catch boundary and are preserved by the Retrieval Service exactly as
received.

### 5.5 Retry boundary

Stage 10B-3 does not introduce a new retrieval retry framework, retry queue,
circuit breaker, or background scheduler. If the existing Jina transport has
a small provider-owned timeout or retry-compatible policy, the V2 adapter may
reuse that seam in a later implementation. Retrieval orchestration itself
performs no additional retry or backoff policy.

## 6. Retrieval API

The frozen conceptual API is:

    retrieve(
        *,
        query: str,
        dataset_key: str,
        destination_code: str | None = None,
        destination_level: DestinationLevel | None = None,
        province_code: str | None = None,
        attraction_id: UUID | None = None,
        candidate_k: int = 40,
        final_k: int = 6,
        score_threshold: float = 0.70,
    ) -> RetrievalResult

The operation may be exposed as a service method with equivalent dependency
injection for the embedder and repository. The externally observable
parameters and semantics remain exactly those shown above; no unrelated
parameters are added.

### 6.1 Repository forwarding

After query normalization and query embedding, retrieval calls the existing:

    RagV2Repository.match_chunks(...)

It forwards the exact values for:

- dataset_key;
- the validated query vector as query_embedding;
- destination_code;
- destination_level;
- province_code;
- attraction_id;
- candidate_k.

The query vector is serialized by the repository boundary according to its
already-frozen contract. Retrieval does not alter the tuple, infer missing
destination data, resolve names to codes, or add contradictory filters.

candidate_k defaults to 40. This is an UNVALIDATED DEFAULT, meaning a
convenient initial value rather than a measured quality or capacity claim.
It means the maximum number of raw database/RPC candidates. Retrieval
forwards it unchanged and does not duplicate the database-owned 1..200
validation or clamp the value. The Stage 10B-2 RPC remains authoritative for
that range.

### 6.2 Parameter validation

final_k defaults to 6 and belongs entirely to the Python retrieval layer. It
must be a Python int; bool is invalid even though bool is an int subclass;
values must also satisfy final_k >= 1. Values such as True, False, 1.5,
"6", None, 0, and -1 are invalid and raise:

    ValueError("final_k must be at least 1")

score_threshold defaults to 0.70 and is also an UNVALIDATED DEFAULT. It
accepts Python int or float values only; bool is invalid. The accepted value
is converted to float and must be finite. Numeric strings, None, NaN,
positive infinity, and negative infinity are invalid and raise:

    ValueError("score_threshold must be finite")

The valid threshold may be 0, 1, 0.70, or -0.25 and is not restricted to
[0, 1]. Retrieval does not accept numeric strings, clamp it,
recompute cosine similarity, rescale scores, or introduce a score threshold
policy into SQL.

Caller validation occurs before provider and repository work whenever the
invalidity can be determined locally. The implementation must preserve the
exact public messages above.

## 7. Post-processing algorithm

The post-processing order is frozen and is part of the retrieval contract:

    1. raw RPC candidate order
    2. score threshold
    3. content_hash deduplication
    4. attraction diversity round 1
    5. attraction diversity round 2
    6. final_k
    7. map to RetrievalEvidence

### 7.1 Stable input order

The repository/RPC owns raw candidate ranking and returns candidates in its
approved order: score descending, then attraction_id ascending, then
chunk_key ascending. Retrieval consumes that order as-is. It must not sort,
reverse, shuffle, rank again, or use a secondary Python score computation.

All later passes are stable traversals of the current sequence.

### 7.2 Threshold pass

The first selection pass retains a candidate exactly when:

    candidate.score >= score_threshold

Equality is retained. A candidate below the threshold is removed. The score
is not changed or recomputed.

### 7.3 Content-hash deduplication

Deduplication identity is exactly content_hash.

The implementation iterates through the thresholded candidates in their
current order, retains the first occurrence of each content_hash, and
drops later occurrences. Therefore the earliest/highest-ranked occurrence
wins naturally.

It does not deduplicate by chunk_key, raw content, attraction_id, or an
attraction_id + chunk_type pair.

### 7.4 Attraction diversity round 1

Round 1 traverses the deduplicated list in order. It selects at most one
candidate for each attraction_id, stopping when final_k is reached or the
deduplicated list is exhausted.

### 7.5 Attraction diversity round 2

If round 1 selected fewer than final_k values, round 2 traverses the
remaining deduplicated candidates in their original order. It appends each
candidate not already selected, allows a repeated attraction_id, and stops
when final_k is reached or no candidates remain.

Remaining means candidates not selected by round 1, not candidates with a
new attraction identity. A candidate is selected at most once in this pass.

For example, with final_k = 4:

    A overview   .95
    A transport  .93
    B overview   .90
    C overview   .88
    A seasonal   .86

the output is:

    A overview
    B overview
    C overview
    A transport

This gives different attractions an initial opportunity while allowing an
attraction-specific query to refill remaining slots with additional chunks.
It is not a permanent one-attraction cap, weighted round robin, MMR,
reranking, or cross-encoder policy.

### 7.6 Final limit and evidence mapping

The two diversity rounds produce at most final_k candidates. The final
mapping creates one immutable RetrievalEvidence for each selected candidate
in the selected order, copying exactly:

    attraction_id
    chunk_key
    chunk_type
    content
    content_hash
    source_label
    source_url
    source_type
    reviewed_on
    score

corpus_version_id is intentionally discarded at this upper-layer boundary.
No summary, rewrite, generated citation, rank, explanation, or debug field is
added.

## 8. Error semantics

Error ownership is kept explicit:

| Failure | Public result |
| --- | --- |
| Invalid caller query or local retrieval parameter | ValueError with the frozen message |
| Jina/provider/network/response/embedding-shape failure | AppError("RAG_V2_EMBEDDING_UNAVAILABLE", "RAG V2 embedding is unavailable") |
| Supabase/RPC/repository failure | Preserve the existing typed RAG V2 persistence AppError |
| Valid retrieval with no usable evidence | Successful RetrievalResult with empty evidence |

The retrieval layer must not collapse embedding and persistence failures into
one generic error. It must not catch and rewrite an existing repository
AppError as an embedding error merely because the call occurs after
embedding. It must not expose raw provider or database content, secrets,
stack traces, or response bodies through a public exception.

Legacy Jina `RagUnavailable` is treated as an embedding/provider dependency
failure when it crosses the V2 Query Embedder boundary and is translated to
`RAG_V2_EMBEDDING_UNAVAILABLE`. Legacy callers and legacy behavior remain
unchanged. A repository `AppError` is outside that boundary and is preserved
unchanged by the Retrieval Service.

The Stage 10B-2 repository remains responsible for its existing
RAG_V2_UNAVAILABLE, not-found, conflict, and other persistence mappings.
Stage 10B-3 does not create a second repository error taxonomy and does not
modify those mappings.

## 9. Empty-result semantics

The following are valid successful retrievals, not errors:

- the repository returns an empty tuple/list;
- no candidate matches the exact optional metadata filters;
- all candidates fall below the threshold;
- content-hash deduplication removes every candidate;
- fewer than final_k candidates remain after selection.

The result is:

    RetrievalResult(
        query=normalized_query,
        evidence=(),
    )

For a non-empty but short result, evidence contains the actual available
count in stable order. Empty valid retrievals do not raise RAG_V2_NOT_FOUND,
RAG_V2_UNAVAILABLE, or RAG_V2_EMBEDDING_UNAVAILABLE.

## 10. Testing strategy

Stage 10B-3 tests are unit and orchestration-contract tests only. They use
fake providers, fake repositories, and deterministic RagV2Candidate
fixtures. They do not call Jina, Supabase, PostgreSQL, a live vector index,
or a network service.

### 10.1 Query embedding tests

tests/unit/test_rag_v2_query_embedding.py assumes it receives an
already-normalized, non-empty query string and must cover:

- forwarding that already-normalized string to the provider;
- fixed model jina-embeddings-v3;
- fixed task retrieval.query;
- fixed dimensions 1024;
- a valid 1024-value output converted to tuple[float, ...];
- too-short and too-long provider vectors;
- missing embedding data and malformed provider response shape;
- non-numeric and boolean values;
- NaN, positive infinity, and negative infinity;
- provider HTTP/client/network exceptions;
- the exact RAG_V2_EMBEDDING_UNAVAILABLE code/message;
- absence of raw secrets or provider/error body text from the public failure.

No test uses a live Jina API key or real HTTP endpoint.

### 10.2 Retrieval orchestration tests

tests/unit/test_rag_v2_retrieval.py must cover:

- non-string query rejection;
- leading and trailing stripping exactly once;
- preservation of internal whitespace;
- empty normalized query rejection;
- zero embedder calls and zero repository calls for invalid/empty queries;
- normalized query forwarding to the embedder;
- RetrievalResult.query equal to normalized_query;
- exact repository forwarding of query vector and all six repository
  parameters: dataset_key, destination_code, destination_level,
  province_code, attraction_id, and candidate_k;
- unchanged forwarding of candidate_k;
- score equality at the threshold being retained;
- scores below the threshold being rejected;
- first content_hash occurrence being retained;
- stable ordering without a Python re-sort;
- round-1 one-per-attraction selection;
- round-2 repeated-attraction refill;
- final_k enforcement;
- fewer than final_k candidates returning the actual available count;
- RPC empty results producing evidence == ();
- all-below-threshold results producing evidence == ();
- exact final_k type and final_k < 1 validation, including bool, float,
  string, None, zero, and negative values;
- exact score_threshold type and finite validation, including bool, numeric
  strings, None, NaN, and infinities;
- exact RagV2Candidate to RetrievalEvidence field mapping;
- corpus_version_id not appearing on RetrievalEvidence.

Tests must also prove that provider failures do not call the repository
and that repository AppError values are preserved rather than recategorized.

### 10.3 Deterministic policy tests

The selection tests should assert ordered field values rather than incidental
implementation helpers. Small pure helper functions are acceptable when
they make the two diversity rounds and first-occurrence deduplication easier
to test, but the module must not grow a generic ranking framework.

## 11. Static-vs-live boundary

The Stage 10B-3 acceptance level is:

    unit / fake-provider / orchestration-contract only

This proves the Python seam, forwarding behavior, validation, deterministic
selection policy, and public result/error contracts. It does not prove:

- real Jina API behavior;
- real API-key configuration;
- real provider timeout or network retry behavior;
- real Supabase RPC behavior;
- real pgvector vector casts or ANN planning;
- existence or quality of an active corpus;
- online retrieval quality, recall, contamination, or latency.

Those claims require later controlled live acceptance with frozen corpus and
query fixtures. Fake-provider results must never be reported as production
retrieval-quality evidence.

## 12. Explicit non-goals

The following are prohibited in Stage 10B-3:

- Planner or runtime integration;
- /api/chat or app/composition.py wiring;
- LLM query rewriting, query expansion, or destination/attraction
  auto-resolution;
- rerankers, cross-encoders, MMR, or other learned ranking layers;
- Stage 10B-4 corpus import, document embedding, incremental embedding,
  vector reuse, or list_embedded_chunks_for_reuse;
- SQL pushdown for thresholding, content-hash deduplication, diversity, or
  final_k;
- new database migrations, new RPCs, or repository semantic changes;
- a new vector database, Elasticsearch, or another retrieval backend;
- live Supabase or Jina acceptance;
- deployment, Render environment verification, production smoke testing, or
  online quality measurement.

No Stage 10B-3 implementation may modify the existing 014_rag_v2.sql,
RagV2Repository, or legacy RAG behavior to move a concern across these
boundaries.

## 13. Future-stage relationship

    Stage 10B-3 = query-time retrieval orchestration
    Stage 10B-4 = corpus import / incremental embedding / reuse pipeline
    Stage 10C   = Planner/runtime integration + real deployment acceptance

Stage 10B-4 may later consume the Stage 10B-1 incremental decision contracts
and own the typed read seam for reusable embedded vectors. Stage 10B-3 must
not implement that seam, copy vectors, or alter the pending-to-embedded
storage transition.

Stage 10C may connect retrieval-first grounded candidates to Planner/runtime
and perform controlled real Jina/Supabase evaluation, parameter calibration,
deployment verification, source behavior checks, and online acceptance. None
of those consumers exists in this stage.

## 14. Acceptance criteria

The Stage 10B-3 design is complete when the later implementation can satisfy
all of the following without reopening earlier contracts:

1. The Retrieval Service is the only query-normalization owner: it strips
   outer whitespace exactly once, preserves internal whitespace, passes the
   normalized non-empty query to the Query Embedder, and rejects an empty
   normalized query with the exact public ValueError. The Query Embedder never
   normalizes.
2. Query embedding uses jina-embeddings-v3, retrieval.query, and exactly
   1024 finite float values, returning a tuple.
3. Provider failures map to RAG_V2_EMBEDDING_UNAVAILABLE with the exact
   stable message and no raw secret/body leakage.
4. The Query Embedder translates legacy/provider embedding failures to the V2
   embedding error, while repository failures preserve existing RAG V2 persistence AppError
   semantics, and RagV2Repository.match_chunks(...) receives the exact
   query vector, filters, and unchanged candidate_k.
5. final_k accepts only Python int values other than bool and requires
   final_k >= 1, using the exact frozen ValueError for every invalid value.
   score_threshold accepts only Python int or float values other than bool,
   converts to float, requires finiteness, and has no [0, 1] restriction,
   using the exact frozen ValueError for every invalid value.
6. candidate_k = 40, final_k = 6, and score_threshold = 0.70 are
   explicitly documented as UNVALIDATED DEFAULT values where applicable;
   retrieval owns final_k and threshold validation while the database owns
   the candidate range.
7. Threshold eligibility is score >= score_threshold, including equality,
   with no clamp, rescale, or cosine recomputation.
8. Candidate order is preserved; deduplication is by first content_hash
   occurrence; diversity runs in the exact two stable rounds; and the final
   count is at most final_k.
9. RetrievalEvidence has exactly the frozen ten fields and excludes
   corpus_version_id; RetrievalResult has exactly query and evidence.
10. Empty valid retrievals return successful empty evidence rather than an
   application error.
11. Unit/fake-provider tests cover the query, provider, forwarding,
   threshold, deduplication, diversity, limit, empty-result, validation,
   error-preservation, and evidence-mapping contracts without live services.
12. The implementation remains additive: app/rag_v2/repository.py,
   supabase/migrations/014_rag_v2.sql, legacy RAG modules,
   app/rag_v2/__init__.py, Planner/runtime wiring, and Stage 10B-4 remain
    outside this stage's implementation scope.

The design intentionally avoids ThresholdFilter, ContentDeduplicator,
AttractionDiversifier, EvidenceAssembler, pipeline registries, plugin
pipelines, and strategy factories. A thin embedder, a thin retrieval service,
and small deterministic helpers are sufficient for the current contract.
