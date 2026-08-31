# 单页导航与认证恢复实现计划

> **For the implementer:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` for each implementation task. Work in small red-green-refactor cycles and run the listed verification after each phase.

**目标：** 将探索、行程、社区、个人信息和管理员入口统一到同一应用壳内，以 History API 实现内部切换，并修复管理员认证失败循环。

**设计规格：** `docs/superpowers/specs/2026-08-23-single-page-navigation-design.md`

**约束：** 保留现有未提交改动；不批量 stage/commit；不修改无关脏文件；不改变 Supabase 权限模型和社区 API 契约。

## 任务 1：锁定路由与认证回归行为

**文件：** `tests/frontend/app.test.js`、`tests/frontend/community-admin.test.js`、必要时新增 `tests/frontend/router.test.js`

- 为五个导航项增加“同壳切换、不整页跳转”的失败测试。
- 增加 `pushState`、`popstate`、刷新深链接和 `aria-current` 测试。
- 增加管理员 401 清理过期会话后进入登录状态的测试。
- 增加管理员 403 保持在管理员视图、清空队列并显示无权限文案的测试。
- 明确测试隔离：每个测试重置 location、history、会话和 DOM。

## 任务 2：抽出最小单页路由器

**文件：** `app/static/app.js`、必要时新增 `app/static/app-router.js`、`app/static/index.html`、`app/static/styles.css`

- 保留探索/行程现有行为，增加规范化内部路由和视图注册表。
- 通过 `pushState` 切换视图，通过 `popstate` 恢复视图；内部导航不调用 `location.href`。
- 将导航项统一为社区风格的文字链接、当前项浅绿色胶囊和 `aria-current`。
- 共享头部与账户入口，避免各页面重复初始化运行时配置和 Supabase 客户端。
- 为旧 `/#trips-page` 和已有深链接提供兼容解析。

## 任务 3：接入社区视图

**文件：** `app/static/index.html`、`app/static/community.html`、`app/static/community.js`、`app/static/community-feed.js`、`app/static/community-note.js`、`app/static/community.css`

- 将社区列表作为应用壳内可挂载视图，复用现有公开加载、分类和搜索逻辑。
- 处理挂载/卸载，避免切换回来重复绑定事件或重复创建客户端。
- 保留 `/community` 直接访问和详情/创作者深链接兼容性。
- 保持匿名浏览、登录互动和公开字段白名单。

## 任务 4：接入个人信息视图

**文件：** `app/static/index.html`、`app/static/profile.html`、`app/static/profile.js`、`app/static/profile.css`、必要时 `app/static/styles.css`

- 将资料加载、保存和头像上传封装为可挂载视图。
- 未登录显示登录提示并保留回跳目标；登录状态只读取当前用户资料。
- 从个人信息返回时回到社区或来源内部路由，不触发整页循环。
- 维护社区视觉系统、表单宽度和按钮不换行规则。

## 任务 5：接入管理员视图并稳定权限失败

**文件：** `app/static/index.html`、`app/static/admin-community.js`、`app/static/admin-community.html`、`app/static/admin-community.css`

- 将审核队列控制器改为接收应用壳 root，保留三类队列、游标、重试和审核操作。
- 401 时只清理 stale session 一次并显示登录视图；用锁或状态机阻止重复跳转。
- 403 时清空队列、取消后续请求并显示无权限状态与返回探索操作。
- 会话变化时正确卸载队列监听和 pending 状态，防止跨账户数据残留。
- 管理员入口仅为导航便利，权限仍由后端校验。

## 任务 6：兼容包装与样式统一

**文件：** `app/main.py`、各独立 HTML、`app/static/styles.css`、`app/static/community.css`、相关页面测试

- 确认 `/community`、`/profile`、`/admin/community` 直接访问仍可用。
- 让独立页面复用同一导航语义和视觉变量，避免单页壳与直接入口样式分叉。
- 检查搜索、账户、管理员入口在窄屏下的布局和按钮宽度。

## 任务 7：验证与范围核对

- 先运行受影响的前端测试，修复红灯后再运行 `node --test tests/frontend/*.test.js`。
- 对所有静态 JavaScript 运行 `node --check`。
- 运行 `git diff --check HEAD`。
- 使用 `git status --short` 和按文件/按 hunk 的 diff 核对范围；不暂存、不提交、不删除其他脏文件。
- 最终记录实现状态、剩余已知问题和下一步提交建议。
