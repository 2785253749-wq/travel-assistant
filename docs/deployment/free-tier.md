# Render + Supabase 免费层部署

本方案把 FastAPI Web 服务部署到 Render，把持久化数据与 RLS 放在 Supabase。两者可以从免费计划起步，但 DeepSeek 是独立的付费服务，整体方案不是“无限免费”。免费层条款、额度与休眠策略可能变化，部署前应在平台控制台再次确认。

## 1. 发布仓库前检查

从仓库根目录运行：

```powershell
python -m pytest -q
python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
```

最后一个命令必须输出 `Public repository check passed`。再用 `git status --short` 和 `git ls-files` 人工确认 `.env`、`.venv/`、`.agents/`、`.codex/`、`.claude/`、编辑器设置、工作树、依赖/构建产物、缓存、数据库、日志和真实密钥都没有被跟踪。`.superpowers/` 只有经过复核且符合 `.superpowers/sdd/<日期-任务>/<名称>-report.md` 的工程报告允许公开，其他内容必须保持未跟踪。

## 2. 创建 Supabase 免费项目

1. 打开 [Supabase Dashboard](https://supabase.com/dashboard)，创建项目并妥善保存数据库密码。
2. 在 SQL Editor 严格按文件名顺序执行 `supabase/migrations/` 下的全部迁移（当前为 `001` 至 `009`）。其中 `008_rag_knowledge.sql` 创建私有知识资料与嵌入额度，`009_weather_quota.sql` 创建天气调用额度；不得跳过原子行程保存、模型成本计量与模型调用槽迁移。
3. 在 Project Settings / API 中记录 Project URL、anon key 和 service-role key。
4. 不要把 service-role key 放入浏览器代码、提交记录、截图或 CI 日志。它只用于 Render 服务端 Secret。
5. 使用两个已登录测试账号实际验证 RLS：账号 A 可创建、读取和修改自己的私有行程，账号 B 不可读取或修改账号 A 的行程；匿名会话不得访问任何私有 CRUD。公开分享只通过迁移提供的受控 RPC 访问。

## 3. 创建 Render Blueprint

1. 把代码推送到公开 GitHub 仓库。
2. 打开 [Render Dashboard](https://dashboard.render.com/)，选择 **New > Blueprint**，连接仓库。
3. Render 会读取根目录的 `render.yaml`，创建 `travel-assistant` Python Web Service，使用免费计划、`/health` 健康检查和平台提供的 `$PORT`。
4. 在首次部署前填写所有标记为 `sync: false` 的值。Render 平台环境变量是唯一的生产 Secret 来源；不要上传或提交 `.env`。

| Render Secret | 值 |
|---|---|
| `JINA_API_KEY` | 仅后端导入与检索使用的 Jina key；作为 Render 私有变量，不得填入浏览器、日志或提交记录 |
| `AMAP_WEB_SERVICE_KEY` | 仅后端天气与足迹城市边界服务使用的高德 Web 服务 Key；作为 Render 私有变量，不得填入浏览器、日志或提交记录，也不得复用地图 JavaScript API Key |
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_ANON_KEY` | Supabase anon key |
| `SUPABASE_SERVICE_KEY` | Supabase service-role key，仅后端 |
| `ANON_SESSION_SIGNING_SECRET` | 32 个随机字节的 URL-safe base64，无 `=` 填充 |
| `DEEPSEEK_API_KEY` | 仅在决定启用 AI 后填写的 DeepSeek key |
| `AMAP_JS_KEY` | 高德 JavaScript 地图浏览器 Key；仅用于 Explore 地图试点，不填写时自动使用本地离线 SVG 地图 |
| `AMAP_SECURITY_JS_CODE` | 与 JavaScript API Key 配套的安全密钥；直连模式会交给浏览器，必须同时配置并限制允许域名 |
| `AI_INPUT_COST_MICROS_PER_MILLION_TOKENS` | 按当前供应商账单维护的每百万输入 token 微元费率；`0` 表示不估算金额 |
| `AI_OUTPUT_COST_MICROS_PER_MILLION_TOKENS` | 按当前供应商账单维护的每百万输出 token 微元费率；`0` 表示不估算金额 |
| `REQUEST_ANONYMOUS_PER_MINUTE` | 匿名网络每分钟聊天请求上限 |
| `REQUEST_AUTHENTICATED_PER_MINUTE` | 登录用户每分钟聊天请求上限 |
| `REQUEST_IP_PER_MINUTE` | 可信客户端网络前缀每分钟聊天请求上限 |

可用 PowerShell 生成会话签名密钥，但只把输出粘贴到 Render Secret，不要保存到仓库：

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
```

### RAG 与高德天气后端配置

迁移清单中的旧编号说明已更新：上线前必须按顺序执行全部迁移，至少确认 `008_rag_knowledge.sql` 和 `009_weather_quota.sql` 均成功。008 创建私有知识资料与嵌入额度，009 创建天气调用额度；两者都不是浏览器端操作。

仅在迁移成功、受控环境完成知识导入后，才在 Render **Environment** 添加 `JINA_API_KEY` 和 `AMAP_WEB_SERVICE_KEY`。两项都是后端私有变量，真实值不得填入浏览器、日志或提交记录，也不得写入 `.env.example`、截图或公开文档。`AMAP_WEB_SERVICE_KEY` 用于后端天气查询和足迹城市边界查询，不能替代地图使用的 `AMAP_JS_KEY`。

未配置 `JINA_API_KEY` 时，资料问答返回“资料库没有足够依据，无法可靠回答。”；未配置 `AMAP_WEB_SERVICE_KEY` 或天气上游失败时，显示“天气信息暂不可用”。两种降级都不得阻塞既有规划、保存与分享，行程仍可正常生成。

`APP_ENV=production` 会让应用在启动时检查 Supabase 与签名配置。缺失或明显的占位符会使部署失败，这是预期的安全行为。

### 高德 Explore 地图试点配置

当前 Explore 地图只覆盖 **福建、云南** 的试点数据；可点击进入厦门、福州、大理、丽江及其本地热门景点。它不覆盖全国地图、真实景点图片、实时搜索或路线、票务/酒店支付和社区功能。

1. 在高德开放平台创建 **JavaScript API** 的浏览器 Key；浏览器地图直连模式不使用 `AMAP_WEB_SERVICE_KEY`，也不会通过地图脚本从后端调用高德 Web 服务。后端天气与足迹城市边界服务使用 `AMAP_WEB_SERVICE_KEY`，其 Render 私有变量配置见上文“RAG 与高德天气后端配置”。
2. 在该 Key 的安全设置中只允许生产域名 `travel-assistant-2cbd.onrender.com` 与本地开发地址 `http://127.0.0.1`。不要使用宽泛的通配域名。
3. 在 Render 的 **Environment** 页面新增或更新 `AMAP_JS_KEY` 与 `AMAP_SECURITY_JS_CODE`，两项必须同时填写并保存。不要把真实 Key 或安全密钥写入 Git、`.env.example`、截图、日志或任何公开文档。
4. 触发 Render 重新部署后，访问 <https://travel-assistant-2cbd.onrender.com>，打开 Explore 页面验证地图。

地图以高德 JavaScript 地图为优先实现。当前采用浏览器直连模式，运行时会把 Key 与安全密钥交给浏览器；域名限制是必需的安全边界。若未来需要更强保护，应另行设计服务代理，本轮不实现。若两项变量缺少任意一个、高德脚本被网络策略拦截或初始化失败，页面必须自动降级为本地**离线** SVG 地图；离线模式仍可点击福建/云南、省内城市和景点标记，且不会因此调用 AI。

人工验收：同时配置两个已轮换变量并重新部署，先确认全国高德地图首屏，再验证福建 → 厦门 → 任一景点的平滑缩放、鼠标/键盘返回和本地助手推荐；然后暂时移除其中任意一个变量（或在浏览器拦截高德脚本）并重新部署，确认离线 SVG 仍可完成同一点击流程。验收后如继续使用高德地图，再恢复变量并重新部署。

`render.yaml` 还固定设置 `TRUSTED_CLIENT_IP_HEADER=cf-connecting-ip`，并通过 `--no-proxy-headers` 禁止 Uvicorn 信任通用转发头。匿名对话仍由签名 Cookie 隔离，但 AI 单用户额度使用客户端网络前缀的 HMAC 摘要；删除 Cookie 不会重置额度，服务端也不会保存原始 IP。这个模式基于 Render 公网流量经过 Cloudflare 的部署边界，只接受格式严格的单值 `CF-Connecting-IP`，完全忽略可由客户端构造链条的 `X-Forwarded-For`。缺失、重复或非法的可信头会统一落到共享的 fail-closed 额度主体，不会为请求创建新额度。参见 [Render 公网服务边界](https://render.com/docs/web-services)、[Render 的 Cloudflare 入口说明](https://render.com/docs/uptime-best-practices) 和 [Cloudflare 请求头契约](https://developers.cloudflare.com/fundamentals/reference/http-headers/)。

如果迁移到其他平台，先确认其入口无法被绕过、可信客户端地址头会由代理覆盖，随后再显式选择对应模式；当前未配置的平台默认忽略所有转发头并使用 socket peer。不要仅凭“代理跳数”信任 `X-Forwarded-For`。

登录用户额度按已验证的用户 UUID 计算；匿名网络额度和全局额度同时保留。每日 AI 额度统计实际模型 invoke 次数，而不是聊天请求数；每次规划在开始前原子预留首次调用和一次 repair 共 `2` 个槽，结算成功时按实际调用数记账并释放未使用槽。若结算暂时失败，持久 reservation 即使过期也会按最坏情况继续占用这 `2` 个槽，直到后续结算或当天额度窗口结束，绝不会自动释放后造成全局日上限超卖。共享网络可能共用匿名额度，这是防止低成本 Cookie 轮换绕过的明确取舍。Render 启动命令关闭 Uvicorn 原始 access log，改用应用的结构化请求日志。浏览器把 bearer 分享令牌保留在 URL fragment（fragment 不会发送到服务器），再通过固定的 `POST /api/shared/resolve` 请求体解析；令牌不会进入应用、Uvicorn 或 Render 平台的请求路径日志。

## 4. 先关闭 AI 做首次冒烟测试

首次部署建议把 Render 的 `AI_ENABLED` 改为 `false` 并重新部署，然后检查：

1. `GET https://<service>.onrender.com/health` 返回 `{"status":"ok"}`。
2. 首页、静态 CSS 与 JavaScript 可加载，浏览器源代码中没有服务端密钥。
3. 匿名用户只能使用未持久化的对话规划；只有登录用户可创建、读取、修改自己的私有行程，并必须验证另一个登录用户无法访问该行程。
4. 公开分享只暴露允许的行程字段。
5. Render 日志没有凭据，也没有完整用户输入等敏感内容。

确认数据与权限路径后再把 `AI_ENABLED` 改为 `true`。保留较低的 `AI_USER_DAILY_LIMIT` 和 `AI_GLOBAL_DAILY_LIMIT`，观察真实用量后再调整。

## 5. 冷启动与免费层限制

Render 免费 Web Service 可能在空闲后休眠。唤醒时第一个请求会明显变慢，健康检查与浏览器请求也可能在启动窗口内超时；等待实例启动后重试。不要用免费层承诺持续在线、固定延迟、备份恢复目标或生产 SLA。

Supabase 免费项目受数据库容量、出口流量、连接数和可能的暂停策略影响。达到配额时，应用应显示安全降级结果，而不是绕过 RLS 或切换到本地数据库。面向公众之前应另行制定监控、备份、恢复与升级预算。

## 6. DeepSeek 费用与止费开关

Render 与 Supabase 使用免费计划时，DeepSeek API 调用仍可能计费。离线测试和 80 条评测不需要真实 key，也不会发出网络请求。不要运行 `--live` 评测。

需要立即停止 AI 费用时：

1. 在 Render 将 `AI_ENABLED=false`，触发重新部署。
2. 可同时把 `AI_USER_DAILY_LIMIT=0` 和 `AI_GLOBAL_DAILY_LIMIT=0`。
3. 若怀疑密钥泄露，在 DeepSeek 控制台撤销并轮换 key；再检查 Git 历史和 Render 日志。

关闭 AI 不会删除已保存行程；静态页面、认证、RLS 和非 AI CRUD 仍可单独验证。

请求速率限制在当前 `render.yaml` 的单进程 Web 服务内以原子内存桶执行，并分别约束匿名网络、登录用户和客户端网络前缀。若将服务扩展到多个进程或多个实例，发布前必须把这些分钟级桶迁移到共享存储；每日 AI 模型调用额度由 Supabase RPC 在同一日期锁下原子预留最坏 `2` 个槽，结算成功后按实际调用数释放余量，结算未知则保留全部预留槽以 fail-closed。模型调用/token/成本估算跨进程保存。成本金额是按上述可配置费率计算的估算值，最终费用以供应商账单为准。

## 7. 发布与回滚清单

- CI 的 pytest、独立 80 条评测和公开仓库扫描全部通过。
- `build/evaluation/evaluation-report.json` 显示 80 个用例、无失败阈值和无已知失败。
- Render `/health` 与核心用户旅程冒烟测试通过。
- `.env`、本地数据库、日志、缓存和真实凭据均未跟踪。
- 已记录当前 Render deploy ID、Supabase migration 状态和 Secret 轮换负责人。
- 回滚时在 Render 选择上一个健康 deploy；数据库迁移不要直接回滚，先制定兼容迁移。

公开发布 `v0.1.0` 时，应在发布说明中写明冷启动、免费层配额、DeepSeek 可能计费，以及不支持实时预订、价格/库存保证和高风险建议。

## 8. 当前发布证据状态

当前仓库只包含可复现的部署配置和验证步骤，**不包含已验证的公开 URL、Render deploy ID、线上 smoke 输出或 `v0.1.0` tag**。这些外部证据在实际完成 Render/Supabase 部署前均为 `BLOCKED`，不得把 `https://<service>.onrender.com` 占位符、离线 `TestClient` 结果或本地 Git 提交描述为线上证据。

逐项证据状态记录在 [release-evidence.md](release-evidence.md)，当前保持 `BLOCKED`。

解除阻塞后，发布记录至少要写入：公开 HTTPS URL、对应 commit SHA、Render deploy ID、已执行的迁移编号、线上 `/health` 响应时间与核心登录/规划/修改/解释/RLS smoke 结果；随后才可创建并推送 `v0.1.0` tag。
