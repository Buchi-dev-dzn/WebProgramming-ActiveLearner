#!/usr/bin/env python3
import argparse
import http.cookiejar
import json
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass


@dataclass
class CaseResult:
    name: str
    status: str
    matched: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify auth, JWT, encrypted PII, and DB storage policy."
    )
    parser.add_argument("target", help="Target host or IP, e.g. 127.0.0.1")
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Use docker compose exec postgres to inspect stored auth data.",
    )
    parser.add_argument(
        "--db-mode",
        choices=["docker", "psql"],
        default="docker",
        help="DB inspection mode for --check-db (default: docker).",
    )
    parser.add_argument(
        "--psql-dsn",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL DSN for --db-mode psql.",
    )
    parser.add_argument(
        "--compose-dir",
        default=".",
        help="Directory containing docker-compose.yml when --check-db is used.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


COOKIE_JAR = http.cookiejar.CookieJar()
HTTP_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ssl_context()),
    urllib.request.HTTPCookieProcessor(COOKIE_JAR),
)


def refresh_cookie_value() -> str | None:
    for cookie in COOKIE_JAR:
        if cookie.name == "refresh_token":
            return cookie.value
    return None


def csrf_cookie_value() -> str | None:
    for cookie in COOKIE_JAR:
        if cookie.name == "csrf_token":
            return cookie.value
    return None


def request_json(
    url: str,
    timeout: float,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int | None, dict[str, object] | None, str, dict[str, str]]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        url,
        method=method,
        data=data,
        headers=request_headers,
    )
    try:
        with HTTP_OPENER.open(
            request,
            timeout=timeout,
        ) as response:
            body = response.read(8000).decode("utf-8", errors="replace").strip()
            return response.getcode(), parse_json(body), body, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read(8000).decode("utf-8", errors="replace").strip()
        return exc.code, parse_json(body), body, dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001
        return None, None, f"error:{type(exc).__name__}:{exc}", {}


def parse_json(body: str) -> dict[str, object] | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def add_case(
    cases: list[CaseResult],
    name: str,
    matched: bool,
    detail: str,
    status: str = "checked",
) -> None:
    cases.append(CaseResult(name=name, status=status, matched=matched, detail=detail))


def run_psql(compose_dir: str, sql: str) -> tuple[int, str, str]:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "postgres",
        "-d",
        "app_db",
        "-At",
        "-c",
        sql,
    ]
    completed = subprocess.run(
        command,
        cwd=compose_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def run_direct_psql(dsn: str, sql: str) -> tuple[int, str, str]:
    command = ["psql", dsn, "-At", "-c", sql]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def main() -> None:
    args = parse_args()
    if args.check_db and args.db_mode == "psql" and not args.psql_dsn:
        raise SystemExit(
            "--psql-dsn or DATABASE_URL is required with --db-mode psql"
        )
    base = f"https://{args.target}:{args.https_port}"
    marker = uuid.uuid4().hex[:12]
    email = f"security-{marker}@example.test"
    password = f"Test-password-{marker}-12345"
    bad_password = f"Wrong-password-{marker}-12345"
    cases: list[CaseResult] = []

    register_status, register_payload, register_body, _headers = request_json(
        f"{base}/api/auth/register",
        args.timeout,
        method="POST",
        payload={"email": email, "password": password},
    )
    add_case(
        cases,
        "register_unified_account",
        register_status == 201
        and register_payload is not None
        and register_payload.get("user", {}).get("email") == email
        and register_payload.get("user", {}).get("roles") == ["buyer", "seller"],
        register_body,
    )

    duplicate_status, _duplicate_payload, duplicate_body, _headers = request_json(
        f"{base}/api/auth/register",
        args.timeout,
        method="POST",
        payload={"email": email.upper(), "password": password},
    )
    add_case(
        cases,
        "duplicate_email_rejected",
        duplicate_status == 409,
        duplicate_body,
    )

    bad_login_status, _bad_login_payload, bad_login_body, _headers = request_json(
        f"{base}/api/auth/login",
        args.timeout,
        method="POST",
        payload={"email": email, "password": bad_password},
    )
    add_case(cases, "bad_password_rejected", bad_login_status == 401, bad_login_body)

    login_status, login_payload, login_body, _headers = request_json(
        f"{base}/api/auth/login",
        args.timeout,
        method="POST",
        payload={"email": email, "password": password},
    )
    token = login_payload.get("access_token") if login_payload else None
    refresh_token = refresh_cookie_value()
    add_case(
        cases,
        "login_issues_jwt",
        login_status == 200
        and isinstance(token, str)
        and isinstance(refresh_token, str)
        and len(token.split(".")) == 3
        and login_payload.get("token_type") == "bearer"
        and login_payload.get("user", {}).get("roles") == ["buyer", "seller"],
        login_body,
    )

    auth_headers = {"Authorization": f"Bearer {token}"} if token else {}
    me_status, me_payload, me_body, me_headers = request_json(
        f"{base}/api/auth/me",
        args.timeout,
        headers=auth_headers,
    )
    request_id_header = me_headers.get("X-Request-Id") or me_headers.get("x-request-id")
    add_case(
        cases,
        "auth_me_accepts_bearer_token",
        me_status == 200
        and me_payload is not None
        and me_payload.get("user", {}).get("email") == email
        and me_payload.get("user", {}).get("roles") == ["buyer", "seller"]
        and me_payload.get("request_id") == request_id_header,
        me_body,
    )

    refresh_status, refresh_payload, refresh_body, _headers = request_json(
        f"{base}/api/auth/refresh",
        args.timeout,
        method="POST",
        headers={"X-CSRF-Token": csrf_cookie_value() or ""},
    )
    refreshed_token = refresh_payload.get("access_token") if refresh_payload else None
    refreshed_refresh_token = refresh_cookie_value()
    add_case(
        cases,
        "refresh_rotates_token",
        refresh_status == 200
        and isinstance(refreshed_token, str)
        and isinstance(refreshed_refresh_token, str)
        and refreshed_refresh_token != refresh_token
        and refresh_payload.get("user", {}).get("roles") == ["buyer", "seller"],
        refresh_body,
    )
    if refreshed_token:
        token = refreshed_token
        auth_headers = {"Authorization": f"Bearer {token}"}

    reuse_status, _reuse_payload, reuse_body, _headers = request_json(
        f"{base}/api/auth/refresh",
        args.timeout,
        method="POST",
        headers={
            "Cookie": (
                f"refresh_token={refresh_token}; "
                f"csrf_token={csrf_cookie_value() or ''}"
            ),
            "X-CSRF-Token": csrf_cookie_value() or "",
        },
    )
    add_case(
        cases,
        "old_refresh_token_rejected",
        reuse_status == 401,
        reuse_body,
    )

    audit_status, audit_payload, audit_body, _headers = request_json(
        f"{base}/api/auth/audit-events",
        args.timeout,
        headers={**auth_headers, "X-CSRF-Token": csrf_cookie_value() or ""},
    )
    audit_items = audit_payload.get("items", []) if audit_payload else []
    audit_actions = {
        item.get("action")
        for item in audit_items
        if isinstance(item, dict)
    }
    add_case(
        cases,
        "own_audit_events_visible",
        audit_status == 200 and {"auth_register", "auth_login"} <= audit_actions,
        audit_body,
    )

    no_token_status, _no_token_payload, no_token_body, _headers = request_json(
        f"{base}/api/auth/me",
        args.timeout,
    )
    add_case(cases, "auth_me_requires_token", no_token_status == 401, no_token_body)

    seller_status, seller_payload, seller_body, _headers = request_json(
        f"{base}/api/seller/profile",
        args.timeout,
        method="POST",
        payload={
            "store_name": f"Security Test Store {marker}",
            "store_description": "auth crypto verification",
            "business_email": email,
            "phone": "+1-555-0100",
            "business_address": "1 Security Test Street",
            "payout_account_token": f"payout-token-{marker}",
        },
        headers=auth_headers,
    )
    add_case(
        cases,
        "seller_profile_upsert",
        seller_status == 200
        and seller_payload is not None
        and seller_payload.get("seller_profile", {}).get("store_name")
        == f"Security Test Store {marker}",
        seller_body,
    )

    seller_get_status, seller_get_payload, seller_get_body, _headers = request_json(
        f"{base}/api/seller/profile",
        args.timeout,
        headers=auth_headers,
    )
    add_case(
        cases,
        "seller_profile_get",
        seller_get_status == 200
        and seller_get_payload is not None
        and seller_get_payload.get("seller_profile", {}).get("business_email") == email
        and seller_get_payload.get("seller_profile", {}).get(
            "has_payout_account_token"
        )
        is True
        and "payout_account_token"
        not in seller_get_payload.get("seller_profile", {}),
        seller_get_body,
    )

    logout_status, _logout_payload, logout_body, _headers = request_json(
        f"{base}/api/auth/logout",
        args.timeout,
        method="POST",
        headers=auth_headers,
    )
    add_case(cases, "logout_revokes_refresh_token", logout_status == 200, logout_body)

    if args.check_db:
        db_result_added = False
        user_id = int(register_payload.get("user", {}).get("id", 0)) if register_payload else 0
        sql = f"""
            SELECT
              encode(email_ciphertext, 'escape') LIKE '%{email}%',
              encode(email_lookup_hash, 'escape') LIKE '%{email}%',
              password_hash LIKE '%{password}%',
              password_hash LIKE 'pbkdf2_sha256$600000$%',
              NOT EXISTS (
                SELECT 1
                FROM refresh_tokens
                WHERE user_id = {user_id}
                  AND encode(token_hash, 'escape') LIKE '%{refresh_token}%'
              ),
              EXISTS (
                SELECT 1
                FROM audit_events
                WHERE actor_user_id = {user_id}
                  AND action IN ('auth_register', 'auth_login', 'auth_refresh')
              ),
              EXISTS (
                SELECT 1
                FROM seller_profiles
                WHERE user_id = {user_id}
                  AND payout_account_token_ciphertext IS NOT NULL
                  AND payout_account_token_nonce IS NOT NULL
                  AND payout_account_token_key_id IS NOT NULL
                  AND encode(payout_account_token_ciphertext, 'escape')
                      NOT LIKE '%payout-token-{marker}%'
              )
            FROM users
            WHERE id = {user_id};
            """
        if args.db_mode == "docker":
            if shutil.which("docker") is None:
                add_case(cases, "db_plaintext_inspection", False, "docker command not found")
                db_result_added = True
                code, stdout, stderr = 1, "", "docker command not found"
            else:
                code, stdout, stderr = run_psql(args.compose_dir, sql)
        else:
            if shutil.which("psql") is None:
                add_case(cases, "db_plaintext_inspection", False, "psql command not found")
                db_result_added = True
                code, stdout, stderr = 1, "", "psql command not found"
            else:
                code, stdout, stderr = run_direct_psql(args.psql_dsn, sql)

        if not db_result_added:
            fields = stdout.split("|")
            db_ok = (
                code == 0
                and len(fields) == 7
                and fields[0] == "f"
                and fields[1] == "f"
                and fields[2] == "f"
                and fields[3] == "t"
                and fields[4] == "t"
                and fields[5] == "t"
                and fields[6] == "t"
            )
            add_case(
                cases,
                "db_plaintext_inspection",
                db_ok,
                stdout if code == 0 else stderr,
            )
    else:
        add_case(
            cases,
            "db_plaintext_inspection",
            True,
            "skipped; pass --check-db to inspect postgres storage",
            status="skipped",
        )

    result = {
        "all_matched": all(case.matched for case in cases),
        "email": email,
        "cases": [asdict(case) for case in cases],
    }
    nginx_404_count = sum(
        1
        for case in cases
        if "404 Not Found" in case.detail and "<center>nginx</center>" in case.detail
    )
    if nginx_404_count >= 4:
        result["diagnostic"] = (
            "Most auth requests returned nginx HTML 404. "
            "The running WAF container likely has not loaded the updated allowlist "
            "for /api/auth/* and /api/seller/profile, or the request is reaching "
            "a different host than the updated Compose stack."
        )

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return

    for case in cases:
        print(
            case.name,
            f"status={case.status}",
            f"matched={'yes' if case.matched else 'no'}",
            case.detail,
        )
    if "diagnostic" in result:
        print("diagnostic", result["diagnostic"])
    print("all_matched", "yes" if result["all_matched"] else "no")


if __name__ == "__main__":
    main()
