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
        "check_commutes_every_5_mins": {
            "task": "app.tasks.check_all_commutes",
            # Run every 5 minutes from 6 AM to 10 AM every day
            "schedule": crontab(minute="*/5", hour="6-10"),
        }
    }
)
