import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
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
REFRESH_TOKEN_SECONDS = 60 * 60 * 24 * 14
MAX_FAILED_LOGINS = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60
MAX_AUDIT_LIMIT = 100
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


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


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


def jsonb_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def audit_event_to_dict(record: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": record["id"],
        "actor_user_id": record["actor_user_id"],
        "action": record["action"],
        "target_type": record["target_type"],
        "target_id": record["target_id"],
        "request_id": record["request_id"],
        "severity": record["severity"],
        "details": jsonb_to_dict(record["details"]),
        "created_at": record["created_at"].isoformat(),
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


def token_hmac(value: str) -> bytes:
    key = decode_required_key(JWT_SECRET_KEY_B64, "JWT_SECRET_KEY_B64")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def source_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def source_ip_hash(request: Request) -> bytes:
    return lookup_hmac(source_ip(request))


def user_agent_summary(request: Request) -> str | None:
    user_agent = request.headers.get("user-agent")
    if not user_agent:
        return None
    summary = re.sub(r"[\r\n\t]+", " ", user_agent).strip()
    return summary[:200]


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


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


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


async def require_role(authorization: str | None, allowed_roles: set[str]) -> asyncpg.Record:
    user = await current_user_record(authorization)
    if user["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail={"error": "insufficient_role"})
    return user


async def insert_audit_event(
    connection: asyncpg.Connection,
    request: Request,
    action: str,
    actor_user_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    severity: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    await connection.execute(
        """
        INSERT INTO audit_events (
            actor_user_id,
            action,
            target_type,
            target_id,
            request_id,
            source_ip_hash,
            user_agent_summary,
            severity,
            details
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
        """,
        actor_user_id,
        action,
        target_type,
        target_id,
        extract_request_id(request),
        source_ip_hash(request),
        user_agent_summary(request),
        severity,
        json.dumps(details or {}),
    )


async def issue_refresh_token(
    connection: asyncpg.Connection,
    request: Request,
    user_id: int,
    family_id: uuid.UUID | None = None,
) -> str:
    refresh_token = create_refresh_token()
    await connection.execute(
        """
        INSERT INTO refresh_tokens (
            user_id,
            token_hash,
            family_id,
            expires_at,
            request_id,
            source_ip_hash,
            user_agent_summary
        )
        VALUES ($1, $2, $3, now() + make_interval(secs => $4), $5, $6, $7)
        """,
        user_id,
        token_hmac(refresh_token),
        family_id or uuid.uuid4(),
        REFRESH_TOKEN_SECONDS,
        extract_request_id(request),
        source_ip_hash(request),
        user_agent_summary(request),
    )
    return refresh_token


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
        await insert_audit_event(
            connection,
            request,
            "auth_register",
            actor_user_id=record["id"],
            target_type="user",
            target_id=str(record["id"]),
        )

    return {
        "user": user_to_public_dict(record, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/login")
async def login_user(payload: UserLogin, request: Request):
    email = normalize_email(payload.email)
    email_lookup_hash = lookup_hmac(email)
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            SELECT id, email_ciphertext, email_nonce, password_hash, role,
                   is_active, created_at, last_login_at, failed_login_count,
                   locked_until
            FROM users
            WHERE email_lookup_hash = $1
            """,
            email_lookup_hash,
        )

        if record is not None and record["locked_until"]:
            locked_until = record["locked_until"]
            if locked_until > datetime.now(UTC):
                await insert_audit_event(
                    connection,
                    request,
                    "auth_login_blocked",
                    actor_user_id=record["id"],
                    target_type="user",
                    target_id=str(record["id"]),
                    severity="warning",
                    details={"reason": "account_locked"},
                )
                raise HTTPException(
                    status_code=423,
                    detail={
                        "error": "account_locked",
                        "locked_until": locked_until.isoformat(),
                    },
                )

        if record is None or not record["is_active"] or not verify_password(
            payload.password,
            record["password_hash"],
        ):
            if record is not None:
                failed_count = int(record["failed_login_count"]) + 1
                locked_until = None
                if failed_count >= MAX_FAILED_LOGINS:
                    locked_until = datetime.now(UTC) + timedelta(
                        seconds=LOGIN_LOCKOUT_SECONDS
                    )
                await connection.execute(
                    """
                    UPDATE users
                    SET failed_login_count = $2,
                        locked_until = $3,
                        updated_at = now()
                    WHERE id = $1
                    """,
                    record["id"],
                    failed_count,
                    locked_until,
                )
                await insert_audit_event(
                    connection,
                    request,
                    "auth_login_failed",
                    actor_user_id=record["id"],
                    target_type="user",
                    target_id=str(record["id"]),
                    severity="warning",
                    details={
                        "failed_count": failed_count,
                        "locked": locked_until is not None,
                    },
                )
            else:
                await insert_audit_event(
                    connection,
                    request,
                    "auth_login_failed",
                    severity="warning",
                    details={"known_user": False},
                )
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_credentials"},
            )

        updated = await connection.fetchrow(
            """
            UPDATE users
            SET last_login_at = now(),
                failed_login_count = 0,
                locked_until = NULL,
                updated_at = now()
            WHERE id = $1
            RETURNING id, email_ciphertext, email_nonce, role, is_active,
                      created_at, last_login_at
            """,
            record["id"],
        )
        refresh_token = await issue_refresh_token(connection, request, updated["id"])
        await insert_audit_event(
            connection,
            request,
            "auth_login",
            actor_user_id=record["id"],
            target_type="user",
            target_id=str(record["id"]),
        )

    return {
        "access_token": create_access_token(updated["id"], updated["role"]),
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "refresh_expires_in": REFRESH_TOKEN_SECONDS,
        "user": user_to_public_dict(updated, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/refresh")
async def refresh_access_token(payload: RefreshTokenRequest, request: Request):
    pool = require_db_pool()
    incoming_hash = token_hmac(payload.refresh_token)
    refresh_failed = False
    failed_actor_user_id = None
    async with pool.acquire() as connection:
        async with connection.transaction():
            record = await connection.fetchrow(
                """
                SELECT rt.id AS refresh_token_id, rt.user_id, rt.family_id,
                       rt.expires_at, rt.revoked_at,
                       u.id, u.role, u.is_active, u.email_ciphertext, u.email_nonce,
                       u.created_at, u.last_login_at
                FROM refresh_tokens rt
                JOIN users u ON u.id = rt.user_id
                WHERE rt.token_hash = $1
                FOR UPDATE OF rt
                """,
                incoming_hash,
            )
            if (
                record is None
                or record["revoked_at"] is not None
                or record["expires_at"] <= datetime.now(UTC)
                or not record["is_active"]
            ):
                refresh_failed = True
                failed_actor_user_id = record["user_id"] if record is not None else None
            else:
                new_refresh_token = await issue_refresh_token(
                    connection,
                    request,
                    record["user_id"],
                    record["family_id"],
                )
                await connection.execute(
                    """
                    UPDATE refresh_tokens
                    SET revoked_at = now(),
                        replaced_by_token_hash = $2
                    WHERE id = $1
                    """,
                    record["refresh_token_id"],
                    token_hmac(new_refresh_token),
                )
                await insert_audit_event(
                    connection,
                    request,
                    "auth_refresh",
                    actor_user_id=record["user_id"],
                    target_type="user",
                    target_id=str(record["user_id"]),
                )

        if refresh_failed:
            await insert_audit_event(
                connection,
                request,
                "auth_refresh_failed",
                actor_user_id=failed_actor_user_id,
                target_type="user" if failed_actor_user_id is not None else None,
                target_id=str(failed_actor_user_id)
                if failed_actor_user_id is not None
                else None,
                severity="warning",
                details={"reason": "invalid_refresh_token"},
            )
            raise HTTPException(
                status_code=401,
                detail={"error": "invalid_refresh_token"},
            )

    email = decrypt_text(record["email_ciphertext"], record["email_nonce"])
    return {
        "access_token": create_access_token(record["user_id"], record["role"]),
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "refresh_expires_in": REFRESH_TOKEN_SECONDS,
        "user": user_to_public_dict(record, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/logout")
async def logout_user(
    payload: LogoutRequest,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = await current_user_record(authorization)
    pool = require_db_pool()
    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = now()
            WHERE token_hash = $1
              AND user_id = $2
              AND revoked_at IS NULL
            """,
            token_hmac(payload.refresh_token),
            user["id"],
        )
        await insert_audit_event(
            connection,
            request,
            "auth_logout",
            actor_user_id=user["id"],
            target_type="user",
            target_id=str(user["id"]),
        )
    return {"revoked": True, "request_id": extract_request_id(request)}


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


@app.get("/api/auth/audit-events")
async def my_audit_events(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=25, ge=1, le=MAX_AUDIT_LIMIT),
):
    user = await current_user_record(authorization)
    pool = require_db_pool()
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            SELECT id, actor_user_id, action, target_type, target_id, request_id,
                   severity, details, created_at
            FROM audit_events
            WHERE actor_user_id = $1
            ORDER BY id DESC
            LIMIT $2
            """,
            user["id"],
            limit,
        )
    return {
        "items": [audit_event_to_dict(record) for record in records],
        "limit": limit,
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
        await insert_audit_event(
            connection,
            request,
            "seller_profile_upsert",
            actor_user_id=user["id"],
            target_type="seller_profile",
            target_id=str(record["id"]),
        )

    return {
        "seller_profile": seller_profile_to_dict(record),
        "request_id": extract_request_id(request),
    }


@app.get("/api/security/audit-events")
async def list_audit_events(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=50, ge=1, le=MAX_AUDIT_LIMIT),
):
    await require_role(authorization, {"admin", "support"})
    pool = require_db_pool()
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            SELECT id, actor_user_id, action, target_type, target_id, request_id,
                   severity, details, created_at
            FROM audit_events
            ORDER BY id DESC
            LIMIT $1
            """,
            limit,
        )
    return {
        "items": [audit_event_to_dict(record) for record in records],
        "limit": limit,
        "request_id": extract_request_id(request),
    }


@app.get("/api/security/monitoring/summary")
async def security_monitoring_summary(
    request: Request,
    authorization: str | None = Header(default=None),
):
    await require_role(authorization, {"admin", "support"})
    pool = require_db_pool()
    async with pool.acquire() as connection:
        records = await connection.fetch(
            """
            SELECT action, severity, count(*) AS count
            FROM audit_events
            WHERE created_at >= now() - interval '24 hours'
            GROUP BY action, severity
            ORDER BY count DESC, action
            """
        )
        recent_warnings = await connection.fetch(
            """
            SELECT id, actor_user_id, action, target_type, target_id, request_id,
                   severity, details, created_at
            FROM audit_events
            WHERE severity IN ('warning', 'critical')
            ORDER BY id DESC
            LIMIT 20
            """
        )
    return {
        "window": "24h",
        "nips": {
            "status": "inline",
            "source": "haproxy at external-firewall -> nips -> waf",
        },
        "nids": {
            "status": "audit-log-backed",
            "signals": [
                "auth_login_failed",
                "auth_login_blocked",
                "auth_refresh_failed",
            ],
        },
        "hids": {
            "status": "application-host-backed",
            "signals": [
                "account lockout state",
                "refresh token revocation",
                "privileged audit access",
            ],
        },
        "counts": [
            {
                "action": record["action"],
                "severity": record["severity"],
                "count": record["count"],
            }
            for record in records
        ],
        "recent_warnings": [audit_event_to_dict(record) for record in recent_warnings],
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
