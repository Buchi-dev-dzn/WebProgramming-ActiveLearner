import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse


DATABASE_URL = os.environ.get("DATABASE_URL")
db_pool: asyncpg.Pool | None = None


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=100_000_000)
    stock: int = Field(ge=0, le=1_000_000)


class ProductStockUpdate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    stock: int = Field(ge=0, le=1_000_000)


def product_to_dict(record: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": record["id"],
        "sku": record["sku"],
        "name": record["name"],
        "price_cents": record["price_cents"],
        "stock": record["stock"],
        "created_at": record["created_at"].isoformat(),
        "updated_at": record["updated_at"].isoformat(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            command_timeout=3,
        )
    try:
        yield
    finally:
        if db_pool:
            await db_pool.close()
            db_pool = None


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


def require_db_pool() -> asyncpg.Pool:
    if db_pool is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "database_unavailable"},
        )
    return db_pool


async def run_health_checks(request: Request) -> tuple[int, dict[str, Any]]:
    checks: dict[str, Any] = {"postgres": {"status": "not_configured"}}
    overall_status = "ok"

    if DATABASE_URL:
        try:
            async with asyncio.timeout(2):
                pool = require_db_pool()
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
        "database_access": "postgres is reachable only from fastapi-app on db_net",
        "sql_safety": "all product queries use asyncpg bind parameters",
        "request_id": extract_request_id(request),
    }


@app.get("/api/products")
async def list_products(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10_000),
):
    pool = require_db_pool()
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            SELECT id, sku, name, price_cents, stock, created_at, updated_at
            FROM products
            ORDER BY id
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return {
        "items": [product_to_dict(record) for record in records],
        "limit": limit,
        "offset": offset,
        "request_id": extract_request_id(request),
    }


@app.post("/api/products", status_code=201)
async def create_product(product: ProductCreate, request: Request):
    pool = require_db_pool()
    async with pool.acquire() as connection:
        try:
            record = await connection.fetchrow(
                """
                INSERT INTO products (sku, name, price_cents, stock)
                VALUES ($1, $2, $3, $4)
                RETURNING id, sku, name, price_cents, stock, created_at, updated_at
                """,
                product.sku,
                product.name,
                product.price_cents,
                product.stock,
            )
        except asyncpg.UniqueViolationError as error:
            raise HTTPException(
                status_code=409,
                detail={"error": "product_sku_already_exists"},
            ) from error
    return {
        "item": product_to_dict(record),
        "request_id": extract_request_id(request),
    }


@app.get("/api/product")
async def get_product(
    request: Request,
    sku: str = Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
):
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            SELECT id, sku, name, price_cents, stock, created_at, updated_at
            FROM products
            WHERE sku = $1
            """,
            sku,
        )
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "product_not_found"})
    return {
        "item": product_to_dict(record),
        "request_id": extract_request_id(request),
    }


@app.post("/api/product/stock")
async def update_product_stock(update: ProductStockUpdate, request: Request):
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            UPDATE products
            SET stock = $2,
                updated_at = now()
            WHERE sku = $1
            RETURNING id, sku, name, price_cents, stock, created_at, updated_at
            """,
            update.sku,
            update.stock,
        )
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "product_not_found"})
    return {
        "item": product_to_dict(record),
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
