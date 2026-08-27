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
    timestamp = datetime.now(timezone.utc)
    timestamp_text = timestamp.isoformat()
    error_text = f" error={error_message}" if error_message else ""
    print(
        f"[api-health] endpoint={endpoint} timestamp={timestamp_text} "
        f"latency_ms={latency_ms} status_code={status_text}{error_text}"
    )
    _persist_api_health_log(
        endpoint=endpoint,
        timestamp=timestamp,
        latency_ms=latency_ms,
        status_code=status_code,
        error_message=error_message,
    )


def _persist_api_health_log(
    endpoint: str,
    timestamp: datetime,
    latency_ms: int,
    status_code: int | None,
    error_message: str | None,
) -> None:
    try:
        from app.crud import record_api_health_log
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            record_api_health_log(
                db=db,
                endpoint=endpoint,
                timestamp=timestamp,
                latency_ms=latency_ms,
                status_code=status_code,
                error_message=error_message,
            )
        finally:
            db.close()
    except Exception as e:
        # Deliberately tolerant: a logging failure must never break the provider
        # call that triggered it. The wiring defect this path once hid — a crud
        # function that did not exist, so every write raised ImportError and was
        # swallowed here for months — is now caught at startup in main.py instead,
        # where it stops the process. See docs/known-issues.md A-6.
        print(f"[api-health] persist failed endpoint={endpoint} error={e}")
