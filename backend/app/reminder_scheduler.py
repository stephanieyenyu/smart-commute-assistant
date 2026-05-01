from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.line_client import push_text, push_with_quick_reply
from app.models import CommuteOverride, User
from app.crud import (
    get_all_profiles,
    get_next_setup_step,
    get_override_for_date,
    get_profile,
    mark_reminder_sent,
    clear_today_reminder_state_db,
)
from app.service import freeze_today_reminder_payload

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_PREPARE_ATTEMPT_CACHE: dict[tuple[int, str], datetime] = {}
PREPARE_RETRY_SECONDS = 300
BUS_RECOMPUTE_SECONDS = 60
METRO_RECOMPUTE_SECONDS = 300
STALE_REMINDER_GRACE_SECONDS = 120

# Quick Reply buttons shown after departure reminder push
REMINDER_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",    "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",    "text": "今天搭捷運"},
    {"type": "message", "label": "📋 今日通勤建議", "text": "今天通勤建議"},
]


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def hhmm_to_seconds(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def current_seconds_of_day() -> int:
    now = now_taipei()
    return now.hour * 3600 + now.minute * 60 + now.second


def _seconds_since(now_dt: datetime, previous_dt: datetime | None) -> float | None:
    if previous_dt is None:
        return None
    if previous_dt.tzinfo is None:
        previous_dt = previous_dt.replace(tzinfo=TAIPEI_TZ)
    return (now_dt - previous_dt).total_seconds()


def _refresh_interval_for_mode(mode: str | None) -> int | None:
    if mode == "bus":
        return BUS_RECOMPUTE_SECONDS
    if mode == "metro":
        return METRO_RECOMPUTE_SECONDS
    return None


async def ensure_today_reminders_prepared(db, today):
    now_dt = now_taipei()
    for profile in get_all_profiles(db):
        try:
            if not getattr(profile, "reminder_enabled", True):
                continue
            if get_next_setup_step(profile) is not None:
                continue

            override = get_override_for_date(db, profile.user_id, today)
            refresh_interval = _refresh_interval_for_mode(
                override.transport_mode_override if override else None
            )
            prepared_age = _seconds_since(
                now_dt,
                override.reminder_prepared_at if override else None,
            )
            frozen_ready = (
                override
                and override.frozen_plan_key
                and override.frozen_departure_time
                and override.frozen_reminder_text
            )
            already_sent = bool(
                override
                and override.last_sent_plan_key
                and override.last_sent_plan_key == override.frozen_plan_key
            )
            should_refresh_dynamic = (
                frozen_ready
                and refresh_interval is not None
                and not already_sent
                and (prepared_age is None or prepared_age >= refresh_interval)
            )

            if frozen_ready and not should_refresh_dynamic:
                continue

            cache_key = (profile.user_id, today.isoformat())
            last_attempt = _PREPARE_ATTEMPT_CACHE.get(cache_key)
            if last_attempt and (now_dt - last_attempt).total_seconds() < PREPARE_RETRY_SECONDS:
                continue

            _PREPARE_ATTEMPT_CACHE[cache_key] = now_dt
            await freeze_today_reminder_payload(db, profile.user_id, today)
            print(f"[reminder-prepare] prepared user_id={profile.user_id} date={today.isoformat()}")
        except Exception as e:
            import traceback
            print(f"[reminder-prepare] failed user_id={getattr(profile, 'user_id', None)} error={e}")
            print(traceback.format_exc())


async def check_and_send_departure_reminders():
    db = SessionLocal()
    try:
        today = now_taipei().date()
        now_dt = now_taipei()
        now_hhmmss = now_dt.strftime("%H:%M:%S")
        now_sec = current_seconds_of_day()

        await ensure_today_reminders_prepared(db, today)

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

                if now_sec > departure_sec + STALE_REMINDER_GRACE_SECONDS:
                    mark_reminder_sent(
                        db=db,
                        user_id=user.id,
                        target_date=today,
                        plan_key=override.frozen_plan_key,
                        sent_at=now_dt,
                    )
                    print(
                        f"[reminder] skipped stale user_id={user.id} now={now_hhmmss} "
                        f"departure={override.frozen_departure_time}"
                    )
                    continue

                if now_sec >= departure_sec:
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
