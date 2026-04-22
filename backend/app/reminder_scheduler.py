from datetime import date, datetime, timedelta
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
PREPARE_LEAD_MINUTES = 3


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def hhmm_to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


async def check_and_send_departure_reminders():
    db = SessionLocal()
    try:
        users = db.query(User).all()
        today = now_taipei().date()
        now_dt = now_taipei()
        now_hhmm = now_dt.strftime("%H:%M")
        now_min = hhmm_to_minutes(now_hhmm)

        for user in users:
            try:
                profile = get_profile(db, user.id)

                if not profile.reminder_enabled:
                    print(f"[reminder-check] user_id={user.id} skipped, reminder disabled")
                    continue

                payload = await build_today_reminder_payload(db, user.id, today)

                if not payload.get("ok"):
                    print(
                        f"[reminder-check] user_id={user.id} skipped, "
                        f"reason={payload.get('reason')}"
                    )
                    continue

                override = get_or_create_override(db, user.id, today)

                # 如果已送過，今天就跳過
                if override.reminder_sent_at is not None:
                    print(
                        f"[reminder-check] user_id={user.id} "
                        f"now={now_hhmm} departure={override.frozen_departure_time} already_sent=True"
                    )
                    continue

                # 還沒凍結，就檢查是否到「提前 3 分鐘」時機
                if not override.frozen_departure_time or not override.frozen_reminder_text:
                    departure_time = payload.get("departure_time")
                    if not departure_time:
                        print(f"[reminder-check] user_id={user.id} skipped, no departure_time")
                        continue

                    departure_min = hhmm_to_minutes(departure_time)
                    prepare_min = departure_min - PREPARE_LEAD_MINUTES

                    print(
                        f"[reminder-check] user_id={user.id} "
                        f"now={now_hhmm} departure={departure_time} "
                        f"prepare_min={prepare_min} prepared=False"
                    )

                    if now_min >= prepare_min:
                        save_frozen_reminder(
                            db=db,
                            user_id=user.id,
                            target_date=today,
                            frozen_departure_time=departure_time,
                            frozen_reminder_text=payload["text"],
                            prepared_at=now_dt,
                        )
                        override = get_or_create_override(db, user.id, today)
                        print(
                            f"[reminder-prepare] user_id={user.id} "
                            f"prepared_at={now_hhmm} frozen_departure={departure_time}"
                        )

                # 已凍結後，只用凍結結果，不再重算
                if override.frozen_departure_time and override.frozen_reminder_text:
                    frozen_departure = override.frozen_departure_time
                    frozen_min = hhmm_to_minutes(frozen_departure)

                    print(
                        f"[reminder-check] user_id={user.id} "
                        f"now={now_hhmm} departure={frozen_departure} "
                        f"already_sent=False"
                    )

                    if now_min >= frozen_min:
                        print(
                            f"[reminder] about to send | user_id={user.id} "
                            f"line_user_id={user.line_user_id} now={now_hhmm} "
                            f"departure={frozen_departure}"
                        )
                        await push_text(user.line_user_id, override.frozen_reminder_text)
                        mark_reminder_sent(db, user.id, today, now_dt)
                        print(f"[reminder] sent to user_id={user.id} at {now_hhmm}")

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
        "cron",
        second=0,
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