import re
from datetime import date, datetime, timedelta


ALL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]
WORKDAYS = [0, 1, 2, 3, 4]
WEEKEND = [5, 6]
SLEEP_BEFORE_DEPARTURE_HOURS = 8
WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]
WEEKDAY_TOKEN_MAP = {
    "一": 0,
    "1": 0,
    "二": 1,
    "2": 1,
    "三": 2,
    "3": 2,
    "四": 3,
    "4": 3,
    "五": 4,
    "5": 4,
    "六": 5,
    "6": 5,
    "日": 6,
    "天": 6,
    "7": 6,
}


def normalize_active_weekdays(value) -> list[int]:
    if value is None:
        return list(ALL_WEEKDAYS)
    if not isinstance(value, (list, tuple, set)):
        return list(ALL_WEEKDAYS)

    normalized = []
    for item in value:
        try:
            weekday = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= weekday <= 6 and weekday not in normalized:
            normalized.append(weekday)
    return sorted(normalized)


def schedule_label(active_weekdays) -> str:
    weekdays = normalize_active_weekdays(active_weekdays)
    if weekdays == ALL_WEEKDAYS:
        return "每天啟用"
    if weekdays == WORKDAYS:
        return "平日啟用（週一至週五）"
    if weekdays == WEEKEND:
        return "週末啟用（週六、週日）"
    if not weekdays:
        return "暫停所有固定排程"
    return "自訂：" + "、".join(WEEKDAY_NAMES[day] for day in weekdays)


def commute_date_is_active(profile, target_date: date, override=None) -> bool:
    if override and getattr(override, "commute_disabled", False):
        return False
    if override and getattr(override, "commute_enabled", False):
        return True
    return target_date.weekday() in normalize_active_weekdays(getattr(profile, "active_weekdays", None))


def next_active_commute_date(db, profile, start_date: date, get_override_for_date, max_days: int = 14) -> date | None:
    for day_offset in range(max_days + 1):
        candidate = start_date + timedelta(days=day_offset)
        override = get_override_for_date(db, profile.user_id, candidate)
        if commute_date_is_active(profile, candidate, override):
            return candidate
    return None


def parse_weekday_preset(preset: str) -> list[int]:
    if preset == "workdays":
        return list(WORKDAYS)
    if preset == "weekend":
        return list(WEEKEND)
    if preset == "none":
        return []
    return list(ALL_WEEKDAYS)


def parse_custom_weekdays(text: str | None) -> list[int] | None:
    """Parse LINE-friendly weekday text like '週一週三週五' or '1,3,5'."""
    if not text:
        return None

    normalized = (
        str(text)
        .strip()
        .replace(" ", "")
        .replace("\u3000", "")
        .replace("，", ",")
        .replace("、", ",")
        .replace("/", ",")
        .replace("星期", "週")
        .replace("禮拜", "週")
        .replace("周", "週")
    )
    normalized = normalized.replace("自訂日曆排程", "").replace("自訂排程", "").replace("排程", "")

    if normalized in {"每天", "每日", "每天啟用", "全週", "週一到週日", "週一至週日"}:
        return list(ALL_WEEKDAYS)
    if normalized in {"平日", "工作日", "上班日", "平日啟用", "週一到週五", "週一至週五"}:
        return list(WORKDAYS)
    if normalized in {"週末", "假日", "週末啟用", "週六週日", "週六,週日"}:
        return list(WEEKEND)
    if normalized in {"暫停", "全休", "暫停固定", "暫停固定排程", "不啟用", "排程全休"}:
        return []

    if "一到五" in normalized or "一至五" in normalized:
        return list(WORKDAYS)
    if "六到日" in normalized or "六至日" in normalized or "六日" in normalized:
        return list(WEEKEND)

    found = []
    for token in re.findall(r"(?:週)?([一二三四五六日天1-7])", normalized):
        weekday = WEEKDAY_TOKEN_MAP.get(token)
        if weekday is not None and weekday not in found:
            found.append(weekday)

    if not found and re.fullmatch(r"[1-7,]+", normalized):
        for token in normalized.replace(",", ""):
            weekday = WEEKDAY_TOKEN_MAP.get(token)
            if weekday is not None and weekday not in found:
                found.append(weekday)

    return sorted(found) if found else None


def combine_target_datetime(target_date: date, hhmm: str, tzinfo) -> datetime | None:
    try:
        parsed_time = datetime.strptime(hhmm, "%H:%M").time()
    except (TypeError, ValueError):
        return None
    return datetime.combine(target_date, parsed_time, tzinfo=tzinfo)


def sleep_until_for_departure(target_date: date, departure_time: str, tzinfo) -> datetime | None:
    departure_at = combine_target_datetime(target_date, departure_time, tzinfo)
    if departure_at is None:
        return None
    return departure_at - timedelta(hours=SLEEP_BEFORE_DEPARTURE_HOURS)


def dashboard_should_sleep(now: datetime, target_date: date, departure_time: str, tzinfo) -> tuple[bool, datetime | None]:
    sleep_until = sleep_until_for_departure(target_date, departure_time, tzinfo)
    if sleep_until is None:
        return False, None
    return now < sleep_until, sleep_until
