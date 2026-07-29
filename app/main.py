from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.core.config import get_settings
from app.core.logging import configure_logging, request_context
from app.api.auth import CurrentUser
from app.api.trips import router as trips_router
from app.api.chat import router as chat_router

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
app.include_router(chat_router)
