from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.line_client import push_text
from app.models import User
from app.service import build_today_reminder_payload

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
_sent_cache: set[tuple[int, str]] = set()


async def check_and_send_departure_reminders():
    db = SessionLocal()
    try:
        users = db.query(User).all()
        today_str = date.today().isoformat()

        for user in users:
            try:
                payload = await build_today_reminder_payload(db, user.id)

                if not payload.get("ok"):
                    continue

                departure_time = payload.get("departure_time")
                if not departure_time:
                    continue

                now_hhmm = payload_time_now()
                cache_key = (user.id, today_str)

                if now_hhmm == departure_time and cache_key not in _sent_cache:
                    await push_text(user.line_user_id, payload["text"])
                    _sent_cache.add(cache_key)
                    print(f"[reminder] sent to user_id={user.id} at {now_hhmm}")

            except Exception as e:
                import traceback
                print(f"[reminder] failed for user_id={user.id}: {e}")
                print(traceback.format_exc())

        cleanup_sent_cache()

    finally:
        db.close()


def payload_time_now():
    from datetime import datetime
    return datetime.now().strftime("%H:%M")


def cleanup_sent_cache():
    today_str = date.today().isoformat()
    stale_keys = [key for key in _sent_cache if key[1] != today_str]
    for key in stale_keys:
        _sent_cache.discard(key)


def start_reminder_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        check_and_send_departure_reminders,
        "cron",
        second=0,
        id="departure_reminder_job",
        replace_existing=True,
    )
    scheduler.start()
    print("[reminder] scheduler started")