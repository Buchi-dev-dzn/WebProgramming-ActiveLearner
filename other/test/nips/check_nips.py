#!/usr/bin/env python3
import argparse
import asyncio
import json
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass


@dataclass
class BurstSummary:
    total: int
    ok_200: int
    blocked_429: int
    other: dict[str, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify NIPS behavior from outside the VM."
    )
    parser.add_argument("target", help="Target host or IP, e.g. 192.168.64.4")
    parser.add_argument("--http-port", type=int, default=80)
    parser.add_argument("--https-port", type=int, default=443)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument(
        "--burst-size",
        type=int,
        default=140,
        help="Number of HTTP requests for burst testing (default: 140)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=60,
        help="Parallelism for burst testing (default: 60)",
    )
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def fetch(url: str, timeout: float, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=build_ssl_context() if url.startswith("https://") else None,
        ) as response:
            body = response.read(200).decode("utf-8", errors="replace").strip()
            return response.getcode(), body
    except urllib.error.HTTPError as exc:
        body = exc.read(200).decode("utf-8", errors="replace").strip()
        return exc.code, body


async def burst_once(target: str, port: int, timeout: float) -> int | str:
    url = f"http://{target}:{port}/health"
    try:
        return await asyncio.to_thread(fetch, url, timeout)
    except Exception as exc:  # noqa: BLE001
        return f"error:{type(exc).__name__}"


async def run_burst(target: str, port: int, timeout: float, burst_size: int, concurrency: int) -> BurstSummary:
    semaphore = asyncio.Semaphore(concurrency)

    async def one() -> int | str:
        async with semaphore:
            return await burst_once(target, port, timeout)

    tasks = [asyncio.create_task(one()) for _ in range(burst_size)]
    results = await asyncio.gather(*tasks)

    ok_200 = 0
    blocked_429 = 0
    other: dict[str, int] = {}

    for result in results:
        if isinstance(result, tuple):
            status_code = result[0]
            if status_code == 200:
                ok_200 += 1
            elif status_code == 429:
                blocked_429 += 1
            else:
                key = str(status_code)
                other[key] = other.get(key, 0) + 1
        else:
            other[result] = other.get(result, 0) + 1

    return BurstSummary(
        total=burst_size,
        ok_200=ok_200,
        blocked_429=blocked_429,
        other=other,
    )


async def main() -> None:
    args = parse_args()

    http_status, http_body = await asyncio.to_thread(
        fetch, f"http://{args.target}:{args.http_port}/health", args.timeout
    )
    https_status, https_body = await asyncio.to_thread(
        fetch, f"https://{args.target}:{args.https_port}/api/health", args.timeout
    )
    burst = await run_burst(
        args.target,
        args.http_port,
        args.timeout,
        args.burst_size,
        args.concurrency,
    )

    result = {
        "baseline_http": {"status": http_status, "body": http_body},
        "baseline_https": {"status": https_status, "body": https_body},
        "burst": asdict(burst),
        "nips_effect_detected": burst.blocked_429 > 0,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return

    print("baseline_http", http_status, http_body)
    print("baseline_https", https_status, https_body)
    print(
        "burst",
        f"total={burst.total}",
        f"ok_200={burst.ok_200}",
        f"blocked_429={burst.blocked_429}",
        f"other={burst.other}",
    )
    print("nips_effect_detected", "yes" if burst.blocked_429 > 0 else "no")


if __name__ == "__main__":
    asyncio.run(main())
