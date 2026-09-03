# RAG V2 Stage 10B-4 Design Specification

Status: architecture approved; implementation is not started.

Stage 10B-4 is the offline corpus-import and deployment-contract stage for RAG V2. It adds the importer seam, incremental passage-embedding orchestration, deterministic offline evaluation, and the minimum deployment-manifest declaration needed to supply the passage provider secret. It does not activate a corpus, alter the database schema, change query-time retrieval, or connect the Planner/runtime.

## 1. Stage Relationship and Boundaries

Stage 10B-1 owns the RAG V2 domain models, canonical normalization and hashing, semantic chunking, incremental decision rules, and the package export surface.

Stage 10B-2 owns the four-table V2 persistence schema, lifecycle constraints, activation RPC, RLS and privilege contracts, and the typed repository persistence boundary.

Stage 10B-3 owns query-time embedding, retrieval evidence/result contracts, and complete RetrievalService behavior. Its query path is complete and is not reopened by this stage.

Stage 10B-4 owns:

- corpus authoring input validation and orchestration;
- stable-attraction identity resolution at import time;
- deterministic chunk persistence;
- prior-vector lookup and incremental reuse;
- passage embedding and vector persistence;
- failure, retry, and idempotency behavior for an offline import run;
- deterministic offline evaluation fixtures and checks;
- the deployment manifest declaration and operational contract for JINA_API_KEY.

Stage 10B-5 is the later full regression, security, readiness, and release-evidence gate.

Stage 10C is the later controlled real-environment work: the three-city corpus migration, real Jina and Supabase end-to-end execution, activation, Planner/runtime integration, Render Dashboard configuration, and online smoke evidence.

Stage 10B-4 never calls activate_corpus. A successful import leaves the corpus in staging and returns a readiness result for a later human-controlled activation step. No code in this stage may invoke the Planner, alter the existing runtime route, perform a real import, or claim online readiness.

## 2. Repository Facts and Ownership

The approved Stage 10B-4 design uses the existing owners rather than inventing modules that are not present:

- EmbeddingInput is owned by app/rag_v2/models.py.
- ManifestInput, ManifestAttraction, and ManifestChunk are owned by app/rag_v2/models.py.
- build_embedding_input, canonical_embedding_text, embedding_input_hash, canonical_manifest_json, and manifest_hash are owned by app/rag_v2/hashing.py.
- SemanticSection and SemanticChunk are owned by app/rag_v2/models.py.
- SemanticChunker is owned by app/rag_v2/chunking.py.
- identity protocols and pure lifecycle functions are owned by app/rag_v2/identity.py.
- incremental identity and decision types are owned by app/rag_v2/incremental.py.
- query embedding remains owned by app/rag_v2/embedding.py.
- query-time retrieval remains owned by app/rag_v2/retrieval.py.
- typed persistence remains owned by app/rag_v2/repository.py.

There is currently no app/rag_v2/embedding_input.py and no app/rag_v2/manifest.py. Stage 10B-4 does not create either file and does not move those contracts.

The root export list remains the frozen Stage 10B-1 surface. New Stage 10B-4 types are not added to app/rag_v2/__init__.py in this stage unless a later export review explicitly approves an additive public-surface change.

## 3. Fixed Profiles and Canonicalization

Document passage embedding is fixed to:

    model = jina-embeddings-v3
    task = retrieval.passage
    dimensions = 1024
    input_schema_version = rag-v2-embedding-input-v1

Query embedding remains separately fixed to:

    model = jina-embeddings-v3
    task = retrieval.query
    dimensions = 1024

The passage and query profiles are not interchangeable. Stage 10B-4 does not modify the query provider or RetrievalService.

For every passage that is newly embedded, the importer builds EmbeddingInput through build_embedding_input and sends canonical_embedding_text of that model to the passage embedder. The importer never embeds raw, unnormalized section text and never reconstructs canonical JSON independently of hashing.py.

The supplied ManifestInput is authoritative input validation. The importer verifies that its dataset key, fixed profile, attraction identities, chunk identities, content hashes, embedding-input hashes, provenance, and canonical manifest hash agree with the generated import artifact. A mismatch is a domain validation failure before the corpus can become ready.

## 4. Public Importer API

The new module is app/rag_v2/importer.py.

The importer composes existing models and seams with the following new frozen input/output types.

    @dataclass(frozen=True)
    class ImportAttraction:
        registry_key: str
        metadata: AttractionVersionMetadata
        sections: tuple[SemanticSection, ...]

    @dataclass(frozen=True)
    class CorpusImportInput:
        corpus_version_id: UUID
        dataset_key: str
        version_label: str
        manifest: ManifestInput
        attractions: tuple[ImportAttraction, ...]
        retry_failed: bool = False

    @dataclass(frozen=True)
    class CorpusImportResult:
        corpus: CorpusVersion
        chunk_rows: tuple[ChunkRow, ...]
        ready_for_activation: bool
        reused_chunk_keys: tuple[str, ...]
        embedded_chunk_keys: tuple[str, ...]

The result type represents successful completion only. A failure does not return a partially-ready result. On success, ready_for_activation is true, all required chunk rows are authoritative embedded rows, and the corpus remains staging.

The public orchestration type is:

    class RagV2Importer:
        def __init__(
            self,
            *,
            identity_source: AttractionIdentitySource,
            repository: RagV2Repository,
            chunker: SemanticChunker,
            passage_embedder: PassageEmbedder,
        ) -> None: ...

        def import_corpus(
            self,
            request: CorpusImportInput,
        ) -> CorpusImportResult: ...

The constructor dependencies are narrow seams. Tests use fakes for each dependency. The importer does not import the query provider, RetrievalService, Planner, Supabase client internals, or a network client directly.

ImportAttraction deliberately uses the existing AttractionVersionMetadata and SemanticSection types. The registry_key is the stable external identity key; attraction_id in metadata and sections is the candidate identity supplied by the source.

Identity resolution is deterministic:

1. Resolve registry_key through AttractionIdentitySource.resolve.
2. If no identity exists, allocate exactly one stable UUID through allocate and adapt the candidate metadata and sections to that allocated ID.
3. If an existing identity is returned, require metadata.attraction_id and every section.attraction_id to equal it.
4. Reject conflicting identity claims with a domain validation error.
5. Require the final generated attraction IDs to agree with the supplied ManifestInput.

Allocation is performed only for a previously unresolved registry key. The registry mapping, not a source row's incidental UUID, is authoritative. A failed import does not silently allocate a second identity on retry.

## 5. Import Flow

RagV2Importer.import_corpus follows this order:

1. Validate request shape, non-empty dataset/version identity, unique registry keys, attraction metadata, sections, fixed passage profile, and manifest consistency.
2. Resolve or allocate stable attraction identities.
3. Create or retrieve the immutable corpus version through RagV2Repository.create_corpus_version.
4. Ensure each stable attraction exists through the existing identity/lifecycle persistence seam.
5. Insert attraction-version metadata through insert_attraction_versions.
6. Chunk each section through the existing SemanticChunker.
7. Build and compare the generated manifest artifact and manifest hash.
8. Insert pending chunks through insert_chunks.
9. For every current chunk, obtain eligible previous embeddings from the dedicated reuse read seam.
10. Build an IncrementalCandidate with the fixed current passage identity and call decide_incremental.
11. For REUSE, persist the exact prior validated vector through mark_chunk_embedded without calling Jina.
12. For EMBED, create the canonical embedding text, call the passage embedder once, validate its vector, and persist it through mark_chunk_embedded.
13. Re-read authoritative persisted rows and perform the application readiness checks.
14. Return a successful CorpusImportResult with ready_for_activation true, without calling activate_corpus.

Lifecycle subjects are handled by decide_incremental before present-chunk reuse logic. Removed, absent, explicitly retired, and merged-source subjects use the already-approved actions. The importer translates those actions to the existing repository lifecycle methods and does not duplicate the decision table.

No greedy chunk recombination, alternate hash implementation, profile inference, query embedding, or activation shortcut is allowed.

## 6. Typed Repository Read Seams

Stage 10B-2 has typed writes and match retrieval but no read seam for prior embedded chunks. Stage 10B-4 adds the smallest direct-table typed seam required for safe incremental reuse:

    def list_embedded_chunks_for_reuse(
        self,
        *,
        dataset_key: str,
        embedding_input_hash: str,
        exclude_corpus_version_id: UUID,
    ) -> tuple[PreviousEmbedding, ...]: ...

The method reads the V2 tables directly through the existing repository client abstraction. It does not add SQL, an RPC, or raw Supabase access.

The query joins rag_attraction_chunks to rag_corpus_versions and applies all of these filters:

- exact dataset_key;
- exact embedding_input_hash;
- source corpus status is active or superseded;
- source corpus_version_id differs from exclude_corpus_version_id;
- chunk status is embedded;
- embedding is non-null.

Staging and failed corpora are never reuse sources. Superseded corpora remain valid historical reuse sources. The current staging corpus is always excluded.

The returned PreviousEmbedding values contain:

- chunk_key;
- EmbeddingIdentity with the exact five persisted identity fields;
- decoded vector;
- validated_corpus = true.

Rows are deterministic in chunk_key ascending order, with corpus_version_id ascending as a tie-breaker. The repository validates the fixed passage profile, exact 1024-dimensional vector length, numeric non-bool coordinates, finiteness, and the response shape. Missing, malformed, or unsafe authoritative rows map to the existing RAG_V2_UNAVAILABLE persistence error and never leak a raw client or conversion exception.

The decision layer still performs exact five-field identity matching and deterministic smallest-chunk-key selection. The read seam is intentionally narrower than a general vector search API.

Safe rerun reconciliation also needs authoritative current-snapshot reads because immutable inserts must not be replaced with blind upserts. Stage 10B-4 therefore adds two minimal typed repository reads using existing dataclasses:

    def list_attraction_versions(
        self,
        *,
        corpus_version_id: UUID,
    ) -> tuple[AttractionVersionRecord, ...]: ...

    def list_chunk_rows(
        self,
        *,
        corpus_version_id: UUID,
    ) -> tuple[ChunkRow, ...]: ...

The first is ordered by attraction_id. The second is ordered by attraction_id, chunk_type, ordinal, and chunk_key. Both map malformed database rows to the repository's typed unavailable error and return authoritative values only.

These snapshot reads are not new business abstractions. They exist to compare an immutable existing staging snapshot with the deterministic requested snapshot during reruns and to perform the final readiness check without trusting only in-memory values.

## 7. Passage Embedding API

The new module is app/rag_v2/passage_embedding.py. It remains separate from app/rag_v2/embedding.py, which owns the query side.

The passage provider seam is:

    class PassageEmbeddingProvider(Protocol):
        def embed(
            self,
            text: str,
            *,
            model: str,
            task: str,
            dimensions: int,
        ) -> object: ...

The application-facing seam is:

    class PassageEmbedder(Protocol):
        def embed_passage(self, text: str) -> tuple[float, ...]: ...

The concrete provider and adapter are:

    class JinaPassageProvider: ...
    class JinaPassageEmbedder: ...

JinaPassageEmbedder always sends one input string with the fixed passage model, task, and dimensions. It validates a response as exactly 1024 finite numeric, non-bool values and returns a tuple of floats.

Provider, transport, malformed response, and conversion failures map to the same frozen public error as the query side:

    code = RAG_V2_EMBEDDING_UNAVAILABLE
    message = RAG V2 embedding is unavailable

No API key, response body, URL credential, or raw exception text is exposed. The passage adapter does not accept caller-selected model, task, or dimensions. There is no range clamp, threshold, cosine recomputation, or normalization of vector magnitude.

The adapter is unit-tested with direct fake-HTTP/provider responses. Tests do not contact Jina. Query-side tests and implementation remain isolated.

## 8. Reuse and Re-embedding Semantics

For each current present chunk, the importer supplies the current fixed identity:

    embedding_input_hash
    embedding_model = jina-embeddings-v3
    embedding_task = retrieval.passage
    embedding_dimensions = 1024
    embedding_input_schema_version = rag-v2-embedding-input-v1

The importer requests previous candidates by the current embedding_input_hash and excludes the current corpus version. decide_incremental then performs the exact identity and vector eligibility checks.

REUSE means:

- the previous corpus is active or superseded;
- validated_corpus is true;
- all five identity fields match exactly;
- the vector is non-null, finite, and exactly 1024-dimensional;
- the smallest eligible chunk_key is selected;
- the exact prior vector is passed to mark_chunk_embedded;
- Jina is not called.

EMBED means no eligible prior vector exists. The importer creates canonical embedding text and calls JinaPassageEmbedder once for that chunk.

A changed normalized content or embedding-input identity therefore re-embeds. A changed provenance-only field does not change content_hash or embedding_input_hash, so it may reuse the vector while the new provenance is persisted in the new chunk row. This preserves the approved content-only versus provenance-only hash semantics.

The importer never copies a vector merely because attraction_id, chunk_type, or ordinal match. Exact identity is required. It never reuses a vector from staging or failed corpus data.

## 9. Persistence and Idempotency

The importer uses the existing create_corpus_version idempotency contract:

- the identity is dataset_key plus version_label;
- an existing staging, active, or superseded version with the same manifest hash is reusable;
- an existing failed version is a conflict;
- a different manifest hash is a conflict;
- a race is resolved by reading the authoritative existing identity;
- the repository's returned corpus row, including its authoritative corpus_version_id, is used for all subsequent operations.

The request corpus_version_id is the candidate ID for a new corpus. If an idempotent existing version is returned with another authoritative UUID, the importer uses the returned UUID and does not create a duplicate.

Attraction versions and chunks are immutable. The importer never updates an existing immutable payload and never turns an insert conflict into an overwrite.

On a rerun of the same deterministic staging request:

- exact existing attraction-version rows are accepted as already persisted;
- exact existing chunk rows are accepted as already persisted;
- a mismatch in any immutable field is a version conflict;
- pending rows remain pending;
- embedded rows are verified against the expected profile, hashes, provenance, and vector shape;
- failed rows remain failed unless retry_failed is true;
- when retry_failed is true, reset_chunk_embedding_for_retry is called explicitly before a fresh reuse-or-embed decision;
- a failed corpus is never silently resurrected by the importer;
- no second identity allocation occurs for an already-resolved registry key;
- no duplicate vector operation is performed for an already-authoritative embedded row unless an explicit retry policy requires it.

The final snapshot is compared by stable keys and canonical field values. Caller order never determines persisted identity or result order.

There is no claim of a distributed transaction across the identity registry, Supabase, and Jina. If a later step fails, already-persisted staging rows remain for diagnostics and explicit retry. The importer marks the corpus failed when the repository can safely do so, then raises the typed failure.

## 10. Failure and Error Semantics

Input and generated-artifact contract violations are domain validation failures. They include:

- invalid manifest profile or hash;
- conflicting stable identity;
- section/attraction identity mismatch;
- missing or incomplete provenance;
- duplicate immutable identity;
- inconsistent generated metadata or chunk payload.

These failures do not expose provider or database internals.

Repository failures preserve the repository's existing AppError contract. Raw Supabase/PostgREST errors do not cross the repository boundary.

Passage-provider failures map to RAG_V2_EMBEDDING_UNAVAILABLE with the frozen safe message. The importer marks the affected chunk failed with a concise safe error code/message, marks the corpus failed where possible, preserves the staging rows, and raises the typed error.

Malformed reuse rows, malformed current snapshot rows, malformed vectors, and unexpected repository response/conversion failures map to RAG_V2_UNAVAILABLE. No raw Python conversion exception or raw response content escapes.

A final readiness failure also marks the corpus failed where possible and raises. A success result is returned only after all required chunks are authoritative embedded rows with valid fixed-profile vectors.

The importer does not catch and reinterpret existing AppError values as generic domain errors. Already-created typed errors remain unchanged.

## 11. Provenance and Source Completeness

Every persisted chunk must carry:

- non-empty source_label after trimming;
- non-empty source_type after trimming;
- an absolute HTTPS source_url;
- a source URL that is not localhost, a .test domain, an example-only host, or another known non-production placeholder domain;
- non-null reviewed_on.

The importer validates these rules before embedding and checks them again in the final authoritative snapshot. It does not fabricate provenance, infer a review date, or replace a missing URL with a default.

The activation RPC remains the final database trust boundary. The importer readiness check is an early application check and does not replace database constraints or activation validation.

## 12. Offline Evaluation

The new module is app/rag_v2/evaluation.py. It is pure and deterministic; it does not invoke Supabase, Jina, RetrievalService, or the Planner.

The frozen evaluation case type is:

    @dataclass(frozen=True)
    class EvaluationCase:
        case_id: str
        query: str
        expected_destination_code: str | None
        attraction_destinations: tuple[tuple[UUID, str], ...]
        expected_attraction_ids: tuple[UUID, ...]
        expected_chunk_keys: tuple[str, ...]
        expect_no_answer: bool
        require_source_urls: bool = True

The frozen result type is:

    @dataclass(frozen=True)
    class EvaluationObservation:
        case_id: str
        passed: bool
        reason: str
        returned_attraction_ids: tuple[UUID, ...]
        returned_chunk_keys: tuple[str, ...]

The pure APIs are:

    def evaluate_case(
        *,
        case: EvaluationCase,
        result: RetrievalResult,
    ) -> EvaluationObservation: ...

    def evaluate_cases(
        cases: Sequence[EvaluationCase],
        results: Sequence[RetrievalResult],
    ) -> tuple[EvaluationObservation, ...]: ...

The evaluator checks:

- deterministic case ordering and matching case IDs;
- expected attraction IDs and expected chunk keys when specified;
- expected destination behavior using the explicit attraction_destinations mapping, because RetrievalEvidence intentionally does not contain destination metadata;
- wrong-destination evidence;
- no-answer cases, which pass only when evidence is empty;
- duplicate chunk_key or content_hash evidence;
- source URL completeness when require_source_urls is true;
- stable, concise failure reasons.

The evaluator does not invent a relevance score, threshold, LLM judge, network call, or hidden metadata lookup. It is a fixture-level contract for offline evidence, not a production ranking algorithm.

## 13. Deployment and Environment Contract

The later implementation may modify render.yaml only by adding this server-side secret declaration:

    - key: JINA_API_KEY
      sync: false

The existing .env.example already contains a safe blank JINA_API_KEY entry and remains unchanged. No real secret is committed. No secret is exposed to browser/client configuration.

Stage 10B-4 does not add RAG_V2_ENABLED, candidate_k, final_k, score threshold, activation flags, or other runtime configuration. The importer is offline and is not wired into the web service.

Dashboard configuration, secret value entry, deploy ID, migration result, and online smoke evidence are not performed in this stage. They belong to the later human-controlled deployment and Stage 10C evidence gate.

A deployment-manifest contract test may statically assert that render.yaml contains JINA_API_KEY with sync false and does not require runtime activation or online claims.

## 14. Runtime, Database, and Legacy Boundaries

The following remain unchanged:

- supabase/migrations/014_rag_v2.sql;
- every prior migration;
- app/rag legacy retrieval and import behavior;
- app/rag_v2/models.py;
- app/rag_v2/hashing.py;
- app/rag_v2/identity.py;
- app/rag_v2/chunking.py;
- app/rag_v2/incremental.py;
- app/rag_v2/embedding.py;
- app/rag_v2/retrieval.py;
- app/rag_v2/__init__.py;
- Planner/runtime routing;
- frontend behavior;
- .env.example.

Stage 10B-4 does not add a migration, RPC, index, trigger, RLS policy, GRANT, or REVOKE. The existing activation RPC remains untouched and is never invoked by the importer. No legacy table, function, route, or client is altered.

## 15. Proposed Implementation Scope

The later implementation is limited to the following files.

Create:

- app/rag_v2/importer.py
- app/rag_v2/passage_embedding.py
- app/rag_v2/evaluation.py
- tests/unit/test_rag_v2_importer.py
- tests/unit/test_rag_v2_passage_embedding.py
- tests/unit/test_rag_v2_evaluation.py
- tests/integration/test_rag_v2_deployment_contract.py

Modify:

- app/rag_v2/repository.py
- tests/unit/test_rag_v2_repository.py
- render.yaml

Do not modify:

- all Stage 10B-1, 10B-2, and 10B-3 production modules and tests except the repository seam explicitly listed above;
- supabase/migrations/014_rag_v2.sql and all legacy migrations;
- .env.example;
- app/rag_v2/__init__.py;
- app/rag_v2/embedding.py;
- app/rag_v2/retrieval.py;
- Planner/runtime/frontend code;
- existing deployment evidence documents.

No implementation file or test file is created by this specification-only change.

## 16. Testing Strategy and TDD Sequence

Implementation follows strict RED/GREEN with human-run Python verification. Tests are static or fake-boundary tests unless a later stage explicitly supplies a database harness.

The passage adapter tests use direct fake provider or fake HTTP responses and cover fixed profile forwarding, one-input behavior, exact vector validation, and safe provider-error mapping.

The repository tests cover:

- active/superseded-only reuse filtering;
- dataset and exact embedding-input-hash filtering;
- current-corpus exclusion;
- pending, failed, excluded, null-vector, malformed-vector, and wrong-profile exclusion or error behavior;
- deterministic ordering;
- typed PreviousEmbedding construction;
- immutable snapshot reads;
- malformed authoritative row normalization.

The importer tests use fake identity, chunker, repository, and passage embedder seams. They cover:

- stable identity resolution and one-time allocation;
- manifest/hash agreement;
- pending-only chunk insertion;
- exact provenance propagation;
- reuse without passage-provider calls;
- re-embedding after content/input identity change;
- exact vector persistence;
- failed chunk and failed corpus behavior;
- explicit retry of failed rows;
- same-request idempotent rerun;
- immutable mismatch conflict;
- no activation call;
- final authoritative readiness validation;
- deterministic result ordering.

The evaluator tests cover correct answers, wrong destinations, no-answer cases, duplicate evidence, missing source URLs, expected-ID mismatches, and deterministic case/result matching.

The deployment contract test is static and checks only the approved Render declaration. It does not contact Render or assert Dashboard state.

The TDD order is:

1. write and human-verify passage adapter RED, then implement GREEN;
2. write and human-verify repository reuse/snapshot RED, then implement GREEN;
3. write and human-verify importer RED, then implement GREEN;
4. write and human-verify offline evaluator RED, then implement GREEN;
5. write deployment contract RED, add the single manifest declaration, then verify GREEN;
6. run the approved focused and cross-stage regression gates before review.

Task boundaries remain atomic. No partial RetrievalService is added to Task 2 or reopened in Stage 10B-4. Task 4 PASS-immediately closure semantics from the prior plan remain unchanged. Stage 10B-4 implementation does not begin until this design specification passes its human gate.

## 17. Evidence Matrix

The implementation review must record:

- Task 10B-4 passage adapter RED and GREEN output;
- repository reuse/snapshot RED and GREEN output;
- importer RED and GREEN output;
- evaluator RED and GREEN output;
- deployment contract RED and GREEN output;
- Stage 10B-4 focused regression output;
- Stage 10B-3 regression output;
- Stage 10B-2 regression output;
- Stage 10B-1 regression output;
- legacy isolation output;
- static diff and scope checks;
- human review approval before commit.

Offline tests do not prove live PostgreSQL behavior, real Jina availability, Dashboard configuration, deployment success, activation, or online smoke behavior. Those claims require the later controlled environment.

## 18. Security and Operational Policy

JINA_API_KEY is server-only, supplied through Render's sync:false secret declaration, and never logged or committed. Provider errors expose only the frozen public unavailable code/message.

The importer writes only the RAG V2 tables through the typed repository. It does not use service-role credentials in client code, does not alter RLS or privileges, and does not broaden database access.

Staging rows are retained after failure for diagnosis and explicit retry. Failed corpora are not activated. A later operator may discard an inactive failed staging corpus through an approved operational procedure; this specification does not add a destructive cleanup command.

## 19. Rollback

The additive Stage 10B-4 implementation can be disabled by removing the importer/evaluator invocation from the offline job or by excluding the additive modules from that job. Existing query retrieval and legacy behavior remain untouched.

If the deployment declaration must be reverted, remove only the added JINA_API_KEY declaration and preserve all unrelated Render environment declarations. No database rollback is required because this stage adds no migration or RPC.

An unsuccessful import leaves the corpus inactive. Activation is a separate later action, so rollback does not require reversing a production corpus switch.

## 20. Acceptance Criteria

Stage 10B-4 implementation is acceptable only when all of the following are true:

- stable identity resolution is deterministic and registry-key authoritative;
- generated chunks and hashes use the existing canonical owners;
- all inserted chunks begin pending and preserve provenance;
- reuse reads only active or superseded same-dataset vectors and excludes the current corpus;
- exact five-field embedding identity and exact 1024 finite-vector eligibility are enforced;
- reuse persists the exact prior vector and skips Jina;
- changed embedding input re-embeds through the fixed passage profile;
- provider, repository, malformed-row, and validation failures have the specified safe boundaries;
- explicit retry is required for failed rows;
- immutable reruns are deterministic and do not upsert or duplicate;
- final readiness checks use authoritative persisted rows;
- the importer never activates a corpus;
- offline evaluation detects wrong destinations, no-answer violations, duplicates, missing sources, and expected-result mismatches;
- render.yaml declares JINA_API_KEY with sync:false and no real secret is committed;
- no migration, RPC, RLS, privilege, Planner, runtime, frontend, or legacy behavior is changed;
- human-run focused and cross-stage regression evidence is green before final review.

## 21. Alternatives Rejected

A new embedding_input.py or manifest.py module is rejected because the current repository already owns those contracts in models.py and hashing.py.

A new reuse RPC or migration is rejected because a typed direct-table repository read is sufficient and Stage 10B-2 explicitly deferred this seam.

Using staging or failed corpora as vector sources is rejected because they are not validated corpus history.

Calling RetrievalService from the importer is rejected because Stage 10B-4 imports passages and does not perform query-time retrieval.

Calling activate_corpus automatically is rejected because activation is a separate final trust-boundary operation.

Adding runtime configuration or wiring the importer into the web request path is rejected because deployment and Planner integration belong to later controlled stages.

Using an LLM judge or network-based evaluator is rejected because Stage 10B-4 requires deterministic offline evidence.

Changing query embedding or the root package export list is rejected because those Stage 10B-3 and Stage 10B-1 contracts are already frozen.

## 22. Contradiction and Completeness Gate

Before implementation begins, the human review must confirm:

- Task 10B-3 query retrieval remains complete and unchanged.
- Stage 10B-4 is the first owner of the importer and passage embedder.
- Task 2 remains data contracts and QueryEmbedder only; no partial service is introduced.
- The repository reuse seam is typed, direct-table, active/superseded-only, and current-corpus-excluding.
- A current snapshot read exists for immutable rerun reconciliation and final authoritative validation.
- provenance-only changes preserve content and embedding-input hashes while persisting new provenance;
- no activation occurs in the importer;
- the migration, RPC, security, legacy, Planner, runtime, and frontend boundaries are explicit;
- the Render change is limited to JINA_API_KEY with sync:false;
- no unresolved work markers or conflicting ownership wording remains.

This document is the complete Stage 10B-4 design specification. It does not create an implementation plan, production implementation, test file, migration, deployment, or online evidence.
