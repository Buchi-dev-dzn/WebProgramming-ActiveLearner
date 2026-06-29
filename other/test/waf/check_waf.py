#!/usr/bin/env python3
import argparse
import json
import ssl
import urllib.error
import urllib.parse
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
        description="Verify WAF behavior from outside the VM."
    )
    parser.add_argument("target", help="Target host or IP, e.g. 192.168.64.4")
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def request_case(
    name: str,
    method: str,
    url: str,
    expected: int,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> CaseResult:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=build_ssl_context() if url.startswith("https://") else None,
        ) as response:
            body = response.read(200).decode("utf-8", errors="replace").strip()
            status = response.getcode()
            return CaseResult(
                name=name,
                method=method,
                url=url,
                status=status,
                expected=expected,
                matched=(status == expected),
                detail=body,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read(200).decode("utf-8", errors="replace").strip()
        return CaseResult(
            name=name,
            method=method,
            url=url,
            status=exc.code,
            expected=expected,
            matched=(exc.code == expected),
            detail=body,
        )
    except Exception as exc:  # noqa: BLE001
        return CaseResult(
            name=name,
            method=method,
            url=url,
            status=None,
            expected=expected,
            matched=False,
            detail=f"error:{type(exc).__name__}",
        )


def main() -> None:
    args = parse_args()
    script_query = urllib.parse.quote("<script>")
    union_query = urllib.parse.quote("union select")

    cases = [
        request_case(
            "http_root_ok",
            "GET",
            f"http://{args.target}:{args.http_port}/",
            200,
            args.timeout,
        ),
        request_case(
            "https_api_health_ok",
            "GET",
            f"https://{args.target}:{args.https_port}/api/health",
            200,
            args.timeout,
        ),
        request_case(
            "blocked_sqlmap_ua",
            "GET",
            f"http://{args.target}:{args.http_port}/",
            403,
            args.timeout,
            headers={"User-Agent": "sqlmap"},
        ),
        request_case(
            "blocked_script_query",
            "GET",
            f"http://{args.target}:{args.http_port}/?q={script_query}",
            403,
            args.timeout,
        ),
        request_case(
            "blocked_union_query",
            "GET",
            f"http://{args.target}:{args.http_port}/?q={union_query}",
            403,
            args.timeout,
        ),
        request_case(
            "blocked_put_method",
            "PUT",
            f"http://{args.target}:{args.http_port}/",
            405,
            args.timeout,
        ),
    ]

    result = {
        "all_matched": all(case.matched for case in cases),
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
    print("all_matched", "yes" if result["all_matched"] else "no")


if __name__ == "__main__":
    main()
