#!/usr/bin/env python3
import argparse
import json
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
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl_context(),
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


def main() -> None:
    args = parse_args()
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
        payload={"email": email, "password": password, "role": "seller"},
    )
    add_case(
        cases,
        "register_seller",
        register_status == 201
        and register_payload is not None
        and register_payload.get("user", {}).get("email") == email,
        register_body,
    )

    duplicate_status, _duplicate_payload, duplicate_body, _headers = request_json(
        f"{base}/api/auth/register",
        args.timeout,
        method="POST",
        payload={"email": email.upper(), "password": password, "role": "seller"},
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
    add_case(
        cases,
        "login_issues_jwt",
        login_status == 200
        and isinstance(token, str)
        and len(token.split(".")) == 3
        and login_payload.get("token_type") == "bearer",
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
        and me_payload.get("request_id") == request_id_header,
        me_body,
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
        and seller_get_payload.get("seller_profile", {}).get("business_email") == email,
        seller_get_body,
    )

    if args.check_db:
        if shutil.which("docker") is None:
            add_case(cases, "db_plaintext_inspection", False, "docker command not found")
        else:
            sql = f"""
            SELECT
              encode(email_ciphertext, 'escape') LIKE '%{email}%',
              encode(email_lookup_hash, 'escape') LIKE '%{email}%',
              password_hash LIKE '%{password}%',
              password_hash LIKE 'pbkdf2_sha256$600000$%'
            FROM users
            WHERE id = {int(register_payload.get('user', {}).get('id', 0)) if register_payload else 0};
            """
            code, stdout, stderr = run_psql(args.compose_dir, sql)
            fields = stdout.split("|")
            db_ok = (
                code == 0
                and len(fields) == 4
                and fields[0] == "f"
                and fields[1] == "f"
                and fields[2] == "f"
                and fields[3] == "t"
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
    print("all_matched", "yes" if result["all_matched"] else "no")


if __name__ == "__main__":
    main()
