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
        + wait_minutes
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
        + wait_minutes
        + transfer_minutes
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
        + bus_wait_minutes
        + transfer_minutes
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

from datetime import datetime, timedelta, date


def _combine_date_hhmm(target_date: date, hhmm: str) -> datetime:
    t = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(target_date, t)


async def calculate_departure_time_by_mode(
    profile,
    target_date: date,
    effective_arrival_time: str,
    weather_buffer_minutes: int,
    best_option: dict,
):
    baseline_minutes = await estimate_commute_minutes(
        profile,
        target_date,
        effective_arrival_time,
    )

    arrival_dt = _combine_date_hhmm(target_date, effective_arrival_time)

    mode = best_option.get("mode", "unknown")
    mode_extra_minutes = 0
    mode_note = "目前先以基礎通勤估算為主"

    if mode == "bus":
        wait_minutes = best_option.get("wait_minutes", 0) or 0
        reliability_penalty = best_option.get("reliability_penalty_minutes", 3) or 0
        mode_extra_minutes = wait_minutes + reliability_penalty
        mode_note = f"已加入公車等待 {wait_minutes} 分鐘與公車穩定緩衝 {reliability_penalty} 分鐘"

    elif mode == "metro":
        wait_minutes = best_option.get("wait_minutes", 0) or 0
        transfer_minutes = best_option.get("transfer_minutes", 0) or 0
        reliability_penalty = best_option.get("reliability_penalty_minutes", 1) or 0
        mode_extra_minutes = wait_minutes + transfer_minutes + reliability_penalty
        mode_note = f"已加入捷運等待 {wait_minutes} 分鐘、進站/轉乘緩衝 {transfer_minutes} 分鐘與穩定緩衝 {reliability_penalty} 分鐘"

    elif mode == "bus_to_metro":
        wait_minutes = best_option.get("wait_minutes", 0) or 0
        transfer_minutes = best_option.get("transfer_minutes", 0) or 0
        reliability_penalty = best_option.get("reliability_penalty_minutes", 2) or 0
        mode_extra_minutes = wait_minutes + transfer_minutes + reliability_penalty
        mode_note = f"已加入第一段公車等待 {wait_minutes} 分鐘、轉乘緩衝 {transfer_minutes} 分鐘與混合方案緩衝 {reliability_penalty} 分鐘"

    total_minutes = baseline_minutes + weather_buffer_minutes + mode_extra_minutes
    departure_dt = arrival_dt - timedelta(minutes=total_minutes)
    departure_time = departure_dt.strftime("%H:%M")

    realtime_info = {
        "used_realtime_adjustment": False,
        "reason": "not_applied",
        "latest_leave_time": None,
    }

    # 只有今天 + 公車模式，才額外套即時公車修正
    if mode == "bus" and target_date == date.today():
        try:
            bus_snapshot = best_option.get("snapshot", {}) or await get_bus_realtime_snapshot(profile)
            realtime_info = choose_departure_time_with_realtime_bus(
                baseline_departure_time=departure_time,
                bus_snapshot=bus_snapshot,
            )
            departure_time = realtime_info.get("final_departure_time", departure_time)
        except Exception:
            import traceback
            print("[mode-departure] bus realtime adjustment failed")
            print(traceback.format_exc())

    return {
        "departure_time": departure_time,
        "baseline_minutes": baseline_minutes,
        "mode_extra_minutes": mode_extra_minutes,
        "total_minutes": total_minutes,
        "mode_note": mode_note,
        "realtime_info": realtime_info,
    }

from datetime import date

from app.crud import (
    get_profile,
    get_next_setup_step,
    get_override_for_date,
    get_transport_mode_override,
)


REMINDER_MODE_LABELS = {
    "bus": "公車優先",
    "metro": "建議改搭捷運",
    "bus_to_metro": "建議公車轉捷運",
    "unknown": "目前無法明確判斷",
}

REMINDER_SELECTION_SOURCE_LABELS = {
    "auto": "系統自動幫你選擇",
    "manual": "已依照你今天指定的交通方式計算",
    "fallback_auto": "你今天指定的交通方式目前不適合，所以改用系統自動幫你選擇",
}

REMINDER_TRANSPORT_MODE_NAME_MAP = {
    None: "未指定，系統自動判斷",
    "auto": "自動判斷",
    "bus": "公車優先",
    "metro": "捷運優先",
    "bus_to_metro": "公車轉捷運",
}


async def build_today_reminder_payload(db, user_id: int, target_date: date | None = None):
    if target_date is None:
        target_date = date.today()

    profile = get_profile(db, user_id)
    next_step = get_next_setup_step(profile)

    if next_step is not None:
        return {
            "ok": False,
            "reason": "setup_incomplete",
            "next_step": next_step,
            "text": None,
        }

    effective_arrival_time = profile.preferred_arrival_time
    override = get_override_for_date(db, user_id, target_date)
    if override and override.target_arrival_time:
        effective_arrival_time = override.target_arrival_time

    weather_info = await get_commute_weather(profile)
    weather_buffer = weather_info.get("extra_buffer_minutes", 0)

    mode_override = get_transport_mode_override(db, user_id, target_date)

    option_choice = await choose_commute_option_with_override(
        profile,
        effective_arrival_time=effective_arrival_time,
        weather_buffer_minutes=weather_buffer,
        mode_override=mode_override,
    )

    best_option = option_choice.get("best_option", {}) or {}
    selection_source = option_choice.get("selection_source", "auto")

    departure_calc = await calculate_departure_time_by_mode(
        profile=profile,
        target_date=target_date,
        effective_arrival_time=effective_arrival_time,
        weather_buffer_minutes=weather_buffer,
        best_option=best_option,
    )

    final_departure_time = departure_calc["departure_time"]
    mode_note = departure_calc.get("mode_note", "目前先以基礎通勤估算為主")

    recommended_mode = best_option.get("mode", "unknown")
    mode_text = REMINDER_MODE_LABELS.get(recommended_mode, "目前無法明確判斷")
    option_summary = best_option.get("summary", "目前先以基礎通勤估算為主")

    weather_text = weather_info.get("weather_text", "未知")
    pop = weather_info.get("pop")
    min_t = weather_info.get("temperature_min")
    max_t = weather_info.get("temperature_max")
    temperature = weather_info.get("temperature")

    weather_line = weather_text
    if temperature is not None:
        weather_line += f"，{temperature}°C"
    elif min_t is not None and max_t is not None:
        weather_line += f"，{min_t}-{max_t}°C"

    if pop is not None:
        weather_line += f"，降雨機率 {pop}%"

    selection_source_text = REMINDER_SELECTION_SOURCE_LABELS.get(selection_source, selection_source)
    mode_override_text = REMINDER_TRANSPORT_MODE_NAME_MAP.get(mode_override, mode_override)

    reminder_lines = [
        "出門提醒：",
        f"你今天建議於 {final_departure_time} 出門",
        f"建議方式：{mode_text}",
        f"到公司時間：{effective_arrival_time}",
        f"方案摘要：{option_summary}",
        f"出門時間依據：{mode_note}",
        f"今日模式設定：{mode_override_text}",
        f"套用方式：{selection_source_text}",
        f"今天天氣：{weather_line}",
    ]

    if recommended_mode == "bus":
        bus_data = best_option.get("snapshot", {}) or {}
        first_stop = bus_data.get("first_stop", {}) or {}
        chosen_bus = bus_data.get("chosen_bus")
        walk_minutes = bus_data.get("walk_minutes")

        if first_stop:
            reminder_lines.append(f"最近站牌：{first_stop.get('stop_name', '未知站牌')}")
        if walk_minutes is not None:
            reminder_lines.append(f"步行到站牌：約 {walk_minutes} 分鐘")
        if chosen_bus and chosen_bus.get("eta_min") is not None:
            bus_label = chosen_bus.get("route_name", "未知路線")
            subroute_name = chosen_bus.get("subroute_name")
            if subroute_name and subroute_name != chosen_bus.get("route_name"):
                bus_label += f"({subroute_name})"
            reminder_lines.append(f"可搭班次：{bus_label}，約 {chosen_bus['eta_min']} 分鐘後到站")

    elif recommended_mode == "metro":
        metro_data = best_option.get("snapshot", {}) or {}
        station = metro_data.get("station", {}) or {}
        walk_minutes = metro_data.get("walk_minutes")

        if station:
            reminder_lines.append(f"最近捷運站：{station.get('name', '未知站名')}")
        if walk_minutes is not None:
            reminder_lines.append(f"步行到捷運站：約 {walk_minutes} 分鐘")

    elif recommended_mode == "bus_to_metro":
        mixed_data = best_option.get("snapshot", {}) or {}
        bus_data = mixed_data.get("bus_snapshot", {}) or {}
        metro_data = mixed_data.get("metro_snapshot", {}) or {}

        first_stop = bus_data.get("first_stop", {}) or {}
        chosen_bus = bus_data.get("chosen_bus")
        bus_walk_minutes = bus_data.get("walk_minutes")
        station = metro_data.get("station", {}) or {}

        if first_stop:
            reminder_lines.append(f"第一段公車站牌：{first_stop.get('stop_name', '未知站牌')}")
        if bus_walk_minutes is not None:
            reminder_lines.append(f"步行到公車站：約 {bus_walk_minutes} 分鐘")
        if chosen_bus and chosen_bus.get("eta_min") is not None:
            bus_label = chosen_bus.get("route_name", "未知路線")
            subroute_name = chosen_bus.get("subroute_name")
            if subroute_name and subroute_name != chosen_bus.get("route_name"):
                bus_label += f"({subroute_name})"
            reminder_lines.append(f"第一段公車：{bus_label}，約 {chosen_bus['eta_min']} 分鐘後到站")
        if station:
            reminder_lines.append(f"轉乘捷運：{station.get('name', '未知站名')}")

    return {
        "ok": True,
        "departure_time": final_departure_time,
        "recommended_mode": recommended_mode,
        "text": "\n".join(reminder_lines),
    }