# Task 14 最终交接

## 范围

Task 14 验证并收紧跨层安全、开发 wiring、测试状态隔离、媒体清理队列、管理员审核边界和部署操作。Task 9–13 明确改变的页面/字段断言已在测试中记录原因；其他 wiring 失败按生产问题修复。

## 已完成验收

- 匿名用户只能读取公开游记、公开资料和已审核评论。
- 账户 A/B 的私有状态、收藏、媒体、草稿和待审评论相互隔离。
- 管理员队列覆盖游记、评论、举报；非管理员返回 403；队列图片使用签名 URL。
- `actioned` 只关闭举报并记录决定，不隐式修改评论或游记状态。
- service-role 密钥不会进入运行时前端配置。
- 日志使用哈希主体和稳定错误码，不记录邮箱、原始 UUID 或 Storage 路径。
- 测试对缓存、依赖覆盖和内存仓储进行显式清理。

## 验证命令

```text
pytest -q
node --test tests/frontend/*.test.js
node --check app/static/admin-community.js
git diff --check
```

实际结果：后端 `pytest -q` 为 839 passed、1 warning；前端 `node --test tests/frontend/*.test.js` 为 147 passed；静态 JavaScript 11 个文件全部 `node --check` 通过；`git diff --check` 通过，仅有 LF→CRLF 提示。仓库保持未提交状态。

## 变更范围

- Task 14 专属测试：`tests/integration/test_task14_acceptance.py`。
- Task 14 规格、计划、部署和交接文档：`docs/` 下对应文件。
- 基线修复仅涉及开发/生产 wiring、内存媒体清理队列和明确过时的 Task 9–13 测试契约。
- 其他工作树脏文件不删除、不恢复、不批量整理、不提交。
