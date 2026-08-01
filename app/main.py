from contextlib import asynccontextmanager
import json
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
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

@app.get("/runtime-config.js", include_in_schema=False)
def runtime_config() -> Response:
    settings = get_settings()
    public_config = {
        "supabaseUrl": str(settings.supabase_url) if settings.supabase_url else None,
        "supabaseAnonKey": (
            settings.supabase_anon_key.get_secret_value()
            if settings.supabase_anon_key is not None
            else None
        ),
    }
    payload = json.dumps(public_config, ensure_ascii=True, separators=(",", ":"))
    return Response(
        f"window.TRAVEL_ASSISTANT_CONFIG=Object.freeze({payload});",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )

@app.get("/api/me")
def api_me(user: CurrentUser):
    return {"id": str(user.id), "email": user.email}

app.include_router(trips_router)
app.include_router(chat_router)
