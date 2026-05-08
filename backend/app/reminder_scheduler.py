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
    mark_reminder_sent,
    clear_today_reminder_state_db,
    get_user_by_id,
)
from app.service import freeze_today_reminder_payload

scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
_PREPARE_ATTEMPT_CACHE: dict[tuple[int, str], datetime] = {}
PREPARE_RETRY_SECONDS = 300
BUS_RECOMPUTE_SECONDS = 60
METRO_RECOMPUTE_SECONDS = 300
STALE_REMINDER_GRACE_SECONDS = 120
REMINDER_LEAD_SECONDS = 15 * 60

REMINDER_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",   "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",   "text": "今天搭捷運"},
    {"type": "message", "label": "📋 今日通勤建議",  "text": "今天通勤建議"},
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


async def ensure_today_reminders_prepared(db, today, schedules_today: list[CommuteSchedule]):
    """確保今天需要提醒的用戶的提醒內容已準備好。"""
    now_dt = now_taipei()
    for schedule in schedules_today:
        user_id = schedule.user_id
        try:
            override = get_override_for_date(db, user_id, today, schedule_id=schedule.id)
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


async def check_and_send_departure_reminders():
    """
    統一排程系統：
    1. 讀取今天需要提醒的 CommuteSchedule（依 days 過濾）
    2. 依排程時間（schedule.time）計算提醒時間
    3. 時間到則發送出發提醒推播（台灣時間 Asia/Taipei）
    """
    db = SessionLocal()
    try:
        now_dt   = now_taipei()
        today    = now_dt.date()
        # 前端 weekdays 慣例：0=週一, 1=週二, ..., 5=週六, 6=週日
        # Python weekday() 慣例：0=週一, 1=週二, ..., 5=週六, 6=週日
        # → 兩者一致，直接使用 weekday() 即可，不需要轉換
        day_of_week = now_dt.weekday()  # 0=Mon, 1=Tue, ..., 6=Sun

        now_hhmmss = now_dt.strftime("%H:%M:%S")
        now_sec    = now_dt.hour * 3600 + now_dt.minute * 60 + now_dt.second

        # 取得今天應提醒的排程
        schedules_today = get_all_schedules_for_day(db, day_of_week)
        active_user_ids = sorted({s.user_id for s in schedules_today})
        active_schedule_ids = [s.id for s in schedules_today]

        # 確保提醒內容已準備
        await ensure_today_reminders_prepared(db, today, schedules_today)

        # 查詢今天已準備好的 override 記錄
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
                user = get_user_by_id(db, override.user_id)
                if not user:
                    continue

                schedule = db.query(CommuteSchedule).filter(
                    CommuteSchedule.id == override.schedule_id,
                    CommuteSchedule.user_id == user.id,
                ).first()
                if not schedule or not schedule.reminder_enabled:
                    continue

                if override.last_sent_plan_key == override.frozen_plan_key:
                    continue

                departure_sec = hhmm_to_seconds(override.frozen_departure_time)
                trigger_sec = max(0, departure_sec - REMINDER_LEAD_SECONDS)
                print(
                    f"[reminder-check] user_id={user.id} now={now_hhmmss} "
                    f"schedule_id={override.schedule_id} "
                    f"trigger={trigger_sec} departure={override.frozen_departure_time}"
                )

                # 過期：跳過並標記已發送
                if now_sec > departure_sec + STALE_REMINDER_GRACE_SECONDS:
                    mark_reminder_sent(db=db, user_id=user.id, target_date=today,
                                       plan_key=override.frozen_plan_key, sent_at=now_dt,
                                       schedule_id=override.schedule_id)
                    print(f"[reminder] skipped stale user_id={user.id}")
                    continue

                # 建議出門前 15 分鐘：發送文字推播與語音提醒
                if now_sec >= trigger_sec:
                    await push_with_quick_reply(
                        user.line_user_id,
                        override.frozen_reminder_text,
                        REMINDER_QUICK_REPLIES,
                    )
                    try:
                        voice_text = (
                            f"出門提醒。建議你在 {override.frozen_departure_time} 出門。"
                            f"{schedule.dest_name or '目的地'} 通勤請預留時間。"
                        )
                        await push_audio_message(
                            user.line_user_id,
                            build_tts_audio_url(voice_text),
                            estimate_tts_duration_ms(voice_text),
                        )
                    except Exception as voice_error:
                        print(f"[reminder-voice] failed user_id={user.id} error={voice_error}")
                    mark_reminder_sent(db=db, user_id=user.id, target_date=today,
                                       plan_key=override.frozen_plan_key, sent_at=now_dt,
                                       schedule_id=override.schedule_id)
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
    print("[reminder] scheduler started (Asia/Taipei)")


def clear_today_reminder_state_for_user(user_id: int):
    db = SessionLocal()
    try:
        today = now_taipei().date()
        clear_today_reminder_state_db(db, user_id, today)
        print(f"[reminder-reset] cleared cache for user_id={user_id} date={today.isoformat()}")
    finally:
        db.close()
