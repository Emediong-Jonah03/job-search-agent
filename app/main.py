from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db.session import engine
from app.dashboard.routes import router as dashboard_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Engine creation validates the configured PostgreSQL URL. Schema changes are migration-only.
    yield
    engine.dispose()


app = FastAPI(title="Job Application Agent", version="0.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key.get_secret_value(),
    session_cookie="job_agent_session",
    same_site="strict",
    https_only=settings.app_env == "production",
    max_age=8 * 60 * 60,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; form-action 'self'; base-uri 'self'; object-src 'self'")
    return response


app.mount("/static", StaticFiles(directory=Path(__file__).with_name("dashboard") / "static"), name="static")
app.include_router(dashboard_router)


@app.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
def ready() -> JSONResponse:
    settings = get_settings()
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return JSONResponse({"status": "ready", "environment": settings.app_env})
    except Exception:
        return JSONResponse({"status": "not_ready"}, status_code=503)

