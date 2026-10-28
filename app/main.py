from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Engine creation validates the configured PostgreSQL URL. Schema changes are migration-only.
    yield
    engine.dispose()


app = FastAPI(title="Job Application Agent", version="0.1.0", lifespan=lifespan)


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

