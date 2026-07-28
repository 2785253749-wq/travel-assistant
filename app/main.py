from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.core.config import get_settings
from app.core.logging import configure_logging, request_context
from app.graph import chat
from app.schemas import ChatRequest, ChatResponse, TravelProfile
from app.api.auth import CurrentUser
from app.api.trips import router as trips_router

BASE = Path(__file__).resolve().parent
configure_logging()

@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    yield

app = FastAPI(title="旅行助手", version="0.1.0", lifespan=lifespan)
app.middleware("http")(request_context)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

@app.get("/", include_in_schema=False)
def home(): return FileResponse(BASE / "static" / "index.html")

@app.get("/health")
def health(): return {"status":"ok"}

@app.get("/api/me")
def api_me(user: CurrentUser):
    return {"id": str(user.id), "email": user.email}

app.include_router(trips_router)

@app.post("/api/chat", response_model=ChatResponse)
def api_chat(request: ChatRequest):
    try:
        result = chat(request.message, request.thread_id)
        return ChatResponse(reply=result["reply"], stage=result["stage"], profile=TravelProfile.model_validate(result.get("profile") or {}))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
