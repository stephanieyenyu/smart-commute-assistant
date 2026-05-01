import time
from datetime import datetime, timezone


def api_timer_start() -> float:
    return time.perf_counter()


def log_api_health(
    endpoint: str,
    started_at: float,
    *,
    status_code: int | None = None,
    error_message: str | None = None,
) -> None:
    latency_ms = round((time.perf_counter() - started_at) * 1000)
    status_text = status_code if status_code is not None else "n/a"
    timestamp = datetime.now(timezone.utc).isoformat()
    error_text = f" error={error_message}" if error_message else ""
    print(
        f"[api-health] endpoint={endpoint} timestamp={timestamp} "
        f"latency_ms={latency_ms} status_code={status_text}{error_text}"
    )
