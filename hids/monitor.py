import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


WATCH_DIR = Path(os.environ.get("HIDS_WATCH_DIR", "/watch/fastapi-app"))
ALERT_PATH = Path(os.environ.get("HIDS_ALERT_PATH", "/alerts/alerts.log"))
HEALTH_URL = os.environ.get("HIDS_HEALTH_URL", "http://fastapi-app:8000/api/health")
SCAN_INTERVAL_SECONDS = int(os.environ.get("HIDS_SCAN_INTERVAL_SECONDS", "10"))


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def emit_alert(event: dict[str, object]) -> None:
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True) + "\n")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot() -> dict[str, str]:
    if not WATCH_DIR.exists():
        return {}

    result: dict[str, str] = {}
    for path in sorted(WATCH_DIR.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(WATCH_DIR))] = file_digest(path)
    return result


def check_integrity(previous: dict[str, str], current: dict[str, str]) -> None:
    previous_files = set(previous)
    current_files = set(current)

    for filename in sorted(current_files - previous_files):
        emit_alert(
            {
                "timestamp": utc_now(),
                "component": "hids-hips",
                "severity": "critical",
                "event": "watched_file_created",
                "file": filename,
            }
        )

    for filename in sorted(previous_files - current_files):
        emit_alert(
            {
                "timestamp": utc_now(),
                "component": "hids-hips",
                "severity": "critical",
                "event": "watched_file_deleted",
                "file": filename,
            }
        )

    for filename in sorted(previous_files & current_files):
        if previous[filename] != current[filename]:
            emit_alert(
                {
                    "timestamp": utc_now(),
                    "component": "hids-hips",
                    "severity": "critical",
                    "event": "watched_file_modified",
                    "file": filename,
                }
            )


def check_health() -> None:
    try:
        with urlopen(HEALTH_URL, timeout=3) as response:
            if response.status >= 500:
                emit_alert(
                    {
                        "timestamp": utc_now(),
                        "component": "hids-hips",
                        "severity": "warning",
                        "event": "application_health_degraded",
                        "status": response.status,
                    }
                )
    except (OSError, URLError) as error:
        emit_alert(
            {
                "timestamp": utc_now(),
                "component": "hids-hips",
                "severity": "warning",
                "event": "application_health_unreachable",
                "error": str(error),
            }
        )


def main() -> None:
    baseline = snapshot()
    emit_alert(
        {
            "timestamp": utc_now(),
            "component": "hids-hips",
            "severity": "info",
            "event": "sensor_started",
            "watch_dir": str(WATCH_DIR),
            "baseline_files": len(baseline),
            "health_url": HEALTH_URL,
        }
    )

    while True:
        current = snapshot()
        check_integrity(baseline, current)
        baseline = current
        check_health()
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
