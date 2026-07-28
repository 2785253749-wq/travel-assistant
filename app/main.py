from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.graph import chat
from app.schemas import ChatRequest, ChatResponse, TravelProfile

BASE = Path(__file__).resolve().parent
app = FastAPI(title="旅行助手", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

@app.get("/", include_in_schema=False)
def home(): return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
def health(): return {"status":"ok"}

@app.post("/api/chat", response_model=ChatResponse)
def api_chat(request: ChatRequest):
    try:
        result = chat(request.message, request.thread_id)
        return ChatResponse(reply=result["reply"], stage=result["stage"], profile=TravelProfile.model_validate(result.get("profile") or {}))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
