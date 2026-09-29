from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import load_config
from src.core.logging import get_logger, setup_logging

settings = load_config()
setup_logging(json_format=settings.json_logs, log_level=settings.log_level)
logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "api_startup", version="0.1.0", host=settings.api_host, port=settings.api_port
    )
    yield
    logger.info("api_shutdown")


app = FastAPI(
    title="Autonomous Codebase Refactoring & Migration Agent API",
    version="0.1.0",
    description="Backend API and WebSocket service for Migration Agent",
    lifespan=lifespan,
)

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(
        "http_request_start",
        method=request.method,
        path=request.url.path,
    )
    response = await call_next(request)
    logger.info(
        "http_request_end",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
    )
    return response


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "name": "Autonomous Codebase Refactoring & Migration Agent",
        "status": "running",
        "version": "0.1.0",
        "docs_url": "/docs",
    }


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "api",
        "version": "0.1.0",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
    )
