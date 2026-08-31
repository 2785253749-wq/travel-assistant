# 2026-08-13 RAG 与天气试点工作交接

## 当前状态

工作目录：`D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp`

工作分支：`agent/zero-cost-public-mvp`

本轮目标是为福建、云南、厦门试点加入可引用的 RAG 知识库，以及独立的高德天气服务；不要扩展到全国，不要把任何 Key 写进代码、文档或 Git。

## 已完成并通过独立复审

1. RAG 配置与领域模型
   - 提交：`6521cb7`、`b280ecc`、`536d261`
   - 提供 Jina、高德服务端配置以及严格的知识、引用、天气数据模型。

2. 私有 pgvector 知识库与导入
   - 提交：`a6e3dcb`、`a6ec1d6`
   - 迁移：`supabase/migrations/008_rag_knowledge.sql`
   - 仅 service role 可导入、检索和操作，匿名用户不能直接读取知识库。

3. Jina 向量、检索、中文引用与拒答
   - 提交：`5459f17`、`88a1fc8`、`0e783f7`、`1a0997e`
   - 没有证据、Jina 失败或检索失败时固定中文拒答；生产配额使用服务端原子 RPC。

4. 高德 Web Service 天气
   - 提交：`d5ac307`、`5a250dd`
   - 迁移：`supabase/migrations/009_weather_quota.sql`
   - 只读取 `AMAP_WEB_SERVICE_KEY`，不会使用浏览器地图 Key；生产配额为数据库原子计数。
   - 只展示报告当天起三天内的实时预报；没有 Key 时安全降级且不消耗天气额度。

5. 聊天与行程整合
   - 提交：`e970b38`、`48a6d04`、`d7f3150`、`c509e7a`
   - 独立天气问句只走天气服务；独立知识问答只走 RAG；完整或不完整的旅行规划需求仍走原有资料收集流程。
   - 行程前三天可使用实时天气；第四天及以后只能使用带“非实时天气”标记的、由 RAG 支持的季节建议；失败不阻断行程。
   - 最终独立回归：相关聊天、意图、规划、用户旅程 `89 passed`，仅 1 条既有 Starlette/httpx 弃用警告。

## 已实现、待独立复审（暂停点）

6. 前端城市天气卡与行程天气显示
   - 提交：`4a7bed2 feat: render city and itinerary weather`
   - 选择试点城市后请求 `/api/weather/cities/{cityId}`，并渲染天气卡；失败显示“天气暂不可用”。
   - 对同一城市的并发选择做了请求去重；不调用 `/api/chat`，不自动打开 AI 助手，渲染使用 `textContent`。
   - 已由实现阶段运行：`node --test tests/frontend/*.test.js`，结果 `61 passed / 0 failed`。
   - **尚未进行独立代码复审；恢复工作时必须先做 Task 6 review，若发现 Critical/Important 必须先修复并复审。**

## 明天恢复工作顺序

1. 进入工作目录：

   ```powershell
   cd "D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp"
   ```

2. 阅读本文件、计划文件和 SDD 进度：

   - `docs/work-log-2026-08-13-rag-weather.md`
   - `docs/superpowers/plans/2026-08-12-rag-weather-pilot.md`
   - `.superpowers/sdd/2026-08-12-rag-weather-pilot/progress.md`

3. 对 `4a7bed2` 做 Task 6 独立复审，重点检查：
   - 不泄露服务端天气 Key；
   - 每次城市选择最多一个请求、失败可恢复；
   - 不会调用聊天接口或自动弹出助手；
   - 只从结构化 `itinerary.days[].weather` 渲染，不解析聊天文本；
   - 响应式与可访问性不回归。

4. Task 6 审查通过后，继续计划 Task 7：80 条离线 RAG/天气评测、发布门禁、README、`.env.example` 与公开仓库扫描。

5. 最后才做 Task 8：用户在 Supabase 执行 `008_rag_knowledge.sql` 和 `009_weather_quota.sql`，导入试点资料，在 Render 设置新的 `JINA_API_KEY` 和 `AMAP_WEB_SERVICE_KEY`，并完成真实浏览器验收。

## 安全和 Git 注意事项

- 绝不在聊天、日志、代码、测试快照、README 或 Git 中写出任何真实 Key。
- `AMAP_JS_KEY` / `AMAP_SECURITY_JS_CODE` 仅用于浏览器地图；`AMAP_WEB_SERVICE_KEY` 仅由后端天气接口使用。
- `JINA_API_KEY` 和 `AMAP_WEB_SERVICE_KEY` 以后只放 Render 环境变量，不放前端 runtime config。
- 下列未跟踪文档是用户文件，不要暂存、提交、删除或重置：
  - `docs/superpowers/plans/2026-08-08-voyage-chinese-frontend.md`
  - `docs/superpowers/plans/2026-08-10-amap-voyage-explore-trial.md`
  - `docs/superpowers/plans/2026-08-11-draggable-ai-assistant.md`
  - `docs/superpowers/plans/2026-08-12-rag-weather-pilot.md`
  - `docs/work-log-2026-07-30.md`
  - `docs/work-log-2026-08-10-amap-voyage.md`
  - 本文件 `docs/work-log-2026-08-13-rag-weather.md`
- 不使用 `git reset --hard`、`git checkout --` 或 `git clean`。

## 2026-08-13 收尾补充（本次继续前必读）

### 最新分支与推送

- 工作目录：`D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp`
- 分支：`agent/zero-cost-public-mvp`
- 已推送到 GitHub；远端已包含本轮 RAG、天气、地图、可拖动助手与景点别名修复。
- 最新代码提交：`32ddce6 fix: resolve unique RAG place aliases safely`。
- 最近的完整 RAG/天气终审修复提交：`21b31ea fix: close rag weather final review gaps`。
- GitHub 草稿 PR 尚未创建：尝试 `gh pr create` 时 GitHub GraphQL 网络连接超时。下次可直接打开：
  `https://github.com/2785253749-wq/travel-assistant/compare/main...agent/zero-cost-public-mvp?expand=1`

### 本次新增的安全景点识别

- 规格提交：`9f8e478 docs: specify RAG place alias handling`。
- 实施计划提交：`d2e0ae6 docs: plan safe RAG place aliases`。
- `鼓浪屿游玩前需要怎样安排？` 这类未写城市、但景点唯一归属试点地区的问题，会在服务端内部识别为厦门并进入知识检索。
- 用户原始问题不会被改写；地区仅作为检索上下文。
- 未知、重名或歧义地点会固定中文拒答并提示补充目的地城市，且不得调用检索服务或全库兜底。
- 评测运行器不再给原始用户问题拼接城市名。
- 该任务已独立复审通过（Spec/Quality 均 PASS，C/I/M 均为 0）。

### 最新验证证据

在 `agent/zero-cost-public-mvp` 的当前树上运行：

```powershell
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m pytest -q
node --test tests/frontend/*.test.js
& 'C:\Users\Asus\AppData\Local\Programs\Python\Python313\python.exe' -m tests.evaluation.runner --rag-weather-cases tests/evaluation/rag_weather_cases.jsonl --output build/evaluation-integration-final
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
git diff --check
```

结果：

- Python：`648 passed`，仅 1 条既有 Starlette/httpx 弃用警告。
- 前端：`63 passed / 0 failed`。
- RAG/天气离线评测：80 条；`citation_completeness`、`grounded_source_rate`、`refusal_accuracy`、`weather_boundary_accuracy` 均为 `1.0`。
- 公开仓库扫描通过；`git diff --check` 无输出。

### 仍未执行的外部部署验收（不可声称完成）

这些步骤需要项目拥有者在控制台完成，当前没有执行：

1. 在 Supabase SQL Editor 按顺序执行：
   - `supabase/migrations/008_rag_knowledge.sql`
   - `supabase/migrations/009_weather_quota.sql`
2. 以受控后端环境运行试点知识导入脚本（仅厦门、福建、云南资料）；确认私有表、RPC 与 RLS 正常。
3. 在 Render 服务的私有环境变量配置：
   - `JINA_API_KEY`
   - `AMAP_WEB_SERVICE_KEY`
   绝不能放入浏览器配置、Git、日志或聊天。
4. 触发 Render 部署后人工验收：
   - 明确城市的资料问答有中文来源；无资料/未知城市中文拒答。
   - `鼓浪屿游玩前需要怎样安排？` 能获取厦门试点资料；未知地点提示补充城市。
   - 天气卡与前三天行程天气正常；天气服务不可用时行程仍能生成。
   - 地图、默认关闭的 AI 助手与登录/历史行程均不回归。

### 下次恢复命令

```powershell
cd "D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp"
git status -sb
git log --oneline -8
```

注意：工作树仍包含用户自己未跟踪的计划与旧工作日志；保持不暂存、不提交、不删除。若需要发布，先创建/审阅草稿 PR，再合并到 `main`，Render 会按现有自动部署配置部署。
