import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


DATABASE_URL = os.environ.get("DATABASE_URL")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = None
    if DATABASE_URL:
        app.state.db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
    try:
        yield
    finally:
        pool = app.state.db_pool
        if pool is not None:
            await pool.close()


app = FastAPI(title="security-ec-fastapi", lifespan=lifespan)


@app.middleware("http")
async def add_request_id_header(request: Request, call_next):
    response = await call_next(request)
    request_id = request.headers.get("x-request-id")
    if request_id:
        response.headers["X-Request-Id"] = request_id
    return response


def extract_request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


async def run_health_checks(request: Request) -> tuple[int, dict[str, Any]]:
    checks: dict[str, Any] = {"postgres": {"status": "not_configured"}}
    overall_status = "ok"

    pool = request.app.state.db_pool
    if pool is not None:
        try:
            async with pool.acquire() as connection:
                await connection.fetchval("SELECT 1")
            checks["postgres"] = {"status": "ok"}
        except Exception as error:  # noqa: BLE001
            overall_status = "degraded"
            checks["postgres"] = {"status": "error", "detail": str(error)}

    payload = {
        "service": "fastapi-api",
        "status": overall_status,
        "checks": checks,
        "request_id": extract_request_id(request),
    }
    return (200 if overall_status == "ok" else 503), payload


@app.get("/health")
async def health(request: Request):
    status_code, payload = await run_health_checks(request)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/health")
async def api_health(request: Request):
    status_code, payload = await run_health_checks(request)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/api/info")
async def api_info(request: Request):
    return {
        "name": "security-ec-3tier",
        "service": "fastapi-api",
        "message": "fastapi reachable only through the reverse proxy and internal firewall",
        "via": ["reverse-proxy", "internal-firewall", "fastapi-api"],
        "dependencies": ["postgres"],
        "networks": ["api_net", "db_net"],
        "request_id": extract_request_id(request),
    }


@app.exception_handler(404)
async def not_found_handler(request: Request, _exc):
    return JSONResponse(
        status_code=404,
        content={
            "error": "not_found",
            "path": request.url.path,
            "request_id": extract_request_id(request),
        },
    )
