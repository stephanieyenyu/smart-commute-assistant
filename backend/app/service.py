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

from datetime import date


def build_commute_option_template():
    return {
        "mode": None,
        "available": False,
        "catchable": False,
        "summary": "",
        "reason": "",
        "walk_minutes": 0,
        "wait_minutes": 0,
        "transfer_minutes": 0,
        "weather_buffer_minutes": 0,
        "reliability_penalty_minutes": 0,
        "baseline_minutes": 0,
        "total_effective_minutes": 0,
        "snapshot": {},
    }


async def build_bus_option(profile, baseline_minutes: int, weather_buffer_minutes: int):
    option = build_commute_option_template()
    option["mode"] = "bus"
    option["baseline_minutes"] = baseline_minutes
    option["weather_buffer_minutes"] = weather_buffer_minutes

    bus_snapshot = await get_bus_realtime_snapshot(profile)
    option["snapshot"] = bus_snapshot

    if not bus_snapshot.get("available"):
        option["reason"] = "bus_unavailable"
        option["summary"] = "目前無法取得可用公車資訊"
        return option

    walk_minutes = bus_snapshot.get("walk_minutes") or 0
    chosen_bus = bus_snapshot.get("chosen_bus")

    if not chosen_bus or chosen_bus.get("eta_min") is None:
        option["reason"] = "no_catchable_bus"
        option["summary"] = "目前沒有明確趕得上的即時公車"
        return option

    wait_minutes = max(chosen_bus["eta_min"] - walk_minutes - 1, 0)
    reliability_penalty = 3

    option["available"] = True
    option["catchable"] = True
    option["walk_minutes"] = walk_minutes
    option["wait_minutes"] = wait_minutes
    option["reliability_penalty_minutes"] = reliability_penalty
    option["total_effective_minutes"] = (
        baseline_minutes
        + weather_buffer_minutes
        + reliability_penalty
    )
    option["reason"] = "catchable_bus_available"

    route_label = chosen_bus["route_name"]
    if chosen_bus.get("subroute_name") and chosen_bus["subroute_name"] != chosen_bus["route_name"]:
        route_label += f"({chosen_bus['subroute_name']})"

    first_stop = bus_snapshot.get("first_stop", {})
    option["summary"] = (
        f"可搭上公車 {route_label}，"
        f"最近站牌 {first_stop.get('stop_name', '未知站牌')}，"
        f"步行約 {walk_minutes} 分鐘，"
        f"等車約 {wait_minutes} 分鐘。"
    )

    return option


async def build_metro_option(profile, baseline_minutes: int, weather_buffer_minutes: int):
    option = build_commute_option_template()
    option["mode"] = "metro"
    option["baseline_minutes"] = baseline_minutes
    option["weather_buffer_minutes"] = weather_buffer_minutes

    metro_snapshot = await get_metro_snapshot(profile)
    option["snapshot"] = metro_snapshot

    if not metro_snapshot.get("available"):
        option["reason"] = "metro_unavailable"
        option["summary"] = "目前無法取得可用捷運資訊"
        return option

    walk_minutes = metro_snapshot.get("walk_minutes") or 0
    wait_minutes = 3
    transfer_minutes = 2
    reliability_penalty = 1

    option["available"] = True
    option["catchable"] = True
    option["walk_minutes"] = walk_minutes
    option["wait_minutes"] = wait_minutes
    option["transfer_minutes"] = transfer_minutes
    option["reliability_penalty_minutes"] = reliability_penalty
    option["total_effective_minutes"] = (
        baseline_minutes
        + weather_buffer_minutes
        + reliability_penalty
    )
    option["reason"] = "metro_available"

    station = metro_snapshot.get("station", {})
    option["summary"] = (
        f"可改搭捷運，最近捷運站 {station.get('name', '未知站名')}，"
        f"步行約 {walk_minutes} 分鐘。"
    )

    return option


async def build_bus_to_metro_option(profile, baseline_minutes: int, weather_buffer_minutes: int):
    option = build_commute_option_template()
    option["mode"] = "bus_to_metro"
    option["baseline_minutes"] = baseline_minutes
    option["weather_buffer_minutes"] = weather_buffer_minutes

    bus_snapshot = await get_bus_realtime_snapshot(profile)
    metro_snapshot = await get_metro_snapshot(profile)

    option["snapshot"] = {
        "bus_snapshot": bus_snapshot,
        "metro_snapshot": metro_snapshot,
    }

    if not bus_snapshot.get("available"):
        option["reason"] = "bus_unavailable"
        option["summary"] = "第一段公車不可用"
        return option

    if not metro_snapshot.get("available"):
        option["reason"] = "metro_unavailable"
        option["summary"] = "第二段捷運不可用"
        return option

    chosen_bus = bus_snapshot.get("chosen_bus")
    if not chosen_bus or chosen_bus.get("eta_min") is None:
        option["reason"] = "no_catchable_bus"
        option["summary"] = "目前沒有可銜接捷運的第一段公車"
        return option

    bus_walk_minutes = bus_snapshot.get("walk_minutes") or 0
    bus_wait_minutes = max(chosen_bus["eta_min"] - bus_walk_minutes - 1, 0)
    transfer_minutes = 5
    reliability_penalty = 2

    option["available"] = True
    option["catchable"] = True
    option["walk_minutes"] = bus_walk_minutes
    option["wait_minutes"] = bus_wait_minutes
    option["transfer_minutes"] = transfer_minutes
    option["reliability_penalty_minutes"] = reliability_penalty
    option["total_effective_minutes"] = (
        baseline_minutes
        + weather_buffer_minutes
        + reliability_penalty
    )
    option["reason"] = "catchable_bus_then_transfer_metro"

    route_label = chosen_bus["route_name"]
    if chosen_bus.get("subroute_name") and chosen_bus["subroute_name"] != chosen_bus["route_name"]:
        route_label += f"({chosen_bus['subroute_name']})"

    station = metro_snapshot.get("station", {})
    option["summary"] = (
        f"可先搭公車 {route_label} 再轉捷運，"
        f"公車步行約 {bus_walk_minutes} 分鐘，"
        f"等車約 {bus_wait_minutes} 分鐘，"
        f"轉乘捷運站參考 {station.get('name', '未知站名')}。"
    )

    return option


async def choose_best_commute_option(profile, effective_arrival_time: str, weather_buffer_minutes: int):
    baseline_minutes = await estimate_commute_minutes(
        profile,
        date.today(),
        effective_arrival_time,
    )

    bus_option = await build_bus_option(
        profile,
        baseline_minutes=baseline_minutes,
        weather_buffer_minutes=weather_buffer_minutes,
    )
    metro_option = await build_metro_option(
        profile,
        baseline_minutes=baseline_minutes,
        weather_buffer_minutes=weather_buffer_minutes,
    )
    bus_to_metro_option = await build_bus_to_metro_option(
        profile,
        baseline_minutes=baseline_minutes,
        weather_buffer_minutes=weather_buffer_minutes,
    )

    all_options = [bus_option, metro_option, bus_to_metro_option]
    available_options = [opt for opt in all_options if opt["available"]]

    if not available_options:
        return {
            "best_option": {
                "mode": "unknown",
                "available": False,
                "summary": "目前無法明確判斷最佳通勤方式",
                "reason": "no_available_options",
                "total_effective_minutes": baseline_minutes + weather_buffer_minutes,
            },
            "all_options": all_options,
            "baseline_minutes": baseline_minutes,
        }

    available_options.sort(key=lambda x: x["total_effective_minutes"])
    best_option = available_options[0]

    return {
        "best_option": best_option,
        "all_options": all_options,
        "baseline_minutes": baseline_minutes,
    }

async def choose_commute_option_with_override(
    profile,
    effective_arrival_time: str,
    weather_buffer_minutes: int,
    mode_override: str | None,
):
    option_result = await choose_best_commute_option(
        profile,
        effective_arrival_time=effective_arrival_time,
        weather_buffer_minutes=weather_buffer_minutes,
    )

    all_options = option_result["all_options"]
    best_option = option_result["best_option"]

    if not mode_override or mode_override == "auto":
        return {
            "best_option": best_option,
            "selection_source": "auto",
        }

    for option in all_options:
        if option.get("mode") == mode_override and option.get("available"):
            return {
                "best_option": option,
                "selection_source": "manual",
            }

    return {
        "best_option": best_option,
        "selection_source": "fallback_auto",
    }