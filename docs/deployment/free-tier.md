# Render + Supabase 免费层部署

本方案把 FastAPI Web 服务部署到 Render，把持久化数据与 RLS 放在 Supabase。两者可以从免费计划起步，但 DeepSeek 是独立的付费服务，整体方案不是“无限免费”。免费层条款、额度与休眠策略可能变化，部署前应在平台控制台再次确认。

## 1. 发布仓库前检查

从仓库根目录运行：

```powershell
python -m pytest -q
python -m tests.evaluation.runner --cases tests/evaluation/cases.jsonl --output build/evaluation
powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1
```

最后一个命令必须输出 `Public repository check passed`。再用 `git status --short` 和 `git ls-files` 人工确认 `.env`、`.venv/`、`.agents/`、缓存、数据库、日志和真实密钥都没有被跟踪。

## 2. 创建 Supabase 免费项目

1. 打开 [Supabase Dashboard](https://supabase.com/dashboard)，创建项目并妥善保存数据库密码。
2. 在 SQL Editor 按文件名顺序执行 `supabase/migrations/` 下的迁移：先 `001_initial.sql`，再两个 `002_*.sql`，最后 `003_ai_usage_reservation_protocol.sql`。
3. 在 Project Settings / API 中记录 Project URL、anon key 和 service-role key。
4. 不要把 service-role key 放入浏览器代码、提交记录、截图或 CI 日志。它只用于 Render 服务端 Secret。
5. 使用匿名会话实际验证 RLS：自己的行程可读写，其他会话的私有行程不可读；公开分享只通过迁移提供的受控 RPC 访问。

## 3. 创建 Render Blueprint

1. 把代码推送到公开 GitHub 仓库。
2. 打开 [Render Dashboard](https://dashboard.render.com/)，选择 **New > Blueprint**，连接仓库。
3. Render 会读取根目录的 `render.yaml`，创建 `travel-assistant` Python Web Service，使用免费计划、`/health` 健康检查和平台提供的 `$PORT`。
4. 在首次部署前填写所有标记为 `sync: false` 的值。Render 平台环境变量是唯一的生产 Secret 来源；不要上传或提交 `.env`。

| Render Secret | 值 |
|---|---|
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_ANON_KEY` | Supabase anon key |
| `SUPABASE_SERVICE_KEY` | Supabase service-role key，仅后端 |
| `ANON_SESSION_SIGNING_SECRET` | 32 个随机字节的 URL-safe base64，无 `=` 填充 |
| `DEEPSEEK_API_KEY` | 仅在决定启用 AI 后填写的 DeepSeek key |

可用 PowerShell 生成会话签名密钥，但只把输出粘贴到 Render Secret，不要保存到仓库：

```powershell
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
[Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
```

`APP_ENV=production` 会让应用在启动时检查 Supabase 与签名配置。缺失或明显的占位符会使部署失败，这是预期的安全行为。

`render.yaml` 还固定设置 `TRUSTED_CLIENT_IP_HEADER=cf-connecting-ip`，并通过 `--no-proxy-headers` 禁止 Uvicorn 信任通用转发头。匿名对话仍由签名 Cookie 隔离，但 AI 单用户额度使用客户端网络前缀的 HMAC 摘要；删除 Cookie 不会重置额度，服务端也不会保存原始 IP。这个模式基于 Render 公网流量经过 Cloudflare 的部署边界，只接受格式严格的单值 `CF-Connecting-IP`，完全忽略可由客户端构造链条的 `X-Forwarded-For`。缺失、重复或非法的可信头会统一落到共享的 fail-closed 额度主体，不会为请求创建新额度。参见 [Render 公网服务边界](https://render.com/docs/web-services)、[Render 的 Cloudflare 入口说明](https://render.com/docs/uptime-best-practices) 和 [Cloudflare 请求头契约](https://developers.cloudflare.com/fundamentals/reference/http-headers/)。

如果迁移到其他平台，先确认其入口无法被绕过、可信客户端地址头会由代理覆盖，随后再显式选择对应模式；当前未配置的平台默认忽略所有转发头并使用 socket peer。不要仅凭“代理跳数”信任 `X-Forwarded-For`。

登录用户额度按已验证的用户 UUID 计算；匿名网络额度和全局额度同时保留。共享网络可能共用匿名额度，这是防止低成本 Cookie 轮换绕过的明确取舍。Render 启动命令关闭 Uvicorn 原始 access log，改用应用的结构化请求日志。浏览器把 bearer 分享令牌保留在 URL fragment（fragment 不会发送到服务器），再通过固定的 `POST /api/shared/resolve` 请求体解析；令牌不会进入应用、Uvicorn 或 Render 平台的请求路径日志。

## 4. 先关闭 AI 做首次冒烟测试

首次部署建议把 Render 的 `AI_ENABLED` 改为 `false` 并重新部署，然后检查：

1. `GET https://<service>.onrender.com/health` 返回 `{"status":"ok"}`。
2. 首页、静态 CSS 与 JavaScript 可加载，浏览器源代码中没有服务端密钥。
3. 匿名会话能创建、读取、修改自己的行程，不能访问其他会话的私有行程。
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

## 7. 发布与回滚清单

- CI 的 pytest、独立 80 条评测和公开仓库扫描全部通过。
- `build/evaluation/evaluation-report.json` 显示 80 个用例、无失败阈值和无已知失败。
- Render `/health` 与核心用户旅程冒烟测试通过。
- `.env`、本地数据库、日志、缓存和真实凭据均未跟踪。
- 已记录当前 Render deploy ID、Supabase migration 状态和 Secret 轮换负责人。
- 回滚时在 Render 选择上一个健康 deploy；数据库迁移不要直接回滚，先制定兼容迁移。

公开发布 `v0.1.0` 时，应在发布说明中写明冷启动、免费层配额、DeepSeek 可能计费，以及不支持实时预订、价格/库存保证和高风险建议。
