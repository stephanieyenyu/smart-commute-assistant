from datetime import date, datetime, time, timedelta, timezone

from app.google_maps import estimate_transit_minutes
from app.metro_basic import get_nearest_metro_station

DEFAULT_COMMUTE_MINUTES = 35
DEFAULT_BUFFER_MINUTES = 10
TAIPEI_TZ = timezone(timedelta(hours=8))


async def estimate_commute_minutes(profile, target_date: date, arrival_time_str: str) -> int:
    if (
        profile.home_lat is None
        or profile.home_lng is None
        or profile.office_lat is None
        or profile.office_lng is None
    ):
        return DEFAULT_COMMUTE_MINUTES

    hour, minute = map(int, arrival_time_str.split(":"))
    arrival_dt = datetime.combine(
        target_date,
        time(hour=hour, minute=minute, tzinfo=TAIPEI_TZ),
    )

    google_minutes = await estimate_transit_minutes(
        origin_lat=profile.home_lat,
        origin_lng=profile.home_lng,
        dest_lat=profile.office_lat,
        dest_lng=profile.office_lng,
        arrival_datetime=arrival_dt,
    )

    if google_minutes is None:
        return DEFAULT_COMMUTE_MINUTES

    return google_minutes


async def calculate_departure_time(
    profile,
    target_date: date,
    arrival_time_str: str,
    safety_buffer_minutes: int = DEFAULT_BUFFER_MINUTES,
    weather_buffer_minutes: int = 0,
) -> str:
    estimated_commute_minutes = await estimate_commute_minutes(
        profile,
        target_date,
        arrival_time_str,
    )

    departure_time = datetime.strptime(arrival_time_str, "%H:%M") - timedelta(
        minutes=(
            estimated_commute_minutes
            + safety_buffer_minutes
            + weather_buffer_minutes
            + profile.walk_to_bus_stop_min
        )
    )

    return departure_time.strftime("%H:%M")

from app.google_maps import estimate_walk_minutes
from app.tdx_bus import (
    get_nearby_stops,
    get_estimated_arrivals,
    simplify_stop_list,
    simplify_eta_list,
    dedupe_stops_by_name,
    choose_catchable_bus,
)


async def get_bus_realtime_snapshot(profile):
    if not profile.home_lat or not profile.home_lng or not profile.home_city:
        return {
            "available": False,
            "reason": "missing_home_location",
        }

    nearby_raw = await get_nearby_stops(
        city_name=profile.home_city,
        lat=profile.home_lat,
        lng=profile.home_lng,
        distance_m=500,
        top=8,
    )
    nearby_stops = simplify_stop_list(nearby_raw)
    nearby_stops = dedupe_stops_by_name(nearby_stops)

    if not nearby_stops:
        return {
            "available": False,
            "reason": "no_stops",
        }

    first_stop = nearby_stops[0]

    walk_minutes = None
    if first_stop.get("lat") is not None and first_stop.get("lng") is not None:
        walk_minutes = await estimate_walk_minutes(
            profile.home_lat,
            profile.home_lng,
            first_stop["lat"],
            first_stop["lng"],
        )

    eta_raw = await get_estimated_arrivals(
        city_name=profile.home_city,
        stop_id=first_stop["stop_id"],
        top=20,
    )
    eta_list = simplify_eta_list(eta_raw)

    chosen_bus, valid_eta_list = choose_catchable_bus(
        eta_list=eta_list,
        walk_minutes=walk_minutes,
        safety_buffer_min=1,
    )

    return {
        "available": True,
        "first_stop": first_stop,
        "nearby_stops": nearby_stops,
        "walk_minutes": walk_minutes,
        "arrival_at_stop_min": (walk_minutes + 1) if walk_minutes is not None else None,
        "chosen_bus": chosen_bus,
        "valid_eta_list": valid_eta_list[:5],
    }

from datetime import datetime, timedelta
from app.google_maps import estimate_walk_minutes
from app.tdx_bus import (
    get_nearby_stops,
    get_estimated_arrivals,
    simplify_stop_list,
    simplify_eta_list,
    dedupe_stops_by_name,
    choose_catchable_bus,
)


async def get_bus_realtime_snapshot(profile):
    if not profile.home_lat or not profile.home_lng or not profile.home_city:
        return {
            "available": False,
            "reason": "missing_home_location",
        }

    nearby_raw = await get_nearby_stops(
        city_name=profile.home_city,
        lat=profile.home_lat,
        lng=profile.home_lng,
        distance_m=500,
        top=8,
    )
    nearby_stops = simplify_stop_list(nearby_raw)
    nearby_stops = dedupe_stops_by_name(nearby_stops)

    if not nearby_stops:
        return {
            "available": False,
            "reason": "no_stops",
        }

    first_stop = nearby_stops[0]

    walk_minutes = None
    if first_stop.get("lat") is not None and first_stop.get("lng") is not None:
        walk_minutes = await estimate_walk_minutes(
            profile.home_lat,
            profile.home_lng,
            first_stop["lat"],
            first_stop["lng"],
        )

    eta_raw = await get_estimated_arrivals(
        city_name=profile.home_city,
        stop_id=first_stop["stop_id"],
        top=20,
    )
    eta_list = simplify_eta_list(eta_raw)

    chosen_bus, valid_eta_list = choose_catchable_bus(
        eta_list=eta_list,
        walk_minutes=walk_minutes,
        safety_buffer_min=1,
    )

    return {
        "available": True,
        "first_stop": first_stop,
        "nearby_stops": nearby_stops,
        "walk_minutes": walk_minutes,
        "arrival_at_stop_min": (walk_minutes + 1) if walk_minutes is not None else None,
        "chosen_bus": chosen_bus,
        "valid_eta_list": valid_eta_list[:5],
    }


def _today_dt_from_hhmm(hhmm: str) -> datetime:
    now = datetime.now()
    t = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(now.date(), t)


def choose_departure_time_with_realtime_bus(
    baseline_departure_time: str,
    bus_snapshot: dict,
    lookahead_limit_min: int = 45,
):
    result = {
        "final_departure_time": baseline_departure_time,
        "used_realtime_adjustment": False,
        "latest_leave_time": None,
        "leave_in_min": None,
        "reason": "baseline_only",
    }

    if not bus_snapshot.get("available"):
        result["reason"] = "bus_unavailable"
        return result

    walk_minutes = bus_snapshot.get("walk_minutes")
    chosen_bus = bus_snapshot.get("chosen_bus")

    if walk_minutes is None or not chosen_bus or chosen_bus.get("eta_min") is None:
        result["reason"] = "no_catchable_bus"
        return result

    now = datetime.now()
    baseline_dt = _today_dt_from_hhmm(baseline_departure_time)

    # 如果現在時間已經超過原本今天應該出門的時間，
    # 就不要再用「現在的即時公車」去修正今天早上那個出門建議
    if baseline_dt <= now:
        result["reason"] = "baseline_departure_passed"
        return result

    leave_in_min = max(chosen_bus["eta_min"] - walk_minutes - 1, 0)
    latest_leave_dt = now + timedelta(minutes=leave_in_min)

    result["latest_leave_time"] = latest_leave_dt.strftime("%H:%M")
    result["leave_in_min"] = leave_in_min

    minutes_to_baseline = int((baseline_dt - now).total_seconds() / 60)

    # 出門時間還很久時，不要用此刻公車 ETA 硬改
    if minutes_to_baseline > lookahead_limit_min:
        result["reason"] = "baseline_too_far_for_realtime"
        return result

    # 若即時公車要求更早出門，就把建議時間往前拉
    if latest_leave_dt < baseline_dt:
        result["final_departure_time"] = latest_leave_dt.strftime("%H:%M")
        result["used_realtime_adjustment"] = True
        result["reason"] = "leave_earlier_to_catch_bus"
        return result

    result["reason"] = "baseline_already_earlier"
    return result



async def get_metro_snapshot(profile):
    if not profile.home_lat or not profile.home_lng:
        return {
            "available": False,
            "reason": "missing_home_location",
        }

    nearest = get_nearest_metro_station(profile.home_lat, profile.home_lng)
    station = nearest.get("station")

    if not station:
        return {
            "available": False,
            "reason": "no_station",
        }

    walk_minutes = await estimate_walk_minutes(
        profile.home_lat,
        profile.home_lng,
        station["lat"],
        station["lng"],
    )

    return {
        "available": True,
        "station": station,
        "distance_km": nearest.get("distance_km"),
        "walk_minutes": walk_minutes,
    }