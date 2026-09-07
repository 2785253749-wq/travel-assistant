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
| `app/rag_v2/repository.py` | staging、version snapshot/chunk persistence、active retrieval candidate access、activation | Supabase service-role adapter |
| 现有 `app/rag/embedding.py` 的兼容扩展 | Jina transport 的 query/passage task、profile 和响应校验 | `JinaEmbeddingTransport` |
| `app/rag_v2/retrieval.py` | query embedding、RPC 调用、threshold、content dedup、attraction diversity | repository + embedding interfaces |

Stage 10B 不把 V2 接入 `app/composition.py`，不增加 runtime flag，不增加 Planner 分支。V2 interface 的存在不等于线上 consumer 的存在。

SemanticChunk 的严格领域模型归属为 `app/rag_v2/models.py`；SemanticChunker、chunk_key_for 和所有 semantic splitting 行为归属为 `app/rag_v2/chunking.py`。

## 3. Corpus lifecycle

一个 `dataset_key` 代表一条可独立激活的 corpus 轨道，例如未来可使用 `travel-attractions-cn`；`version_label` 代表该轨道中的一个不可变资料版本；`manifest_hash` 代表该版本输入 manifest 的 canonical SHA-256。

### 3.1 `manifest_hash` canonicalization

manifest hash schema version 固定为 `rag-v2-manifest-v1`。最终 canonical logical object 固定为以下结构，key 顺序也固定为示例顺序：

```text
{
  schema_version,
  dataset_key,
  embedding_profile: {
    model,
    task,
    dimensions,
    input_schema_version
  },
  attractions: [
    {
      attraction_id,
      metadata_hash,
      chunks: [
        {
          chunk_key,
          chunk_type,
          ordinal,
          content_hash,
          embedding_input_hash,
          source_label,
          source_url,
          source_type,
          reviewed_on
        }
      ]
    }
  ]
}
```

top-level 必须是 `schema_version = rag-v2-manifest-v1`、`dataset_key`、`embedding_profile` 和 `attractions`。`embedding_profile` 固定进入 manifest identity，document corpus 的值为 `model = jina-embeddings-v3`、`task = retrieval.passage`、`dimensions = 1024`、`input_schema_version = rag-v2-embedding-input-v1`。相同资料使用不同 embedding profile 必须得到不同 manifest_hash。

每个 attraction entry 只包含 `attraction_id`、`metadata_hash` 和 `chunks`，不重复展开全部 versioned metadata；attractions 按 `attraction_id ASC` 排序。每个 chunk entry 最终固定包含 `chunk_key`、`chunk_type`、`ordinal`、`content_hash`、`embedding_input_hash`、`source_label`、`source_url`、`source_type` 和 `reviewed_on`。chunks 按固定 semantic `chunk_type` 顺序、`ordinal ASC`、`chunk_key ASC` 排序。

所有 manifest 字符串先执行既定 normalization；aliases/tags 的 deterministic sorting 已由 metadata_hash canonicalization 定义。JSON 使用 `ensure_ascii = false`、compact separators、固定 key order 和 UTF-8 encoding，最后计算 SHA-256 并保存 lowercase hex。

以下内容绝不能进入 manifest hash：actual embedding vector、embedding execution status、`pending`/`embedded`/`failed`、`corpus_version_id`、`version_label`、created/activated/superseded timestamps、database-generated runtime IDs（stable `attraction_id` 除外）、import timestamp、source file path、filesystem order、Supabase row order 和 temporary staging information。相同逻辑 corpus + 相同 embedding profile，不论 YAML key order、file traversal order、DB result order、staging UUID 或 import timestamp，都必须得到相同 manifest_hash。

导入顺序固定为：创建 `staging` 版本 → 写入并验证 attraction versions/chunks → 完成 hash/profile/来源检查 → 将失败版本标记为 `failed` 或执行原子激活。任何校验失败都不得部分激活。

同一个 `dataset_key` 同时最多有一个 `active` corpus。数据库使用 partial unique index `unique (dataset_key) where status = 'active'` 保证该不变量；应用层不得用先查后写替代约束。

激活使用 compare-and-swap：调用方可传入 expected active corpus ID；在锁定同一 `dataset_key` 的 lifecycle rows 后，RPC 必须先重读当前 active 并比较 expected，再在同一数据库事务内完成当前 `active` → `superseded`、目标 `staging` → `active`，设置 `activated_at` 和 `superseded_at`。expected 为 null 只允许 first activation（当前无 active）；不允许 last-writer-wins。任一更新、校验或约束失败，整个 transaction rollback；不得出现新旧都 active 或两者都被错误标记的中间提交。

Importer/application validation 只是 early-failure optimization；其结果不是 activation RPC 信任的 persisted proof，也不写入可能 stale 的 `validated = true` 布尔值。最终 validation trust boundary 是 activation RPC 本身。RPC 必须在执行状态切换的同一 transaction 内重新验证 database invariants：`p_dataset_key` 与目标 corpus 一致；目标当前为 `staging` 且不是 `active`、`superseded` 或 `failed`；corpus 至少有合法的 version rows/chunks；included attraction versions 满足 required metadata；所有 active retrieval 所需 chunks 为 `embedded` 且 `embedding is not null`；embedding model、task、dimensions 和 profile 合法；required provenance 非空；FK 和 corpus ownership 合法；不存在任何无法进入 active retrieval 的非法 row state。只有全部检查通过，才允许执行 active → superseded、staging → active。

`superseded` corpus 保留完整数据，供审计、回滚诊断和增量比较使用，但 active retrieval 永远不读取 superseded 或 failed corpus。

## 4. Database model

V2 严格采用以下四张业务表：`rag_corpus_versions`、`rag_attractions`、`rag_attraction_versions`、`rag_attraction_chunks`。本 migration 只新增 V2 对象；`knowledge_chunks`、`match_knowledge_chunks` 及其既有 migration 保持不变，不允许通过 ALTER、替换或兼容层改写 legacy 行为。

### 4.1 `rag_corpus_versions`

该表负责 dataset version lifecycle。`corpus_version_id` 由 application/importer 预先分配，数据库不提供 UUID default；它不是内容 hash。

精确字段为：

- `corpus_version_id uuid not null`，primary key
- `dataset_key text not null`
- `version_label text not null`
- `manifest_hash text not null`
- `status text not null default 'staging'`
- `created_at timestamptz not null default now()`
- `activated_at timestamptz null`
- `superseded_at timestamptz null`

`(dataset_key, version_label)` 唯一。另有 partial unique index：`(dataset_key) where status = 'active'`，因此每个 dataset 最多一个 active corpus。`status` 只允许 `staging`、`active`、`superseded`、`failed`；合法转移只有 `staging -> active`、`staging -> failed`、`active -> superseded`。`failed -> *`、`superseded -> *`、`active -> staging` 和 `active -> failed` 都必须拒绝。

`corpus_version_id`、`dataset_key`、`version_label`、`manifest_hash`、`created_at` 在 insert 后 immutable；只有 lifecycle 字段可按上述合法转移更新。`manifest_hash` 必须匹配 `^[0-9a-f]{64}$`，但不要求全局唯一。

Corpus 创建/重试的 idempotency 固定如下：不存在对应 `(dataset_key, version_label)` 时创建 staging；存在且 hash 相同并为 staging 时复用现有 ID；存在且 hash 相同并为 active/superseded 时返回现有 ID，支持已完成操作的重放；存在且 hash 相同但为 failed 时拒绝，必须使用新的 version label/new corpus version 重试；hash 不同一律 conflict/reject，绝不能在相同 version label 下静默替换内容。

### 4.2 `rag_attractions`

该表只保存稳定业务 entity identity，不保存 corpus-specific metadata、正文或 provenance。精确字段为：

- `attraction_id uuid not null`，primary key
- `lifecycle_status text not null default 'active'`
- `created_at timestamptz not null default now()`
- `retired_at timestamptz null`
- `merged_into_attraction_id uuid null references rag_attractions(attraction_id) on delete restrict on update restrict`

一致性约束固定为：active 必须 `retired_at is null` 且 merge target 为 null；retired 必须 `retired_at is not null` 且 merge target 为 null；merged 必须有 merge target。self-merge 必须拒绝。正常 lifecycle 只允许 `active -> retired` 和 `active -> merged`，不允许隐式恢复 retired/merged。复杂 merge graph cycle 是 service-level known-descendant validation 的责任，不增加 recursive SQL graph subsystem。

该表不含 `canonical_name`、aliases、destination、category、tags、coordinates、content、provenance、hashes。稳定 ID 来自显式 entity registry/allocation，不能由当前景点名称 hash 代替。改名只改变 versioned metadata；corpus 中缺少某 attraction 也不表示它 retired。

### 4.3 `rag_attraction_versions`

该表保存某一 corpus 中某一 stable attraction 的 immutable metadata snapshot。主键和逻辑 identity 为 `(corpus_version_id, attraction_id)`，分别 foreign key 到 corpus 与 stable attraction，均使用 `on delete restrict on update restrict`。

精确字段为：

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
- `status text not null`
- `metadata_hash text not null`

`status` 只允许 `included`、`suppressed`。`destination_code` 与 `province_code` 必须匹配 `^\d{6}$`；`destination_level` 只允许 `province`、`prefecture_city`、`autonomous_prefecture`、`county_city`；`metadata_hash` 必须匹配 `^[0-9a-f]{64}$`。只有 active corpus 中的 included rows 参与检索；suppressed rows 保留用于审核但不产生候选。这里不保存 provenance；provenance 只属于 chunk。

不得依赖 SQL silent defaults 填充 canonical metadata；importer 必须显式提供 aliases、tags、status，即使值为空或看似默认值。snapshot 只能在 parent corpus 为 staging 时 INSERT；UPDATE 永远禁止；DELETE 仅在 parent corpus 为 staging 时允许。错误 metadata 必须在 staging 中 delete/reinsert，或放弃整个 corpus。corpus 一旦 active、superseded 或 failed，其所有 version rows immutable；数据库必须通过 trigger/equivalent enforcement，而不是仅依赖 repository convention。

### 4.4 `rag_attraction_chunks`

chunk 必须属于同一 corpus 的 attraction version。复合 foreign key `(corpus_version_id, attraction_id)` 引用 `rag_attraction_versions(corpus_version_id, attraction_id)`，使用 `on delete restrict on update restrict`。主键/逻辑 identity 为 `(corpus_version_id, chunk_key)`；另有 unique `(corpus_version_id, attraction_id, chunk_type, ordinal)`。

精确字段为：

- `corpus_version_id uuid not null`
- `attraction_id uuid not null`
- `chunk_key text not null`
- `chunk_type text not null`
- `ordinal integer not null`
- `content text not null`
- `content_hash text not null`
- `embedding_input_hash text not null`
- `embedding_input_schema_version text not null`
- `source_label text not null`
- `source_url text not null`
- `source_type text not null`
- `reviewed_on date not null`
- `embedding_model text not null`
- `embedding_task text not null`
- `embedding_dimensions integer not null`
- `embedding vector(1024) null`
- `status text not null`
- `embedding_error_code text null`
- `embedding_error_message text null`

`chunk_type` 只允许 `overview`、`highlights`、`transport`、`visit_advice`、`seasonal`；`ordinal >= 0`；`content_hash` 与 `embedding_input_hash` 都必须匹配 lowercase `^[0-9a-f]{64}$`。固定 document profile 为 `embedding_model = 'jina-embeddings-v3'`、`embedding_task = 'retrieval.passage'`、`embedding_dimensions = 1024`、`embedding_input_schema_version = 'rag-v2-embedding-input-v1'`。不增加 generic row timestamps。

状态只允许 `pending`、`embedded`、`failed`、`excluded`。pending 必须无 vector 且 error code/message 均为 null；embedded 必须有 vector 且 errors 均为 null；failed 必须无 vector、有非空 error code，error message 可为 null；excluded 必须无 vector 且 errors 均为 null。failed/excluded 保留在库中，二者都不可检索。错误字段只保存 concise diagnostic，不得保存完整 provider response、stack trace、secret 或 sensitive payload；10B-2 不增加 `retry_count`、`next_retry_at`、`provider_request_id`。

所有 chunk identity/content/profile/provenance 字段在 staging 期间也 immutable：`corpus_version_id`、`attraction_id`、`chunk_key`、`chunk_type`、`ordinal`、`content`、`content_hash`、`embedding_input_hash`、`embedding_input_schema_version`、`source_label`、`source_url`、`source_type`、`reviewed_on`、`embedding_model`、`embedding_task`、`embedding_dimensions`。staging UPDATE 只允许 status、embedding、embedding_error_code、embedding_error_message，并且只能走合法状态转移：`pending -> embedded`、`pending -> failed`、`failed -> pending`；不允许 `failed -> embedded`、`embedded -> *`、`excluded -> *`。retry 是先清空 errors 将 failed 变为 pending，再由 Stage 10B-3 执行 provider，结果为 embedded 或 failed。单 chunk failure 不使 corpus failed；corpus failed 是 terminal。parent corpus active/superseded/failed 后，所有 chunk mutation 均禁止；DELETE 只在 parent staging 时允许。

`source_url` 必须是实际内容来源页面，不能从 `source_label` 猜根 URL；每个 chunk 保持一个明确 source label、URL、type 和 reviewed date，不在 10B-2 建立 multi-source graph。

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

`destination_code` 和 `province_code` 都必须通过当前 administrative destination validation contract：类型为 string，不转换为 integer，并匹配 `^\d{6}$`。当前 mapping 中的行政 code 都是六位数字；不满足该 regex 的输入拒绝进入 domain model、version row 或 manifest。若 `latitude` 非空，必须满足 `-90 <= latitude <= 90`；若 `longitude` 非空，必须满足 `-180 <= longitude <= 180`。这些是行政 code 和坐标的 domain validation，不引入 `tourism_region`。

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

ordinal 表示某个 `attraction_id + chunk_type` 在当前 canonical chunking algorithm 下的 deterministic fragment position：`overview:0`、`overview:1`、`transport:0` 可以同时存在，不同 chunk_type 独立编号。若某个 section 前面的 fragment 新增、删除或语义重排，导致后续 fragment ordinal 改变，则这些后续 fragment 视为 logical chunk replacement：旧 chunk 从新 corpus removed，新 chunk created，再根据 embedding_input_hash 和 embedding profile 决定是否重新 embedding。Stage 10B 不引入 persistent fragment ID；这是接受的 YAGNI trade-off。

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

`SemanticChunk` 是 `app/rag_v2/models.py` 中的严格领域模型，字段顺序和类型固定为：

```python
chunk_key: str
attraction_id: UUID
chunk_type: ChunkType
ordinal: int
normalized_content: str
content_hash: str
embedding_input_hash: str
source_label: str
source_url: str
source_type: str
reviewed_on: date
```

它继承 `RagV2Schema`，禁止 extra fields，且 `ordinal >= 0`。Task 6 不新增 hash 格式校验；模型不包含 vector、database ID、runtime status 或 runtime timestamps。

Task 6 的 public chunking API 固定为：

```python
CHUNK_KEY_SCHEMA_VERSION = "rag-v2-chunk-key-v1"
DEFAULT_CHUNK_BUDGET = 4000

def chunk_key_for(
    *,
    attraction_id: UUID,
    chunk_type: ChunkType,
    ordinal: int,
) -> str: ...

class SemanticChunker:
    def __init__(self, max_code_points: int = DEFAULT_CHUNK_BUDGET) -> None: ...

    def chunk(
        self,
        section: SemanticSection,
        *,
        attraction: AttractionVersionMetadata,
    ) -> tuple[SemanticChunk, ...]: ...
```

`SemanticChunker(max_code_points=...)` 要求 `max_code_points > 0`；非正值必须抛出 `ValueError`。`chunk_key_for(...)` 要求 `ordinal >= 0`；负 ordinal 必须抛出 `ValueError`，不得生成负 ordinal 的 logical key。

`DEFAULT_CHUNK_BUDGET = 4000` 使用 Unicode code points，即 Python `len(text)`；它是 `UNVALIDATED DEFAULT`，不是经过 retrieval 实验验证的最优值。Task 6 不使用 token、UTF-8 byte count 或 tokenizer。

原始 section 的处理顺序固定为：先将 CRLF/CR 统一为 LF，同时保留 paragraph 信息；再以一个或多个 blank 或 whitespace-only line（`line.strip() == ""`）识别 paragraph boundary。随后计算 `whole_normalized = normalize_content(line_ending_canonicalized_text)`。如果 `whole_normalized == ""`，必须抛出 `ValueError`；不能返回空 tuple 或发出空 chunk。不能在 paragraph detection 前丢弃 blank lines，因为既有 `normalize_content` 会删除 blank lines。

如果 `len(whole_normalized) <= max_code_points`，必须只发出一个 ordinal 为 `0` 的 semantic chunk，即使原始 section 包含多个 paragraph。只有当 `len(whole_normalized) > max_code_points` 时才激活 semantic splitting hierarchy；此时如果存在多个 paragraph，paragraph 是第一层 split，且每个 paragraph 使用既有 `normalize_content` 独立处理。

对于任一 oversized semantic unit，递归规则固定为：使用最高层剩余 boundary；若该层产生两个或更多 meaningful children，则分别独立处理；不超预算的 child 直接成为 final fragment；超预算的 child 下降到下一层；不得返回上一层重新合并或 repack children。分割优先级固定为 paragraph → sentence → clause → whitespace，且任何层级都不得 greedy repack 或为填满预算而合并相邻 siblings。

对于 oversized paragraph，sentence terminators 仅为 `。！？.!?`，terminator 保留在左侧 segment。对于 oversized sentence，clause delimiters 仅为 `，,；;：:`，delimiter 保留在左侧 segment。若 clause 仍超预算，才使用 whitespace boundary；whitespace 是 boundary 而非 substantive content，不得单独发出 whitespace chunk。每个 whitespace child 使用既有 `normalize_content` canonicalize；如果 resulting non-whitespace atomic token 仍超预算，必须抛出 `ValueError`。不得使用 character hard slice、1200-character fallback、truncate、silent oversized output 或 tokenizer。

空白 canonicalization 后没有 substantive content、section 与 attraction 的 `attraction_id` 不一致，或 atomic unit 超过 budget 且没有合法 paragraph/sentence/clause/whitespace boundary，均必须抛出 `ValueError`。不得返回空 chunk 或静默丢弃内容。

每个最终 unit 按确定性 emission order 从 ordinal `0` 开始递增；ordinal 是同一 `attraction_id + chunk_type` 内的 logical storage position。每个 child 必须保留 section 的 `attraction_id`、`chunk_type`、`source_label`、`source_url`、`source_type` 和 `reviewed_on`，并且输出 chunk 满足 `len(normalized_content) <= max_code_points`。

每个最终 unit 必须使用现有 canonical APIs 填充 hash：`content_hash(normalized_content)`，以及用 attraction 的 canonical name/destination 和 section 的 chunk type/content 调用 `build_embedding_input(...)` 后得到的 `embedding_input_hash(embedding_input)`。如果同一 logical position 的 `normalized_content` 改变，则 `chunk_key` 不变、`content_hash` 改变，且 canonical embedding input 改变时 `embedding_input_hash` 改变。如果只改变 `source_label`、`source_url`、`source_type` 或 `reviewed_on`，则 `chunk_key`、`content_hash` 和 `embedding_input_hash` 都不变；provenance 只在后续 manifest identity/manifest_hash 中参与。不得创建第二套 embedding-input serialization，不调用 Jina，不创建 vector。

内容保留的通用测试不比较 raw source bytes。令 `strip_ws` 只移除 Unicode whitespace，则必须满足：

```python
strip_ws(normalize_content(section.content)) == strip_ws(
    "".join(chunk.normalized_content for chunk in chunks)
)
```

该 invariant 必须检测 substantive text、punctuation 的丢失或重复；boundary whitespace 的 canonicalization 可以不同。boundary-specific tests 还必须断言 ordered child contents，包括 sentence terminator 和 clause delimiter 保留在左侧 child。

`chunk_key` 固定格式为：

```text
rag-v2-chunk-key-v1|<attraction_id>|<chunk_type>|<ordinal>
```

UUID 使用标准小写字符串，ChunkType 使用 `.value`，ordinal 使用十进制字符串。chunk_key 只依赖 schema version、attraction_id、chunk_type 和 ordinal；不依赖 content、任何 hash、provenance、embedding profile 或 vector。它是 logical/storage identity，不是 vector reuse identity。Task 6 的 chunker 必须纯函数式、deterministic，不能 mutate 输入，也不能依赖时间、随机数或 process hash。

## 8. Incremental update decision table

Task 7 的纯 decision API 由以下逻辑类型和函数组成：

- `IncrementalSubject` 的值固定为 `present_chunk`、`removed_chunk`、`attraction_absent`、`explicitly_retired`、`merged_source`。
- `IncrementalAction` 的值固定为 `reuse`、`embed`、`remove`、`exclude`、`no_action`。
- `EmbeddingIdentity` 只包含 `embedding_input_hash`、`embedding_model`、`embedding_task`、`embedding_dimensions`、`embedding_input_schema_version` 五个字段。
- `PreviousEmbedding` 包含 `chunk_key`、`identity`、`vector: tuple[float, ...] | None` 和 `validated_corpus: bool`。
- `IncrementalCandidate` 包含 `subject`、`current_chunk_key`、`current_identity` 和 `previous_embeddings`。
- `IncrementalDecisionResult` 只包含 `action`、`reason` 和 `reused_from_chunk_key`，不包含 vector、embedding 或 provider response。
- `decide_incremental(candidate: IncrementalCandidate) -> IncrementalDecisionResult` 是纯函数。

`chunk_key` 只决定 logical chunk identity 和 storage position；它不是 vector reuse identity。vector 是否可复用只由以下 exact embedding identity tuple 共同决定：`embedding_input_hash`、`embedding_model`、`embedding_task`、`embedding_dimensions`、`embedding_input_schema_version`。metadata_hash、content_hash、source_label、source_url、source_type 和 reviewed_on 不属于该 tuple；Task 7 不重新计算 `embedding_input_hash`。

### 8.1 Lifecycle precedence and actions

`decide_incremental` 必须先处理 lifecycle/removal subject，再进入 embedding reuse logic。只有 `present_chunk` 会检查 `current_identity` 和 `previous_embeddings`；其他 subject 不得检查 candidate vector 来决定 action，也不执行 storage/lifecycle mutation。

| subject | exact action | decision rule |
| --- | --- | --- |
| `present_chunk` | `reuse` or `embed` | 至少一个 eligible previous embedding 时 `reuse`，否则 `embed` |
| `removed_chunk` | `remove` | logical chunk 不在新 corpus；新 corpus 不写该 chunk；旧 corpus lifecycle 在本函数外处理 |
| `attraction_absent` | `no_action` | corpus absence 不等同于 retirement；stable attraction lifecycle 不变，当前不表示 present chunk |
| `explicitly_retired` | `exclude` | 明确 retired 的 source 不进入新 corpus；lifecycle mutation 由 identity/lifecycle layer 处理 |
| `merged_source` | `exclude` | merged source 不进入新 source content；后续内容使用 merge target；merge mutation 在本函数外处理 |

所有非 `present_chunk` subject 都直接返回对应 action、非空 deterministic reason 和 `reused_from_chunk_key = None`。

### 8.2 Exact reuse and vector validity

可复用旧 corpus 必须已经通过 validation。对 `present_chunk`，`previous_embeddings` 可包含 `0..N` 项，决策必须检查全部项。某一项只有同时满足以下条件才是 eligible：

1. `validated_corpus is True`。
2. vector 非 `None`。
3. vector 每个值都是 finite；`NaN`、`+inf`、`-inf` 均无效。
4. `len(vector) == previous.identity.embedding_dimensions`。
5. `previous.identity` 的五个字段与 `current_identity` exact equality。

无效或不匹配项只是不具备 reuse eligibility，不抛出业务异常，不 sanitize、truncate、pad、resize，也不调用 provider。空 tuple `()` 只按普通 dimension rule 处理；除非 identity dimension 也是零，否则因长度不匹配而不 eligible，不增加特殊 exception contract。

如果没有 eligible 项，返回 `action = embed` 和 `reused_from_chunk_key = None`。如果只有一个 eligible 项，返回 `action = reuse`，并将其 `chunk_key` 放入 `reused_from_chunk_key`。如果有多个 eligible 项，不得因 ambiguity 拒绝 reuse，也不得依赖输入顺序；按 eligible 项的 `chunk_key` 字符串升序选择字典序最小者。`chunk_key` 只作为 eligibility 已通过后的 deterministic tie-break，不参与 eligibility。若多个 eligible 项具有相同 `chunk_key`，不增加错误或对象 identity 语义。

`reason` 必须存在、非空、deterministic，并提供人类可读的 action 诊断；exact wording 不是 public contract，不得要求固定 reason 字符串或 reason enum。

### 8.3 Embedding task and dimensions boundary

上游当前 RAG V2 stored document profile 使用 `embedding_task = retrieval.passage`；但 Task 7 不因 task 值不是 `retrieval.passage` 而拒绝创建 `EmbeddingIdentity`。在本纯 decision layer 中，`embedding_task` 只参与五字段 exact equality，因此 `retrieval.passage` 与 `retrieval.query` 不相等时不能 reuse。Task 7 不新增 generic constructor validation。

Task 7 是 generic decision layer，不硬编码 `1024`。reuse 要求：

```text
current.embedding_dimensions
== previous.identity.embedding_dimensions
== len(previous.vector)
```

因此 current/previous 都为 `1536` 且 vector 长度为 `1536`、其余 identity exact 相同、corpus validated、vector finite 时，具备 reuse eligibility。这不改变 Manifest V1 的 `jina-embeddings-v3`、`retrieval.passage`、`1024`、`rag-v2-embedding-input-v1` 固定 profile。

### 8.4 Result and purity boundary

当 `action = reuse` 时，`reused_from_chunk_key` 必须是选中旧 embedding 的 key；当 action 为 `embed`、`remove`、`exclude` 或 `no_action` 时，该字段必须为 `None`。decision 不返回 vector，vector 的实际复制/存储留给后续 stage。

`decide_incremental` 不得 mutate `IncrementalCandidate`、`EmbeddingIdentity`、`PreviousEmbedding`、`previous_embeddings` 或 vector；相同输入必须得到相同 public result。实现表示保持未指定：不要求 dataclass、Pydantic、NamedTuple、frozen、slots 或额外 annotation。

Task 7 不调用 Jina 或其他 provider，不执行 embedding generation、retry、batching、SQL、Supabase、PostgreSQL、pgvector、nearest-neighbor、cosine similarity 或 Planner runtime wiring。新的 staging corpus 在全部必需 vectors 和校验完成前不能 active。

## 9. Jina embedding contract

V2 固定使用：

- model：`jina-embeddings-v3`
- dimensions：`1024`
- document task：`retrieval.passage`
- query task：`retrieval.query`

chunk row 永远保存 passage profile；query vector 是一次 retrieval 请求的临时值，不写入 chunk table。旧 `knowledge_chunks.embedding` 与 V2 `rag_attraction_chunks.embedding` 物理隔离、profile 隔离、RPC 隔离，不允许混合排序或互相复用。

未来 Stage 10B-3 如修改 `JinaEmbeddingTransport`，必须保留现有调用兼容：旧调用的默认行为、Bearer header、响应 index 顺序检查、1024 维检查、非有限数拒绝、`RagUnavailable` failure semantics 和日志脱敏都不能被破坏。真实 Jina 调用留到 Stage 10C controlled E2E。

## 10. Retrieval V2 contract

完整 Retrieval V2 流程固定为：

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

后续 retrieval service 的初始参数固定为：

- `candidate_k = 40`
- `final_k = 6`
- `threshold = 0.60`

这三个值必须标记为 `UNVALIDATED DEFAULT`。它们不是生产质量结论，Stage 10C 的 frozen corpus + frozen queries + real Jina + real pgvector E2E 后才可校准。

score contract 固定为 pgvector cosine distance：数据库计算 `embedding <=> p_query_embedding`，candidate RPC 对外返回 `score = 1 - (embedding <=> p_query_embedding)`。因此 higher score 表示 more similar；SQL RPC 和 Python retrieval service 必须使用同一 score semantic。threshold 保留规则为 `score >= threshold`，经 Stage 10C-1 live calibration 后的默认规则是 `score >= 0.60`。score 的排序方向固定为 `score DESC`，随后按 `attraction_id ASC`、`chunk_key ASC` 做 deterministic tie-break。

候选按上述 score contract 排序。content hash dedup 每个 normalized content 只保留最高分一条。attraction diversity 以最高分顺序选择，默认同一 attraction 只贡献一个 final evidence chunk；若候选不足，再按分数顺序补足而不跨 active corpus。

database candidate RPC 负责 destination/province/attraction exact filters、active-only scope、candidate_k、cosine score 和 deterministic candidate order；它不负责 threshold、content_hash dedup、attraction diversity 或 final_k。后续 retrieval service 才执行这些 ranking/policy steps，并将没有 active corpus、没有足够分数、embedding/provider/RPC failure 转换为 safe empty/refusal result；不得回退到 legacy 混合检索。

V2 不使用 LLM reranker。Planner grounding 不是本阶段的 consumer。

## 11. SQL, RLS and RPC contract

四张 V2 业务表全部启用 RLS。对 `public`、`anon`、`authenticated` 撤销直接 select/insert/update/delete 权限；后端 persistence 只使用 `service_role`。不设计 owner-scoped end-user policies，也不允许 frontend client 直接访问 V2 表。精确 GRANT/REVOKE 语法属于 implementation-plan 细节，但安全结果是本节 contract。

### 11.1 Database enforcement and staging semantics

SQL CHECK 必须覆盖 enum-like statuses、destination levels、六位行政 code、lowercase 64-hex hashes、ordinal、固定 task/dimensions/input schema、vector/status/error consistency 和 attraction lifecycle。Postgres trigger 或等价机制必须执行 parent corpus status lookup、OLD 与 NEW immutable-field comparison、snapshot/chunk mutation guard 及 legal lifecycle transitions。不能只依赖 Python repository；service-role 直接写入也必须被 PostgreSQL 拒绝。

具体 migration/constraint/index/trigger 的名称不是 architecture contract；只要满足本节语义，implementation plan 可以选择 deterministic naming。

staging 允许 partial/incomplete：version rows 可以不完整，chunks 可以 pending/failed；staging 永远不进入 runtime retrieval，也不等价于 validated。application/importer 可以 early validate，但 activation RPC 是最终 DB trust boundary。放弃整版时使用 `staging -> failed` 并保留 rows 供诊断；failed corpus immutable 且不能 resurrect。Chunk failed 在 parent staging 期间可按 `failed -> pending` 恢复，单个 chunk failure 不会把 corpus 自动置 failed。

Python Stage 10B-1 是唯一的 canonical manifest serialization/hash owner。Postgres 不实现第二套 serializer/hash；activation SQL 不重新计算 canonical manifest SHA-256。数据库只负责 relational/state/profile/hash-format/provenance invariants，避免 Python/SQL drift。

### 11.2 Retrieval RPC interface

未来 SQL migration 固定提供以下受保护函数 interface，返回列必须恰好如下，不添加 ranking-policy fields：

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

RPC 负责解析 `dataset_key` 的 active corpus，要求恰好一个 active；0 个返回 empty rows，超过 1 个是 invariant violation/error，绝不能任意选一行。它必须在 vector distance 前 join `rag_attraction_versions`，只保留 included version、stable attraction active、embedded 且 embedding 非 null 的 chunks，并应用精确 metadata filters、cosine distance 和 deterministic order。staging、superseded、failed 不可搜索。RPC 不负责 destination/name resolution、Jina query embedding、threshold、dedup、diversity、final_k、evidence formatting 或 Planner。

可选 filter 的 NULL 表示不筛选；非 NULL 时对 `destination_code`、`destination_level`、`province_code`、`attraction_id` 做 exact equality。无 fuzzy、prefix、alias、case folding、inference 或 contradiction repair；冲突的 exact filters 返回 zero。`p_candidate_k = 40` 是 `UNVALIDATED DEFAULT`；安全范围为 `1 <= p_candidate_k <= 200`，超出必须 reject，不得 clamp。

score 固定为 `1 - (embedding <=> p_query_embedding)`，返回 real；排序固定为 score DESC、`attraction_id ASC`、`chunk_key ASC`。10B-2 不做 SQL threshold、content_hash dedup、attraction diversity 或 final_k。该函数为 `SECURITY INVOKER`、read-only、无 state mutation；只有 service_role 有 execute 权限。

### 11.3 Activation RPC and CAS

未来 SQL migration 固定提供以下函数 interface：

```text
activate_rag_v2_corpus(
  p_dataset_key text,
  p_corpus_version_id uuid,
  p_expected_active_corpus_version_id uuid default null
) -> void
```

`p_expected_active_corpus_version_id is null` 时，只允许当前没有 active corpus 的 first activation；非 NULL 时，当前 active 必须恰好等于该 UUID，否则 conflict/raise。不得 last-writer-wins。

activation 在同一 transaction 内执行：锁定 dataset lifecycle rows，解析当前 active，比较 expected，验证目标属于 dataset 且为 staging，执行最终 invariant gate，然后 current active -> superseded、target staging -> active，设置 `activated_at`/`superseded_at` 并 commit。任一步失败都 rollback。partial unique active index 是最终并发保护。

最终 invariant gate 必须要求：目标 dataset ownership 正确；至少一个 included attraction version；每个 included version 至少一个 embedded chunk；required chunk 不得 pending/failed（excluded 可存在且不阻塞）；每个 embedded chunk vector 非空；embedding profile 恰为 jina-embeddings-v3、retrieval.passage、1024、rag-v2-embedding-input-v1；hash 为 lowercase 64 hex；provenance 满足 `btrim(source_label) <> ''` 且 source_url/source_type 非空、reviewed_on 非 null；不做 SQL URL parser 或 URL canonicalization；destination/admin code/domain checks 有效；每个 included version 的 stable attraction lifecycle 为 active；FK、unique、check 均有效。此 gate 不要求 SQL 重算 manifest hash，也不要求 expected attraction/chunk counts。

activation 是幂等的：target 已经是 current active 时成功 no-op，支持 network retry；target 为 superseded/failed、dataset 不匹配或 terminal state resurrect 时拒绝。activation RPC 为 `SECURITY DEFINER`，固定 `SET search_path = public`，仅 service_role 有 execute 权限；不信任 caller 提交的 DB state/profile/hash，必须在 transaction 内重读并验证。

### 11.4 Retrieval storage, indexes and pgvector

destination fields 只存于 `rag_attraction_versions`，不在 chunks denormalize；retrieval 必须先完成 metadata join/filter 再计算 vector distance。规划的 B-tree indexes 为 `(corpus_version_id, destination_code, destination_level)`、`(corpus_version_id, province_code)`；已有 `(corpus_version_id, attraction_id)` key 支持 attraction lookup，不因猜测另建 attraction index，除非后续 query-plan evidence 证明需要。

V2 embedding 固定 `vector(1024)`，使用 `vector_cosine_ops`，默认 HNSW；不得复制 legacy IVFFlat tuning。HNSW 参数未 benchmark-validated，初始使用 defaults。vector index 只包含 `embedding is not null and status = 'embedded'` 的 searchable vectors。若 pgvector/Postgres 对 partial predicate 存在兼容性问题，implementation plan 必须显式暴露该问题，不得静默改变 retrieval semantics。可另加 `(corpus_version_id, status)` helper B-tree；不能建立跨 corpus table 的 active-status partial vector index，因为 active status 在另一张表。

### 11.5 Thin Python repository boundary

Stage 10B-2 只引入 persistence-focused `RagV2Repository`，不拥有 Jina、chunking、hashing、incremental decision、import orchestration 或 Planner。logical methods 为：

- corpus：`create_corpus_version`、`get_corpus_version`、`mark_corpus_failed`
- stable attraction：`get_attraction`、`insert_attraction`、`update_attraction_lifecycle`
- version snapshot：`insert_attraction_versions`
- chunks：`insert_chunks`、`mark_chunk_embedded`、`mark_chunk_embedding_failed`、`reset_chunk_embedding_for_retry`
- DB-controlled：`activate_corpus`、`match_chunks`

implementation plan 再定义 exact signatures/types，但不得借此扩大边界；不提供 generic public `execute_sql`、table accessor 或 raw client。

Direct persistence 负责 create/read corpus、mark failed、read/create stable attraction、受 guard 保护的 lifecycle mutation、insert versions、insert chunks 和 embedding lifecycle updates。activation 与 vector candidate retrieval 只能通过 RPC，activation 不得在 Python 中拆成多个 updates 重实现。

staging persistence 不要求 giant transaction，batch writes 可以 incremental commit；activation 的 lifecycle switch、CAS comparison 和 final validation 必须由 activation RPC 在 atomic transaction 中拥有。

### 11.6 Legacy compatibility

Stage 10B 不修改 `knowledge_chunks`、`match_knowledge_chunks`、migration 008 或 legacy app/rag models、repository、embedding/service、`app/composition.py`、`KnowledgeAnswerService`、`/api/chat`。旧表继续服务旧 production retrieval；V2 新表和新 RPC 不向旧接口写数据，也不从 legacy vectors 建立 V2 evidence。V2 是 additive-only，不能进行 Planner/runtime cutover。

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

实现严格 models、stable identity registry seam、三类 canonical hash、manifest canonicalization、六位行政 code/coordinate validation、五种 semantic sections、sentence-aware split 和以 embedding identity tuple 为唯一 reuse 判定的增量 decision logic。为每个 interface 添加 deterministic unit coverage；不接线上 consumer。

### Stage 10B-2：SQL schema, RPC, repository

新增四表 migration、constraints/triggers、indexes/pgvector、RLS/grants、CAS activation RPC、candidate retrieval RPC 和 thin service-role `RagV2Repository`；覆盖 staging/failed、snapshot/chunk mutation guards、activation rollback、active-only retrieval、security 和 legacy isolation。只建立 persistence boundary，不实现 Jina/provider execution、query embedding、ranking policy、import orchestration 或 Planner/runtime wiring。

### Stage 10B-3：Jina query/passage and Retrieval V2

扩展兼容的 Jina transport，分别发送 `retrieval.passage` 和 `retrieval.query`，实现 profile validation、prefilter、candidate_k、threshold、content dedup 和 attraction diversity。默认参数保持 `UNVALIDATED DEFAULT`，不接 Planner。

### Stage 10B-4：Incremental importer, offline evaluation, deployment contract/docs

实现 manifest/version staging、identity mapping、reuse/re-embed decision、atomic activation preparation、deterministic offline evaluation 和 deployment contract。此阶段才将 `JINA_API_KEY` 以 `sync: false` 添加到 `render.yaml`；仍不写真实 secret，不做 Render Dashboard 假设。

### Stage 10B-5：Full regression, security, readiness review

运行全量 regression、public-repo secret scan、RLS/RPC security review、legacy isolation review、scope review 和 rollback readiness review。未通过不得进入真实迁移。

### Stage 10C：三城真实迁移与线上接入

只迁移厦门、福州、大理；执行真实 Jina + pgvector E2E、参数 calibration、staging validation、activation、Planner grounding、Render Dashboard verification 和 Render online RAG E2E。Stage 10C 也不增加第四城市或全国导入，除非另有单独批准的范围变更。

## 17. Final self-review

本次 Stage 10B-2 database contract self-review 结果：Critical = None；Important = None；Minor = None。以下事项已逐项核对：

- 无 placeholder/TBD；Task 1–7 已批准的 manifest identity、models、hashing、semantic chunking、incremental tuple、legacy boundary 未被改写。
- 四张表、primary/unique identity、foreign key `restrict` 行为和所有固定字段/default/profile 已明确；稳定 attraction 表没有混入 versioned metadata、正文、provenance 或 hashes。
- corpus lifecycle、idempotency、staging partial/incomplete 语义、failed/superseded terminal 语义及 version/chunk mutation guards 已明确；没有 upsert 或静默覆盖语义。
- attraction lifecycle、self-merge、retired/merged 不隐式恢复和 service-level known-descendant cycle validation 已明确。
- destination/admin code、destination levels、hash formats、chunk status/vector/error consistency、provenance 和固定 passage profile 的 DB enforcement 已明确。
- activation 只有带 expected-active CAS 的三参数 interface；同 transaction 执行 lock、compare、final invariant gate、switch 和 timestamps；activation no-op/reject/rollback 语义已明确，没有 last-writer-wins。
- activation 不读取 importer 的 `validated = true` proof，不由 SQL 重算 canonical manifest hash；Python Stage 10B-1 仍是唯一 canonical manifest serializer/hash owner。
- retrieval RPC 的 exact signature/return columns、active corpus 解析、metadata join/filter、candidate_k `40` UNVALIDATED DEFAULT、安全范围 `1..200`、cosine score 和 tie-break 已明确；threshold/dedup/diversity/final_k 保留给后续 service stage。
- destination 只在 version snapshot 存储；V2 使用 `vector(1024)`、`vector_cosine_ops`、默认 HNSW；没有 legacy IVFFlat tuning 或跨表 active partial vector index 假设。
- `RagV2Repository` 是 thin persistence boundary；直接写入与 RPC 边界、batch/activation transaction 边界、RLS/service-role security 已明确，没有 generic raw SQL surface。
- 没有重新引入旧的两参数 activation、last-writer-wins、SQL manifest recomputation、destination denormalization、all-RPC staging writes、mutable snapshot upsert、failed corpus resurrection、failed chunk terminal-forever 或 excluded deletion 规则。
- 没有将 Jina、Planner、runtime cutover、第四城市、全国导入、Render Dashboard inspection 或 online smoke 提前到 10B-2；Stage 10B-3/4/5/10C boundaries 保持分离。
- 离线 deterministic tests、controlled real E2E 和 Render online smoke 的证据边界保持分离；离线指标不代表线上质量。
