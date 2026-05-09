from datetime import datetime, timedelta


WEEKDAY_LABELS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")
STATUS_HEX = {
    "green": "#22c55e",
    "blue": "#3b82f6",
    "orange": "#f97316",
    "red": "#ef4444",
}
DEFAULT_DASHBOARD_COMMUTE_MINUTES = 60


def normalize_days(days) -> list[int]:
    normalized = []
    for day in days or []:
        try:
            value = int(day)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 6 and value not in normalized:
            normalized.append(value)
    return sorted(normalized)


def coerce_schedules(schedules) -> list:
    if schedules is None:
        return []
    if isinstance(schedules, (list, tuple)):
        return [schedule for schedule in schedules if schedule is not None]
    return [schedules]


def primary_schedule(schedules):
    schedule_list = coerce_schedules(schedules)
    active = [s for s in schedule_list if getattr(s, "is_active", True)]
    enabled = [s for s in active if getattr(s, "reminder_enabled", True)]
    return (enabled or active or schedule_list or [None])[0]


def format_weekdays(days) -> str:
    normalized = normalize_days(days)
    if not normalized:
        return "尚未設定"
    return "、".join(WEEKDAY_LABELS[day] for day in normalized)


def schedule_destination(schedule, profile=None) -> str:
    if schedule:
        return (
            getattr(schedule, "dest_name", None)
            or getattr(schedule, "dest_address", None)
            or "目的地"
        )
    return (
        getattr(profile, "office_place_name", None)
        or getattr(profile, "office_address", None)
        or "目的地"
    )


def schedule_origin(schedule, profile=None) -> str:
    if schedule:
        return (
            getattr(schedule, "origin_name", None)
            or getattr(schedule, "origin_address", None)
            or "尚未設定"
        )
    return (
        getattr(profile, "home_place_name", None)
        or getattr(profile, "home_address", None)
        or "尚未設定"
    )


def schedule_arrival_time(schedule, profile=None) -> str:
    if schedule and getattr(schedule, "time", None):
        return schedule.time
    return getattr(profile, "preferred_arrival_time", None) or "尚未設定"


def schedule_reminder_enabled(schedule, profile=None) -> bool:
    if schedule is not None and hasattr(schedule, "reminder_enabled"):
        return bool(schedule.reminder_enabled)
    return bool(getattr(profile, "reminder_enabled", True))


def schedule_to_summary(schedule, profile=None) -> dict:
    return {
        "scheduleId": getattr(schedule, "id", None),
        "origin": schedule_origin(schedule, profile),
        "destination": schedule_destination(schedule, profile),
        "arrivalTime": schedule_arrival_time(schedule, profile),
        "weekdays": normalize_days(getattr(schedule, "days", None)),
        "weekdayText": format_weekdays(getattr(schedule, "days", None)),
        "reminderEnabled": schedule_reminder_enabled(schedule, profile),
    }


def _hhmm_to_minutes(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    try:
        hour, minute = [int(part) for part in str(hhmm).split(":")[:2]]
        return hour * 60 + minute
    except Exception:
        return None


def active_schedules_for_date(schedules, target_date, require_time: bool = True) -> list:
    target_weekday = target_date.weekday()
    matched = []
    for schedule in coerce_schedules(schedules):
        if not getattr(schedule, "is_active", True):
            continue
        if target_weekday not in normalize_days(getattr(schedule, "days", None)):
            continue
        if require_time and not getattr(schedule, "time", None):
            continue
        matched.append(schedule)
    return sorted(matched, key=lambda schedule: schedule_arrival_time(schedule))


def dashboard_target_schedule(schedules, now_dt: datetime | None = None):
    now_dt = now_dt or datetime.now()
    now_minutes = now_dt.hour * 60 + now_dt.minute
    today_candidates = []
    for schedule in active_schedules_for_date(schedules, now_dt.date()):
        arrival_minutes = _hhmm_to_minutes(getattr(schedule, "time", None))
        if arrival_minutes is None or arrival_minutes >= now_minutes:
            today_candidates.append(schedule)
    if today_candidates:
        return now_dt.date(), sorted(today_candidates, key=lambda schedule: schedule_arrival_time(schedule))[0]

    tomorrow = (now_dt + timedelta(days=1)).date()
    tomorrow_candidates = active_schedules_for_date(schedules, tomorrow)
    if tomorrow_candidates:
        return tomorrow, tomorrow_candidates[0]
    return None, None


def dashboard_display_schedule_rows(schedules, now_dt: datetime | None = None) -> tuple[str, list[dict]]:
    now_dt = now_dt or datetime.now()
    target_date, _ = dashboard_target_schedule(schedules, now_dt=now_dt)
    if target_date == now_dt.date():
        selected = active_schedules_for_date(schedules, target_date)
        now_minutes = now_dt.hour * 60 + now_dt.minute
        selected = [
            schedule for schedule in selected
            if (_hhmm_to_minutes(getattr(schedule, "time", None)) or now_minutes) >= now_minutes
        ]
        title = "今日排程"
    elif target_date:
        selected = active_schedules_for_date(schedules, target_date)
        title = "明日排程"
    else:
        selected = []
        title = "明日排程"

    rows = []
    for schedule in selected:
        rows.append({
            "scheduleId": getattr(schedule, "id", None),
            "origin": schedule_origin(schedule),
            "destination": schedule_destination(schedule),
            "arrivalTime": schedule_arrival_time(schedule),
            "weekdayText": format_weekdays(getattr(schedule, "days", None)),
        })
    return title, rows


def weekly_schedule_rows(schedules) -> list[dict]:
    schedule_list = coerce_schedules(schedules)
    rows = []
    for index, label in enumerate(WEEKDAY_LABELS):
        entries = []
        for schedule in schedule_list:
            if not getattr(schedule, "is_active", True):
                continue
            if index not in normalize_days(getattr(schedule, "days", None)):
                continue
            entries.append(f"{schedule_arrival_time(schedule)} 到{schedule_destination(schedule)}")
        rows.append({
            "index": index,
            "label": label,
            "active": bool(entries),
            "text": "；".join(entries) if entries else "休息 / 未啟用",
        })
    return rows


def format_weekly_schedule_text(schedules) -> str:
    schedule_list = coerce_schedules(schedules)
    if not schedule_list:
        return "一週排程設定：\n尚未設定通勤排程。請先使用「新增排程設定」。"
    lines = ["一週排程設定："]
    for row in weekly_schedule_rows(schedule_list):
        lines.append(f"{row['label']}：{row['text']}")
    enabled_count = sum(1 for schedule in schedule_list if schedule_reminder_enabled(schedule))
    lines.append(f"\n排程總數：{len(schedule_list)} 筆")
    lines.append(f"自動提醒：{enabled_count}/{len(schedule_list)} 筆開啟")
    return "\n".join(lines)


def build_schedule_status_payload(
    schedules,
    profile=None,
    today_mode_label: str = "自動判斷",
    now_dt: datetime | None = None,
) -> dict:
    schedule_list = coerce_schedules(schedules)
    main_schedule = primary_schedule(schedule_list)
    has_schedule = main_schedule is not None
    payload = {
        "hasSchedule": has_schedule,
        "origin": schedule_origin(main_schedule, profile),
        "destination": schedule_destination(main_schedule, profile),
        "arrivalTime": schedule_arrival_time(main_schedule, profile),
        "weekdays": normalize_days(getattr(main_schedule, "days", None)) if main_schedule else [],
        "weekdayText": format_weekdays(getattr(main_schedule, "days", None) if main_schedule else None),
        "reminderEnabled": any(schedule_reminder_enabled(schedule, profile) for schedule in schedule_list) if schedule_list else schedule_reminder_enabled(None, profile),
        "todayTransportMode": today_mode_label,
        "weeklySchedule": weekly_schedule_rows(schedule_list) if schedule_list else [],
        "schedules": [schedule_to_summary(schedule, profile) for schedule in schedule_list],
        "scheduleCount": len(schedule_list),
        "updatedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    display_title, display_schedules = dashboard_display_schedule_rows(schedule_list, now_dt=now_dt)
    payload["displayScheduleTitle"] = display_title
    payload["displaySchedules"] = display_schedules
    payload["lineText"] = format_commute_setting_text(
        schedule_list,
        profile=profile,
        today_mode_label=today_mode_label,
    )
    payload["weeklyText"] = format_weekly_schedule_text(schedule_list)
    return payload


def format_commute_setting_text(schedules, profile=None, today_mode_label: str = "自動判斷") -> str:
    schedule_list = coerce_schedules(schedules)
    main_schedule = primary_schedule(schedule_list)
    if not schedule_list:
        return (
            "📋 目前通勤設定：\n"
            f"🏠 出發地：{schedule_origin(None, profile)}\n"
            "🏢 目的地：尚未設定\n"
            "⏰ 到達時間：尚未設定\n"
            "📅 提醒星期：尚未設定\n"
            "📢 自動提醒：尚未設定\n"
            f"🚇 今天交通：{today_mode_label}"
        )

    lines = [
        "📋 目前通勤設定：",
        f"🏠 主要出發地：{schedule_origin(main_schedule, profile)}",
        f"📌 排程總數：{len(schedule_list)} 筆",
    ]
    for index, schedule in enumerate(schedule_list, start=1):
        lines.append(
            f"{index}. {schedule_arrival_time(schedule, profile)} 到{schedule_destination(schedule, profile)}"
            f"（{format_weekdays(getattr(schedule, 'days', None))}，"
            f"{'提醒開啟' if schedule_reminder_enabled(schedule, profile) else '提醒關閉'}）"
        )
    lines.append(f"🚇 今天交通：{today_mode_label}")
    return "\n".join(lines)


def _combine_today_hhmm(now_dt: datetime, hhmm: str) -> datetime | None:
    try:
        hour, minute = [int(part) for part in hhmm.split(":")[:2]]
        return now_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def _status_for_today_schedule(schedule, now_dt: datetime) -> dict:
    arrival_dt = _combine_today_hhmm(now_dt, getattr(schedule, "time", "") or "")
    if arrival_dt is None:
        return {
            "statusColor": "green",
            "statusHex": STATUS_HEX["green"],
            "statusLabel": "休息中",
            "statusReason": "尚未設定今日到達時間",
            "minutesToDeparture": None,
            "suggestedDepartureTime": None,
        }

    departure_dt = arrival_dt - timedelta(minutes=DEFAULT_DASHBOARD_COMMUTE_MINUTES)
    minutes_to_departure = round((departure_dt - now_dt).total_seconds() / 60)
    if minutes_to_departure > 60:
        color = "green"
        label = "休息中"
    elif 30 <= minutes_to_departure <= 60:
        color = "blue"
        label = "時間充足"
    elif minutes_to_departure <= 0:
        color = "red"
        label = "已到出門時間"
    elif minutes_to_departure < 15:
        color = "orange"
        label = "準備出門"
    else:
        color = "orange"
        label = "準備中"

    return {
        "statusColor": color,
        "statusHex": STATUS_HEX[color],
        "statusLabel": label,
        "statusReason": f"下一筆：{schedule_arrival_time(schedule)} 到{schedule_destination(schedule)}",
        "minutesToDeparture": minutes_to_departure,
        "suggestedDepartureTime": departure_dt.strftime("%H:%M"),
    }


def build_member_status_payload(
    user,
    schedules,
    profile=None,
    today_mode_label: str = "自動判斷",
    now_dt: datetime | None = None,
) -> dict:
    now_dt = now_dt or datetime.now()
    schedule_list = [
        schedule for schedule in coerce_schedules(schedules)
        if getattr(schedule, "is_active", True)
        and now_dt.weekday() in normalize_days(getattr(schedule, "days", None))
    ]
    schedule_list.sort(key=lambda schedule: schedule_arrival_time(schedule, profile))

    if schedule_list:
        status = _status_for_today_schedule(schedule_list[0], now_dt)
    else:
        status = {
            "statusColor": "green",
            "statusHex": STATUS_HEX["green"],
            "statusLabel": "休息中",
            "statusReason": "今天沒有啟用的排程",
            "minutesToDeparture": None,
            "suggestedDepartureTime": None,
        }

    display_name = getattr(user, "display_name", None) or f"家人 {getattr(user, 'id', '')}".strip()
    return {
        "userId": getattr(user, "id", None),
        "displayName": display_name,
        "lineUserId": getattr(user, "line_user_id", None),
        **status,
        "schedule": build_schedule_status_payload(coerce_schedules(schedules), profile, today_mode_label, now_dt=now_dt),
    }
