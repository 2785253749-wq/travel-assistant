# RAG V2 Stage 10B Final Design Specification

日期：2026-09-01
状态：Stage 10B-0 设计固化；本文件提交后等待人工审阅，不开始实现。

## 1. 目标、范围与已知问题

Stage 10B 只建设 RAG V2 的底层架构契约：稳定景点 identity、版本化 corpus、版本化景点元数据、景点级语义 chunks、Jina v3 向量 profile、Supabase PostgreSQL/pgvector 存储、增量导入判定、受保护的检索/激活 RPC，以及后续 Stage 10C 可执行的评测和部署契约。

Stage 10B 不服务线上用户，不改变现有 Planner、聊天或生产检索路径，不迁移现有三城数据，不执行真实 Jina/Supabase E2E，不校准线上参数，不激活 V2。

保留并兼容：Supabase、PostgreSQL、pgvector、Jina `jina-embeddings-v3`、1024 维向量、Attraction first-class entity、semantic attraction chunks、destination metadata prefilter、`retrieval.query`、`retrieval.passage`、staging corpus、validation、atomic activation、superseded corpus、incremental embedding，以及旧 `knowledge_chunks` / `match_knowledge_chunks`。

Stage 10B 的范围严格限于厦门、福州、大理相关的既有试点语义。禁止增加第四城市，禁止全国扩容，禁止在 Stage 10B 导入全国数据。

### 1.1 当前已经验证的问题

当前资料并不是三个真正的 city/destination-level corpus：

| 当前资料语义 | 实际 corpus 标识 | 风险 |
| --- | --- | --- |
| 厦门 | `厦门` | city-level，现状可保留但须迁入 V2 identity |
| 福州 | `福建` | 福建内容可能混合多个城市，存在 destination contamination |
| 大理 | `云南` | 云南内容可能混合昆明、丽江、大理，存在 destination contamination |

因此，V2 的 destination prefilter 必须使用结构化 code/level，而不能把省级资料假设为单一城市资料。Stage 10C 迁移时必须将厦门、福州、大理分别校准为真实的 destination corpus；Stage 10B 只提供承载和验证这些语义的底层结构。

当前 Planner 是“先生成 itinerary，再做 enrichment”。当前没有“RAG attraction retrieval → grounded candidates → Planner”的 retrieval-first grounding。Stage 10B 不修正这个行为，Planner grounding 属于 Stage 10C。

现有 deterministic offline RAG evaluation 使用离线 seam/fixture，不是真实 Jina + pgvector retrieval benchmark。离线 100% 不能解释为线上 recall 100%。

## 2. V2 架构与模块 seams

V2 的数据流设计如下：

```text
versioned source manifest
        │
        ▼
staging corpus ── validation ── identity/version/hash decision
        │                              │
        │                              ├─ reusable vector copy
        │                              └─ Jina passage embedding
        ▼
rag_attraction_versions + rag_attraction_chunks
        │
        ├─ transactional activation: staging → active
        └─ active-only retrieval RPC
                         │
question ── Jina query embedding ── metadata prefilter ── pgvector
                                      └─ threshold/dedup/diversity
```

未来实现必须以深模块为目标：调用方只了解少量稳定 interface，复杂的 canonicalization、hash、增量判定、事务状态和供应商响应校验集中在模块内部。每个外部 seam 由 adapter 实现，纯 identity/hash/chunking 模块不创建外部依赖，测试跨越与生产调用方相同的 interface。

建议的未来文件责任如下；本轮不创建这些文件：

| 未来模块 | 责任 | 外部 seam |
| --- | --- | --- |
| `app/rag_v2/models.py` | corpus、attraction、version、chunk、filter、retrieval result 的严格领域模型 | 无外部副作用 |
| `app/rag_v2/identity.py` | stable attraction allocation、rename、merge、explicit retirement | entity registry adapter |
| `app/rag_v2/hashing.py` | content、embedding input、metadata 的 canonical serialization 和 SHA-256 | 无外部副作用 |
| `app/rag_v2/chunking.py` | semantic section 组织和 sentence-aware split | 无外部副作用 |
| `app/rag_v2/incremental.py` | 新旧 corpus 比较和 embedding reuse/recompute decision | 只接收已规范化模型 |
| `app/rag_v2/repository.py` | staging、version row、chunk upsert、active retrieval、activation | Supabase service-role adapter |
| 现有 `app/rag/embedding.py` 的兼容扩展 | Jina transport 的 query/passage task、profile 和响应校验 | `JinaEmbeddingTransport` |
| `app/rag_v2/retrieval.py` | query embedding、RPC 调用、threshold、content dedup、attraction diversity | repository + embedding interfaces |

Stage 10B 不把 V2 接入 `app/composition.py`，不增加 runtime flag，不增加 Planner 分支。V2 interface 的存在不等于线上 consumer 的存在。

## 3. Corpus lifecycle

一个 `dataset_key` 代表一条可独立激活的 corpus 轨道，例如未来可使用 `travel-attractions-cn`；`version_label` 代表该轨道中的一个不可变资料版本；`manifest_hash` 代表该版本输入 manifest 的 canonical SHA-256。

导入顺序固定为：创建 `staging` 版本 → 写入并验证 attraction versions/chunks → 完成 hash/profile/来源检查 → 将失败版本标记为 `failed` 或执行原子激活。任何校验失败都不得部分激活。

同一个 `dataset_key` 同时最多有一个 `active` corpus。数据库使用 partial unique index `unique (dataset_key) where status = 'active'` 保证该不变量；应用层不得用先查后写替代约束。

激活锁定同一 `dataset_key` 的状态行，在一个数据库事务内完成：当前 `active` → `superseded`，目标 `staging` → `active`，设置 `activated_at` 和 `superseded_at`。任一更新、校验或约束失败，整个 transaction rollback；不得出现新旧都 active 或两者都被错误标记的中间提交。

`superseded` corpus 保留完整数据，供审计、回滚诊断和增量比较使用，但 active retrieval 永远不读取 superseded 或 failed corpus。

## 4. Database model

V2 只采用以下四张业务表。所有 ID/code 都按本节明确的类型和 identity 使用，不能以未定义的等价键替代。

### 4.1 `rag_corpus_versions`

职责是 dataset version lifecycle。主键固定为 `corpus_version_id uuid primary key`，由数据库生成或由导入器预分配；它不是资料内容 hash。

字段：

- `corpus_version_id uuid primary key`
- `dataset_key text not null`
- `version_label text not null`
- `manifest_hash text not null`
- `status text not null check (status in ('staging', 'active', 'superseded', 'failed'))`
- `created_at timestamptz not null`
- `activated_at timestamptz null`
- `superseded_at timestamptz null`

唯一约束为 `(dataset_key, version_label)`；`manifest_hash` 使用 lowercase SHA-256 hex。`status = 'active'` 的每个 dataset_key 由 partial unique index 限制为最多一行。状态时间的语义固定为：staging 只有 `created_at`，active 必须有 `activated_at`，superseded 必须有 `superseded_at`，failed 不得被 active RPC 选中。

### 4.2 `rag_attractions`

该表只保存稳定业务 entity identity，不保存任何 corpus-specific metadata 或正文。

字段：

- `attraction_id uuid primary key`
- `lifecycle_status text not null check (lifecycle_status in ('active', 'retired', 'merged'))`
- `created_at timestamptz not null`
- `retired_at timestamptz null`
- `merged_into_attraction_id uuid null references rag_attractions(attraction_id)`

该表禁止保存 `canonical_name`、`aliases`、destination、category、tags、coordinates、content、source、content_hash。稳定 ID 由显式 entity registry/allocation 产生，不能简单由当前景点名称 hash 产生。

景点改名只新增或更新其 versioned metadata，`attraction_id` 不变。景点 merge 将来源 entity 标记为 `merged`，写入 `merged_into_attraction_id` 指向保留 entity；目标 entity 保持自己的 stable ID。景点真正 retired 只能通过显式 lifecycle 操作写入 `retired` 和 `retired_at`。

一个 attraction 从新 corpus 中消失，只表示它没有出现在该 `corpus_version_id` 的 version rows 中，不表示 `lifecycle_status = 'retired'`。corpus absence 与 entity lifecycle 永远分离。

### 4.3 `rag_attraction_versions`

该表保存某个 corpus 中某个 stable attraction 的完整 versioned metadata。

主键和唯一 identity 固定为 `(corpus_version_id, attraction_id)`，并分别引用 `rag_corpus_versions` 和 `rag_attractions`。字段为：

- `corpus_version_id uuid not null`
- `attraction_id uuid not null`
- `canonical_name text not null`
- `aliases jsonb not null`
- `destination_code text not null`
- `destination_level text not null`
- `destination_name text not null`
- `province_code text not null`
- `province_name text not null`
- `district_name text null`
- `category text null`
- `tags jsonb not null`
- `latitude numeric null`
- `longitude numeric null`
- `status text not null check (status in ('included', 'suppressed'))`
- `metadata_hash text not null`

只有 `status = 'included'` 且其 corpus 为 active 的 rows 可被 retrieval RPC 使用。`aliases` 和 `tags` 的存储顺序不具有语义；hash 时按第 7 节排序。`status = 'suppressed'` 仍属于该版本，可用于审核，但不产生检索候选。

### 4.4 `rag_attraction_chunks`

一个 chunk 只能属于一个 attraction，并且必须属于同一 corpus 的 attraction version。通过复合 foreign key `(corpus_version_id, attraction_id)` 引用 `rag_attraction_versions(corpus_version_id, attraction_id)`，避免跨 corpus 归属。

主键和 logical storage identity 固定为 `(corpus_version_id, chunk_key)`。在此基础上增加唯一约束 `(corpus_version_id, attraction_id, chunk_type, ordinal)`，防止同一 section 的 ordinal 冲突。

字段：

- `corpus_version_id uuid not null`
- `attraction_id uuid not null`
- `chunk_key text not null`
- `chunk_type text not null check (chunk_type in ('overview', 'highlights', 'transport', 'visit_advice', 'seasonal'))`
- `ordinal integer not null check (ordinal >= 0)`
- `content text not null`
- `content_hash text not null`
- `embedding_input_hash text not null`
- `embedding_input_schema_version text not null`
- `source_label text not null`
- `source_url text not null`
- `source_type text not null`
- `reviewed_on date not null`
- `embedding_model text not null`
- `embedding_task text not null check (embedding_task = 'retrieval.passage')`
- `embedding_dimensions integer not null check (embedding_dimensions = 1024)`
- `embedding vector(1024) null`
- `status text not null check (status in ('pending', 'embedded', 'failed', 'excluded'))`

`status = 'embedded'` 必须同时有非空 embedding，`pending`/`failed`/`excluded` 不得进入 active retrieval。`source_url` 是实际内容来源页面，不能根据 `source_label` 猜省级根 URL。Stage 10B 不设计 multi-source graph；每个 chunk 保持一个明确 source label、URL、type 和 reviewed date。

## 5. Destination identity

V2 不定义 `city_code` 作为唯一概念，统一使用：

- `destination_code text`
- `destination_level text`
- `destination_name text`

同时保留：

- `province_code text`
- `province_name text`

当前真实需求的固定 mapping 为：

| Destination | Code | Level |
| --- | --- | --- |
| 厦门 | `350200` | `prefecture_city` |
| 福州 | `350100` | `prefecture_city` |
| 大理 | `532900` | `autonomous_prefecture` |
| 福建 | `350000` | `province` |
| 云南 | `530000` | `province` |

所有 code 都是 string，保留前导零的能力。当前 `destination_level` 只实现真实需要的行政层级：`province`、`prefecture_city`、`autonomous_prefecture`、`county_city`。Stage 10B 不加入没有真实 consumer 的 `tourism_region`。Stage 10B 不改变当前产品对“大理”的范围语义；大理仍按 `532900` 的 autonomous prefecture 语义建模，真实城市覆盖在 Stage 10C 校准。

destination filter 的匹配是 exact code + exact level；province filter 是 exact province code；两者同时存在时使用 AND。没有 destination filter 时不得把 province corpus 自动解释成某个城市 corpus。

## 6. Stable attraction and chunk identity

### 6.1 Stable attraction identity

`attraction_id` 是跨 corpus、跨名称版本的业务 identity。导入器需要从显式 registry 或人工确认的 identity mapping 取得它；名称、别名、所在省份和正文都不能单独生成 stable ID。

- 改名：保留 `attraction_id`，只更新 `rag_attraction_versions`。
- merge：来源 ID 保留历史记录，`lifecycle_status = 'merged'`，`merged_into_attraction_id` 指向目标 ID；后续 corpus 使用目标 ID。
- retired：只由显式 lifecycle command 执行；新 corpus 缺少 attraction 不触发 retired。

### 6.2 `chunk_key`

`chunk_key` 是 logical identity，不是正文 identity。格式固定为：

```text
rag-v2-chunk-key-v1|<attraction_id>|<chunk_type>|<ordinal>
```

字段顺序、ASCII `|` separator、UTF-8 encoding 固定。它基于 chunk-key schema version、stable attraction ID、chunk type 和 ordinal；正文变化不能改变 logical identity。

ordinal 在 `attraction_id + chunk_type` 范围内独立从 `0` 编号：`overview:0`、`overview:1`、`transport:0` 可以同时存在。transport 增减不能重新编号 overview。若 section 中间删除一个 chunk，后续 ordinal 保持其原 logical identity；重新组织 section 是新的 chunk-key schema 或显式内容重排变更，而不是隐式全量重编号。

### 6.3 `content_hash`

先得到唯一的 normalized content：

1. Unicode NFKC。
2. 将 CRLF 和 CR 统一为 LF。
3. 对每一行 trim；连续 horizontal whitespace 归一为一个 ASCII space。
4. 删除空行；以单个 LF 连接非空行。
5. 对整体 trim。
6. 使用 UTF-8 编码后计算 SHA-256，保存 lowercase hex。

同一 normalized content 必须始终产生相同 `content_hash`；database 返回顺序和普通 dict 顺序不得参与计算。

### 6.4 `embedding_input_hash`

embedding input schema 固定为 `rag-v2-embedding-input-v1`。真正发送给 Jina 的完整 `embedding_text` 是以下 canonical JSON 的 UTF-8 文本，不能在发送前再添加或删除字段：

```json
{"schema_version":"rag-v2-embedding-input-v1","canonical_attraction_name":"...","destination_name":"...","destination_code":"...","destination_level":"...","chunk_type":"...","normalized_content":"..."}
```

key 顺序固定为示例顺序，separator 固定为 `,` 和 `:`，`ensure_ascii = false`，不带 trailing newline；字符串使用 JSON escaping。`normalized_content` 使用第 6.3 节结果。`embedding_input_hash` 就是这份完整 embedding_text 的 UTF-8 SHA-256。存储值、hash 输入和 Jina request 的 `input` 值必须字节级一致。

canonical attraction name、destination name/code/level、chunk type 或 normalized content 的任一变化都会改变 embedding input；aliases、province、district、category、tags、coordinates 和 source provenance 不进入该 text。

### 6.5 `metadata_hash`

metadata hash 使用固定 key 集合：

```text
canonical_name
aliases
destination_code
destination_level
destination_name
province_code
province_name
district_name
category
tags
latitude
longitude
status
```

计算规则固定为：字符串先执行 Unicode NFKC、newline normalization、trim 和 whitespace normalization；aliases 去重后按 normalized value ascending 排序；tags 同样去重、normalize、排序；latitude/longitude 缺失为 JSON null，存在时按固定 7 位小数的 canonical decimal string 序列化；key 使用固定顺序；JSON 使用 UTF-8、`ensure_ascii = false`、compact separators；最后计算 SHA-256 lowercase hex。该 canonical serialization 不依赖普通 dict/database 返回顺序。

## 7. Semantic chunking and provenance

每个 attraction 的 section 顺序固定为：`overview`、`highlights`、`transport`、`visit_advice`、`seasonal`。每个 chunk 只包含一个 attraction 的一个 semantic section，绝不把两个景点写入同一 chunk。

section 先按标题、段落和语义边界组成。只有 section 超过 Stage 10B 固定的 4000 Unicode code-point chunk budget 时才 split；split 顺序为段落边界、中文/英文句末标点、分句边界、空白边界。单个过长句子才允许在最近的 clause/whitespace 边界继续切分。不得把简单的 `1200 chars hard slice` 作为主 chunking 方法，也不得用它作为无条件 fallback。

每个 chunk 的 provenance 必须同时有实际来源页面的 `source_label`、`source_url`、`source_type`、`reviewed_on`。一个 chunk 只有一个明确来源；复杂来源图谱留待真实需求出现后再设计。

## 8. Incremental update decision table

增量决策以 stable attraction identity 为第一匹配键，再比较以下 profile：metadata_hash、chunk_key、content_hash、embedding_input_hash、embedding model、embedding task、embedding dimensions、embedding input schema version。

| 情形 | 比较结果 | 行为 | Jina |
| --- | --- | --- | --- |
| unchanged | stable ID、chunk_key、content_hash、metadata/hash 和 embedding profile 全部相同 | 将旧 active/superseded row 的 vector/provenance 按新 corpus 写入，保持新版本 identity | 禁止调用 |
| provenance-only change | source_url/source_label/source_type/reviewed_on 改变，但 embedding text 和 profile 不变 | 写新 version row；更新 provenance/hash 所需字段 | 禁止调用 |
| metadata-only change | aliases、province、district、category、tags、coordinates 或 status 改变，且不进入 embedding text | 写新 metadata/version row，复用 vector | 禁止调用 |
| embedding-relevant metadata change | canonical name、destination name/code/level 改变，导致 embedding_input_hash 改变 | 新 row 进入 pending，成功后写新 passage vector | 必须调用 |
| content changed | normalized content 或 content_hash 改变 | 新 row 进入 pending，成功后写新 passage vector | 必须调用 |
| embedding profile changed | model、task、dimensions 或 embedding input schema version 改变 | 所有受影响 chunks 视为 pending；不复用旧 profile vector | 必须调用 |
| new chunk | 新的 chunk_key 没有旧匹配 | 写新 pending row | 必须调用 |
| removed chunk | 旧 chunk_key 不在新 corpus | 新 corpus 不写该 chunk；旧 active version 保留为 superseded | 不调用 |
| new attraction | registry 分配 stable ID，存在新 version/chunks | 写 stable entity、version、chunks | chunks 必须调用 |
| attraction absent from new corpus | stable entity 仍存在，但该 corpus 没有 version row | 只从当前 corpus 排除；不更新 lifecycle | 不调用 |
| explicitly retired attraction | lifecycle command 明确 retired | 更新 stable entity 的 lifecycle 和 retired_at；新 corpus 排除 | 不因 retirement 调用 |
| merged attraction | lifecycle command 明确 source → target | source 标记 merged 并写 merged_into_attraction_id；后续版本使用 target | 仅 target 的新/变更 chunks 调用 |

增量复制只允许从已验证的相同 embedding profile 复制。任何 profile 或 embedding_input_hash 不一致都必须重新 embedding。新的 staging corpus 在全部必需 vectors 和校验完成前不能 active。

## 9. Jina embedding contract

V2 固定使用：

- model：`jina-embeddings-v3`
- dimensions：`1024`
- document task：`retrieval.passage`
- query task：`retrieval.query`

chunk row 永远保存 passage profile；query vector 是一次 retrieval 请求的临时值，不写入 chunk table。旧 `knowledge_chunks.embedding` 与 V2 `rag_attraction_chunks.embedding` 物理隔离、profile 隔离、RPC 隔离，不允许混合排序或互相复用。

未来 Stage 10B-3 如修改 `JinaEmbeddingTransport`，必须保留现有调用兼容：旧调用的默认行为、Bearer header、响应 index 顺序检查、1024 维检查、非有限数拒绝、`RagUnavailable` failure semantics 和日志脱敏都不能被破坏。真实 Jina 调用留到 Stage 10C controlled E2E。

## 10. Retrieval V2 contract

逻辑流程固定为：

```text
question
  → destination / attraction resolution
  → Jina query embedding(task = retrieval.query)
  → active corpus for dataset_key
  → destination/province/attraction metadata prefilter
  → pgvector candidate retrieval
  → score threshold
  → content_hash deduplication
  → attraction diversity selection
  → final evidence chunks
```

初始参数固定为：

- `candidate_k = 40`
- `final_k = 6`
- `threshold = 0.70`

这三个值必须标记为 `UNVALIDATED DEFAULT`。它们不是生产质量结论，Stage 10C 的 frozen corpus + frozen queries + real Jina + real pgvector E2E 后才可校准。

候选排序先按 cosine score descending，再按 `attraction_id` ascending、`chunk_key` ascending 做 deterministic tie-break。threshold 过滤 score `< 0.70` 的候选。content hash dedup 每个 normalized content 只保留最高分一条。attraction diversity 以最高分顺序选择，默认同一 attraction 只贡献一个 final evidence chunk；若候选不足，再按分数顺序补足而不跨 active corpus。

destination filter、province filter、attraction filter 都在 vector distance 前作为 metadata prefilter；过滤条件同时存在时使用 AND。查询只允许命中 `status = 'active'` 的 corpus、`included` attraction version 和 `embedded` chunk。没有 active corpus、没有足够分数、embedding 失败或 RPC 失败都返回 safe empty/refusal result，不回退到旧 V2 混合检索。

V2 不使用 LLM reranker。Planner grounding 不是本阶段的 consumer。

## 11. SQL, RLS and RPC contract

四张 V2 业务表全部启用 RLS。对 `public`、`anon`、`authenticated` 撤销直接读取、插入、更新、删除权限；只有后端 `service_role` 通过受控 repository/RPC 使用这些表。RLS 不得被误解为允许匿名调用 retrieval RPC。

### 11.1 Retrieval RPC

未来 SQL migration 固定提供以下受保护函数 interface：

```text
match_rag_v2_chunks(
  p_dataset_key text,
  p_query_embedding vector(1024),
  p_destination_code text default null,
  p_destination_level text default null,
  p_province_code text default null,
  p_attraction_id uuid default null,
  p_candidate_k integer default 40
) -> rows(
  corpus_version_id uuid,
  attraction_id uuid,
  chunk_key text,
  chunk_type text,
  content text,
  content_hash text,
  source_label text,
  source_url text,
  source_type text,
  reviewed_on date,
  score real
)
```

RPC 内部只解析 `dataset_key` 的唯一 active corpus，并在 vector distance 前 join version metadata 执行 filters。`p_candidate_k` 必须为正整数，调用方初始传 40，数据库设置安全上限 100；函数不读取 superseded、staging、failed corpus。匿名和 authenticated 没有 execute 权限；只有 service_role 可执行。

### 11.2 Activation RPC

未来 SQL migration 固定提供：

```text
activate_rag_v2_corpus(
  p_dataset_key text,
  p_corpus_version_id uuid
) -> void
```

函数必须锁定该 dataset 的 corpus lifecycle rows，确认目标是 `staging`、manifest/rows/profile 校验已完成，然后在单事务内执行 old active → superseded、new staging → active，并设置对应 timestamps。函数失败抛出错误并 rollback 全部变更。只有 service_role 可 execute。

### 11.3 Legacy compatibility

Stage 10B 不修改 `knowledge_chunks`、`match_knowledge_chunks` 或其既有 migration/RLS/grants。旧表继续服务旧 production retrieval；V2 新表和新 RPC 不向旧接口写数据，也不从旧向量建立 V2 evidence。

## 12. Stage 10B runtime boundary

Stage 10B 不增加 `RAG_V2_ENABLED`，不增加 compatibility runtime branching，不增加 Planner wiring。因为当前没有 V2 online runtime consumer，无意义的 feature flag 会制造未验证状态，因此不设计。

Stage 10B 不修改：

- `/api/chat`
- `SafeTravelAgent`
- Planner
- `app/composition.py`
- `KnowledgeAnswerService`
- existing production retrieval path

Stage 10C 才允许将 retrieval-first grounded candidates 接入 Planner，并验证用户行为、拒答和来源展示。

## 13. Render deployment gate

当前旧 RAG 已依赖 `JINA_API_KEY`，但当前 `render.yaml` 缺少该变量声明。Stage 10B-4 才修改 Render manifest，使用 secret-safe 声明：

```yaml
- key: JINA_API_KEY
  sync: false
```

绝不把真实 secret 写进仓库、spec、日志、测试 fixture、前端或 commit。Stage 10B 不加入没有 runtime consumer 的 V2 Render 参数。

manifest 中存在 `JINA_API_KEY` 不等于 Render Dashboard 已配置真实值。未来 online acceptance 必须依次完成：

1. Render Dashboard environment verification。
2. 确认 variable name 为 `JINA_API_KEY`。
3. 确认 value non-empty，但不打印、不复制、不回显 secret。
4. redeploy。
5. 执行 real online RAG smoke，确认真实 Jina、Supabase/pgvector、active corpus 和 safe failure。

## 14. Testing and evidence strategy

### 14.1 Deterministic CI

Stage 10B/10B-1 至 10B-5 的 deterministic CI 验证：models、stable identity、rename/merge/retirement semantics、canonical hashes、semantic chunking、repository behavior、destination/province/attraction filters、content dedup、attraction diversity、staging state、activation rollback、safe failures、RLS/RPC contract 和 legacy isolation。

不得真实调用 Jina、Supabase、Baidu、AMap、Juhe 或 DeepSeek。使用 deterministic fake adapter/fixture 验证 interface，不把 fake provider 的结果称为生产 retrieval quality。

### 14.2 Controlled real E2E（Stage 10C）

Stage 10C 使用 frozen corpus、frozen queries、真实 Jina `retrieval.query/passage`、真实 Supabase pgvector，记录 measured Top1/Recall@5、destination contamination、来源完整性、no-answer、duplicate rate 和 critical wrong-destination。该证据必须保存 query/corpus/profile/threshold 版本，才能复现。

### 14.3 Render online smoke（Stage 10C）

部署后通过真实 Render Dashboard 配置和线上 endpoint 验证 secret contract、Jina 调用、active corpus、retrieval evidence、拒答和回滚行为。Render online smoke 不能由 deterministic CI 或 controlled E2E 替代。

三种证据不能互相替代：离线 seam 证明代码契约，controlled E2E 证明真实 retrieval quality，Render smoke 证明部署环境行为。

当前 baseline 由用户本机 PowerShell 验证：Python 3.13.15；pytest `1307 passed, 7 skipped, 0 failed`；frontend `145 passed, 0 failed`；`scripts/verify_public_repo.ps1` passed；`git diff --check` passed；working tree clean。Codex sandbox 无法启动 Windows Python 进程，因此本轮不重复执行 Python baseline，也不把用户提供的 baseline 与本地未执行结果混淆。

## 15. Stage 10C quality targets

Stage 10C acceptance targets 固定为：

- specific attraction Top1 ≥ 95%
- Recall@5 ≥ 90%
- destination contamination ≤ 2%
- source completeness = 100%
- no-answer accuracy ≥ 95%
- duplicate result rate ≤ 5%
- critical wrong-destination = 0

这些是 Stage 10C real retrieval acceptance targets。Stage 10B 的 deterministic offline tests、现有离线 100% 指标和 schema contract 都不能证明 production retrieval quality 已达到上述目标。

## 16. Stage breakdown and file scope

### Stage 10B-0：Baseline + final design

已完成 baseline 证据收集；本次只提交本 design spec。不得修改 Python、SQL、tests、YAML、`.env`、`.env.example`、`render.yaml`、README、deployment docs 或 application code。

### Stage 10B-1：Domain models, identity, hashing, semantic chunking

实现严格 models、stable identity registry seam、三类 canonical hash、五种 semantic sections、sentence-aware split 和增量 decision table 的纯逻辑。为每个 interface 添加 deterministic unit coverage；不接线上 consumer。

### Stage 10B-2：SQL schema, RPC, repository

新增四表 migration、constraints、indexes、RLS/grants、retrieval RPC、activation RPC 和 service-role repository adapter；保留旧 `knowledge_chunks`/`match_knowledge_chunks` 不变。覆盖 staging、failed、activation rollback、active-only retrieval 和 legacy isolation。

### Stage 10B-3：Jina query/passage and Retrieval V2

扩展兼容的 Jina transport，分别发送 `retrieval.passage` 和 `retrieval.query`，实现 profile validation、prefilter、candidate_k、threshold、content dedup 和 attraction diversity。默认参数保持 `UNVALIDATED DEFAULT`，不接 Planner。

### Stage 10B-4：Incremental importer, offline evaluation, deployment contract/docs

实现 manifest/version staging、identity mapping、reuse/re-embed decision、atomic activation preparation、deterministic offline evaluation 和 deployment contract。此阶段才将 `JINA_API_KEY` 以 `sync: false` 添加到 `render.yaml`；仍不写真实 secret，不做 Render Dashboard 假设。

### Stage 10B-5：Full regression, security, readiness review

运行全量 regression、public-repo secret scan、RLS/RPC security review、legacy isolation review、scope review 和 rollback readiness review。未通过不得进入真实迁移。

### Stage 10C：三城真实迁移与线上接入

只迁移厦门、福州、大理；执行真实 Jina + pgvector E2E、参数 calibration、staging validation、activation、Planner grounding、Render Dashboard verification 和 Render online RAG E2E。Stage 10C 也不增加第四城市或全国导入，除非另有单独批准的范围变更。

## 17. Final self-review

- 未解析 placeholder：0。
- 四表的 primary/unique identity 已固定；`rag_corpus_versions` 为 `corpus_version_id` 主键并以 `(dataset_key, version_label)` 唯一，`rag_attraction_versions` 为 `(corpus_version_id, attraction_id)` 主键，`rag_attraction_chunks` 为 `(corpus_version_id, chunk_key)` 主键并有 section ordinal 唯一约束。
- lifecycle 已固定：corpus 缺失不触发 retired；retired 和 merged 只能由显式 lifecycle 操作产生；merge 必须写 `merged_into_attraction_id`。
- V2 destination identity 只使用 `destination_code + destination_level + destination_name`，全部 code 为 string；没有将省级 corpus 当作城市 corpus 的隐式 fallback。
- 未实现无真实 consumer 的 `tourism_region`。
- 离线测试、controlled real E2E、Render online smoke 的证据边界已分离；离线指标不代表线上质量。
- Render manifest 声明与 Dashboard secret 配置已分离；`JINA_API_KEY` manifest change 明确推迟到 Stage 10B-4。
- Stage 10B 没有第四城市、全国导入、Planner 用户行为变更或现有 production retrieval 改动。
