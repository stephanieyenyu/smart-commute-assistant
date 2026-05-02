import os
from celery import Celery
from celery.schedules import crontab
from app.config import REDIS_URL

celery_app = Celery(
    "smart_commute",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"]
)

celery_app.conf.update(
    timezone="Asia/Taipei",
    enable_utc=False,
    beat_schedule={
        "check_departure_reminders": {
            "task": "app.tasks.check_departure_reminders",
            "schedule": 10.0,
        },
        "send_nightly_briefs": {
            "task": "app.tasks.send_nightly_briefs",
            "schedule": crontab(minute=0, hour=21),
        },
        "run_morning_watchdog": {
            "task": "app.tasks.run_morning_watchdog",
            "schedule": crontab(minute="*/5"),
        },
    }
)
