# 旅行助手

一个面向国内自由行的公开 MVP：用户通过多轮对话补全目的地、日期、人数和预算，系统生成带预算结构与来源引用的行程草案，并保存到 Supabase。后端采用 FastAPI 与显式的确定性工作流，规划模型可使用 DeepSeek。

## 垂直场景

本项目只解决“国内多日自由行草案”这一条窄场景。例如：“两个人 10 月去西安 4 天，预算 6000 元，偏好历史与美食”。助手会追问缺失字段、生成日程和预算、解释或修改已保存行程。

它不会代订机票、酒店或门票，不保证实时价格、库存、天气或绝对安全，也不提供签证、医疗、法律、投资等高风险结论。所有行程、预算和外部信息都需要用户在出发前自行复核。

## 架构

```text
Browser
  -> FastAPI routes and static UI
     -> SafeTravelAgent / deterministic workflow
        -> deterministic safety, intent and profile extraction
        -> DeepSeek structured planning (optional and rate-limited)
        -> free weather/place evidence providers
     -> TripService -> Supabase/Postgres
```

浏览器只接触匿名会话标识和 Supabase anon 配置；DeepSeek key、Supabase service key 与会话签名密钥只存在于服务端环境变量中。生产环境不会从仓库读取 `.env`。

## 本地启动

需要 Python 3.11+（CI 与 Render 使用 3.13）。

```powershell
git clone https://github.com/2785253749-wq/travel-assistant.git
cd travel-assistant
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

访问 <http://127.0.0.1:8000>，健康检查为 <http://127.0.0.1:8000/health>。真实凭据只写入未跟踪的 `.env`，不要修改 `.env.example` 来保存密钥。

## 配置

| 变量 | 必需 | 默认值/用途 |
|---|---|---|
| `APP_ENV` | 生产必需 | `development`；生产设为 `production` |
| `LOG_LEVEL` | 否 | `INFO` |
| `DEEPSEEK_API_KEY` | AI 开启时 | DeepSeek 服务端密钥 |
| `DEEPSEEK_MODEL` | 否 | `deepseek-v4-flash` |
| `DEEPSEEK_API_BASE` | 否 | `https://api.deepseek.com` |
| `AMAP_JS_KEY` | 地图在线模式 | 高德 JavaScript API 浏览器 Key；必须与安全密钥同时配置，否则使用离线地图 |
| `AMAP_SECURITY_JS_CODE` | 地图在线模式 | 高德 JavaScript API 安全密钥；直连模式会交给浏览器，必须在高德控制台限制允许域名 |
| `SUPABASE_URL` | 生产必需 | Supabase 项目 URL |
| `SUPABASE_ANON_KEY` | 生产必需 | 浏览器可用的 anon key，仍受 RLS 保护 |
| `SUPABASE_SERVICE_KEY` | 生产必需 | 仅服务端使用，绝不能暴露到前端或仓库 |
| `ANON_SESSION_SIGNING_SECRET` | 生产必需 | 至少 32 字节随机值的 URL-safe base64（无填充） |
| `AI_ENABLED` | 否 | `true`；设为 `false` 可立即停用 AI 调用 |
| `AI_USER_DAILY_LIMIT` | 否 | 单用户每日模型调用上限，默认 `5`；每次规划先预留首次调用和 repair 共 `2` 个槽，结算成功后按实际调用数释放余量 |
| `AI_GLOBAL_DAILY_LIMIT` | 否 | 全局每日模型调用上限，默认 `100`；预留与结算均由 Supabase 原子执行，结算未知时保留全部预留槽以 fail-closed |
| `REQUEST_ANONYMOUS_PER_MINUTE` | 否 | 匿名网络每分钟聊天请求上限，默认 `30` |
| `REQUEST_AUTHENTICATED_PER_MINUTE` | 否 | 登录用户每分钟聊天请求上限，默认 `120` |
| `REQUEST_IP_PER_MINUTE` | 否 | 单一可信网络前缀每分钟聊天请求上限，默认 `180` |
| `AI_INPUT_COST_MICROS_PER_MILLION_TOKENS` | 否 | 每百万输入 token 的微元费率；默认 `0` 表示仅记录调用与 token，不输出金额估算 |
| `AI_OUTPUT_COST_MICROS_PER_MILLION_TOKENS` | 否 | 每百万输出 token 的微元费率；默认 `0`；应按供应商当前账单价格人工维护 |

## 测试与发布门禁

```powershell
python -m pytest -q
python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
```

固定离线评测包含 80 条用例：20 条完整规划、20 条缺失或矛盾需求、15 条拒绝场景、15 条自然语言变体和 10 条供应商/模型/额度异常。发布门禁检查意图准确率、槽位 micro-F1、澄清召回率、拒绝精确率与召回率、结构和预算有效率、引用覆盖率与有效率、不受支持事实率、任务成功率和降级成功率。80 条门禁明确标记为组件 fixture，不调用网络或付费模型；报告另含生产规则与应用编排的 plan/save/modify/explain/reopen 离线流程、P50/P95 和零调用成本口径，不能冒充线上模型基准。阈值见 `tests/evaluation/baseline.json`，详细口径见 `docs/evaluation/README.md`。

CI 对每次 push 和 pull request 运行完整 pytest、独立的 80 条离线评测以及公开仓库敏感信息扫描，并保存评测报告为构建产物。

## 高德地图 Explore 试点

Explore 页面当前只实现 **福建、云南** 两个省份的地图试点：可从省级地图进入厦门、福州、大理、丽江，并查看各城市的本地热门景点标记。地图优先使用高德 JavaScript 地图；`AMAP_JS_KEY` 与 `AMAP_SECURITY_JS_CODE` 缺少任意一个、脚本加载失败或地图初始化失败时，页面会自动切换到可点击的本地离线 SVG 地图，基础浏览流程仍可运行。

该试点不包含全国地图数据、真实景点图片、实时搜索与路线、票务或酒店支付、社区内容等能力。地图点选只会在前端打开助手并展示本地推荐，不会自动发起 AI 请求。

高德 Key 与安全密钥分别通过 Render 环境变量 `AMAP_JS_KEY`、`AMAP_SECURITY_JS_CODE` 配置，禁止把真实值写入 `.env.example`、源码、文档、日志或 Git 提交记录。当前直连模式会把这两项浏览器配置交给前端，因此必须在高德控制台限制允许域名；若未来需要更强隔离，应另行设计服务代理。具体的生产配置和验收步骤见 [Render + Supabase 免费层部署说明](docs/deployment/free-tier.md)。

## 免费层部署

详细步骤见 [Render + Supabase 免费层部署说明](docs/deployment/free-tier.md)。简要流程：

1. 在 [Supabase Dashboard](https://supabase.com/dashboard) 创建免费项目并执行 `supabase/migrations/` 中的迁移。
2. 在 [Render Dashboard](https://dashboard.render.com/) 通过仓库根目录的 `render.yaml` 创建 Blueprint。
3. 在 Render 平台输入所有 `sync: false` 的 Secret；不要上传 `.env`。
4. 部署后先检查 `/health`，再执行文档中的在线冒烟测试。

Render 免费实例可能在空闲后休眠，首次访问会有明显冷启动延迟；免费层配额与策略也可能调整，不适合可用性承诺或生产 SLA。Supabase 免费项目同样受数据库容量、带宽和休眠策略限制。

Render 与 Supabase 免费层不代表 DeepSeek 免费。任何真实 AI 请求都可能产生费用。首次公开部署建议先设置 `AI_ENABLED=false`，确认认证、RLS、行程 CRUD 和静态页面后再启用；需要紧急止费时，在 Render 将 `AI_ENABLED` 改为 `false` 并重新部署，也可把两个每日额度设为 `0`。不要运行带 `--live` 的评测。

## 公开仓库边界

发布前必须运行 `scripts/verify_public_repo.ps1`。脚本只扫描 `git ls-files` 返回的已跟踪文件，并拒绝 `.env`、虚拟环境、编辑器/Agent 本地配置、工作树、依赖与构建产物、Python/pytest 缓存、数据库、日志、真实 DeepSeek/Supabase/会话签名密钥、GitHub token、裸 secret token 和私钥头；JSON、YAML、TOML、JavaScript 与 env 风格的敏感赋值都在扫描范围内。仓库可公开 `.env.example` 中的占位符与环境变量引用，但不能公开任何真实凭据。

`.superpowers/` 默认属于本地工作元数据。公开仓库只允许 `.superpowers/sdd/<日期-任务>/<名称>-report.md` 这一种经过复核的工程报告路径，其他 `.superpowers` 内容一律由发布门禁拒绝；允许的报告仍会接受完整凭据扫描。
