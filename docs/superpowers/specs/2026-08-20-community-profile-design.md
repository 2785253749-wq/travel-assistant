# Voyage 社区与个人资料设计

日期：2026-08-20
状态：待用户评审
范围：社区发布、社区浏览、作者撤下、个人资料读取与编辑

## 1. 背景

Voyage 已有 Supabase 账户认证、私有行程、可撤销分享链接和一个社区占位页。数据库已经存在 `profiles` 表，但应用没有个人资料读写模块；社区也没有持久化模型或后端接口。

本阶段交付完整但克制的社区 MVP：登录用户可以把自己的已规划行程发布为公开快照，所有访客可以浏览社区内容，作者可以撤下自己的发布；登录用户可以在独立个人信息页维护昵称和旅行偏好。

## 2. 目标

- 社区页面对未登录访客可读。
- 登录用户可以从自己的已规划行程中选择一条并发布。
- 发布内容是脱敏快照，不依赖分享令牌，也不随私有行程变化。
- 作者可以撤下自己的社区发布，且不能管理其他作者的发布。
- 个人信息页可以读取和保存昵称、简介、常驻城市和旅行风格。
- 邮箱只在个人信息页展示，不进入社区数据或公开响应。
- 本地开发使用内存适配器，生产环境使用带 RLS 的 Supabase 适配器。

## 3. 非目标

本阶段不实现评论、点赞、关注、图片上传、全文搜索、内容推荐算法、管理员后台、草稿、社区发布编辑或举报流程。作者若要更新公开内容，应撤下旧发布并重新发布。

社区不是分享链接的索引。现有分享链接继续用于持有令牌者的临时只读访问，不会自动出现在社区。

## 4. 已选择的方案

采用“公开快照”模型。

发布时，后端读取当前用户拥有的 `planned` 私有行程，验证其结构，复制允许公开的标题、目的地和行程内容，并附加作者当时的公开昵称。社区发布与源行程拥有不同生命周期：修改或删除源行程不会修改已经发布的快照，撤下社区发布也不会影响源行程。

未采用以下方案：

- 复用分享链接：链接有过期和撤销语义，不适合作为稳定社区目录。
- 直接公开读取私有行程：会把私有数据模型与公开内容耦合，并使源行程修改意外改变公开内容。

## 5. 领域模型

### 5.1 个人资料

个人资料属于一个账户，由以下字段组成：

- `display_name`：公开昵称，可为空；去除首尾空白后最多 40 个字符。
- `bio`：个人简介，可为空；最多 160 个字符。
- `home_city`：常驻城市，可为空；最多 40 个字符。
- `travel_styles`：旅行风格数组，最多 5 项，每项必须来自固定集合：`美食`、`人文`、`自然`、`亲子`、`户外`、`休闲`。

现有 `profiles.preferences` JSONB 保存 `bio`、`home_city` 和 `travel_styles`。后端对外提供固定字段，不把任意 JSON 暴露为公共接口。

### 5.2 社区发布

社区发布包含：

- 发布标识和作者标识；作者标识仅用于权限判断，不出现在公开响应。
- 可空的源行程标识，用于阻止同一行程重复发布；源行程删除后可置空。
- 发布时的作者昵称快照；昵称为空时使用“Voyage 旅行者”。
- 标题、目的地、作者补充摘要和经过验证的行程快照。
- 创建时间和更新时间。

仅 `planned` 且包含有效 itinerary 的私有行程可以发布。同一用户的同一源行程最多存在一个社区发布。

### 5.3 生命周期规则

1. 私有行程创建和规划仍由现有行程模块负责。
2. 发布模块从行程模块读取已验证的私有行程，生成公开快照。
3. 源行程后续修改不传播到社区发布。
4. 源行程删除不删除社区发布，只清空其内部来源引用。
5. 作者撤下社区发布后，公开列表和详情立即不可见。
6. 删除账户时，数据库级联删除个人资料、私有行程和社区发布。

## 6. 数据库设计

新增迁移 `010_community_profile.sql`。

### 6.1 `profiles`

继续使用现有表：

```sql
profiles (
  user_id uuid primary key,
  display_name text,
  preferences jsonb not null default '{}',
  created_at timestamptz not null,
  updated_at timestamptz not null
)
```

迁移补充字段约束和 `updated_at` 触发器。旧的、不符合新结构的 `preferences` 读取时按空值处理；更新后写回规范结构。

### 6.2 `community_posts`

```sql
community_posts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_trip_id uuid references trips(id) on delete set null,
  author_display_name text not null,
  title text not null,
  destination text not null,
  summary text not null,
  itinerary_snapshot jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
)
```

约束：

- 标题 1–100 字符。
- 目的地 1–80 字符。
- 摘要 1–300 字符。
- 作者昵称 1–40 字符。
- `itinerary_snapshot` 必须是 JSON object。
- 对 `(user_id, source_trip_id)` 建立仅在 `source_trip_id is not null` 时生效的唯一索引。
- 按 `(created_at desc, id desc)` 建立社区列表索引。

### 6.3 RLS 与公开投影

`community_posts` 启用 RLS。认证用户只能读取和删除 `auth.uid() = user_id` 的记录；不授予客户端直接插入或更新权限，也不授予 `anon` 或 `public` 直接表权限。

发布只通过 `publish_community_post(source_trip_id, summary)` 执行。该 `SECURITY DEFINER` 函数固定 `search_path`，使用 `auth.uid()` 读取调用者自己的 `planned` 行程和个人资料，在数据库内构造快照并插入记录。执行权限仅授予 `authenticated`。这样即使用户绕过 FastAPI 直接调用 Supabase，也不能伪造作者、发布他人的行程或提交任意快照。

匿名浏览只通过两个 `SECURITY DEFINER` 函数：

- `list_community_posts(cursor_created_at, cursor_id, page_size)`
- `get_community_post(post_id)`

函数固定 `search_path`，只返回白名单字段，不返回 `user_id`、`source_trip_id`、邮箱或任何对话数据。函数执行权限仅授予 `anon` 和 `authenticated`。所有基础表对 `public`、`anon` 继续显式 revoke。

## 7. 后端模块

### 7.1 Profile 模块

外部接口保持很小：

```python
class ProfileModule:
    def get_profile(user) -> UserProfile
    def replace_profile(user, input) -> UserProfile
```

模块负责默认值、长度限制、旅行风格白名单、JSON 规范化和邮箱拼装。存储 seam 提供两个适配器：`InMemoryProfileRepository` 和 `SupabaseProfileRepository`。

### 7.2 Community 模块

外部接口：

```python
class CommunityModule:
    def list_posts(cursor, limit, viewer_id=None) -> CommunityPage
    def get_post(post_id, viewer_id=None) -> CommunityPost
    def publish(user_id, trip_id, summary) -> CommunityPost
    def withdraw(user_id, post_id) -> None
```

发布实现内部完成行程所有权验证、状态验证、公开字段选择、作者昵称回退和快照复制。调用方不需要了解 Supabase RPC 或行程 JSON 的脱敏细节。

存储 seam 提供：

- `InMemoryCommunityRepository`：测试和本地开发。
- `SupabaseCommunityRepository`：用户 JWT + 发布 RPC/RLS，用于发布、撤下和查询当前用户拥有的发布 ID。
- `SupabasePublicCommunityRepository`：anon key + 白名单 RPC，用于列表和详情。

不新增只有单一适配器的抽象。模块与适配器沿用现有行程模块的组织方式。

## 8. HTTP 接口

### 8.1 个人资料

`GET /api/profile`

- 需要 Bearer token。
- 不存在资料行时返回默认空资料，并包含认证邮箱。
- 响应：`user_id`、`email`、`display_name`、`bio`、`home_city`、`travel_styles`、`updated_at`。

`PUT /api/profile`

- 需要 Bearer token。
- 使用完整替换语义和 upsert。
- 请求只接受 `display_name`、`bio`、`home_city`、`travel_styles`，额外字段返回 422。
- 响应为保存后的完整个人资料。

### 8.2 社区

`GET /api/community/posts?cursor=<opaque>&limit=20`

- 匿名可访问；Bearer token 可选。
- `limit` 范围 1–50，默认 20。
- 按创建时间倒序，返回 `items` 和可空 `next_cursor`。
- 登录时为自己的记录返回 `can_delete: true`，匿名或其他作者为 `false`。

`GET /api/community/posts/{post_id}`

- 匿名可访问。
- 返回公开详情或稳定的 404 `COMMUNITY_POST_NOT_FOUND`。

`POST /api/community/posts`

- 需要 Bearer token。
- 请求：`trip_id`、`summary`。
- 成功返回 201。
- 非自己的行程统一返回 404，避免泄露资源存在性。
- 未规划行程返回 422 `COMMUNITY_TRIP_NOT_PUBLISHABLE`。
- 重复发布返回 409 `COMMUNITY_POST_EXISTS`。

`DELETE /api/community/posts/{post_id}`

- 需要 Bearer token。
- 仅作者可执行；非作者和不存在统一返回 404。
- 成功返回 204。该操作在领域语言中称为“撤下”。

游标编码创建时间和 UUID，客户端将其视为 opaque string。无效游标返回 422，不回退到第一页。

## 9. 前端设计

### 9.1 社区页

替换现有占位内容：

- 页面标题和简短说明。
- “发布行程”按钮；未登录点击后跳转 `/auth?mode=signin`。
- 最新发布卡片列表，支持加载更多、空状态、加载失败和重试。
- 卡片展示作者昵称、标题、目的地、摘要和发布时间。
- 点击卡片打开社区详情，只渲染公开快照。
- 作者自己的卡片显示“撤下”按钮，并要求二次确认。
- 发布面板列出当前用户的 `planned` 行程，填写 1–300 字摘要后提交。

社区请求具备与“我的行程”相同的世代标识：切换账户、退出或重复加载时，旧响应不能覆盖新状态。

### 9.2 个人信息页

新增：

- `GET /profile` 后端页面路由。
- `app/static/profile.html`。
- `app/static/profile.js`。

页面显示只读邮箱，以及昵称、简介、常驻城市和旅行风格表单。保存成功显示中文状态提示。未登录访问时跳转到 `/auth?mode=signin&return_to=%2Fprofile`；认证页只接受同源绝对路径白名单中的 `return_to`，防止开放重定向。

账户菜单在登录后显示“个人信息”入口；未登录时继续显示登录和注册入口。

所有用户内容通过 `textContent` 或表单 value 渲染，不使用 `innerHTML`。

## 10. 数据流

### 10.1 发布

1. 浏览器携带 Supabase access token 请求发布。
2. FastAPI 验证 token，得到可信 `user_id`。
3. Community 模块验证摘要，并调用用户作用域发布接口。
4. Supabase 适配器携带用户 JWT 调用 `publish_community_post`。
5. RPC 再次验证所有权和 `planned` 状态，读取个人资料，在数据库内构造脱敏快照并插入记录。
6. 后端返回公开响应，前端将其加入列表。

### 10.2 公开浏览

1. 浏览器请求社区列表，不要求登录。
2. 后端通过 anon key 调用白名单 RPC。
3. RPC 只返回公开字段。
4. 若请求带有效登录身份，后端使用用户作用域适配器，在当前页的发布 ID 中查询当前用户拥有的 ID，再生成 `can_delete`；公开 RPC 和 HTTP 响应均不返回作者 UUID。

### 10.3 更新个人资料

1. 浏览器提交完整表单和 access token。
2. 后端验证字段并规范化空白。
3. 用户作用域 Supabase 适配器 upsert 自己的资料行。
4. RLS 阻止跨账户写入。
5. 后端附加认证邮箱并返回规范响应。

## 11. 错误和并发

- 上游认证失败沿用 `AUTH_REQUIRED`、`AUTH_INVALID`、`AUTH_UNAVAILABLE`。
- 数据库或 RPC 不可用返回稳定的 503，不暴露供应商异常。
- 发布和撤下按钮在请求中禁用，防止重复操作。
- 数据库唯一索引是重复发布的最终并发保护。
- 社区列表采用游标分页，避免新发布插入时的 offset 漂移。
- 删除或读取不存在/无权访问的资源统一为 404，避免所有权探测。
- 前端在账户切换时先清除个人资料、待发布行程和作者操作按钮，再渲染新账户状态。

## 12. 安全与隐私

- 公开快照不包含邮箱、用户 UUID、对话记录、分享令牌或完整私有 TravelProfile。
- 社区快照只保留 itinerary 及独立的 `destination`；不复制出发地、预算、人数等个人规划字段到列表投影。
- 行程详情中的自由文本按纯文本渲染。
- 服务端使用认证得到的 user ID，忽略请求中任何所有者字段。
- 公开 RPC 固定 `search_path`、显式列清单和最小执行授权。
- 新迁移必须扩展现有 RLS 合同测试，阻止后续迁移重新授予匿名表访问。

## 13. 测试策略

### 13.1 领域与模块测试

- 个人资料默认值、规范化、长度和旅行风格白名单。
- 仅已规划且属于当前用户的行程可发布。
- 快照与源行程对象深拷贝；源行程修改不改变发布。
- 重复发布、跨用户撤下和不存在资源的错误映射。
- 源行程删除后发布仍可读取。

### 13.2 适配器与迁移测试

- Supabase 用户作用域客户端携带 JWT。
- 公开适配器只能调用允许的 RPC。
- `community_posts` RLS、grant/revoke、唯一索引和函数白名单合同。
- RPC 不返回 `user_id`、`source_trip_id` 或邮箱。

### 13.3 HTTP 集成测试

- Profile GET/PUT 的认证、验证、upsert 和跨用户隔离。
- 社区匿名列表/详情、登录发布、作者撤下、重复发布和错误状态码。
- 请求体限制和供应商错误不泄露。

### 13.4 前端测试

- 社区视图的加载、空状态、分页、失败重试和详情。
- 未登录发布跳转；登录发布成功；作者撤下。
- 旧请求在账户切换后不能恢复私有操作能力。
- 个人信息页登录守卫、保存、中文错误提示和 `return_to` 白名单。
- 用户输入始终作为文本渲染。

### 13.5 完整验证

- `node --test tests/frontend/*.test.js`
- `python -m pytest -q`
- `node --check app/static/app.js`
- `node --check app/static/profile.js`
- `git diff --check`

## 14. 迁移与部署顺序

1. 先在 Supabase 应用迁移，验证 RLS 与 RPC 合同。
2. 部署后端模块和 HTTP 接口。
3. 部署社区页和个人信息页。
4. 使用两个测试账户验证隔离、发布、匿名浏览和撤下。
5. 验证公开响应和日志中没有邮箱、token 或用户 UUID。

迁移是向后兼容的：新增表和函数不会影响现有行程及分享接口。若应用部署需要回滚，保留新表但撤回应用代码；不要在生产回滚过程中删除用户发布数据。

## 15. 验收标准

- 未登录用户能浏览社区列表和详情，但不能发布或撤下。
- 登录用户能编辑个人资料，并在刷新后看到持久化结果。
- 登录用户能发布自己的已规划行程，不能发布他人或未规划行程。
- 社区内容不随源行程修改或删除而变化。
- 作者能撤下自己的发布，不能撤下他人的发布。
- 公开接口、页面和日志不暴露邮箱、用户 UUID、聊天记录、分享令牌或私有规划字段。
- RLS、RPC、后端、前端和完整测试全部通过。
