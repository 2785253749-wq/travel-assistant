# 旅行助手

基于 LangGraph、FastAPI 与 DeepSeek API 的旅行规划 Agent。

## 启动

需要 Python 3.11+：

```powershell
cd "$HOME\Desktop\旅行助手"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env` 填写 API Key，然后执行 `uvicorn app.main:app --reload`，访问 <http://127.0.0.1:8000>。

默认 `deepseek-v4-flash`；复杂规划可在 `.env` 改为 `deepseek-v4-pro`。当前版本支持多轮需求收集、会话记忆、行程与预算建议，但不会执行真实预订。
