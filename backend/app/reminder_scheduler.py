from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.line_client import push_text
from app.models import User
from app.crud import (
    get_profile,
    get_or_create_override,
    save_frozen_reminder,
    mark_reminder_sent,
    clear_today_reminder_state_db,
)
from app.service import build_today_reminder_payload

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 提前準備方案，但真正發送仍在接近或到達出門時間點
PREPARE_LEAD_SECONDS = 90


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
        users = db.query(User).all()
        today = now_taipei().date()
        now_dt = now_taipei()
        now_hhmmss = now_dt.strftime("%H:%M:%S")
        now_sec = current_seconds_of_day()

        for user in users:
            try:
                profile = get_profile(db, user.id)

                if not profile.reminder_enabled:
                    print(f"[reminder-check] user_id={user.id} skipped, reminder disabled")
                    continue

                payload = await build_today_reminder_payload(db, user.id, today)
                if payload is None:
                    print(f"[reminder-check] user_id={user.id} skipped, payload is None")
                    continue

                if not payload.get("ok"):
                    print(f"[reminder-check] user_id={user.id} skipped, reason={payload.get('reason')}")
                    continue

                plan_key = payload.get("plan_key")
                departure_time = payload.get("departure_time")
                reminder_text = payload.get("text")

                if not plan_key or not departure_time or not reminder_text:
                    print(f"[reminder-check] user_id={user.id} skipped, payload incomplete")
                    continue

                override = get_or_create_override(db, user.id, today)
                departure_sec = hhmm_to_seconds(departure_time)
                prepare_sec = max(0, departure_sec - PREPARE_LEAD_SECONDS)

                # 接近出門時間時先凍結方案
                if now_sec >= prepare_sec:
                    if (
                        override.frozen_plan_key != plan_key
                        or override.frozen_departure_time != departure_time
                        or override.frozen_reminder_text != reminder_text
                    ):
                        save_frozen_reminder(
                            db=db,
                            user_id=user.id,
                            target_date=today,
                            plan_key=plan_key,
                            frozen_departure_time=departure_time,
                            frozen_reminder_text=reminder_text,
                            prepared_at=now_dt,
                        )
                        override = get_or_create_override(db, user.id, today)
                        print(
                            f"[reminder-prepare] user_id={user.id} "
                            f"prepared_at={now_hhmmss} frozen_departure={departure_time}"
                        )

                # 到達或非常接近出門時間點就送
                if override.frozen_plan_key and override.frozen_departure_time and override.frozen_reminder_text:
                    frozen_sec = hhmm_to_seconds(override.frozen_departure_time)

                    print(
                        f"[reminder-check] user_id={user.id} "
                        f"now={now_hhmmss} departure={override.frozen_departure_time} "
                        f"last_sent_plan_key={override.last_sent_plan_key}"
                    )

                    if now_sec >= frozen_sec and override.last_sent_plan_key != override.frozen_plan_key:
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
                print(f"[reminder] failed for user_id={user.id}: {e}")
                print(traceback.format_exc())

    finally:
        db.close()


def start_reminder_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        check_and_send_departure_reminders,
        "interval",
        seconds=30,
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