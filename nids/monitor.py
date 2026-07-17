import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


SOURCE_DIR = Path(os.environ.get("NIDS_SOURCE_DIR", "/sources"))
ALERT_PATH = Path(os.environ.get("NIDS_ALERT_PATH", "/alerts/alerts.log"))
STATE_PATH = Path(os.environ.get("NIDS_STATE_PATH", "/alerts/state.json"))
SCAN_INTERVAL_SECONDS = int(os.environ.get("NIDS_SCAN_INTERVAL_SECONDS", "5"))
INGEST_URL = os.environ.get("NIDS_INGEST_URL")
SENSOR_TOKEN = os.environ.get("SECURITY_SENSOR_TOKEN")

SIGNATURES = [
    ("warning", "nids_block_status", re.compile(r'"\s(?:403|405|429)\s')),
    ("critical", "nids_sql_injection_probe", re.compile(r"(?i)(union\s+select|or\s+1=1|sleep\(|benchmark\()")),
    ("critical", "nids_xss_probe", re.compile(r"(?i)(<script|%3cscript|javascript:)")),
    ("warning", "nids_sensitive_path_probe", re.compile(r"(?i)(/\.git|/etc/passwd|wp-admin|phpmyadmin)")),
    ("warning", "nids_scanner_user_agent", re.compile(r"(?i)(sqlmap|nikto|nmap|masscan|acunetix|dirbuster)")),
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def emit_alert(event: dict[str, object]) -> None:
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True) + "\n")


def load_offsets() -> dict[str, int]:
    if not STATE_PATH.exists():
        return {}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): int(value) for key, value in data.items()}


def save_offsets(offsets: dict[str, int]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(offsets, file, sort_keys=True)
    tmp_path.replace(STATE_PATH)


def post_sensor_event(event: dict[str, object]) -> None:
    if not INGEST_URL or not SENSOR_TOKEN:
        return

    payload = {
        "component": "nids",
        "action": event.get("action", "nids_event"),
        "severity": event.get("severity", "info"),
        "target_type": event.get("target_type", "network-log"),
        "target_id": event.get("target_id"),
        "details": {
            key: value
            for key, value in event.items()
            if key not in {"action", "severity", "target_type", "target_id"}
        },
    }
    request = Request(
        INGEST_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Sensor-Token": SENSOR_TOKEN,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=3) as response:
            response.read()
    except (OSError, URLError) as error:
        emit_alert(
            {
                "timestamp": utc_now(),
                "component": "nids",
                "severity": "warning",
                "action": "nids_ingest_failed",
                "error": str(error),
            }
        )


def emit(event: dict[str, object]) -> None:
    event.setdefault("timestamp", utc_now())
    event.setdefault("component", "nids")
    emit_alert(event)
    post_sensor_event(event)


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
    for severity, action, pattern in SIGNATURES:
        if pattern.search(line):
            relative_path = str(path.relative_to(SOURCE_DIR))
            emit(
                {
                    "severity": severity,
                    "action": action,
                    "target_type": "network-log",
                    "target_id": relative_path,
                    "source_file": relative_path,
                    "sample": line.strip()[:500],
                }
            )


def main() -> None:
    offsets = load_offsets()
    emit(
        {
            "severity": "info",
            "action": "sensor_heartbeat",
            "target_type": "sensor",
            "target_id": "nids",
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
                emit(
                    {
                        "severity": "warning",
                        "action": "nids_source_read_failed",
                        "target_type": "network-log",
                        "target_id": str(path),
                        "source_file": str(path),
                        "error": str(error),
                    }
                )
        save_offsets(offsets)
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
