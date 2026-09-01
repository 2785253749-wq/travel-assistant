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

激活锁定同一 `dataset_key` 的状态行，在一个数据库事务内完成：当前 `active` → `superseded`，目标 `staging` → `active`，设置 `activated_at` 和 `superseded_at`。任一更新、校验或约束失败，整个 transaction rollback；不得出现新旧都 active 或两者都被错误标记的中间提交。

Importer/application validation 只是 early-failure optimization；其结果不是 activation RPC 信任的 persisted proof，也不写入可能 stale 的 `validated = true` 布尔值。最终 validation trust boundary 是 activation RPC 本身。RPC 必须在执行状态切换的同一 transaction 内重新验证 database invariants：`p_dataset_key` 与目标 corpus 一致；目标当前为 `staging` 且不是 `active`、`superseded` 或 `failed`；corpus 至少有合法的 version rows/chunks；included attraction versions 满足 required metadata；所有 active retrieval 所需 chunks 为 `embedded` 且 `embedding is not null`；embedding model、task、dimensions 和 profile 合法；required provenance 非空；FK 和 corpus ownership 合法；不存在任何无法进入 active retrieval 的非法 row state。只有全部检查通过，才允许执行 active → superseded、staging → active。

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

数据库一致性约束固定为：`lifecycle_status = 'active'` 时 `retired_at is null` 且 `merged_into_attraction_id is null`；`lifecycle_status = 'retired'` 时 `retired_at is not null` 且 `merged_into_attraction_id is null`；`lifecycle_status = 'merged'` 时 `merged_into_attraction_id is not null`。数据库还必须禁止 `merged_into_attraction_id = attraction_id`，因此 entity 不能 self-merge。复杂 merge cycle 不要求由单个 SQL CHECK 完全解决；identity/registry service 在 merge command 中至少验证 target != source、target 不是已知 source descendant、target entity 存在。Stage 10B 不增加 graph subsystem。

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

score contract 固定为 pgvector cosine distance：数据库计算 `embedding <=> p_query_embedding`，RPC 对外返回 `score = 1 - (embedding <=> p_query_embedding)`。因此 higher score 表示 more similar；SQL RPC 和 Python retrieval service 必须使用同一 score semantic。threshold 保留规则为 `score >= threshold`，初始规则是 `score >= 0.70`。score 的排序方向固定为 `score DESC`，随后按 `attraction_id ASC`、`chunk_key ASC` 做 deterministic tie-break。

候选按上述 score contract 排序。content hash dedup 每个 normalized content 只保留最高分一条。attraction diversity 以最高分顺序选择，默认同一 attraction 只贡献一个 final evidence chunk；若候选不足，再按分数顺序补足而不跨 active corpus。

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

其中“manifest/rows/profile 校验已完成”不是读取 importer 写入的 persisted proof。activation RPC 必须在同一 transaction 内重新执行第 3 节列出的 database invariants，包括 dataset ownership、staging-only 状态、合法 version rows/chunks、included metadata、embedded 非空 vectors、合法 embedding profile、完整 provenance、FK ownership 和 active retrieval 可用状态；全部通过后才可切换状态。

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

实现严格 models、stable identity registry seam、三类 canonical hash、manifest canonicalization、六位行政 code/coordinate validation、五种 semantic sections、sentence-aware split 和以 embedding identity tuple 为唯一 reuse 判定的增量 decision logic。为每个 interface 添加 deterministic unit coverage；不接线上 consumer。

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
- lifecycle 已固定：corpus 缺失不触发 retired；retired 和 merged 只能由显式 lifecycle 操作产生；merge 必须写 `merged_into_attraction_id`，且 active/retired/merged 的 timestamp、target 和 self-merge constraints 已明确。
- V2 destination identity 只使用 `destination_code + destination_level + destination_name`，全部 code 为 string；没有将省级 corpus 当作城市 corpus 的隐式 fallback。
- destination/province code 的 `^\d{6}$` validation 和 latitude/longitude bounds 已明确。
- activation RPC 是最终 validation trust boundary；importer validation 不被视为 persisted proof，RPC 在 transaction 内重新验证 database invariants。
- `manifest_hash` 的 schema version、固定排序、canonical JSON、UTF-8 SHA-256 规则和不影响 hash 的输入已明确。
- manifest canonical object 已固定为 `schema_version`、`dataset_key`、`embedding_profile`、`attractions` 四个 top-level keys，且 vectors/status/timestamps/staging IDs 被排除。
- chunk ordinal 变化被明确视为 logical replacement；vector reuse 只由 embedding identity tuple 决定，4000 code-point budget 已标记为 `UNVALIDATED DEFAULT`。
- retrieval score 已固定为 `1 - (embedding <=> p_query_embedding)`，并统一 `score >= threshold` 规则和 tie-break。
- 未实现无真实 consumer 的 `tourism_region`。
- 离线测试、controlled real E2E、Render online smoke 的证据边界已分离；离线指标不代表线上质量。
- Render manifest 声明与 Dashboard secret 配置已分离；`JINA_API_KEY` manifest change 明确推迟到 Stage 10B-4。
- Stage 10B 没有第四城市、全国导入、Planner 用户行为变更或现有 production retrieval 改动。
