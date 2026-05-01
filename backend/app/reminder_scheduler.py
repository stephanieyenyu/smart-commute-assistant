from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.line_client import push_text, push_with_quick_reply
from app.models import CommuteOverride, User
from app.crud import (
    get_profile,
    mark_reminder_sent,
    clear_today_reminder_state_db,
)

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# Quick Reply buttons shown after departure reminder push
REMINDER_QUICK_REPLIES = [
    {"type": "message", "label": "📋 今日通勤建議", "text": "今天通勤建議"},
    {"type": "message", "label": "⏰ 修改到公司時間", "text": "修改今天到公司時間"},
    {"type": "message", "label": "🚌 今天搭公車",    "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",    "text": "今天搭捷運"},
]


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
                user = db.query(User).filter(User.id == override.user_id).first()
                if not user:
                    continue

                profile = user.profile or get_profile(db, user.id)
                if not profile.reminder_enabled:
                    continue

                if override.last_sent_plan_key == override.frozen_plan_key:
                    print(
                        f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                        f"departure={override.frozen_departure_time} already_sent=True"
                    )
                    continue

                departure_sec = hhmm_to_seconds(override.frozen_departure_time)
                print(
                    f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                    f"departure={override.frozen_departure_time} last_sent_plan_key={override.last_sent_plan_key}"
                )

                if now_sec >= departure_sec - 180:
                    await push_with_quick_reply(
                        user.line_user_id,
                        override.frozen_reminder_text,
                        REMINDER_QUICK_REPLIES,
                    )
                    mark_reminder_sent(
                        db=db,
                        user_id=user.id,
                        target_date=today,
                        plan_key=override.frozen_plan_key,
                        sent_at=now_dt,
                    )
                    print(f"[reminder] sent user_id={user.id} at {now_hhmmss}")

            except Exception as e:
                import traceback
                print(f"[reminder] failed user_id={override.user_id} error={e}")
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