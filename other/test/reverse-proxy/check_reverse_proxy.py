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


UPSTREAM_UNAVAILABLE_STATUSES = {502, 503}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify reverse-proxy behavior from outside the VM."
    )
    parser.add_argument("target", help="Target host or IP, e.g. 192.168.64.4")
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--expect-upstream-unavailable",
        action="store_true",
        help="Expect /api/info to return reverse-proxy generated 503 JSON.",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def open_request(
    method: str,
    url: str,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> tuple[int | None, str, dict[str, str]]:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=build_ssl_context() if url.startswith("https://") else None,
        ) as response:
            body = response.read(400).decode("utf-8", errors="replace").strip()
            return response.getcode(), body, dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        body = exc.read(400).decode("utf-8", errors="replace").strip()
        return exc.code, body, dict(exc.headers.items())
    except Exception as exc:  # noqa: BLE001
        return None, f"error:{type(exc).__name__}", {}


def request_case(
    name: str,
    method: str,
    url: str,
    expected: int,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> CaseResult:
    status, body, _response_headers = open_request(method, url, timeout, headers)
    return CaseResult(
        name=name,
        method=method,
        url=url,
        status=status,
        expected=expected,
        matched=(status == expected),
        detail=body,
    )


def main() -> None:
    args = parse_args()

    info_expected = 503 if args.expect_upstream_unavailable else 200

    cases = [
        request_case(
            "http_health_ok",
            "GET",
            f"http://{args.target}:{args.http_port}/health",
            200,
            args.timeout,
        ),
        request_case(
            "http_root_not_found",
            "GET",
            f"http://{args.target}:{args.http_port}/",
            404,
            args.timeout,
        ),
        request_case(
            "https_api_route_restricted",
            "GET",
            f"https://{args.target}:{args.https_port}/api",
            404,
            args.timeout,
        ),
        request_case(
            "https_api_unknown_not_found",
            "GET",
            f"https://{args.target}:{args.https_port}/api/unknown",
            404,
            args.timeout,
        ),
        request_case(
            "https_api_info_status",
            "GET",
            f"https://{args.target}:{args.https_port}/api/info",
            info_expected,
            args.timeout,
        ),
    ]

    info_status, info_body, info_headers = open_request(
        "GET",
        f"https://{args.target}:{args.https_port}/api/info",
        args.timeout,
    )
    request_id_header = info_headers.get("X-Request-Id") or info_headers.get("x-request-id")

    request_id_ok = False
    response_shape_ok = False
    info_status_ok = False
    if info_status == info_expected:
        info_status_ok = True
        try:
            payload = json.loads(info_body)
            if args.expect_upstream_unavailable:
                response_shape_ok = payload.get("error") == "upstream_unavailable"
            else:
                response_shape_ok = (
                    payload.get("service") == "fastapi-api"
                    and payload.get("via") == [
                        "reverse-proxy",
                        "internal-firewall",
                        "fastapi-api",
                    ]
                )
                request_id_ok = (
                    isinstance(request_id_header, str)
                    and bool(request_id_header)
                    and payload.get("request_id") == request_id_header
                )
        except json.JSONDecodeError:
            response_shape_ok = False
    elif args.expect_upstream_unavailable and info_status in UPSTREAM_UNAVAILABLE_STATUSES:
        info_status_ok = True
        try:
            payload = json.loads(info_body)
            response_shape_ok = payload.get("error") == "upstream_unavailable"
        except json.JSONDecodeError:
            response_shape_ok = False

    result = {
        "all_matched": all(
            case.matched or (case.name == "https_api_info_status" and info_status_ok)
            for case in cases
        ) and response_shape_ok and (
            args.expect_upstream_unavailable or request_id_ok
        ),
        "expect_upstream_unavailable": args.expect_upstream_unavailable,
        "request_id_header": request_id_header,
        "request_id_matched": request_id_ok,
        "response_shape_ok": response_shape_ok,
        "cases": [asdict(case) for case in cases],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return

    for case in cases:
        print(
            case.name,
            f"expected={case.expected if case.name != 'https_api_info_status' or not args.expect_upstream_unavailable else '502_or_503'}",
            f"actual={case.status}",
            f"matched={'yes' if (case.matched or (case.name == 'https_api_info_status' and info_status_ok)) else 'no'}",
            case.detail,
        )
    if not args.expect_upstream_unavailable:
        print(
            "request_id_propagated",
            "yes" if request_id_ok else "no",
            request_id_header or "missing",
        )
    print("response_shape_ok", "yes" if response_shape_ok else "no")
    print("all_matched", "yes" if result["all_matched"] else "no")


if __name__ == "__main__":
    main()
