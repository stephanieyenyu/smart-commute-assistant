from datetime import datetime
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
)
from app.service import freeze_today_reminder_payload


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


def hhmm_to_seconds(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


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
    return text_sent


async def check_and_send_departure_reminders():
    """
<<<<<<< HEAD
    統一排程系統：
    1. 讀取今天需要提醒的 CommuteSchedule（依 days 過濾）
    2. 依排程時間（schedule.time）計算提醒時間
    3. 時間到則：
       a. 發送 LINE push 提醒（文字 + Quick Reply）
       b. 設定 alert_status = "pending" 並透過 WebSocket 觸發語音提醒
    （台灣時間 Asia/Taipei）
=======
    只允許三種時間觸發：
    1. 預估出門前 1 小時：刷新 API 後推播一次。
    2. 預估出門前 5 分鐘：刷新 API 後推播一次。
    3. 預估出門時間到：強制送 LINE「您出門了嗎？」；語音為獨立 best-effort。
>>>>>>> 93967a0d3e3c86434f19cc2278ebe4a4fad71eb5
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

                schedule = db.query(CommuteSchedule).filter(
                    CommuteSchedule.id == override.schedule_id,
                    CommuteSchedule.user_id == user.id,
                ).first()
                if not schedule or not schedule.reminder_enabled:
                    continue

                departure_sec = hhmm_to_seconds(override.frozen_departure_time)
                print(
                    f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                    f"schedule_id={override.schedule_id} departure={override.frozen_departure_time}"
                )

                if now_sec > departure_sec + STALE_REMINDER_GRACE_SECONDS:
                    continue

<<<<<<< HEAD
                # 時間到：發送提醒
                if now_sec >= departure_sec:
                    # 1) 發送 LINE push 提醒（文字 + Quick Reply）
                    await push_with_quick_reply(
                        user.line_user_id,
                        override.frozen_reminder_text,
                        REMINDER_QUICK_REPLIES,
                    )

                    # 2) 觸發 Web Dashboard 語音提醒（WebSocket + alert_status）
                    try:
                        from app.dashboard_ws import trigger_voice_alert
                        await trigger_voice_alert(
                            line_user_id=user.line_user_id,
                            reminder_text=override.frozen_reminder_text,
                            departure_time=override.frozen_departure_time,
                        )
                    except Exception as ws_err:
                        print(f"[reminder] voice_alert error user_id={user.id}: {ws_err}")

                    mark_reminder_sent(db=db, user_id=user.id, target_date=today,
                                       plan_key=override.frozen_plan_key, sent_at=now_dt)
                    print(f"[reminder] sent user_id={user.id} at {now_hhmmss}")
=======
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
>>>>>>> 93967a0d3e3c86434f19cc2278ebe4a4fad71eb5

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
        seconds=SCHEDULER_TICK_SECONDS,
        id="departure_reminder_job",
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
