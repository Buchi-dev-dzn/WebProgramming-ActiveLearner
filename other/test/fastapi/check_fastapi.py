#!/usr/bin/env python3
import argparse
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass


@dataclass
class CaseResult:
    name: str
    method: str
    url: str
    status: int | None
    expected: int
    matched: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify FastAPI behavior from outside the VM."
    )
    parser.add_argument("target", help="Target host or IP, e.g. 192.168.64.4")
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--expect-degraded-health",
        action="store_true",
        help="Expect /api/health to return degraded/503, e.g. after postgres stop.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def open_request(url: str, timeout: float) -> tuple[int | None, str, dict[str, str]]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=build_ssl_context(),
        ) as response:
            body = response.read(400).decode("utf-8", errors="replace").strip()
            return response.getcode(), body, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode("utf-8", errors="replace").strip()
        return exc.code, body, dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}", {}


def request_case(name: str, url: str, expected: int, timeout: float) -> CaseResult:
    status, body, _headers = open_request(url, timeout)
    return CaseResult(
        name=name,
        method="GET",
        url=url,
        status=status,
        expected=expected,
        matched=(status == expected),
        detail=body,
    )


def main() -> None:
    args = parse_args()
    health_expected = 503 if args.expect_degraded_health else 200
    timeout = max(args.timeout, 8.0) if args.expect_degraded_health else args.timeout

    cases = [
        request_case(
            "api_health_status",
            f"https://{args.target}:{args.https_port}/api/health",
            health_expected,
            timeout,
        ),
        request_case(
            "api_info_status",
            f"https://{args.target}:{args.https_port}/api/info",
            200,
            timeout,
        ),
    ]

    health_status, health_body, health_headers = open_request(
        f"https://{args.target}:{args.https_port}/api/health",
        timeout,
    )
    info_status, info_body, info_headers = open_request(
        f"https://{args.target}:{args.https_port}/api/info",
        timeout,
    )
    health_shape_ok = False
    info_shape_ok = False
    request_id_ok = False

    try:
        health_payload = json.loads(health_body)
        if args.expect_degraded_health:
            health_shape_ok = (
                health_status == 503
                and (
                    (
                        health_payload.get("status") == "degraded"
                        and health_payload.get("checks", {}).get("postgres", {}).get("status") == "error"
                    )
                    or health_payload.get("error") == "upstream_unavailable"
                )
            )
        else:
            health_shape_ok = (
                health_status == 200
                and health_payload.get("status") == "ok"
                and health_payload.get("checks", {}).get("postgres", {}).get("status") == "ok"
            )
    except json.JSONDecodeError:
        health_shape_ok = False

    try:
        info_payload = json.loads(info_body)
        request_id_header = info_headers.get("X-Request-Id") or info_headers.get("x-request-id")
        info_shape_ok = (
            info_status == 200
            and info_payload.get("service") == "fastapi-api"
            and info_payload.get("dependencies") == ["postgres"]
        )
        request_id_ok = (
            isinstance(request_id_header, str)
            and bool(request_id_header)
            and info_payload.get("request_id") == request_id_header
        )
    except json.JSONDecodeError:
        info_shape_ok = False

    result = {
        "all_matched": all(case.matched for case in cases)
        and health_shape_ok
        and info_shape_ok
        and request_id_ok,
        "expect_degraded_health": args.expect_degraded_health,
        "health_shape_ok": health_shape_ok,
        "info_shape_ok": info_shape_ok,
        "request_id_matched": request_id_ok,
        "cases": [asdict(case) for case in cases],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return

    for case in cases:
        print(
            case.name,
            f"expected={case.expected}",
            f"actual={case.status}",
            f"matched={'yes' if case.matched else 'no'}",
            case.detail,
        )
    print("health_shape_ok", "yes" if health_shape_ok else "no")
    print("info_shape_ok", "yes" if info_shape_ok else "no")
    print("request_id_propagated", "yes" if request_id_ok else "no")
    print("all_matched", "yes" if result["all_matched"] else "no")


if __name__ == "__main__":
    main()
