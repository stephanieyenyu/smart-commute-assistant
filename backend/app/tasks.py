import asyncio

from app.celery_app import celery_app
from app.reminder_scheduler import (
    check_and_send_departure_reminders,
    send_nightly_briefs,
)

# NOTE: there is no standalone "morning watchdog" task anymore. Its job
# (1hr/5min pre-departure alerts with commute time + weather) already runs
# inside check_and_send_departure_reminders, which check_departure_reminders
# below wraps. This module is only exercised by local `docker compose up`
# (celery_worker / celery_beat); Render production runs uvicorn only, and
# uses the APScheduler jobs registered in start_reminder_scheduler() instead.


def _run_async_task(coro) -> None:
    asyncio.run(coro)


@celery_app.task(name="app.tasks.check_departure_reminders")
def check_departure_reminders() -> None:
    _run_async_task(check_and_send_departure_reminders())


@celery_app.task(name="app.tasks.send_nightly_briefs")
def celery_send_nightly_briefs() -> None:
    _run_async_task(send_nightly_briefs())
