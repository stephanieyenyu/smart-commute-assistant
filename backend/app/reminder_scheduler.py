import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.line_client import (
    build_tts_audio_url,
    estimate_tts_duration_ms,
    push_audio_message,
    push_with_quick_reply,
)
from app.models import CommuteOverride, CommuteSchedule
from app.crud import (
    get_all_schedules_for_day,
    get_override_for_date,
    get_or_create_override,
    get_user_by_id,
    mark_departure_question_sent,
    mark_departed_for_today,
    mark_monitor_sent,
    clear_today_reminder_state_db,
    effective_commute_date_is_active,
    effective_commute_setting_for_date,
)
from app.departure_confirmation import (
    DEPARTURE_TIMEOUT_LINE_MESSAGE,
    DEPARTURE_TIMEOUT_LOOKAHEAD_HOURS,
    DEPARTURE_TIMEOUT_MINUTES,
    send_departure_check_for_user,
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
STALE_REMINDER_GRACE_SECONDS = 120
SCHEDULER_TICK_SECONDS = 30
EXACT_TRIGGER_WINDOW_SECONDS = 75
MORNING_MONITOR_OFFSETS = {
    "one_hour": 60 * 60,
    "five_min": 5 * 60,
}

MONITOR_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車", "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運", "text": "今天搭捷運"},
    {"type": "message", "label": "📋 今日通勤建議", "text": "今天通勤建議"},
]

DEPARTURE_CONFIRM_QR = [
    {"type": "message", "label": "✅ 已出門", "text": "已出門"},
]


def now_taipei() -> datetime:
    return datetime.now(TAIPEI_TZ)


def current_seconds_of_day() -> int:
    now = now_taipei()
    return now.hour * 3600 + now.minute * 60 + now.second


def _is_inside_trigger_window(now_sec: int, trigger_sec: int) -> bool:
    return trigger_sec <= now_sec < trigger_sec + EXACT_TRIGGER_WINDOW_SECONDS


def _is_departure_confirmation_window(now_sec: int, departure_sec: int) -> bool:
    return departure_sec <= now_sec <= departure_sec + STALE_REMINDER_GRACE_SECONDS


async def ensure_today_reminders_prepared(db, today, schedules_today: list[CommuteSchedule]):
    """只在缺少凍結提醒時準備內容；不做高頻 API 重算。"""
    now_dt = now_taipei()
    for schedule in schedules_today:
        user_id = schedule.user_id
        try:
            override = get_override_for_date(db, user_id, today, schedule_id=schedule.id)
            frozen_ready = (
                override
                and override.frozen_plan_key
                and override.frozen_departure_time
                and override.frozen_reminder_text
            )
            if frozen_ready:
                continue

            cache_key = (user_id, f"{today.isoformat()}:{schedule.id}")
            last_attempt = _PREPARE_ATTEMPT_CACHE.get(cache_key)
            if last_attempt and (now_dt - last_attempt).total_seconds() < PREPARE_RETRY_SECONDS:
                continue

            _PREPARE_ATTEMPT_CACHE[cache_key] = now_dt
            await freeze_today_reminder_payload(db, user_id, today, schedule_id=schedule.id)
            print(f"[reminder-prepare] prepared user_id={user_id} schedule_id={schedule.id} date={today.isoformat()}")
        except Exception as e:
            import traceback
            print(f"[reminder-prepare] failed user_id={user_id} error={e}")
            print(traceback.format_exc())


async def _refresh_frozen_plan(db, user_id: int, today, schedule_id: int):
    return await freeze_today_reminder_payload(
        db=db,
        user_id=user_id,
        target_date=today,
        schedule_id=schedule_id,
    )


async def _send_morning_monitor(
    db,
    *,
    user,
    override: CommuteOverride,
    schedule: CommuteSchedule,
    today,
    monitor_key: str,
    now_dt: datetime,
) -> bool:
    refreshed = await _refresh_frozen_plan(db, user.id, today, schedule.id)
    text = refreshed.get("text") if refreshed.get("ok") else override.frozen_reminder_text
    if not text:
        return False

    heading = "出門前一小時更新" if monitor_key == "one_hour" else "出門前五分鐘更新"
    await push_with_quick_reply(
        user.line_user_id,
        f"{heading}\n{text}",
        MONITOR_QUICK_REPLIES,
    )
    mark_monitor_sent(
        db=db,
        user_id=user.id,
        target_date=today,
        schedule_id=schedule.id,
        monitor_key=monitor_key,
        sent_at=now_dt,
    )
    print(f"[monitor] sent user_id={user.id} schedule_id={schedule.id} key={monitor_key}")
    return True


async def _send_departure_voice_notice(user, schedule: CommuteSchedule, departure_time: str) -> None:
    try:
        voice_text = (
            f"出門提醒。建議你現在出門。"
            f"{schedule.dest_name or '目的地'} 通勤請預留時間。"
        )
        await push_audio_message(
            user.line_user_id,
            build_tts_audio_url(voice_text),
            estimate_tts_duration_ms(voice_text),
        )
        print(f"[reminder-voice] sent user_id={user.id} schedule_id={schedule.id}")
    except Exception as voice_error:
        print(f"[reminder-voice] failed user_id={user.id} schedule_id={schedule.id} error={voice_error}")


async def _send_departure_question(
    db,
    *,
    user,
    override: CommuteOverride,
    schedule: CommuteSchedule,
    today,
    now_dt: datetime,
) -> bool:
    question_text = (
        f"您出門了嗎？\n"
        f"建議出門時間：{override.frozen_departure_time}\n"
        "若已出門，請點選「已出門」，今天這筆排程就會停止後續監控。"
    )

    text_sent = False
    try:
        await push_with_quick_reply(user.line_user_id, question_text, DEPARTURE_CONFIRM_QR)
        mark_departure_question_sent(
            db=db,
            user_id=user.id,
            target_date=today,
            schedule_id=schedule.id,
            sent_at=now_dt,
        )
        text_sent = True
        print(f"[departure-question] sent user_id={user.id} schedule_id={schedule.id}")
    except Exception as text_error:
        print(f"[departure-question] text failed user_id={user.id} schedule_id={schedule.id} error={text_error}")

    await _send_departure_voice_notice(user, schedule, override.frozen_departure_time)
    try:
        from app.dashboard_ws import trigger_voice_alert
        await trigger_voice_alert(
            line_user_id=user.line_user_id,
            reminder_text=override.frozen_reminder_text or question_text,
            departure_time=override.frozen_departure_time,
        )
    except Exception as ws_error:
        print(f"[departure-question] voice alert failed user_id={user.id} schedule_id={schedule.id} error={ws_error}")
    return text_sent


async def check_and_send_departure_reminders():
    """
    只允許三種時間觸發：
    1. 預估出門前 1 小時：刷新 API 後推播一次。
    2. 預估出門前 5 分鐘：刷新 API 後推播一次。
    3. 預估出門時間到：強制送 LINE「您出門了嗎？」；語音為獨立 best-effort。
    """
    db = SessionLocal()
    try:
        now_dt = now_taipei()
        today = now_dt.date()
        day_of_week = now_dt.weekday()  # 0=週一, 1=週二, ..., 6=週日
        now_hhmmss = now_dt.strftime("%H:%M:%S")
        now_sec = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second

        schedules_today = get_all_schedules_for_day(db, day_of_week)
        active_user_ids = sorted({s.user_id for s in schedules_today})
        active_schedule_ids = [s.id for s in schedules_today]

        await ensure_today_reminders_prepared(db, today, schedules_today)

        overrides = db.query(CommuteOverride).filter(
            CommuteOverride.target_date == today,
            CommuteOverride.user_id.in_(active_user_ids) if active_user_ids else False,
            CommuteOverride.schedule_id.in_(active_schedule_ids) if active_schedule_ids else False,
            CommuteOverride.frozen_plan_key.isnot(None),
            CommuteOverride.frozen_departure_time.isnot(None),
            CommuteOverride.frozen_reminder_text.isnot(None),
        ).all() if active_user_ids else []

        for override in overrides:
            try:
                if override.departed_at:
                    continue

                user = get_user_by_id(db, override.user_id)
                if not user:
                    continue
                if not effective_commute_date_is_active(db, profile, today, override):
                    continue
                if override.departure_confirmed_at:
                    continue

                schedule = db.query(CommuteSchedule).filter(
                    CommuteSchedule.id == override.schedule_id,
                    CommuteSchedule.user_id == user.id,
                ).first()
                if not schedule or not schedule.reminder_enabled:
                    continue

                print(
                    f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                    f"schedule_id={override.schedule_id} departure={override.frozen_departure_time}"
                )

                if now_sec > departure_sec + STALE_REMINDER_GRACE_SECONDS:
                    continue

                for monitor_key, offset_seconds in MORNING_MONITOR_OFFSETS.items():
                    already_sent = (
                        override.monitor_one_hour_sent_at
                        if monitor_key == "one_hour"
                        else override.monitor_five_min_sent_at
                    )
                    trigger_sec = max(0, departure_sec - offset_seconds)
                    if not already_sent and _is_inside_trigger_window(now_sec, trigger_sec):
                        await _send_morning_monitor(
                            db,
                            user=user,
                            override=override,
                            schedule=schedule,
                            today=today,
                            monitor_key=monitor_key,
                            now_dt=now_dt,
                        )
                        override = get_or_create_override(db, user.id, today, schedule_id=schedule.id)

                if (
                    not override.departure_question_sent_at
                    and _is_departure_confirmation_window(now_sec, departure_sec)
                ):
                    await _send_departure_question(
                        db,
                        user=user,
                        override=override,
                        schedule=schedule,
                        today=today,
                        now_dt=now_dt,
                    )

                    # Immediately trigger departure check ("您出門了嗎？") after sending reminder
                    # This is fully decoupled from voice - only depends on time
                    if not override.departure_check_sent_at:
                        try:
                            await send_departure_check_for_user(db, user.id, today, sent_at=now_dt)
                            print(f"[reminder] departure-check triggered user_id={user.id}")
                        except Exception as e:
                            print(f"[reminder] departure-check failed user_id={user.id} error={e}")

            except Exception as e:
                import traceback
                print(f"[reminder] failed user_id={override.user_id} error={e}")
                print(traceback.format_exc())

        await check_and_send_snoozed_departure_reminders(db, today, now_dt)
        await check_and_close_departure_timeouts(db, today, now_dt)

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
            if not effective_commute_date_is_active(db, profile, today, override):
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


async def _member_departure_datetime(db, member: User, today, now_dt: datetime) -> datetime | None:
    profile = member.profile or get_profile(db, member.id)
    if not getattr(profile, "reminder_enabled", True):
        return None
    if get_next_setup_step(profile) is not None:
        return None

    override = get_override_for_date(db, member.id, today)
    if override and (override.departure_confirmed_at or override.departure_timeout_at):
        return None
    if not effective_commute_date_is_active(db, profile, today, override):
        return None

    departure_hhmm = override.frozen_departure_time if override else None
    if not departure_hhmm:
        plan = await build_today_commute_payload(
            db=db,
            user_id=member.id,
            target_date=today,
            force_mode_override=None,
            header="今日通勤建議：",
            log_plan=False,
        )
        if not plan.get("ok"):
            return None
        departure_hhmm = plan.get("final_departure_time")

    departure_dt = _parse_hhmm_for_date(today, departure_hhmm)
    if departure_dt is None or departure_dt <= now_dt:
        return None
    return departure_dt


async def household_has_upcoming_departure_window(db, user: User, today, now_dt: datetime) -> bool:
    household_id = get_household_id_for_user(user)
    members = [
        member
        for member in get_users_for_household(db, household_id)
        if member.id != user.id
    ]
    if not members:
        return False

    for member in members:
        try:
            departure_dt = await _member_departure_datetime(db, member, today, now_dt)
            if departure_dt is None:
                continue
            window_start = departure_dt - timedelta(hours=DEPARTURE_TIMEOUT_LOOKAHEAD_HOURS)
            if window_start <= now_dt <= departure_dt:
                return True
        except Exception as e:
            print(f"[departure-timeout] household member check skipped member_id={member.id} error={e}")
    return False


async def check_and_close_departure_timeouts(db, today, now_dt: datetime):
    overrides = db.query(CommuteOverride).filter(
        CommuteOverride.target_date == today,
        CommuteOverride.departure_check_sent_at.isnot(None),
        CommuteOverride.departure_confirmed_at.is_(None),
        CommuteOverride.departure_timeout_at.is_(None),
    ).all()

    for override in overrides:
        try:
            sent_at = _as_taipei_datetime(override.departure_check_sent_at)
            if sent_at is None or (now_dt - sent_at).total_seconds() < DEPARTURE_TIMEOUT_SECONDS:
                continue

            user = db.query(User).filter(User.id == override.user_id).first()
            if not user:
                continue

            silent = await household_has_upcoming_departure_window(db, user, today, now_dt)
            if user.line_user_id:
                await push_text(user.line_user_id, DEPARTURE_TIMEOUT_LINE_MESSAGE)
            mark_departure_timeout(db, user.id, today, now_dt, silent=silent)
            mode_text = "silent" if silent else "normal"
            print(f"[departure-timeout] closed user_id={user.id} mode={mode_text}")
        except Exception as e:
            import traceback
            print(f"[departure-timeout] failed user_id={override.user_id} error={e}")
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
                if not effective_commute_date_is_active(db, profile, tomorrow, override):
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
                    "可用下方按鈕修改明日到達時間。"
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
    """Morning watchdog that sends a consolidated alert 15-20 minutes before departure.
    Includes: estimated travel time, weather summary, and suggested departure time.
    When MERGE_WATCHDOG_ALERTS is True, merges 1-hour and 5-minute alerts into one.
    """
    if not MERGE_WATCHDOG_ALERTS:
        # Fall back to original two-trigger logic
        await _run_morning_watchdog_original()
        return

    db = SessionLocal()
    try:
        now_dt = now_taipei()
        today = now_dt.date()
        now_sec = current_seconds_of_day()

        for profile in get_all_profiles(db):
            try:
                if not getattr(profile, "reminder_enabled", True):
                    continue
                if get_next_setup_step(profile) is not None:
                    continue
                override = get_override_for_date(db, profile.user_id, today)
                if not effective_commute_date_is_active(db, profile, today, override):
                    continue
                if override and override.departure_confirmed_at:
                    continue

                # Get departure time from frozen data or compute fresh
                departure_hhmm = override.frozen_departure_time if override else None
                if not departure_hhmm:
                    plan = await build_today_commute_payload(
                        db=db,
                        user_id=profile.user_id,
                        target_date=today,
                        force_mode_override=None,
                        header="⚠️ 早晨通勤監控：",
                    )
                    if not plan.get("ok"):
                        continue
                    departure_hhmm = plan.get("final_departure_time")
                    if not departure_hhmm:
                        continue

                departure_sec = hhmm_to_seconds(departure_hhmm)
                seconds_until = departure_sec - now_sec

                # Check if we are within the 15-20 minute window before departure
                in_merge_window = (MERGE_WATCHDOG_ALERT_SECONDS_BEFORE - MERGE_WATCHDOG_ALERT_WINDOW) <= seconds_until <= (MERGE_WATCHDOG_ALERT_SECONDS_BEFORE + MERGE_WATCHDOG_ALERT_WINDOW)

                if not in_merge_window:
                    continue

                user = db.query(User).filter(User.id == profile.user_id).first()
                if not user or not user.line_user_id:
                    continue

                # Re-fetch live API data for consolidated alert
                payload = await build_today_commute_payload(
                    db=db,
                    user_id=user.id,
                    target_date=today,
                    force_mode_override=None,
                    header="⚠️ 出發前提醒：",
                )
                if not payload.get("ok"):
                    continue

                # Build consolidated alert key
                alert_key = _hash_notification_key(
                    "watchdog_merged",
                    today.isoformat(),
                    payload.get("effective_arrival_time"),
                    payload.get("final_departure_time"),
                    payload.get("recommended_mode"),
                    payload.get("weather_buffer"),
                )
                if override and override.watchdog_alert_key == alert_key:
                    print(f"[watchdog-merged] duplicate skipped user_id={user.id}")
                    continue

                try:
                    await freeze_today_reminder_payload(db, user.id, today, plan=payload)
                except Exception as e:
                    print(f"[watchdog-merged] freeze skipped user_id={user.id} error={e}")

                # Build friendly, concise push message
                weather_info = payload.get("weather_info") or {}
                weather_text = ""
                if weather_info.get("weather_text"):
                    weather_text = f"天氣：{weather_info['weather_text']}"
                    if weather_info.get("pop"):
                        weather_text += f"，降雨機率 {weather_info['pop']}%"

                travel_min = payload.get("baseline_minutes", "未知")
                departure_display = payload.get("final_departure_time", "未知")

                text = (
                    f"🚌 出發提醒（{departure_display} 出門）\n"
                    f"預計車程：約 {travel_min} 分鐘\n"
                    f"{weather_text}\n"
                    f"建議 {departure_display} 出門，祝您一路順風！"
                )
                await push_with_quick_reply(user.line_user_id, text, WATCHDOG_QUICK_REPLIES)
                mark_watchdog_alert_sent(db, user.id, today, alert_key, now_dt)
                print(f"[watchdog-merged] sent user_id={user.id} departure={departure_hhmm}")
            except Exception as e:
                import traceback
                print(f"[watchdog-merged] failed user_id={getattr(profile, 'user_id', None)} error={e}")
                print(traceback.format_exc())
    finally:
        db.close()


async def _run_morning_watchdog_original():
    """Original two-trigger watchdog (1 hour and 5 minutes before)."""
    db = SessionLocal()
    try:
        now_dt = now_taipei()
        today = now_dt.date()
        now_sec = current_seconds_of_day()

        for profile in get_all_profiles(db):
            try:
                if not getattr(profile, "reminder_enabled", True):
                    continue
                if get_next_setup_step(profile) is not None:
                    continue
                override = get_override_for_date(db, profile.user_id, today)
                if not effective_commute_date_is_active(db, profile, today, override):
                    continue
                if override and override.departure_confirmed_at:
                    continue

                departure_hhmm = override.frozen_departure_time if override else None
                if not departure_hhmm:
                    plan = await build_today_commute_payload(
                        db=db,
                        user_id=profile.user_id,
                        target_date=today,
                        force_mode_override=None,
                        header="⚠️ 早晨通勤監控：",
                    )
                    if not plan.get("ok"):
                        continue
                    departure_hhmm = plan.get("final_departure_time")
                    if not departure_hhmm:
                        continue

                departure_sec = hhmm_to_seconds(departure_hhmm)
                seconds_until = departure_sec - now_sec

                one_hour_trigger = abs(seconds_until - WATCHDOG_ONE_HOUR_BEFORE_SECONDS) <= WATCHDOG_TRIGGER_TOLERANCE_SECONDS
                five_min_trigger = abs(seconds_until - WATCHDOG_FIVE_MIN_BEFORE_SECONDS) <= WATCHDOG_TRIGGER_TOLERANCE_SECONDS

                if not one_hour_trigger and not five_min_trigger:
                    continue

                trigger_label = "一小時前" if one_hour_trigger else "五分鐘前"
                alert_key_prefix = "watchdog_1h" if one_hour_trigger else "watchdog_5m"

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

                alert_key = _hash_notification_key(
                    alert_key_prefix,
                    today.isoformat(),
                    payload.get("effective_arrival_time"),
                    payload.get("final_departure_time"),
                    payload.get("recommended_mode"),
                    payload.get("weather_buffer"),
                )
                if override and override.watchdog_alert_key == alert_key:
                    continue

                try:
                    await freeze_today_reminder_payload(db, user.id, today, plan=payload)
                except Exception as e:
                    print(f"[watchdog] freeze skipped user_id={user.id} error={e}")

                text = (
                    f"{payload['text']}\n\n"
                    f"🔔 這是出發前{trigger_label}的即時更新，系統會持續監控交通與天氣狀況。"
                )
                await push_with_quick_reply(user.line_user_id, text, WATCHDOG_QUICK_REPLIES)
                mark_watchdog_alert_sent(db, user.id, today, alert_key, now_dt)
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
        seconds=SCHEDULER_TICK_SECONDS,
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
        minutes=1,
        id="morning_watchdog_job",
        replace_existing=True,
    )
    scheduler.start()
    print("[reminder] scheduler started (Asia/Taipei)")


def clear_today_reminder_state_for_user(user_id: int):
    db = SessionLocal()
    try:
        today = now_taipei().date()
        clear_today_reminder_state_db(db, user_id, today)
        print(f"[reminder-reset] cleared cache for user_id={user_id} date={today.isoformat()}")
    finally:
        db.close()


def mark_user_departed_for_today(user_id: int, schedule_id: int | None = None):
    db = SessionLocal()
    try:
        today = now_taipei().date()
        departed = mark_departed_for_today(
            db=db,
            user_id=user_id,
            target_date=today,
            schedule_id=schedule_id,
            departed_at=now_taipei(),
        )
        print(f"[departed] user_id={user_id} schedules={len(departed)}")
        return departed
    finally:
        db.close()
