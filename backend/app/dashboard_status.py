from datetime import date, datetime, time

from app.reminder_timing import hhmm_to_seconds


SAFE_STATE = "safe"
WARNING_STATE = "warning"
URGENT_STATE = "urgent"
DEGRADED_STATE = "degraded"
ERROR_STATE = "error"
SLEEPING_STATE = "sleeping"

WARNING_SECONDS = 15 * 60
URGENT_SECONDS = 3 * 60
TODAY_PLAN_EXPIRES_AFTER_SECONDS = 60 * 60


def seconds_until_hhmm(now: datetime, hhmm: str) -> int:
    now_seconds = now.hour * 3600 + now.minute * 60 + now.second
    return hhmm_to_seconds(hhmm) - now_seconds


def _coerce_target_date(target_date) -> date | None:
    if isinstance(target_date, datetime):
        return target_date.date()
    if isinstance(target_date, date):
        return target_date
    if isinstance(target_date, str):
        try:
            return date.fromisoformat(target_date)
        except ValueError:
            return None
    return None


def seconds_until_departure_datetime(now: datetime, target_date, hhmm: str) -> int:
    plan_date = _coerce_target_date(target_date)
    if plan_date is None:
        return seconds_until_hhmm(now, hhmm)

    departure_seconds = hhmm_to_seconds(hhmm)
    departure_time = time(
        hour=departure_seconds // 3600,
        minute=(departure_seconds % 3600) // 60,
        second=departure_seconds % 60,
    )
    departure_datetime = datetime.combine(plan_date, departure_time)
    if now.tzinfo is not None:
        departure_datetime = departure_datetime.replace(tzinfo=now.tzinfo)
    return int((departure_datetime - now).total_seconds())


def seconds_until_iso_datetime(now: datetime, iso_value: str | None) -> int | None:
    if not iso_value:
        return None
    try:
        departure_datetime = datetime.fromisoformat(iso_value)
    except (TypeError, ValueError):
        return None
    if now.tzinfo is not None and departure_datetime.tzinfo is None:
        departure_datetime = departure_datetime.replace(tzinfo=now.tzinfo)
    elif now.tzinfo is not None:
        departure_datetime = departure_datetime.astimezone(now.tzinfo)
    return int((departure_datetime - now).total_seconds())


def dashboard_plan_is_expired(now: datetime, plan: dict) -> bool:
    if not plan.get("ok"):
        return False

    departure_time = plan.get("final_departure_time")
    seconds_until_departure = seconds_until_iso_datetime(
        now,
        plan.get("departure_snoozed_until") or plan.get("departure_at"),
    )
    if seconds_until_departure is None and not departure_time:
        return False

    if seconds_until_departure is None:
        seconds_until_departure = seconds_until_departure_datetime(
            now,
            plan.get("target_date"),
            departure_time,
        )
    return seconds_until_departure < -TODAY_PLAN_EXPIRES_AFTER_SECONDS


def dashboard_state_for_departure(
    seconds_until_departure: int | None,
    *,
    degraded: bool = False,
) -> str:
    if degraded:
        return DEGRADED_STATE
    if seconds_until_departure is None:
        return ERROR_STATE
    if seconds_until_departure <= URGENT_SECONDS:
        return URGENT_STATE
    if seconds_until_departure <= WARNING_SECONDS:
        return WARNING_STATE
    return SAFE_STATE


def weather_is_degraded(weather_info: dict) -> bool:
    scope = str(weather_info.get("scope") or "")
    return "stale" in scope or "failed" in scope or "fallback" in scope


def build_dashboard_payload(user_id: int, plan: dict, now: datetime) -> dict:
    if not plan.get("ok"):
        return {
            "ok": False,
            "user_id": user_id,
            "state": ERROR_STATE,
            "reason": plan.get("reason", "plan_unavailable"),
            "next_step": plan.get("next_step"),
        }

    departure_time = plan.get("final_departure_time")
    seconds_until_departure = None
    departure_at = plan.get("departure_snoozed_until") or plan.get("departure_at")
    if departure_at:
        seconds_until_departure = seconds_until_iso_datetime(now, departure_at)
    if departure_time:
        seconds_until_departure = (
            seconds_until_departure
            if seconds_until_departure is not None
            else seconds_until_departure_datetime(
                now,
                plan.get("target_date"),
                departure_time,
            )
        )

    degraded = weather_is_degraded(plan.get("weather_info") or {})
    state = dashboard_state_for_departure(seconds_until_departure, degraded=degraded)

    return {
        "ok": True,
        "user_id": user_id,
        "state": state,
        "target_date": plan["target_date"].isoformat() if hasattr(plan.get("target_date"), "isoformat") else plan.get("target_date"),
        "target_arrival_time": plan.get("effective_arrival_time"),
        "departure_time": departure_time,
        "departure_at": departure_at,
        "is_snoozed": bool(plan.get("departure_snoozed_until")),
        "plan_key": plan.get("plan_key"),
        "seconds_until_departure": seconds_until_departure,
        "recommended_mode": plan.get("recommended_mode"),
        "transport_line": plan.get("transport_line"),
        "commute_minutes": plan.get("baseline_minutes"),
        "weather": plan.get("weather_info"),
        "updated_at": now.isoformat(),
        "refresh_seconds": 30,
    }


def build_sleeping_payload(user_id: int, plan: dict, now: datetime, sleep_until: datetime | None) -> dict:
    payload = build_dashboard_payload(user_id=user_id, plan=plan, now=now)
    payload["state"] = SLEEPING_STATE
    payload["sleeping"] = True
    payload["sleep_until"] = sleep_until.isoformat() if sleep_until else None
    payload["refresh_seconds"] = 300
    return payload


def build_no_active_day_payload(user_id: int, now: datetime, reason: str = "schedule_inactive") -> dict:
    return {
        "ok": True,
        "user_id": user_id,
        "state": SLEEPING_STATE,
        "sleeping": True,
        "reason": reason,
        "target_date": None,
        "target_arrival_time": "--:--",
        "departure_time": "--:--",
        "departure_at": None,
        "is_snoozed": False,
        "plan_key": None,
        "seconds_until_departure": None,
        "recommended_mode": None,
        "transport_line": "排程休息中，今天不會推送出門提醒。",
        "commute_minutes": None,
        "weather": None,
        "updated_at": now.isoformat(),
        "refresh_seconds": 300,
    }
