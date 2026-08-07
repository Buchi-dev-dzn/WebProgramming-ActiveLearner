import asyncio
import base64
from collections import deque
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
from fastapi import Cookie, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
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
SECURITY_SENSOR_TOKEN = os.environ.get("SECURITY_SENSOR_TOKEN")
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
REFRESH_COOKIE_NAME = os.environ.get("REFRESH_COOKIE_NAME", "refresh_token")
REFRESH_COOKIE_SECURE = (
    os.environ.get("REFRESH_COOKIE_SECURE", "true").strip().lower() == "true"
)
REFRESH_COOKIE_SAMESITE = os.environ.get(
    "REFRESH_COOKIE_SAMESITE",
    "lax",
).strip().lower()
if REFRESH_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise RuntimeError("REFRESH_COOKIE_SAMESITE must be lax, strict, or none")
CSRF_COOKIE_NAME = os.environ.get("CSRF_COOKIE_NAME", "csrf_token")
CSRF_HEADER_NAME = "x-csrf-token"
AUTH_RATE_WINDOW_SECONDS = int(os.environ.get("AUTH_RATE_WINDOW_SECONDS", "60"))
AUTH_RATE_LIMIT_PER_IP = int(os.environ.get("AUTH_RATE_LIMIT_PER_IP", "30"))
AUTH_RATE_LIMIT_PER_ACCOUNT = int(
    os.environ.get("AUTH_RATE_LIMIT_PER_ACCOUNT", "10")
)
_rate_limit_events: dict[str, deque[float]] = {}
_rate_limit_lock = asyncio.Lock()
db_pool: asyncpg.Pool | None = None
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=100_000_000)
    stock: int = Field(ge=0, le=1_000_000)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="", max_length=120)
    tag: str = Field(default="", max_length=120)
    image_url: str | None = Field(
        default=None,
        max_length=2048,
        pattern=r"^https?://[^\s]+$",
    )


class ProductUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price_cents: int = Field(ge=0, le=100_000_000)
    stock: int = Field(ge=0, le=1_000_000)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="", max_length=120)
    tag: str = Field(default="", max_length=120)
    image_url: str | None = Field(
        default=None,
        max_length=2048,
        pattern=r"^https?://[^\s]+$",
    )


class ProductStockUpdate(BaseModel):
    sku: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    stock: int = Field(ge=0, le=1_000_000)


class UserRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class UserLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)


class PublicUser(BaseModel):
    id: int
    email: str
    roles: list[str]
    is_active: bool
    created_at: str
    last_login_at: str | None


class UserResponse(BaseModel):
    user: PublicUser
    request_id: str | None


class AuthResponse(UserResponse):
    access_token: str
    token_type: str
    expires_in: int
    refresh_expires_in: int


class SellerProfileUpsert(BaseModel):
    store_name: str = Field(min_length=1, max_length=120)
    store_description: str = Field(default="", max_length=2000)
    business_email: str | None = Field(default=None, min_length=3, max_length=254)
    phone: str | None = Field(default=None, min_length=5, max_length=40)
    business_address: str | None = Field(default=None, min_length=1, max_length=1000)
    payout_account_token: str | None = Field(default=None, min_length=1, max_length=200)


class SecuritySensorEvent(BaseModel):
    component: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")
    action: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9._-]+$")
    severity: str = Field(default="info", pattern=r"^(info|warning|critical)$")
    target_type: str | None = Field(default=None, max_length=80)
    target_id: str | None = Field(default=None, max_length=200)
    details: dict[str, Any] = Field(default_factory=dict)


def product_to_dict(record: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": record["id"],
        "sku": record["sku"],
        "name": record["name"],
        "price_cents": record["price_cents"],
        "stock": record["stock"],
        "description": record["description"],
        "category": record["category"],
        "tag": record["tag"],
        "image_url": record["image_url"],
        "created_at": record["created_at"].isoformat(),
        "updated_at": record["updated_at"].isoformat(),
    }


def roles_for_legacy_role(role: str) -> list[str]:
    """Map stored legacy roles to the public capability-based representation."""
    if role in {"member", "customer", "seller"}:
        return ["buyer", "seller"]
    return [role]


def user_to_public_dict(record: asyncpg.Record, email: str) -> dict[str, Any]:
    return {
        "id": record["id"],
        "email": email,
        "roles": roles_for_legacy_role(record["role"]),
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
        "has_payout_account_token": bool(record["payout_account_token_ciphertext"]),
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
    # Uvicorn resolves the forwarded client address only when the immediate
    # proxy is explicitly trusted in the container command.  Do not parse
    # X-Forwarded-For here: accepting it from arbitrary callers lets clients
    # forge audit identity and rate-limit keys.
    if request.client:
        return request.client.host
    return "unknown"


def source_ip_hash(request: Request) -> bytes:
    return lookup_hmac(source_ip(request))


async def enforce_auth_rate_limit(
    request: Request,
    account_key: str | None = None,
) -> None:
    """Apply a bounded per-process guard before expensive authentication work.

    A distributed limiter still belongs at the edge for multi-instance
    deployments; this prevents one application worker from being exhausted.
    """
    now = asyncio.get_running_loop().time()
    keys = [f"ip:{source_ip(request)}"]
    if account_key:
        keys.append(f"account:{account_key}")
    async with _rate_limit_lock:
        for key in keys:
            events = _rate_limit_events.setdefault(key, deque())
            while events and now - events[0] >= AUTH_RATE_WINDOW_SECONDS:
                events.popleft()
            limit = (
                AUTH_RATE_LIMIT_PER_ACCOUNT
                if key.startswith("account:")
                else AUTH_RATE_LIMIT_PER_IP
            )
            if len(events) >= limit:
                retry_after = max(
                    1,
                    int(AUTH_RATE_WINDOW_SECONDS - (now - events[0])) + 1,
                )
                raise HTTPException(
                    status_code=429,
                    detail={"error": "auth_rate_limited"},
                    headers={"Retry-After": str(retry_after)},
                )
        for key in keys:
            _rate_limit_events[key].append(now)
        if len(_rate_limit_events) > 10_000:
            stale_before = now - AUTH_RATE_WINDOW_SECONDS
            for key in list(_rate_limit_events):
                events = _rate_limit_events[key]
                if not events or events[-1] < stale_before:
                    del _rate_limit_events[key]


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
        "roles": roles_for_legacy_role(role),
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
    if not set(roles_for_legacy_role(user["role"])) & allowed_roles:
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


async def insert_sensor_event(
    connection: asyncpg.Connection,
    payload: SecuritySensorEvent,
    request_id: str | None = None,
) -> None:
    await connection.execute(
        """
        INSERT INTO audit_events (
            action,
            target_type,
            target_id,
            request_id,
            severity,
            details
        )
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """,
        payload.action,
        payload.target_type,
        payload.target_id,
        request_id,
        payload.severity,
        json.dumps({"component": payload.component, **payload.details}),
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


app = FastAPI(
    title="security-ec-fastapi",
    version="2.0.0",
    description=(
        "EC API with unified buyer/seller accounts. Registration accepts only email "
        "and password; authorization is enforced by the backend."
    ),
    lifespan=lifespan,
)

if CORS_ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-Id",
            "X-CSRF-Token",
        ],
        expose_headers=["X-Request-Id"],
        max_age=600,
    )


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=REFRESH_TOKEN_SECONDS,
        path="/api/auth",
        secure=REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=REFRESH_COOKIE_SAMESITE,
    )


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    csrf_token = token or secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=REFRESH_TOKEN_SECONDS,
        path="/",
        secure=REFRESH_COOKIE_SECURE,
        httponly=False,
        samesite=REFRESH_COOKIE_SAMESITE,
    )
    return csrf_token


def delete_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/api/auth",
        secure=REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=REFRESH_COOKIE_SAMESITE,
    )


def delete_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        path="/",
        secure=REFRESH_COOKIE_SECURE,
        httponly=False,
        samesite=REFRESH_COOKIE_SAMESITE,
    )


def validate_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in CORS_ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail={"error": "origin_not_allowed"})


def require_csrf(
    request: Request,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> None:
    validate_origin(request)
    if (
        not csrf_cookie
        or not csrf_header
        or not secrets.compare_digest(csrf_cookie, csrf_header)
    ):
        raise HTTPException(status_code=403, detail={"error": "csrf_failed"})


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
        except Exception:  # noqa: BLE001
            overall_status = "degraded"
            # Do not expose database driver errors or connection details through
            # the public health endpoint.
            checks["postgres"] = {"status": "error"}

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


@app.post("/api/auth/register", status_code=201, response_model=UserResponse)
async def register_user(payload: UserRegister, request: Request):
    email = normalize_email(payload.email)
    validate_origin(request)
    await enforce_auth_rate_limit(
        request,
        hashlib.sha256(email.encode("utf-8")).hexdigest(),
    )
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
                VALUES ($1, $2, $3, $4, $5, 'pbkdf2_hmac_sha256', $6, 'member')
                RETURNING id, email_ciphertext, email_nonce, role, is_active,
                          created_at, last_login_at
                """,
                email_lookup_hash,
                email_ciphertext,
                email_nonce,
                email_key_id,
                password_hash,
                PASSWORD_ITERATIONS,
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


@app.post("/api/auth/login", response_model=AuthResponse)
async def login_user(payload: UserLogin, request: Request, response: Response):
    email = normalize_email(payload.email)
    validate_origin(request)
    await enforce_auth_rate_limit(
        request,
        hashlib.sha256(email.encode("utf-8")).hexdigest(),
    )
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

    set_refresh_cookie(response, refresh_token)
    set_csrf_cookie(response)
    return {
        "access_token": create_access_token(updated["id"], updated["role"]),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "refresh_expires_in": REFRESH_TOKEN_SECONDS,
        "user": user_to_public_dict(updated, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/refresh", response_model=AuthResponse)
async def refresh_access_token(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE_NAME),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
):
    require_csrf(request, csrf_cookie, csrf_header)
    await enforce_auth_rate_limit(request)
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail={"error": "missing_refresh_token"},
        )
    pool = require_db_pool()
    incoming_hash = token_hmac(refresh_token)
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
                if record is not None and record["revoked_at"] is not None:
                    await connection.execute(
                        """
                        UPDATE refresh_tokens
                        SET revoked_at = COALESCE(revoked_at, now())
                        WHERE family_id = $1 AND revoked_at IS NULL
                        """,
                        record["family_id"],
                    )
                    await insert_audit_event(
                        connection,
                        request,
                        "auth_refresh_reuse_detected",
                        actor_user_id=record["user_id"],
                        target_type="user",
                        target_id=str(record["user_id"]),
                        severity="critical",
                        details={"family_id": str(record["family_id"])},
                    )
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
    set_refresh_cookie(response, new_refresh_token)
    set_csrf_cookie(response, csrf_cookie)
    return {
        "access_token": create_access_token(record["user_id"], record["role"]),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
        "refresh_expires_in": REFRESH_TOKEN_SECONDS,
        "user": user_to_public_dict(record, email),
        "request_id": extract_request_id(request),
    }


@app.post("/api/auth/logout")
async def logout_user(
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE_NAME),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER_NAME),
):
    require_csrf(request, csrf_cookie, csrf_header)
    user = None
    if authorization:
        try:
            user = await current_user_record(authorization)
        except HTTPException as error:
            # A logout request with an expired access token must still be able
            # to revoke the refresh cookie.  Invalid non-authentication
            # failures are not suppressed.
            if error.status_code != 401:
                raise
    pool = require_db_pool()
    async with pool.acquire() as connection:
        if refresh_token:
            await connection.execute(
                """
                UPDATE refresh_tokens
                SET revoked_at = now()
                WHERE token_hash = $1
                  AND ($2::bigint IS NULL OR user_id = $2)
                  AND revoked_at IS NULL
                """,
                token_hmac(refresh_token),
                user["id"] if user else None,
            )
        await insert_audit_event(
            connection,
            request,
            "auth_logout",
            actor_user_id=user["id"] if user else None,
            target_type="user" if user else None,
            target_id=str(user["id"]) if user else None,
        )
    delete_refresh_cookie(response)
    delete_csrf_cookie(response)
    return {"revoked": True, "request_id": extract_request_id(request)}


@app.get("/api/auth/me", response_model=UserResponse)
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
    if not set(roles_for_legacy_role(user["role"])) & {"seller", "admin"}:
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

    payout_token_ciphertext = None
    payout_token_nonce = None
    payout_token_key_id = None
    if payload.payout_account_token:
        (
            payout_token_ciphertext,
            payout_token_nonce,
            payout_token_key_id,
        ) = encrypt_text(payload.payout_account_token)

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
                payout_account_token_ciphertext,
                payout_account_token_nonce,
                payout_account_token_key_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
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
                payout_account_token_ciphertext = EXCLUDED.payout_account_token_ciphertext,
                payout_account_token_nonce = EXCLUDED.payout_account_token_nonce,
                payout_account_token_key_id = EXCLUDED.payout_account_token_key_id,
                updated_at = now()
            RETURNING id, user_id, store_name, store_description,
                      business_email_ciphertext, business_email_nonce,
                      phone_ciphertext, phone_nonce,
                      business_address_ciphertext, business_address_nonce,
                      verification_status, payout_account_token_ciphertext,
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
            payout_token_ciphertext,
            payout_token_nonce,
            payout_token_key_id,
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
        sensor_records = await connection.fetch(
            """
            SELECT
                COALESCE(details->>'component', 'unknown') AS component,
                action,
                severity,
                count(*) AS count,
                max(created_at) AS last_seen_at
            FROM audit_events
            WHERE created_at >= now() - interval '24 hours'
              AND (
                  action LIKE 'nids_%'
                  OR action LIKE 'hids_%'
                  OR action = 'sensor_heartbeat'
              )
            GROUP BY COALESCE(details->>'component', 'unknown'), action, severity
            ORDER BY component, count DESC, action
            """
        )
    return {
        "window": "24h",
        "nips": {
            "status": "inline",
            "source": "haproxy at external-firewall -> nips -> waf",
        },
        "nids": {
            "status": "log-sensor-and-audit-log-backed",
            "signals": [
                "nids_signature_match",
                "nids_source_read_failed",
                "auth_login_failed",
                "auth_login_blocked",
                "auth_refresh_failed",
                "auth_refresh_reuse_detected",
            ],
        },
        "hids": {
            "status": "host-sensor-and-application-host-backed",
            "signals": [
                "hids_file_created",
                "hids_file_modified",
                "hids_file_deleted",
                "hids_health_unreachable",
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
        "sensor_counts": [
            {
                "component": record["component"],
                "action": record["action"],
                "severity": record["severity"],
                "count": record["count"],
                "last_seen_at": record["last_seen_at"].isoformat(),
            }
            for record in sensor_records
        ],
        "recent_warnings": [audit_event_to_dict(record) for record in recent_warnings],
        "request_id": extract_request_id(request),
    }


@app.post("/api/internal/security-events", status_code=202)
async def ingest_security_sensor_event(
    payload: SecuritySensorEvent,
    request: Request,
    x_sensor_token: str | None = Header(default=None),
):
    if not SECURITY_SENSOR_TOKEN:
        raise HTTPException(
            status_code=503,
            detail={"error": "security_sensor_token_not_configured"},
        )
    if not x_sensor_token or not hmac.compare_digest(
        x_sensor_token,
        SECURITY_SENSOR_TOKEN,
    ):
        raise HTTPException(status_code=401, detail={"error": "invalid_sensor_token"})

    pool = require_db_pool()
    async with pool.acquire() as connection:
        await insert_sensor_event(
            connection,
            payload,
            request_id=extract_request_id(request),
        )
    return {"accepted": True, "request_id": extract_request_id(request)}


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
                   verification_status, payout_account_token_ciphertext,
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
            SELECT id, sku, name, price_cents, stock, description, category, tag,
                   image_url, created_at, updated_at
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
async def create_product(
    product: ProductCreate,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = await require_role(authorization, {"seller", "admin"})
    pool = require_db_pool()
    async with pool.acquire() as connection:
        try:
            record = await connection.fetchrow(
                """
                INSERT INTO products (
                    sku, name, price_cents, stock, description, category, tag,
                    image_url, owner_user_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id, sku, name, price_cents, stock, description, category,
                          tag, image_url, created_at, updated_at
                """,
                product.sku,
                product.name,
                product.price_cents,
                product.stock,
                product.description,
                product.category,
                product.tag,
                product.image_url,
                user["id"],
            )
        except asyncpg.UniqueViolationError as error:
            raise HTTPException(
                status_code=409,
                detail={"error": "product_sku_already_exists"},
            ) from error
        await insert_audit_event(
            connection,
            request,
            "product_create",
            actor_user_id=user["id"],
            target_type="product",
            target_id=str(record["id"]),
            details={"sku": record["sku"]},
        )
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
            SELECT id, sku, name, price_cents, stock, description, category, tag,
                   image_url, created_at, updated_at
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


@app.put("/api/products/{sku}")
async def update_product(
    product: ProductUpdate,
    request: Request,
    sku: str = Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"),
    authorization: str | None = Header(default=None),
):
    user = await require_role(authorization, {"seller", "admin"})
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            UPDATE products
            SET name = $2,
                price_cents = $3,
                stock = $4,
                description = $5,
                category = $6,
                tag = $7,
                image_url = $8,
                updated_at = now()
            WHERE sku = $1
              AND (owner_user_id = $9 OR $10)
            RETURNING id, sku, name, price_cents, stock, description, category,
                      tag, image_url, created_at, updated_at
            """,
            sku,
            product.name,
            product.price_cents,
            product.stock,
            product.description,
            product.category,
            product.tag,
            product.image_url,
            user["id"],
            user["role"] == "admin",
        )
        if record is None:
            exists = await connection.fetchval(
                "SELECT 1 FROM products WHERE sku = $1",
                sku,
            )
            if exists:
                raise HTTPException(status_code=403, detail={"error": "not_product_owner"})
            raise HTTPException(status_code=404, detail={"error": "product_not_found"})
        await insert_audit_event(
            connection,
            request,
            "product_update",
            actor_user_id=user["id"],
            target_type="product",
            target_id=str(record["id"]),
            details={"sku": record["sku"]},
        )
    return {"item": product_to_dict(record), "request_id": extract_request_id(request)}


@app.post("/api/product/stock")
async def update_product_stock(
    update: ProductStockUpdate,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user = await require_role(authorization, {"seller", "admin"})
    pool = require_db_pool()
    async with pool.acquire() as connection:
        record = await connection.fetchrow(
            """
            UPDATE products
            SET stock = $2,
                updated_at = now()
            WHERE sku = $1
              AND (owner_user_id = $3 OR $4)
            RETURNING id, sku, name, price_cents, stock, description, category, tag,
                      image_url, created_at, updated_at
            """,
            update.sku,
            update.stock,
            user["id"],
            user["role"] == "admin",
        )
        if record is None:
            existing = await connection.fetchval(
                "SELECT 1 FROM products WHERE sku = $1",
                update.sku,
            )
            if existing:
                raise HTTPException(status_code=403, detail={"error": "not_product_owner"})
            raise HTTPException(status_code=404, detail={"error": "product_not_found"})
        await insert_audit_event(
            connection,
            request,
            "product_stock_update",
            actor_user_id=user["id"],
            target_type="product",
            target_id=str(record["id"]),
            details={"sku": record["sku"], "stock": record["stock"]},
        )
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
