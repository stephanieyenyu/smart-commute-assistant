import asyncio

from app.celery_app import celery_app
from app.reminder_scheduler import (
    check_and_send_departure_reminders,
    run_morning_watchdog,
    send_nightly_briefs,
)


def _run_async_task(coro) -> None:
    asyncio.run(coro)


@celery_app.task(name="app.tasks.check_departure_reminders")
def check_departure_reminders() -> None:
    _run_async_task(check_and_send_departure_reminders())


@celery_app.task(name="app.tasks.send_nightly_briefs")
def celery_send_nightly_briefs() -> None:
    _run_async_task(send_nightly_briefs())


@celery_app.task(name="app.tasks.run_morning_watchdog")
def celery_run_morning_watchdog() -> None:
    _run_async_task(run_morning_watchdog())
