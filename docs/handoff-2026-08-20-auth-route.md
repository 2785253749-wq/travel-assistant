# 旅行助手交接文档：独立登录注册页（2026-08-20）

## 当前状态

- 项目：Voyage 旅行助手
- 工作树：`D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp`
- 分支：`agent/page-shell-my-trips`
- 最新提交：`c933143 fix: route authentication through standalone page`
- PR：[PR #22](https://github.com/2785253749-wq/travel-assistant/pull/22)
- PR 状态：Draft，等待审核、合并和 Render 部署
- 线上地址：`https://travel-assistant-2cbd.onrender.com`

## 本阶段背景

线上探索页曾同时出现两套认证状态：顶部账户菜单显示已登录，但页面下方仍保留旧的内嵌登录卡。原因是账户认证页被作为探索页内的隐藏/显示区块实现，容易与 Supabase session 回调产生状态不同步。

本阶段将登录注册改为真正的独立路由，避免认证表单继续嵌入探索页。

## 已完成

1. 新增后端路由：

   ```text
   GET /auth?mode=signin
   GET /auth?mode=signup
   ```

2. 新增独立页面和脚本：

   - `app/static/auth.html`
   - `app/static/auth.js`

3. 认证入口统一跳转到独立页面：

   - 账户菜单登录表单
   - 注册按钮
   - 账户页入口
   - “我的行程”页面的“去登录”按钮

4. 独立认证页支持：

   - 登录
   - 注册
   - 邮箱验证提示
   - 忘记密码邮件
   - 已有 Supabase session 时自动返回首页
   - 登录成功后返回首页
   - 中文错误提示，不直接暴露供应商错误信息

5. 删除探索页旧的内嵌认证页面及相关死代码，避免旧 DOM 再次显示。

## 配置要求

Render 环境需要继续配置以下公开浏览器认证变量：

- `SUPABASE_URL`
- `SUPABASE_ANON_KEY`

不要把以下敏感值写入仓库、截图或交接文档：

- `SUPABASE_SERVICE_KEY`
- `JINA_API_KEY`
- `MAP_WEB_SERVICE_KEY`
- 任何用户密码或完整 token

Supabase 侧还需要确认：

- Email provider 已启用；
- Confirm email 设置符合当前测试流程；
- Site URL 为 Render 线上地址；
- Redirect URLs 至少允许 `https://travel-assistant-2cbd.onrender.com/auth`。

## 验证结果

- 前端：`node --test tests/frontend/*.test.js` → **95 passed**
- 后端完整测试：`python -m pytest -q` → **665 passed**
- JS 语法：`node --check app/static/app.js` → 通过
- JS 语法：`node --check app/static/auth.js` → 通过
- 差异检查：`git diff --check` → 通过
- 后端测试有 1 条既有 Starlette/httpx 弃用警告，不影响结果

## 合并后验收顺序

1. 合并 PR #22，等待 Render 完成部署。
2. 打开：

   ```text
   https://travel-assistant-2cbd.onrender.com/auth?mode=signin
   ```

   确认页面只显示独立登录卡，不显示地图、探索推荐或 AI 助手。

3. 打开：

   ```text
   https://travel-assistant-2cbd.onrender.com/auth?mode=signup
   ```

   确认标题为“注册 Voyage 账户”，提交后显示邮箱验证提示。

4. 在探索页依次验证：

   - 账户菜单登录/注册入口跳转；
   - “我的行程”未登录时点击“去登录”跳转；
   - 登录成功回到首页；
   - 登录状态下不再出现旧的内嵌登录卡；
   - 退出登录后再次访问“我的行程”，仍显示登录提示。

5. 测试忘记密码：填写邮箱，点击“忘记密码”，确认收到邮件并能回到 `/auth`。

## 后续工作

1. PR #22 合并并完成 Render 线上验收。
2. 如认证流程通过，继续完成社区页和个人信息页。
3. 补充福州、大理景点、交通和避坑知识库。
4. 增加天气接口、RAG 配额和 AI 请求异常的生产监控。
5. 整理最终演示截图和发布说明。

## 恢复工作命令

```powershell
Set-Location 'D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp'
git status --short
node --test tests/frontend/*.test.js
```

当前工作树中已有用户未跟踪文档。恢复工作时不要使用 `git add .` 或 `git add -A`，只暂存明确属于当前任务的文件。
