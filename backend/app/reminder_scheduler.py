from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.line_client import push_text
from app.models import CommuteOverride
from app.crud import (
    get_profile,
    mark_reminder_sent,
    clear_today_reminder_state_db,
)

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def hhmm_to_seconds(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def current_seconds_of_day() -> int:
    now = now_taipei()
    return now.hour * 3600 + now.minute * 60 + now.second


async def check_and_send_departure_reminders():
    db = SessionLocal()
    try:
        today = now_taipei().date()
        now_dt = now_taipei()
        now_hhmmss = now_dt.strftime("%H:%M:%S")
        now_sec = current_seconds_of_day()

        overrides = db.query(CommuteOverride).filter(
            CommuteOverride.target_date == today,
            CommuteOverride.frozen_plan_key.isnot(None),
            CommuteOverride.frozen_departure_time.isnot(None),
            CommuteOverride.frozen_reminder_text.isnot(None),
        ).all()

        for override in overrides:
            try:
                user = override.user
                if not user:
                    continue

                profile = user.profile or get_profile(db, user.id)
                if not profile.reminder_enabled:
                    print(f"[reminder-check] user_id={user.id} skipped, reminder disabled")
                    continue

                if override.last_sent_plan_key == override.frozen_plan_key:
                    print(
                        f"[reminder-check] user_id={user.id} "
                        f"now={now_hhmmss} departure={override.frozen_departure_time} already_sent=True"
                    )
                    continue

                departure_sec = hhmm_to_seconds(override.frozen_departure_time)

                print(
                    f"[reminder-check] user_id={user.id} "
                    f"now={now_hhmmss} departure={override.frozen_departure_time} "
                    f"last_sent_plan_key={override.last_sent_plan_key}"
                )

                if now_sec >= departure_sec:
                    print(
                        f"[reminder] about to send | user_id={user.id} "
                        f"line_user_id={user.line_user_id} now={now_hhmmss} "
                        f"departure={override.frozen_departure_time}"
                    )
                    await push_text(user.line_user_id, override.frozen_reminder_text)
                    mark_reminder_sent(
                        db=db,
                        user_id=user.id,
                        target_date=today,
                        plan_key=override.frozen_plan_key,
                        sent_at=now_dt,
                    )
                    print(f"[reminder] sent to user_id={user.id} at {now_hhmmss}")

            except Exception as e:
                import traceback
                print(f"[reminder] failed for user_id={override.user_id}: {e}")
                print(traceback.format_exc())

    finally:
        db.close()


def start_reminder_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        check_and_send_departure_reminders,
        "interval",
        seconds=10,
        id="departure_reminder_job",
        replace_existing=True,
    )
    scheduler.start()
    print("[reminder] scheduler started")


def clear_today_reminder_state_for_user(user_id: int):
    db = SessionLocal()
    try:
        today = now_taipei().date()
        clear_today_reminder_state_db(db, user_id, today)
        print(f"[reminder-reset] cleared cache for user_id={user_id} date={today.isoformat()}")
    finally:
        db.close()