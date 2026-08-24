from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.config import get_settings
from app.core.http import ChatNetworkRateLimitMiddleware, RequestBodyLimitMiddleware
from app.core.logging import configure_logging, operational_context, request_context
from app.api.auth import CurrentUser
from app.api.trips import router as trips_router
from app.api.chat import router as chat_router
from app.api.community import router as community_router
from app.api.profile import router as profile_router
from app.api.travel_notes import router as travel_notes_router
from app.api.community_interactions import router as community_interactions_router
from app.api.community_moderation import router as community_moderation_router
from app.api.weather import router as weather_router

BASE = Path(__file__).resolve().parent
configure_logging()

@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    yield

app = FastAPI(title="旅行助手", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(ChatNetworkRateLimitMiddleware)
app.middleware("http")(request_context)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


def _error_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unavailable")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, error: StarletteHTTPException) -> JSONResponse:
    request_id = _error_request_id(request)
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": jsonable_encoder(error.detail), "request_id": request_id},
        headers={**(error.headers or {}), "X-Request-ID": request_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    del error
    request_id = _error_request_id(request)
    return JSONResponse(
        status_code=422,
        content={
            "detail": {"code": "REQUEST_INVALID", "message": "Request validation failed"},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
    request_id = _error_request_id(request)
    logging.getLogger("app.error").error(
        "unhandled_error",
        extra=operational_context(
            request_id=request_id,
            error_code="INTERNAL_ERROR",
            exception_type=type(error).__name__,
        ),
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {"code": "INTERNAL_ERROR", "message": "Service temporarily unavailable"},
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

@app.get("/", include_in_schema=False)
def home(): return FileResponse(BASE / "static" / "index.html")

@app.get("/auth", include_in_schema=False)
def auth_page(): return FileResponse(BASE / "static" / "auth.html")

@app.get("/profile", include_in_schema=False)
def profile_page(): return FileResponse(BASE / "static" / "profile.html")

@app.get("/health")
def health(): return {"status":"ok"}

@app.get("/runtime-config.js", include_in_schema=False)
def runtime_config() -> Response:
    settings = get_settings()
    public_config = {
        "amapJsKey": (
            settings.amap_js_key.get_secret_value()
            if settings.amap_js_key is not None
            else None
        ),
        "amapSecurityJsCode": (
            settings.amap_security_js_code.get_secret_value()
            if settings.amap_security_js_code is not None
            else None
        ),
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
app.include_router(community_router)
app.include_router(travel_notes_router)
app.include_router(community_interactions_router)
app.include_router(community_moderation_router)
app.include_router(profile_router)
app.include_router(weather_router)
