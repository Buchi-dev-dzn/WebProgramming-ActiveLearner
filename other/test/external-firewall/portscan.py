#!/usr/bin/env python3
import argparse
import asyncio
import ipaddress
import json
from dataclasses import asdict, dataclass


@dataclass
class ScanResult:
    port: int
    status: str
    detail: str


def parse_ports(value: str) -> list[int]:
    ports: set[int] = set()

    for chunk in value.split(","):
        item = chunk.strip()
        if not item:
            continue

        if "-" in item:
            start_text, end_text = item.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise argparse.ArgumentTypeError(f"invalid range: {item}")
            for port in range(start, end + 1):
                validate_port(port)
                ports.add(port)
            continue

        port = int(item)
        validate_port(port)
        ports.add(port)

    if not ports:
        raise argparse.ArgumentTypeError("no ports specified")

    return sorted(ports)


def validate_port(port: int) -> None:
    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError(f"port out of range: {port}")


async def scan_port(target: str, port: int, timeout: float) -> ScanResult:
    try:
        connect = asyncio.open_connection(target, port)
        reader, writer = await asyncio.wait_for(connect, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return ScanResult(port=port, status="open", detail="tcp_connect_succeeded")
    except TimeoutError:
        return ScanResult(port=port, status="filtered", detail="connection_timed_out")
    except ConnectionRefusedError:
        return ScanResult(port=port, status="closed", detail="connection_refused")
    except OSError as exc:
        lowered = str(exc).lower()
        if "timed out" in lowered:
            return ScanResult(port=port, status="filtered", detail="connection_timed_out")
        return ScanResult(port=port, status="error", detail=type(exc).__name__)


async def bounded_scan(
    target: str,
    ports: list[int],
    timeout: float,
    concurrency: int,
) -> list[ScanResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def run(port: int) -> ScanResult:
        async with semaphore:
            return await scan_port(target, port, timeout)

    tasks = [asyncio.create_task(run(port)) for port in ports]
    return [await task for task in tasks]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single-host TCP connect scanner for authorized firewall verification."
    )
    parser.add_argument("target", help="Target IPv4 or IPv6 address")
    parser.add_argument(
        "--ports",
        default="1-1024",
        help="Ports to scan, e.g. 22,80,443,8000-8100 (default: 1-1024)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0.75,
        help="Per-port timeout in seconds (default: 0.75)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=200,
        help="Maximum simultaneous connections (default: 200)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results as JSON",
    )
    return parser.parse_args()


def print_text(results: list[ScanResult]) -> None:
    interesting = [result for result in results if result.status in {"open", "filtered"}]
    target_set = interesting if interesting else results

    for result in target_set:
        print(f"{result.port:5d}  {result.status:8s}  {result.detail}")

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    print()
    print(
        "summary "
        + " ".join(f"{status}={counts[status]}" for status in sorted(counts))
    )


async def main() -> None:
    args = parse_args()
    ipaddress.ip_address(args.target)

    if args.concurrency < 1:
        raise SystemExit("concurrency must be >= 1")
    if args.timeout <= 0:
        raise SystemExit("timeout must be > 0")

    ports = parse_ports(args.ports)
    results = await bounded_scan(args.target, ports, args.timeout, args.concurrency)
    results.sort(key=lambda item: item.port)

    if args.json:
        print(json.dumps([asdict(result) for result in results], ensure_ascii=True))
        return

    print_text(results)


if __name__ == "__main__":
    asyncio.run(main())

