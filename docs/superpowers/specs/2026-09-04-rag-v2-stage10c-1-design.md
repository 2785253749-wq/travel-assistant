# RAG V2 Stage 10C-1 Live Acceptance Design

## 1. Status and Purpose

This document defines Stage 10C-1, the controlled live-environment acceptance stage after the completed Stage 10B repository/local contracts. Implementation has not started.

Stage 10C-1 proves the real RAG V2 data path with the intended Supabase project, PostgreSQL/pgvector, Render configuration, and Jina provider. It prepares and validates a production corpus, performs retrieval acceptance, and leaves activation as an explicit operator action.

Stage 10C-1 does not connect RAG V2 to the Planner, request runtime, API routes, or frontend. Those responsibilities belong to Stage 10C-2.

## 2. Graduation-Project Scope

The design optimizes for correctness, demonstrability, repeatability, clear ownership, easy debugging, and reasonable thesis implementation effort. It uses a small local operator workflow, deterministic reports, a focused acceptance case set, and existing Stage 10B seams.

The workflow is intentionally not an enterprise release platform. It does not introduce environment fingerprint governance, approval workflow engines, background orchestration, automatic rollback, audit infrastructure, complex secret-management abstractions, or a public acceptance endpoint.

## 3. Current Stage 10B Baseline

Stage 10B-4 has completed repository/local-contract verification. Its important boundaries are frozen:

- `RagV2Importer.import_corpus(...)` imports and validates a staging corpus and never activates it.
- `RagV2Repository` owns typed Supabase persistence and the existing activation/candidate RPC wrappers.
- `JinaPassageEmbedder` owns fixed `retrieval.passage` calls using `jina-embeddings-v3` and 1024 dimensions.
- `JinaQueryEmbedder` owns fixed `retrieval.query` calls using 1024 dimensions.
- `RetrievalService` owns query-time selection, thresholding, deduplication, diversity, and final-k behavior.
- `evaluate_case` and `evaluate_cases` provide deterministic offline evidence checks.
- `014_rag_v2.sql` is deployed by an operator and is not changed by this stage.
- The legacy `app/rag` path remains independent.

The Stage 10B tests did not prove real Supabase, real pgvector, Render Dashboard configuration, real Jina calls, corpus import, activation, online retrieval, or runtime integration. Those are the live acceptance claims addressed here.

## 4. Goals

Stage 10C-1 will:

1. verify required environment state without exposing secrets;
2. verify the live RAG V2 schema and existing RPC prerequisites without executing DDL;
3. prove one small real smoke chain from authoring through retrieval;
4. import the official three-destination authoring source as a production staging corpus;
5. prove same-manifest idempotent rerun behavior;
6. prove controlled incremental reuse and selective re-embedding on an isolated acceptance dataset;
7. run a small versioned retrieval acceptance case set;
8. present an explicit activation preview;
9. activate only after explicit operator confirmation; and
10. run a representative post-activation retrieval smoke.

## 5. Non-Goals

This stage does not:

- modify `014_rag_v2.sql`, create `015_rag_v2.sql`, or run migrations;
- alter legacy tables, `knowledge_chunks`, `match_knowledge_chunks`, or legacy content;
- add a public HTTP endpoint or expose acceptance commands through the web service;
- add Planner, runtime, API, frontend, background-job, or CLI wiring to normal requests;
- add runtime feature flags or change the existing route selection;
- automatically activate, rollback, retry, or repair a corpus;
- claim production retrieval quality from a small case set;
- claim that a Render manifest declaration proves a Dashboard secret value exists; or
- complete Stage 10C-2.

## 6. High-Level Architecture

The operator workflow has two small layers:

```text
app/scripts/rag_v2_acceptance.py
    argparse dispatch, safe terminal output, exit codes

app/rag_v2/acceptance.py
    testable live orchestration, case/report contracts, command guards
```

The CLI is a thin adapter. It does not duplicate authoring parsing, chunking, hashing, importing, embedding, retrieval, or evaluation. `acceptance.py` composes the existing owners and coordinates command-specific sequencing.

If parsing responsibilities make `acceptance.py` too large, the implementation may add one module-local `app/rag_v2/authoring.py` owner for YAML parsing and conversion to `CorpusImportInput`. It must remain a pure authoring adapter and must not become a second importer, hash owner, chunker, or repository.

No new root exports are required. New acceptance and authoring APIs remain module-local.

## 7. Data and Dataset Isolation

Three documented dataset keys are separate:

| Role | Dataset key | Use | Production activation candidate |
| --- | --- | --- | --- |
| smoke | `rag-v2-smoke` | Tiny real chain check | Never |
| acceptance | `rag-v2-acceptance` | Full-source rerun and controlled incremental check | Never as official production |
| production | `rag-v2-production` | Official authoring, retrieval gate, final activation | Yes |

The exact version labels are operator inputs recorded in each report. The keys are stable and simple; no environment fingerprint or namespace framework is added.

Production authoring is the only canonical real corpus source. Smoke and acceptance imports read the same production authoring when they need representative data; they do not maintain duplicate full-corpus copies. The acceptance incremental fixture is the only artificial content and is applied in memory.

The acceptance baseline may be explicitly activated within its own isolated dataset solely to create an `active` historical source for reuse testing. That activation is not production activation and is never performed automatically. If no active or superseded acceptance baseline exists, `incremental-check` fails clearly and instructs the operator to establish one through the normal explicit activation command.

## 8. V2 Authoring Schema

Production authoring is added under:

```text
app/rag_v2/content/production/
    xiamen.yaml
    fuzhou.yaml
    dali.yaml
```

Each file uses `schema_version: rag-v2-authoring-v1` and maps directly to existing Stage 10B models. The authoring shape is:

```yaml
schema_version: rag-v2-authoring-v1
destination:
  destination_code: "350200"
  destination_level: prefecture_city
  destination_name: 厦门市
  province_code: "350000"
  province_name: 福建省

attractions:
  - registry_key: xiamen.example-attraction
    attraction_id: "00000000-0000-4000-8000-000000000001"
    canonical_name: 示例景点
    aliases: [别名]
    category: 景区
    tags: [海滨]
    status: included
    sections:
      overview:
        content: "示例景点概览文本"
        source_label: "示例来源"
        source_url: "https://example.org/attractions/example"
        source_type: official
        reviewed_on: "2026-09-01"
      highlights:
        content: "示例景点亮点文本"
        source_label: "示例来源"
        source_url: "https://example.org/attractions/example"
        source_type: official
        reviewed_on: "2026-09-01"
```

The remaining semantic sections are `transport`, `visit_advice`, and `seasonal`. Each included attraction supplies the five section keys unless an intentionally suppressed/zero-section input is being tested. `registry_key`, stable `attraction_id`, human metadata, section content, and section provenance are authoring fields.

The authoring adapter validates the schema version, destination profile, UUIDs, six-digit administrative codes, fixed destination levels, attraction metadata, unique registry keys, section identities, and structural HTTPS provenance. It constructs `AttractionVersionMetadata` and `SemanticSection` values and delegates canonical hashes and semantic chunking to existing Stage 10B owners.

The following are never hand-authored:

```text
chunk_key
content_hash
embedding_input_hash
metadata_hash
manifest_hash
embedding
final ChunkRow values
```

Legacy `app/rag/content/*.yaml` is not treated as V2 authoring and is not modified.

The initial production files contain only:

- Xiamen: `350200`, `prefecture_city`;
- Fuzhou: `350100`, `prefecture_city`; and
- Dali: `532900`, `autonomous_prefecture`.

No fourth destination or national corpus is included.

## 9. Incremental Acceptance Fixture

The small controlled patch is stored at:

```text
app/rag_v2/content/acceptance/incremental-v1.yaml
```

It identifies an existing acceptance attraction and one semantic section, for example `visit_advice`, with the intended replacement content. It does not contain a copied full corpus. The acceptance runner loads production authoring, applies this patch in memory, and passes the resulting next request to the existing importer.

The runner computes expected reuse and re-embedding from generated chunk keys and the exact embedding identity tuple. It does not assert a fixed number such as “reuse four, embed one”; a section may split into multiple chunks. Unchanged generated identities must be eligible for reuse from an active/superseded acceptance baseline, while changed canonical input identities must be embedded again. The patch version is never activated as production.

## 10. Retrieval Acceptance Cases

The fixed case set is:

```text
tests/rag_v2_acceptance/cases-v1.jsonl
tests/rag_v2_acceptance/README.md
```

The JSONL is small, versioned, and roughly 10–20 cases. Each record contains `case_id`, `required`, `match_mode`, `query`, destination expectations, attraction mappings, optional expected attraction IDs/chunk keys, `expect_no_answer`, and `require_source_urls`.

Supported modes are:

- `exact`: destination, attraction, expected chunk, duplicate protection, and source completeness are checked;
- `attraction`: destination, attraction, duplicate protection, and source completeness are checked without requiring a fixed chunk key; and
- `no-answer`: `expect_no_answer` is true and evidence must be empty.

The runner converts the case data to existing `EvaluationCase` values and uses `evaluate_case` where its contract is sufficient. Any mode-specific wrapper only supplies the missing expected-chunk policy; it does not rewrite the evaluator or add a ranking algorithm.

The initial required cases cover Xiamen, Fuzhou, and Dali destination questions; specific attractions; transport; visit advice; seasonality; cross-destination isolation; no-answer behavior; provenance; and duplicate-evidence protection. Optional cases may be observational but cannot hide a failed required case.

The report records the case-set identifier, SHA-256 of the case file, required count, passed required count, failed required count, and per-case evidence. A material semantic change creates `cases-v2.jsonl`; no benchmark governance system is added.

## 11. Acceptance CLI

The local operator CLI uses these subcommands:

```text
preflight
smoke
acceptance-import
incremental-check
production-import
retrieval-check
activation-preview
activate
post-activation-smoke
```

Each command has explicit dataset/version arguments where relevant and writes a local JSON report. Exit status is non-zero for a failed prerequisite, failed required case, non-ready corpus, or failed post-activation smoke.

Only the `activate` subcommand may call `RagV2Repository.activate_corpus(...)`. `preflight`, `smoke`, `acceptance-import`, `incremental-check`, `production-import`, `retrieval-check`, `activation-preview`, and `post-activation-smoke` must not call it.

## 12. Preflight

`preflight` performs quick operator checks and prints only state, never values:

```text
JINA_API_KEY: SET / MISSING
SUPABASE_URL: SET / MISSING
SUPABASE_SERVICE_KEY: SET / MISSING
```

It then performs a real Jina passage and query embedding sanity call, a real Supabase connectivity check, typed reads against the V2 tables, and a read-only candidate RPC probe through `RagV2Repository.match_chunks(...)`. A successful expected empty candidate result, or a stable typed response, distinguishes an available candidate boundary from an unavailable service. The check uses a harmless non-production dataset key and a valid 1024-vector; it does not mutate data.

The existing typed repository boundary has no non-mutating activation-RPC introspection method. Therefore preflight never invokes `activate_corpus` merely to probe availability. Activation RPC availability is verified at the explicit activation boundary after the operator has reviewed an authoritative preview; missing prerequisites fail before any activation state change. This preserves the rule that only `activate` may call the activation method and avoids a new generic SQL/RPC escape hatch.

Preflight does not execute DDL, migrations, or schema repairs. A missing table, RPC, pgvector prerequisite, or credential produces a clear prerequisite failure and blocks later commands.

Render Dashboard configuration is operator-verified separately after deployment configuration: the operator records only `JINA_API_KEY: SET/MISSING` and the required Supabase configuration states. The CLI never prints Render secret values, calls a public acceptance endpoint, or treats `render.yaml` as proof of Dashboard state. A normal redeploy is performed after changing Render environment variables.

## 13. Smoke Flow

`smoke` uses a tiny subset of the production authoring source under the smoke dataset key. It proves:

```text
authoring
→ SemanticChunker
→ JinaPassageEmbedder / retrieval.passage
→ Supabase staging persistence
→ authoritative readiness
→ JinaQueryEmbedder / retrieval.query
→ match_rag_v2_chunks
→ RetrievalService
→ evidence checks
```

Smoke remains staging and is never activated. It is intentionally small and is not evidence of production retrieval quality.

## 14. Acceptance and Incremental Flow

`acceptance-import` imports the full official authoring source under `rag-v2-acceptance`. The operator may rerun the exact same manifest. The rerun must demonstrate no duplicate logical rows and zero unnecessary passage-provider calls, using authoritative state and observed provider-call instrumentation rather than a hardcoded vector count.

`incremental-check` requires an active or superseded acceptance baseline, loads the controlled patch in memory, generates the next manifest, and imports it as staging. It compares generated identity sets and records which chunks reused exact historical vectors and which required new passage embeddings. It verifies the new version is ready, remains staging, and was not promoted automatically.

The incremental runner does not mark a source active to manufacture its own prerequisite. Baseline activation, when needed, is an explicit operator action against the isolated acceptance dataset.

## 15. Production Import Flow

`production-import` loads only the three production authoring files, builds the canonical importer request, and imports dataset `rag-v2-production` as staging. It uses the existing importer for identity resolution, chunking, hashes, reuse, embedding, persistence, and authoritative readiness.

The command must report:

```text
ready_for_activation=True
corpus.status=staging
```

before retrieval acceptance. It fails on incomplete provenance, pending/failed chunks, manifest mismatch, unsafe vectors, or any authoritative snapshot conflict. It never calls activation.

## 16. Retrieval Gate

`retrieval-check` runs the fixed case set against the production staging candidate using real query embedding, the existing repository match RPC, and the existing `RetrievalService`. It then applies the deterministic evaluator and mode-specific expected-chunk policy.

For every case the report includes:

```text
case_id
PASS / FAIL
reason
returned attraction IDs
returned chunk keys
```

Every required case must pass before production activation. This is a focused acceptance gate, not a precision/recall/MRR/NDCG benchmark and not a general claim about model quality.

## 17. Manual Activation

`activation-preview` freshly reads and displays a concise non-secret summary:

```text
dataset_key
version_label
corpus_version_id
manifest_hash
attraction count
chunk count
embedded count
pending count
failed count
ready_for_activation
current active corpus
candidate corpus
required acceptance PASS/FAIL summary
```

It does not invoke the activation RPC.

`activate` re-reads authoritative state and verifies `dataset_key`, `corpus_version_id`, `manifest_hash`, staging status, readiness, and the required-case summary. It asks for explicit confirmation equal to `ACTIVATE`. Only after that confirmation does it call the existing three-argument `activate_corpus(...)` method. It does not trust process memory from an earlier command and does not auto-activate merely because readiness is true.

Activation failure is reported with the existing safe repository error contract. There is no automatic retry or rollback.

## 18. Post-Activation Smoke

`post-activation-smoke` runs a small representative retrieval set against the now-active production dataset using real query embedding, candidate retrieval, `RetrievalService`, and source/evidence checks.

On success the report may state that Stage 10C-1 live acceptance succeeded. On failure it reports the failure, preserves the previous evidence, and does not automatically rollback. Manual inspection or restoration through the existing activation mechanics remains the operator’s responsibility.

## 19. Reporting and Secret Safety

Every command emits a concise terminal summary and a local machine-readable JSON report under an ignored local report directory. Reports include:

```text
timestamp and run identifier
command
dataset_key
version_label
corpus_version_id
manifest_hash
attraction/chunk counts
embedded/pending/failed counts
smoke result
incremental reuse/re-embedding result
retrieval case results
activation status when applicable
```

Reports never contain:

```text
JINA_API_KEY
Supabase service-role key
Authorization headers
full environment dumps
provider response bodies
raw secret-bearing exceptions
```

Only `SET` or `MISSING` state is recorded for credentials. Reports are local artifacts and are not committed. If the report directory is not already ignored, implementation adds one narrow ignore rule rather than a reporting framework.

## 20. Error Handling

Existing error ownership remains authoritative:

- missing credentials or configuration: preflight failure;
- Jina transport/provider/conversion failure: existing `RAG_V2_EMBEDDING_UNAVAILABLE` behavior;
- Supabase/row/RPC failure: existing repository `AppError` mapping without raw upstream text;
- schema/RPC prerequisite missing: clear acceptance prerequisite failure with no migration attempt;
- import not ready: block retrieval and activation;
- required case failure: block activation;
- activation failure: report failure without automatic rollback;
- post-activation smoke failure: report clearly without automatic rollback.

Acceptance-specific errors are minimal, local, and secret-safe. No second persistence, provider, hash, or lifecycle error system is introduced.

## 21. Testing Strategy

Normal unit and contract tests remain offline and must not require Jina, Supabase, Render, or network access. Proposed focused tests are:

```text
tests/unit/test_rag_v2_authoring.py
tests/unit/test_rag_v2_acceptance.py
tests/unit/test_rag_v2_acceptance_cli.py
```

They use fakes for importer, repository, passage/query embedders, and operator input. They cover:

- authoring schema parsing and validation;
- production/smoke/acceptance dataset selection;
- in-memory incremental patching and identity-derived expectations;
- JSONL case parsing and case-file hashing;
- exact, attraction, and no-answer matching;
- required-case gating;
- secret-safe report serialization;
- activation preview and explicit confirmation guards;
- the rule that non-`activate` commands cannot activate; and
- CLI argument dispatch and safe exit behavior.

Static contract tests may verify file ownership and absence of public endpoint/runtime wiring. They do not pretend to prove live database or provider behavior.

Live acceptance is deliberately invoked through the operator CLI, not pytest. No ordinary test collection depends on `JINA_API_KEY`, real Supabase, real Render, or network availability.

## 22. Live Acceptance Checklist

Stage 10C-1 is complete only when all of the following have raw operator evidence:

1. focused unit/contract tests for new 10C-1 code pass;
2. actual Render Dashboard `JINA_API_KEY` is confirmed `SET` without printing it;
3. required Render environment is configured and a normal redeploy is completed;
4. live Supabase RAG V2 schema, RPC, pgvector, and service-role prerequisites are verified;
5. a real Jina `retrieval.passage` call succeeds;
6. a real Jina `retrieval.query` call succeeds;
7. the smoke dataset imports and retrieves successfully;
8. the full acceptance dataset imports successfully;
9. an exact-manifest rerun proves no unnecessary passage re-embedding;
10. the controlled incremental variant proves identity-derived reuse and selective re-embedding;
11. the official production corpus imports as staging;
12. the production candidate reports `ready_for_activation=True`;
13. every required retrieval acceptance case passes;
14. a human reviews the activation preview;
15. explicit manual production activation succeeds; and
16. post-activation retrieval smoke succeeds.

Completion of this checklist does not claim Planner/runtime integration.

## 23. Stage 10C-2 Handoff

Stage 10C-2 owns the later runtime work:

- Planner grounding from RAG V2 evidence;
- runtime selection between legacy and V2 behavior;
- API/request integration and fallback behavior;
- frontend changes;
- runtime feature flags;
- Render-process RAG V2 request E2E; and
- production request-path activation assumptions.

None of these are implemented, tested as live behavior, or claimed by Stage 10C-1.

## 24. Frozen Decisions

- Activation is manual; only `activate` may call `activate_corpus`.
- `acceptance.py` orchestrates and does not reimplement importer, repository, embedding, retrieval, evaluation, hashing, or chunking.
- `app/scripts/rag_v2_acceptance.py` is a thin local operator CLI; no public endpoint is added.
- `014_rag_v2.sql` is deployed manually and never modified or executed by acceptance tooling.
- Smoke and acceptance datasets are isolated from official production activation; production authoring remains the sole canonical real source.
- The only real production destinations are Xiamen, Fuzhou, and Dali.
- The V2 authoring schema is `rag-v2-authoring-v1` and legacy authoring remains untouched.
- The controlled incremental patch is in-memory and never overwrites production authoring.
- Cases are a small versioned `cases-v1.jsonl` set with required-case gating and exact/attraction/no-answer modes.
- Reports are local, machine-readable, concise, and secret-safe.
- No enterprise release, audit, rollback, background orchestration, or secret-management platform is added.
- Passing live acceptance is not production-quality proof beyond the defined cases.
- Stage 10C-2 owns Planner/runtime/frontend/API integration.
