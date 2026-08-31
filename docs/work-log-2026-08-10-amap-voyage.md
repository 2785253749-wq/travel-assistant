# 2026-08-10 高德地图 Voyage 探索页交接

## 已确认的决定

- 探索页只做福建、云南试点；全国地图数据以后再扩展。
- 使用本地占位视觉，保证离线也能演示；不接入真实图片、路线搜索、支付或真实社区。
- 用户选择高德「直接模式」：浏览器在脚本加载前使用 `securityJsCode`。实际 Key 和安全码绝不写进 Git、文档、日志或聊天记录。
- Render 上线时要配置两个变量：`AMAP_JS_KEY` 和 `AMAP_SECURITY_JS_CODE`；缺少任一项就必须使用可点击的离线地图。

## 已完成并已提交

- `14de053 feat: expose optional amap browser config`
- `6acca56 feat: add amap-backed offline map explorer`
- `bbfd437 fix: clean up amap loader failures`
- `ee68b1b feat: build voyage explore page trial`
- `3ca1e1f docs: explain amap explore trial deployment`
- `1caf981 test: protect amap deployment guidance`
- `c0635e6 fix: complete amap explore direct mode`

`c0635e6` 已完成：双变量运行时配置、安全码先于高德脚本加载、离线 SVG 热点、全国/省/市单实例地图、悬浮层返回按钮、动态省市卡、中文景点信息卡、真实前端模块接线和部署文档。

## 已验证结果

- 前端 Node：`36 passed, 0 failed`。
- 完整 Python：`505 passed`，仅 1 条已有 Starlette/httpx 弃用警告。
- 离线 80 条评测：所有指标 `1.0`，`unsupported_fact_rate = 0`，无失败门槛。
- 公开仓库敏感信息扫描通过。
- 评测执行中看到的 `model_provider_failure` / `agent_failed` 是刻意模拟的降级场景日志，不代表评测失败；以 `build/evaluation/evaluation-report.json` 的 `failed_thresholds: []` 为准。

## 当前未完成：最终复审阻断项

独立复审报告：
`.superpowers/sdd/2026-08-10-amap-voyage-explore-trial/final-fix-rereview.md`

结论为 **NOT READY**。还需要修复：

1. 网络慢时用户先从离线层进入城市，在线高德加载完成后可能错误回到全国视野。
2. 景点信息卡未显示该景点的“本地推荐”文字。
3. `createElement` 或脚本 `src` 赋值在严格浏览器安全策略下抛错时，尚未统一降级为离线地图。
4. 在线地图在省、市切换过程中如果 SDK 抛错，没有自动回退到离线可点击地图。

这些问题不影响已通过的离线流程，但会影响真实高德线上体验；下一次继续时应先修复这 4 项，再做一次 scoped re-review，不能直接宣称线上地图已验收。

## 下一次恢复步骤

1. 进入工作树：
   `cd "D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp"`
2. 阅读本文件、`.superpowers/sdd/2026-08-10-amap-voyage-explore-trial/final-fix-rereview.md` 和 `progress.md`。
3. 只针对上面 4 个阻断项做测试先行修复；不要改评测题库、阈值、鉴权、RLS、行程保存或配额逻辑。
4. 修复后执行 Node 全前端测试、完整 Python 测试、80 条离线评测、公开仓库扫描，并再次独立复审。
5. 自动验证通过后，再请用户在 Render 添加两个高德变量并重新部署；随后在真实浏览器验收：中国首屏、福建→厦门→景点平滑缩放、可见返回按钮/键盘操作、以及删除其中一个配置后的离线降级。

## Git 与文件注意事项

- 当前分支：`agent/zero-cost-public-mvp`；工作树：`D:\Users\Asus\Desktop\旅行助手\.worktrees\zero-cost-public-mvp`。
- 这份交接文档和下列用户原有未跟踪文件都不要加入提交：
  - `docs/superpowers/plans/2026-08-08-voyage-chinese-frontend.md`
  - `docs/superpowers/plans/2026-08-10-amap-voyage-explore-trial.md`
  - `docs/work-log-2026-07-30.md`
- 不要使用 `git reset --hard`、`git checkout --`、`git clean` 或删除上述文件。
