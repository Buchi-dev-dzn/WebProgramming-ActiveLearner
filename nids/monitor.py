import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path


SOURCE_DIR = Path(os.environ.get("NIDS_SOURCE_DIR", "/sources"))
ALERT_PATH = Path(os.environ.get("NIDS_ALERT_PATH", "/alerts/alerts.log"))
SCAN_INTERVAL_SECONDS = int(os.environ.get("NIDS_SCAN_INTERVAL_SECONDS", "5"))

SIGNATURES = [
    ("waf_block_status", re.compile(r'"\s(?:403|405|429)\s')),
    ("sql_injection_probe", re.compile(r"(?i)(union\s+select|or\s+1=1|sleep\(|benchmark\()")),
    ("xss_probe", re.compile(r"(?i)(<script|%3cscript|javascript:)")),
    ("sensitive_path_probe", re.compile(r"(?i)(/\.git|/etc/passwd|wp-admin|phpmyadmin)")),
    ("scanner_user_agent", re.compile(r"(?i)(sqlmap|nikto|nmap|masscan|acunetix|dirbuster)")),
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def emit_alert(event: dict[str, object]) -> None:
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True) + "\n")


def list_log_files() -> list[Path]:
    if not SOURCE_DIR.exists():
        return []
    return sorted(path for path in SOURCE_DIR.rglob("*.log") if path.is_file())


def read_new_lines(path: Path, offsets: dict[str, int]) -> list[str]:
    key = str(path)
    previous_offset = offsets.get(key, 0)
    current_size = path.stat().st_size
    if current_size < previous_offset:
        previous_offset = 0

    with path.open("r", encoding="utf-8", errors="replace") as file:
        file.seek(previous_offset)
        lines = file.readlines()
        offsets[key] = file.tell()
    return lines


def inspect_line(path: Path, line: str) -> None:
    for signature, pattern in SIGNATURES:
        if pattern.search(line):
            emit_alert(
                {
                    "timestamp": utc_now(),
                    "component": "nids",
                    "severity": "warning",
                    "signature": signature,
                    "source_file": str(path.relative_to(SOURCE_DIR)),
                    "sample": line.strip()[:500],
                }
            )


def main() -> None:
    offsets: dict[str, int] = {}
    emit_alert(
        {
            "timestamp": utc_now(),
            "component": "nids",
            "severity": "info",
            "event": "sensor_started",
            "source_dir": str(SOURCE_DIR),
        }
    )

    while True:
        for path in list_log_files():
            try:
                for line in read_new_lines(path, offsets):
                    inspect_line(path, line)
            except OSError as error:
                emit_alert(
                    {
                        "timestamp": utc_now(),
                        "component": "nids",
                        "severity": "warning",
                        "event": "source_read_failed",
                        "source_file": str(path),
                        "error": str(error),
                    }
                )
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
