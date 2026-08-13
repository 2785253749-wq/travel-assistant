# Task 8 — RAG 与天气部署验收清单

## 完成内容

- README 明确要求按顺序执行 `008_rag_knowledge.sql` 与 `009_weather_quota.sql`。
- 记录知识导入前置条件、Render 私有变量配置时机，以及浏览器、日志和提交记录不得包含真实凭据的边界。
- 增加真实浏览器验收清单：厦门天气卡、资料内问答来源、资料外拒答、四日行程的非实时天气边界，以及天气失败时仍可生成行程。
- 增加部署文档合约测试，防止关键验收步骤被意外删除。

## 已验证（离线）

- `python -m pytest tests/integration/test_rag_migration_contract.py tests/integration/test_weather_api.py -q`：7 项通过；测试运行环境产生既有 Starlette/httpx 弃用警告。
- `powershell -ExecutionPolicy Bypass -File scripts/verify_public_repo.ps1`：`Public repository check passed`。

## 未执行的外部操作

- 未执行 Supabase 迁移。
- 未导入知识资料，未调用 Jina 或高德天气服务。
- 未在 Render 写入任何变量，未进行线上浏览器验收。

线上验收应由部署负责人按 README 的人工验收清单执行，并且只保存状态码、用例 ID 与可公开摘要。
