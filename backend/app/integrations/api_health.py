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
    except ImportError as e:
        # A missing crud function is a wiring defect, not a runtime condition.
        # This was silent for months: the ImportError was caught here, printed to
        # stdout, and lost on the next Render restart — leaving api_health_logs
        # permanently empty while every call site appeared to be instrumented.
        # See docs/known-issues.md A-6.
        raise RuntimeError(
            f"api_health persistence is not wired up: {e}"
        ) from e
    except Exception as e:
        # A database error must not break the provider call that triggered it.
        print(f"[api-health] persist failed endpoint={endpoint} error={e}")
