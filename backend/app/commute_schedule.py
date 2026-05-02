from datetime import date, datetime, timedelta


ALL_WEEKDAYS = [0, 1, 2, 3, 4, 5, 6]
WORKDAYS = [0, 1, 2, 3, 4]
WEEKEND = [5, 6]
SLEEP_BEFORE_DEPARTURE_HOURS = 8
WEEKDAY_NAMES = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


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
