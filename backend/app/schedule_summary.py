from datetime import datetime


WEEKDAY_LABELS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")


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


def weekly_schedule_rows(schedule) -> list[dict]:
    active_days = set(normalize_days(getattr(schedule, "days", None)))
    arrival_time = schedule_arrival_time(schedule)
    destination = schedule_destination(schedule)
    rows = []
    for index, label in enumerate(WEEKDAY_LABELS):
        active = index in active_days
        rows.append({
            "index": index,
            "label": label,
            "active": active,
            "text": f"{arrival_time} 到{destination}" if active else "休息 / 未啟用",
        })
    return rows


def format_weekly_schedule_text(schedule) -> str:
    if not schedule:
        return "一週排程設定：\n尚未設定通勤排程。請先使用「新增排程設定」。"
    lines = ["一週排程設定："]
    for row in weekly_schedule_rows(schedule):
        lines.append(f"{row['label']}：{row['text']}")
    lines.append(f"\n自動提醒：{'開啟' if schedule_reminder_enabled(schedule) else '關閉'}")
    return "\n".join(lines)


def build_schedule_status_payload(schedule, profile=None, today_mode_label: str = "自動判斷") -> dict:
    has_schedule = schedule is not None
    payload = {
        "hasSchedule": has_schedule,
        "origin": schedule_origin(schedule, profile),
        "destination": schedule_destination(schedule, profile),
        "arrivalTime": schedule_arrival_time(schedule, profile),
        "weekdays": normalize_days(getattr(schedule, "days", None)) if schedule else [],
        "weekdayText": format_weekdays(getattr(schedule, "days", None) if schedule else None),
        "reminderEnabled": schedule_reminder_enabled(schedule, profile),
        "todayTransportMode": today_mode_label,
        "weeklySchedule": weekly_schedule_rows(schedule) if schedule else [],
        "updatedAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    payload["lineText"] = format_commute_setting_text(
        schedule,
        profile=profile,
        today_mode_label=today_mode_label,
    )
    payload["weeklyText"] = format_weekly_schedule_text(schedule)
    return payload


def format_commute_setting_text(schedule, profile=None, today_mode_label: str = "自動判斷") -> str:
    return (
        "📋 目前通勤設定：\n"
        f"🏠 出發地：{schedule_origin(schedule, profile)}\n"
        f"🏢 目的地：{schedule_destination(schedule, profile)}\n"
        f"⏰ 到達時間：{schedule_arrival_time(schedule, profile)}\n"
        f"📅 提醒星期：{format_weekdays(getattr(schedule, 'days', None) if schedule else None)}\n"
        f"📢 自動提醒：{'開啟' if schedule_reminder_enabled(schedule, profile) else '關閉'}\n"
        f"🚇 今天交通：{today_mode_label}"
    )
