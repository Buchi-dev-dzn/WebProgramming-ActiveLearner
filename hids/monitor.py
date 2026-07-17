import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


WATCH_DIR = Path(os.environ.get("HIDS_WATCH_DIR", "/watch/fastapi-app"))
ALERT_PATH = Path(os.environ.get("HIDS_ALERT_PATH", "/alerts/alerts.log"))
BASELINE_PATH = Path(os.environ.get("HIDS_BASELINE_PATH", "/alerts/baseline.json"))
HEALTH_URL = os.environ.get("HIDS_HEALTH_URL", "http://fastapi-app:8000/api/health")
SCAN_INTERVAL_SECONDS = int(os.environ.get("HIDS_SCAN_INTERVAL_SECONDS", "10"))
INGEST_URL = os.environ.get("HIDS_INGEST_URL")
SENSOR_TOKEN = os.environ.get("SECURITY_SENSOR_TOKEN")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def emit_alert(event: dict[str, object]) -> None:
    ALERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, sort_keys=True) + "\n")


def post_sensor_event(event: dict[str, object]) -> None:
    if not INGEST_URL or not SENSOR_TOKEN:
        return

    payload = {
        "component": "hids-hips",
        "action": event.get("action", "hids_event"),
        "severity": event.get("severity", "info"),
        "target_type": event.get("target_type", "host"),
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
                "component": "hids-hips",
                "severity": "warning",
                "action": "hids_ingest_failed",
                "error": str(error),
            }
        )


def emit(event: dict[str, object]) -> None:
    event.setdefault("timestamp", utc_now())
    event.setdefault("component", "hids-hips")
    emit_alert(event)
    post_sensor_event(event)


def load_baseline() -> dict[str, str] | None:
    if not BASELINE_PATH.exists():
        return None
    try:
        with BASELINE_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(key): str(value) for key, value in data.items()}


def save_baseline(baseline: dict[str, str]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = BASELINE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as file:
        json.dump(baseline, file, sort_keys=True)
    tmp_path.replace(BASELINE_PATH)


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
        emit(
            {
                "severity": "critical",
                "action": "hids_file_created",
                "target_type": "host-file",
                "target_id": filename,
                "file": filename,
            }
        )

    for filename in sorted(previous_files - current_files):
        emit(
            {
                "severity": "critical",
                "action": "hids_file_deleted",
                "target_type": "host-file",
                "target_id": filename,
                "file": filename,
            }
        )

    for filename in sorted(previous_files & current_files):
        if previous[filename] != current[filename]:
            emit(
                {
                    "severity": "critical",
                    "action": "hids_file_modified",
                    "target_type": "host-file",
                    "target_id": filename,
                    "file": filename,
                    "previous_sha256": previous[filename],
                    "current_sha256": current[filename],
                }
            )


def check_health() -> None:
    try:
        with urlopen(HEALTH_URL, timeout=3) as response:
            if response.status >= 500:
                emit(
                    {
                        "severity": "warning",
                        "action": "hids_health_degraded",
                        "target_type": "application-health",
                        "target_id": HEALTH_URL,
                        "status": response.status,
                    }
                )
    except (OSError, URLError) as error:
        emit(
            {
                "severity": "warning",
                "action": "hids_health_unreachable",
                "target_type": "application-health",
                "target_id": HEALTH_URL,
                "error": str(error),
            }
        )


def main() -> None:
    baseline = load_baseline()
    if baseline is None:
        baseline = snapshot()
        save_baseline(baseline)

    emit(
        {
            "severity": "info",
            "action": "sensor_heartbeat",
            "target_type": "sensor",
            "target_id": "hids-hips",
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
        save_baseline(baseline)
        check_health()
        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
