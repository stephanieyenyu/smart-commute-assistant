import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.commute_schedule import commute_date_is_active
from app.db import SessionLocal
from app.line_client import push_text, push_with_quick_reply
from app.models import CommuteOverride, User
from app.crud import (
    get_all_profiles,
    get_next_setup_step,
    get_override_for_date,
    get_profile,
    mark_snooze_departure_sent,
    mark_snooze_one_min_sent,
    mark_nightly_brief_sent,
    mark_reminder_sent,
    mark_watchdog_alert_sent,
    clear_today_reminder_state_db,
)
from app.reminder_timing import (
    ReminderTimingDecision,
    evaluate_departure_reminder,
    hhmm_to_seconds,
    STALE_REMINDER_GRACE_SECONDS,
)
from app.service import build_today_commute_payload, freeze_today_reminder_payload

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_PREPARE_ATTEMPT_CACHE: dict[tuple[int, str], datetime] = {}
PREPARE_RETRY_SECONDS = 300
BUS_RECOMPUTE_SECONDS = 60
METRO_RECOMPUTE_SECONDS = 300
MORNING_WATCHDOG_LOOKAHEAD_HOURS = 8
WATCHDOG_DEPARTURE_WARNING_SECONDS = 15 * 60
SNOOZE_ONE_MINUTE_WARNING_SECONDS = 60

# Quick Reply buttons shown after departure reminder push
REMINDER_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",    "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",    "text": "今天搭捷運"},
    {"type": "message", "label": "📋 今日通勤建議", "text": "今天通勤建議"},
]

NIGHTLY_BRIEF_QUICK_REPLIES = [
    {"type": "datetimepicker", "label": "⏰ 修改明日時間", "data": "action=set_tomorrow_arrival_time", "mode": "time"},
    {"type": "message", "label": "📋 今日通勤建議", "text": "今天通勤建議"},
    {"type": "message", "label": "🚇 明天幾點出門", "text": "明天幾點出門"},
]

WATCHDOG_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車", "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運", "text": "今天搭捷運"},
    {"type": "message", "label": "📋 今日通勤建議", "text": "今天通勤建議"},
]


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def current_seconds_of_day() -> int:
    now = now_taipei()
    return now.hour * 3600 + now.minute * 60 + now.second


def _parse_hhmm_for_date(target_date, hhmm: str) -> datetime | None:
    try:
        parsed = datetime.strptime(hhmm, "%H:%M").time()
    except (TypeError, ValueError):
        return None
    return datetime.combine(target_date, parsed, tzinfo=TAIPEI_TZ)


def _effective_arrival_time_for_profile(db, profile, target_date) -> str | None:
    override = get_override_for_date(db, profile.user_id, target_date)
    if override and override.target_arrival_time:
        return override.target_arrival_time
    return profile.preferred_arrival_time


def _hash_notification_key(*parts) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _seconds_since(now_dt: datetime, previous_dt: datetime | None) -> float | None:
    if previous_dt is None:
        return None
    if previous_dt.tzinfo is None:
        previous_dt = previous_dt.replace(tzinfo=TAIPEI_TZ)
    return (now_dt - previous_dt).total_seconds()


def _as_taipei_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=TAIPEI_TZ)
    return value.astimezone(TAIPEI_TZ)


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
            if not commute_date_is_active(profile, today, override):
                continue
            if override and override.departure_confirmed_at:
                continue
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
                if not commute_date_is_active(profile, today, override):
                    continue
                if override.departure_confirmed_at:
                    continue

                already_sent = override.last_sent_plan_key == override.frozen_plan_key
                timing_decision = evaluate_departure_reminder(
                    now_sec,
                    override.frozen_departure_time,
                    already_sent=already_sent,
                )

                if timing_decision == ReminderTimingDecision.ALREADY_SENT:
                    print(
                        f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                        f"departure={override.frozen_departure_time} already_sent=True"
                    )
                    continue

                print(
                    f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                    f"departure={override.frozen_departure_time} last_sent_plan_key={override.last_sent_plan_key}"
                )

                if timing_decision == ReminderTimingDecision.SKIP_STALE:
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

                if timing_decision == ReminderTimingDecision.SEND:
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

        await check_and_send_snoozed_departure_reminders(db, today, now_dt)

    finally:
        db.close()


async def check_and_send_snoozed_departure_reminders(db, today, now_dt: datetime):
    overrides = db.query(CommuteOverride).filter(
        CommuteOverride.target_date == today,
        CommuteOverride.departure_snoozed_until.isnot(None),
        CommuteOverride.departure_confirmed_at.is_(None),
    ).all()

    for override in overrides:
        try:
            snoozed_until = _as_taipei_datetime(override.departure_snoozed_until)
            if snoozed_until is None:
                continue

            user = db.query(User).filter(User.id == override.user_id).first()
            if not user or not user.line_user_id:
                continue

            profile = user.profile or get_profile(db, user.id)
            if not profile.reminder_enabled:
                continue
            if not commute_date_is_active(profile, today, override):
                continue

            seconds_until = (snoozed_until - now_dt).total_seconds()
            if (
                seconds_until <= SNOOZE_ONE_MINUTE_WARNING_SECONDS
                and seconds_until > 0
                and not override.snooze_one_min_sent_at
            ):
                await push_text(
                    user.line_user_id,
                    "🔔 出門提醒：距離出門剩下一分鐘，請準備出門。",
                )
                mark_snooze_one_min_sent(db, user.id, today, now_dt)
                print(f"[snooze-reminder] one-minute user_id={user.id}")

            if seconds_until <= 0 and not override.snooze_departure_sent_at:
                await push_text(
                    user.line_user_id,
                    "🔔 已到出門時間，請準時出門。",
                )
                mark_snooze_departure_sent(db, user.id, today, now_dt)
                print(f"[snooze-reminder] leave-now user_id={user.id}")

        except Exception as e:
            import traceback
            print(f"[snooze-reminder] failed user_id={override.user_id} error={e}")
            print(traceback.format_exc())


async def send_nightly_briefs():
    db = SessionLocal()
    try:
        now_dt = now_taipei()
        tomorrow = now_dt.date() + timedelta(days=1)

        for profile in get_all_profiles(db):
            try:
                if not getattr(profile, "reminder_enabled", True):
                    continue
                if get_next_setup_step(profile) is not None:
                    continue
                override = get_override_for_date(db, profile.user_id, tomorrow)
                if not commute_date_is_active(profile, tomorrow, override):
                    continue

                user = db.query(User).filter(User.id == profile.user_id).first()
                if not user or not user.line_user_id:
                    continue

                payload = await build_today_commute_payload(
                    db=db,
                    user_id=user.id,
                    target_date=tomorrow,
                    force_mode_override=None,
                    header="🌙 明日通勤預報：",
                )
                if not payload.get("ok"):
                    continue

                plan_key = _hash_notification_key(
                    "nightly",
                    tomorrow.isoformat(),
                    payload.get("effective_arrival_time"),
                    payload.get("final_departure_time"),
                    payload.get("recommended_mode"),
                    payload.get("text"),
                )
                if override and override.nightly_brief_plan_key == plan_key:
                    continue

                text = (
                    f"{payload['text']}\n\n"
                    "可用下方按鈕修改明日到公司時間。"
                )
                await push_with_quick_reply(user.line_user_id, text, NIGHTLY_BRIEF_QUICK_REPLIES)
                mark_nightly_brief_sent(db, user.id, tomorrow, plan_key, now_dt)
                print(f"[nightly-brief] sent user_id={user.id} target_date={tomorrow.isoformat()}")
            except Exception as e:
                import traceback
                print(f"[nightly-brief] failed user_id={getattr(profile, 'user_id', None)} error={e}")
                print(traceback.format_exc())
    finally:
        db.close()


async def run_morning_watchdog():
    db = SessionLocal()
    try:
        now_dt = now_taipei()
        today = now_dt.date()

        for profile in get_all_profiles(db):
            try:
                if not getattr(profile, "reminder_enabled", True):
                    continue
                if get_next_setup_step(profile) is not None:
                    continue
                override = get_override_for_date(db, profile.user_id, today)
                if not commute_date_is_active(profile, today, override):
                    continue

                effective_arrival_time = _effective_arrival_time_for_profile(db, profile, today)
                arrival_dt = _parse_hhmm_for_date(today, effective_arrival_time)
                if arrival_dt is None:
                    continue
                if not (arrival_dt - timedelta(hours=MORNING_WATCHDOG_LOOKAHEAD_HOURS) <= now_dt <= arrival_dt):
                    continue

                user = db.query(User).filter(User.id == profile.user_id).first()
                if not user or not user.line_user_id:
                    continue

                payload = await build_today_commute_payload(
                    db=db,
                    user_id=user.id,
                    target_date=today,
                    force_mode_override=None,
                    header="⚠️ 早晨通勤監控：",
                )
                if not payload.get("ok"):
                    continue

                departure_sec = hhmm_to_seconds(payload["final_departure_time"])
                seconds_until_departure = departure_sec - current_seconds_of_day()
                weather_buffer = payload.get("weather_buffer") or 0
                recommended_mode = payload.get("recommended_mode")
                mode_override = payload.get("mode_override") or "auto"
                should_alert = (
                    weather_buffer > 0
                    or 0 <= seconds_until_departure <= WATCHDOG_DEPARTURE_WARNING_SECONDS
                    or (mode_override == "auto" and recommended_mode == "metro")
                )
                if not should_alert:
                    continue

                alert_key = _hash_notification_key(
                    "watchdog",
                    today.isoformat(),
                    payload.get("effective_arrival_time"),
                    payload.get("final_departure_time"),
                    recommended_mode,
                    weather_buffer,
                    payload.get("text"),
                )
                if override and override.watchdog_alert_key == alert_key:
                    continue

                try:
                    await freeze_today_reminder_payload(db, user.id, today, plan=payload)
                except Exception as e:
                    print(f"[watchdog] freeze skipped user_id={user.id} error={e}")

                text = (
                    f"{payload['text']}\n\n"
                    "系統已更新今天的出門提醒，會持續監控即時交通與天氣。"
                )
                await push_with_quick_reply(user.line_user_id, text, WATCHDOG_QUICK_REPLIES)
                mark_watchdog_alert_sent(db, user.id, today, alert_key, now_dt)
                print(f"[watchdog] sent user_id={user.id} target_date={today.isoformat()}")
            except Exception as e:
                import traceback
                print(f"[watchdog] failed user_id={getattr(profile, 'user_id', None)} error={e}")
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
    scheduler.add_job(
        send_nightly_briefs,
        "cron",
        hour=21,
        minute=0,
        id="nightly_brief_job",
        replace_existing=True,
    )
    scheduler.add_job(
        run_morning_watchdog,
        "interval",
        minutes=5,
        id="morning_watchdog_job",
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
