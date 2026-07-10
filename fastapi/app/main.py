import asyncio
import base64
import hashlib
import hmac
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse


DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET_KEY_B64 = os.environ.get("JWT_SECRET_KEY_B64")
DATA_ENCRYPTION_KEY_B64 = os.environ.get("DATA_ENCRYPTION_KEY_B64")
EMAIL_LOOKUP_KEY_B64 = os.environ.get("EMAIL_LOOKUP_KEY_B64")
PASSWORD_ITERATIONS = 600_000
AUTH_KEY_ID = "dev-key-1"
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "security-ec-3tier"
ACCESS_TOKEN_SECONDS = 900
db_pool: asyncpg.Pool | None = None
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=100_000_000)
    stock: int = Field(ge=0, le=1_000_000)


class ProductStockUpdate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    stock: int = Field(ge=0, le=1_000_000)


class UserRegister(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="customer", pattern=r"^(customer|seller)$")


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class SellerProfileUpsert(BaseModel):
    store_name: str = Field(min_length=1, max_length=120)
    store_description: str = Field(default="", max_length=2000)
    business_email: str | None = Field(default=None, min_length=3, max_length=254)
    phone: str | None = Field(default=None, min_length=5, max_length=40)
    business_address: str | None = Field(default=None, min_length=1, max_length=1000)
    payout_account_token: str | None = Field(default=None, min_length=1, max_length=200)


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


def user_to_public_dict(record: asyncpg.Record, email: str) -> dict[str, Any]:
    return {
        "id": record["id"],
        "email": email,
        "role": record["role"],
        "is_active": record["is_active"],
        "created_at": record["created_at"].isoformat(),
        "last_login_at": (
            record["last_login_at"].isoformat()
            if record["last_login_at"]
            else None
        ),
    }


def seller_profile_to_dict(record: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": record["id"],
        "user_id": record["user_id"],
        "store_name": record["store_name"],
        "store_description": record["store_description"],
        "business_email": decrypt_optional(
            record["business_email_ciphertext"],
            record["business_email_nonce"],
        ),
        "phone": decrypt_optional(record["phone_ciphertext"], record["phone_nonce"]),
        "business_address": decrypt_optional(
            record["business_address_ciphertext"],
            record["business_address_nonce"],
        ),
        "verification_status": record["verification_status"],
        "has_payout_account_token": bool(record["payout_account_token"]),
        "created_at": record["created_at"].isoformat(),
        "updated_at": record["updated_at"].isoformat(),
    }


def decode_required_key(value: str | None, name: str, expected_len: int = 32) -> bytes:
    if not value:
        raise HTTPException(
            status_code=503,
            detail={"error": "crypto_key_not_configured", "key": name},
        )
    try:
        key = base64.b64decode(value, validate=True)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail={"error": "crypto_key_invalid", "key": name},
        ) from error
    if len(key) != expected_len:
        raise HTTPException(
            status_code=503,
            detail={"error": "crypto_key_invalid_length", "key": name},
        )
    return key


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail={"error": "invalid_email"})
    return normalized


def encrypt_text(value: str) -> tuple[bytes, bytes, str]:
    key = decode_required_key(DATA_ENCRYPTION_KEY_B64, "DATA_ENCRYPTION_KEY_B64")
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return ciphertext, nonce, AUTH_KEY_ID


def decrypt_text(ciphertext: bytes, nonce: bytes) -> str:
    key = decode_required_key(DATA_ENCRYPTION_KEY_B64, "DATA_ENCRYPTION_KEY_B64")
    plaintext = AESGCM(key).decrypt(nonce, bytes(ciphertext), None)
    return plaintext.decode("utf-8")


def decrypt_optional(ciphertext: bytes | None, nonce: bytes | None) -> str | None:
    if ciphertext is None or nonce is None:
        return None
    return decrypt_text(ciphertext, nonce)


def lookup_hmac(value: str) -> bytes:
    key = decode_required_key(EMAIL_LOOKUP_KEY_B64, "EMAIL_LOOKUP_KEY_B64")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
        dklen=32,
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = stored_hash.split("$", 3)
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
    except Exception:
        return False

    if algorithm != "pbkdf2_sha256" or iterations < PASSWORD_ITERATIONS:
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


def create_access_token(user_id: int, role: str) -> str:
    key = decode_required_key(JWT_SECRET_KEY_B64, "JWT_SECRET_KEY_B64")
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ACCESS_TOKEN_SECONDS)).timestamp()),
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    key = decode_required_key(JWT_SECRET_KEY_B64, "JWT_SECRET_KEY_B64")
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
        )
    except jwt.PyJWTError as error:
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_token"},
        ) from error


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail={"error": "missing_token"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    return token


async def current_user_record(authorization: str | None) -> asyncpg.Record:
    payload = decode_access_token(bearer_token(authorization))
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"}) from error

    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            SELECT id, email_ciphertext, email_nonce, role, is_active,
                   created_at, last_login_at
            FROM users
            WHERE id = $1
            """,
            user_id,
        )
    if record is None or not record["is_active"]:
        raise HTTPException(status_code=401, detail={"error": "invalid_token"})
    return record


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
        "auth_crypto": {
            "password_hash": "PBKDF2-HMAC-SHA-256",
            "personal_data_encryption": "AES-256-GCM",
            "lookup_index": "HMAC-SHA-256 blind index",
            "jwt": "HS256",
        },
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/register", status_code=201)
async def register_user(payload: UserRegister, request: Request):
    email = normalize_email(payload.email)
    email_ciphertext, email_nonce, email_key_id = encrypt_text(email)
    email_lookup_hash = lookup_hmac(email)
    password_hash = hash_password(payload.password)
    pool = require_db_pool()

    async with pool.acquire() as connection:
        try:
            record = await connection.fetchrow(
                """
                INSERT INTO users (
                    email_lookup_hash,
                    email_ciphertext,
                    email_nonce,
                    email_key_id,
                    password_hash,
                    password_algorithm,
                    password_iterations,
                    role
                )
                VALUES ($1, $2, $3, $4, $5, 'pbkdf2_hmac_sha256', $6, $7)
                RETURNING id, email_ciphertext, email_nonce, role, is_active,
                          created_at, last_login_at
                """,
                email_lookup_hash,
                email_ciphertext,
                email_nonce,
                email_key_id,
                password_hash,
                PASSWORD_ITERATIONS,
                payload.role,
            )
        except asyncpg.UniqueViolationError as error:
            raise HTTPException(
                status_code=409,
                detail={"error": "email_already_registered"},
            ) from error
        await connection.execute(
            """
            INSERT INTO audit_events (actor_user_id, action, request_id)
            VALUES ($1, 'auth_register', $2)
            """,
            record["id"],
            extract_request_id(request),
        )

    return {
        "user": user_to_public_dict(record, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/login")
async def login_user(payload: UserLogin, request: Request):
    email = normalize_email(payload.email)
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            SELECT id, email_ciphertext, email_nonce, password_hash, role,
                   is_active, created_at, last_login_at
            FROM users
            WHERE email_lookup_hash = $1
            """,
            lookup_hmac(email),
        )

        if (
            record is None
            or not record["is_active"]
            or not verify_password(payload.password, record["password_hash"])
        ):
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_credentials"},
            )

        updated = await connection.fetchrow(
            """
            UPDATE users
            SET last_login_at = now(),
                updated_at = now()
            WHERE id = $1
            RETURNING id, email_ciphertext, email_nonce, role, is_active,
                      created_at, last_login_at
            """,
            record["id"],
        )
        await connection.execute(
            """
            INSERT INTO audit_events (actor_user_id, action, request_id)
            VALUES ($1, 'auth_login', $2)
            """,
            record["id"],
            extract_request_id(request),
        )

    return {
        "access_token": create_access_token(updated["id"], updated["role"]),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "user": user_to_public_dict(updated, email),
        "request_id": extract_request_id(request),
    }


@app.get("/api/auth/me")
async def auth_me(
    request: Request,
    authorization: str | None = Header(default=None),
):
    record = await current_user_record(authorization)
    email = decrypt_text(record["email_ciphertext"], record["email_nonce"])
    return {
        "user": user_to_public_dict(record, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/seller/profile")
async def upsert_seller_profile(
    payload: SellerProfileUpsert,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = await current_user_record(authorization)
    if user["role"] not in {"seller", "admin"}:
        raise HTTPException(status_code=403, detail={"error": "seller_role_required"})

    business_email_hash = None
    business_email_ciphertext = None
    business_email_nonce = None
    business_email_key_id = None
    if payload.business_email:
        business_email = normalize_email(payload.business_email)
        business_email_hash = lookup_hmac(business_email)
        (
            business_email_ciphertext,
            business_email_nonce,
            business_email_key_id,
        ) = encrypt_text(business_email)

    phone_hash = None
    phone_ciphertext = None
    phone_nonce = None
    phone_key_id = None
    if payload.phone:
        phone_value = payload.phone.strip()
        phone_hash = lookup_hmac(phone_value)
        phone_ciphertext, phone_nonce, phone_key_id = encrypt_text(phone_value)

    address_ciphertext = None
    address_nonce = None
    address_key_id = None
    if payload.business_address:
        address_value = payload.business_address.strip()
        address_ciphertext, address_nonce, address_key_id = encrypt_text(address_value)

    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            INSERT INTO seller_profiles (
                user_id,
                store_name,
                store_description,
                business_email_lookup_hash,
                business_email_ciphertext,
                business_email_nonce,
                business_email_key_id,
                phone_lookup_hash,
                phone_ciphertext,
                phone_nonce,
                phone_key_id,
                business_address_ciphertext,
                business_address_nonce,
                business_address_key_id,
                payout_account_token
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ON CONFLICT (user_id)
            DO UPDATE SET
                store_name = EXCLUDED.store_name,
                store_description = EXCLUDED.store_description,
                business_email_lookup_hash = EXCLUDED.business_email_lookup_hash,
                business_email_ciphertext = EXCLUDED.business_email_ciphertext,
                business_email_nonce = EXCLUDED.business_email_nonce,
                business_email_key_id = EXCLUDED.business_email_key_id,
                phone_lookup_hash = EXCLUDED.phone_lookup_hash,
                phone_ciphertext = EXCLUDED.phone_ciphertext,
                phone_nonce = EXCLUDED.phone_nonce,
                phone_key_id = EXCLUDED.phone_key_id,
                business_address_ciphertext = EXCLUDED.business_address_ciphertext,
                business_address_nonce = EXCLUDED.business_address_nonce,
                business_address_key_id = EXCLUDED.business_address_key_id,
                payout_account_token = EXCLUDED.payout_account_token,
                updated_at = now()
            RETURNING id, user_id, store_name, store_description,
                      business_email_ciphertext, business_email_nonce,
                      phone_ciphertext, phone_nonce,
                      business_address_ciphertext, business_address_nonce,
                      verification_status, payout_account_token,
                      created_at, updated_at
            """,
            user["id"],
            payload.store_name,
            payload.store_description,
            business_email_hash,
            business_email_ciphertext,
            business_email_nonce,
            business_email_key_id,
            phone_hash,
            phone_ciphertext,
            phone_nonce,
            phone_key_id,
            address_ciphertext,
            address_nonce,
            address_key_id,
            payload.payout_account_token,
        )
        await connection.execute(
            """
            INSERT INTO audit_events (actor_user_id, action, target_type, target_id, request_id)
            VALUES ($1, 'seller_profile_upsert', 'seller_profile', $2, $3)
            """,
            user["id"],
            str(record["id"]),
            extract_request_id(request),
        )

    return {
        "seller_profile": seller_profile_to_dict(record),
        "request_id": extract_request_id(request),
    }


@app.get("/api/seller/profile")
async def get_seller_profile(
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = await current_user_record(authorization)
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            SELECT id, user_id, store_name, store_description,
                   business_email_ciphertext, business_email_nonce,
                   phone_ciphertext, phone_nonce,
                   business_address_ciphertext, business_address_nonce,
                   verification_status, payout_account_token,
                   created_at, updated_at
            FROM seller_profiles
            WHERE user_id = $1
            """,
            user["id"],
        )
    if record is None:
        raise HTTPException(status_code=404, detail={"error": "seller_profile_not_found"})
    return {
        "seller_profile": seller_profile_to_dict(record),
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
