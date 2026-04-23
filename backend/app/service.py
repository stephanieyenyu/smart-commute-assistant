import asyncio
import hashlib
import math
import time
from datetime import date, datetime, timedelta

from app.address_utils import extract_city_from_text
from app.google_maps import estimate_transit_minutes, estimate_walking_minutes
from app.metro_basic import get_nearest_metro_station_async
from app.tdx_bus import (
    get_nearby_stops,
    get_estimated_arrivals,
    simplify_stop_list,
    simplify_eta_list,
)
from app.weather import get_commute_weather
from app.crud import (
    get_profile,
    get_next_setup_step,
    get_override_for_date,
    get_transport_mode_override,
    save_frozen_reminder,
)

DEFAULT_COMMUTE_MINUTES = 56

_TRANSIT_CACHE = {}
_BUS_CACHE = {}
_METRO_CACHE = {}

TRANSIT_CACHE_SECONDS = 180
BUS_CACHE_SECONDS = 120
METRO_CACHE_SECONDS = 180

MODE_LABELS = {
    "bus": "公車優先",
    "metro": "建議改搭捷運",
    "bus_to_metro": "建議公車轉捷運",
    "unknown": "目前無法明確判斷",
}

MODE_REASON_LABELS = {
    "catchable_bus_available": "目前有趕得上的公車",
    "metro_available": "目前搭捷運比較穩",
    "catchable_bus_then_transfer_metro": "目前適合先搭公車再轉捷運",
    "no_catchable_bus": "目前沒有明確能趕上的班次",
    "fallback_baseline_only": "目前先用基本通勤時間估算",
    "metro_fallback_no_realtime": "即時乘車資料暫時不足，先依通勤時間與天氣估算",
    "unknown": "目前還無法明確判斷",
}

TRANSPORT_MODE_NAME_MAP = {
    None: "未指定，系統自動判斷",
    "auto": "自動判斷",
    "bus": "公車優先",
    "metro": "捷運優先",
    "bus_to_metro": "公車轉捷運",
}

SELECTION_SOURCE_NAME_MAP = {
    "auto": "系統自動判斷",
    "manual": "已依照你今天指定的交通方式計算",
    "fallback_auto": "你今天指定的交通方式目前不適合，所以改用系統自動幫你選擇",
}


def combine_date_hhmm(target_date: date, hhmm: str) -> datetime:
    t = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(target_date, t)


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        print(f"[safe_call] failed: {e}")
        return None


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _dedupe_stops_by_name(stops: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for stop in stops:
        name = stop.get("stop_name")
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(stop)
    return deduped


def _city_from_profile(profile) -> str | None:
    return (
        getattr(profile, "home_city", None)
        or extract_city_from_text(getattr(profile, "home_address", None))
        or getattr(profile, "office_city", None)
        or extract_city_from_text(getattr(profile, "office_address", None))
    )


async def _fetch_arrivals_for_stop(city_name: str, stop: dict) -> list[dict]:
    stop_uid = stop.get("stop_uid")
    stop_id = stop.get("stop_id")

    try:
        raw_eta = await get_estimated_arrivals(
            city_name=city_name,
            stop_uid=stop_uid,
            stop_id=stop_id,
        )
        return simplify_eta_list(raw_eta)
    except Exception as e:
        print(f"[bus-arrivals] failed city={city_name}, stop={stop}, error={e}")
        return []


async def estimate_commute_minutes(profile, target_date: date, arrival_time_str: str) -> int:
    if (
        getattr(profile, "home_lat", None) is None
        or getattr(profile, "home_lng", None) is None
        or getattr(profile, "office_lat", None) is None
        or getattr(profile, "office_lng", None) is None
        or not arrival_time_str
    ):
        return DEFAULT_COMMUTE_MINUTES

    cache_key = (
        f"{profile.home_lat}|{profile.home_lng}|"
        f"{profile.office_lat}|{profile.office_lng}|"
        f"{target_date.isoformat()}|{arrival_time_str}"
    )
    now = time.time()
    cached = _TRANSIT_CACHE.get(cache_key)
    if cached and now - cached[0] <= TRANSIT_CACHE_SECONDS:
        return cached[1]

    arrival_dt = combine_date_hhmm(target_date, arrival_time_str)

    try:
        minutes = await estimate_transit_minutes(
            origin_lat=profile.home_lat,
            origin_lng=profile.home_lng,
            destination_lat=profile.office_lat,
            destination_lng=profile.office_lng,
            arrival_datetime=arrival_dt,
        )
        _TRANSIT_CACHE[cache_key] = (now, minutes)
        return minutes
    except Exception as e:
        print(f"[routes] estimate failed: {e}")
        return DEFAULT_COMMUTE_MINUTES


async def calculate_departure_time(
    profile,
    target_date: date,
    effective_arrival_time: str,
    weather_buffer_minutes: int = 0,
) -> str:
    baseline_minutes = await estimate_commute_minutes(
        profile,
        target_date,
        effective_arrival_time,
    )
    arrival_dt = combine_date_hhmm(target_date, effective_arrival_time)
    departure_dt = arrival_dt - timedelta(
        minutes=baseline_minutes + weather_buffer_minutes
    )
    return departure_dt.strftime("%H:%M")


async def calculate_departure_time_by_mode_fast(
    target_date: date,
    effective_arrival_time: str,
    baseline_minutes: int,
    weather_buffer_minutes: int,
    best_option: dict,
):
    arrival_dt = combine_date_hhmm(target_date, effective_arrival_time)

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

    return {
        "departure_time": departure_dt.strftime("%H:%M"),
        "baseline_minutes": baseline_minutes,
        "mode_extra_minutes": mode_extra_minutes,
        "total_minutes": total_minutes,
        "mode_note": mode_note,
        "realtime_info": {
            "used_realtime_adjustment": False,
            "reason": "not_applied",
            "latest_leave_time": None,
        },
    }


async def get_bus_realtime_snapshot(profile):
    try:
        home_lat = getattr(profile, "home_lat", None)
        home_lng = getattr(profile, "home_lng", None)
        city_name = _city_from_profile(profile)

        if home_lat is None or home_lng is None or not city_name:
            return {
                "available": False,
                "reason": "missing_home_data",
            }

        cache_key = f"{home_lat}|{home_lng}|{city_name}"
        now = time.time()
        cached = _BUS_CACHE.get(cache_key)
        if cached and now - cached[0] <= BUS_CACHE_SECONDS:
            return cached[1]

        raw_stops = await get_nearby_stops(
            city_name=city_name,
            lat=home_lat,
            lng=home_lng,
            distance_m=500,
            top=8,
        )
        nearby_stops = simplify_stop_list(raw_stops)
        nearby_stops = _dedupe_stops_by_name(nearby_stops)

        if not nearby_stops:
            result = {
                "available": False,
                "reason": "no_nearby_stops",
            }
            _BUS_CACHE[cache_key] = (now, result)
            return result

        first_stop = nearby_stops[0]

        walk_minutes = None
        stop_lat = first_stop.get("lat")
        stop_lng = first_stop.get("lng")
        if stop_lat is not None and stop_lng is not None:
            try:
                walk_minutes = await estimate_walking_minutes(
                    origin_lat=home_lat,
                    origin_lng=home_lng,
                    destination_lat=stop_lat,
                    destination_lng=stop_lng,
                )
            except Exception as e:
                print(f"[bus-walk] estimate failed: {e}")
                try:
                    distance_km = _haversine_km(home_lat, home_lng, stop_lat, stop_lng)
                    walk_minutes = max(1, round(distance_km / 5 * 60))
                except Exception:
                    walk_minutes = None

        arrival_at_stop_min = (walk_minutes or 0) + 1
        valid_eta_list = await _fetch_arrivals_for_stop(city_name, first_stop)
        valid_eta_list = [eta for eta in valid_eta_list if eta.get("eta_min") is not None]

        chosen_bus = None
        for eta in valid_eta_list:
            eta_min = eta.get("eta_min")
            if eta_min is not None and eta_min >= arrival_at_stop_min:
                chosen_bus = eta
                break

        result = {
            "available": True,
            "city_name": city_name,
            "nearby_stops": nearby_stops,
            "first_stop": first_stop,
            "walk_minutes": walk_minutes,
            "arrival_at_stop_min": arrival_at_stop_min,
            "valid_eta_list": valid_eta_list,
            "chosen_bus": chosen_bus,
        }
        _BUS_CACHE[cache_key] = (now, result)
        return result

    except Exception as e:
        print(f"[bus-snapshot] failed: {e}")
        return {
            "available": False,
            "reason": "exception",
        }


async def get_metro_snapshot(profile):
    try:
        home_lat = getattr(profile, "home_lat", None)
        home_lng = getattr(profile, "home_lng", None)

        if home_lat is None or home_lng is None:
            return {
                "available": False,
                "reason": "missing_home_coords",
            }

        cache_key = f"{home_lat}|{home_lng}"
        now = time.time()
        cached = _METRO_CACHE.get(cache_key)
        if cached and now - cached[0] <= METRO_CACHE_SECONDS:
            return cached[1]

        station = await get_nearest_metro_station_async(home_lat, home_lng)

        if not station:
            result = {
                "available": False,
                "reason": "no_station_found",
            }
            _METRO_CACHE[cache_key] = (now, result)
            return result

        station_lat = station.get("lat")
        station_lng = station.get("lng")

        walk_minutes = None
        distance_km = None

        if station_lat is not None and station_lng is not None:
            try:
                walk_minutes = await estimate_walking_minutes(
                    origin_lat=home_lat,
                    origin_lng=home_lng,
                    destination_lat=station_lat,
                    destination_lng=station_lng,
                )
            except Exception as e:
                print(f"[metro-walk] estimate failed: {e}")

            try:
                distance_km = _haversine_km(home_lat, home_lng, station_lat, station_lng)
                if walk_minutes is None:
                    walk_minutes = max(1, round(distance_km / 5 * 60))
            except Exception:
                distance_km = None

        result = {
            "available": True,
            "station": station,
            "walk_minutes": walk_minutes,
            "distance_km": distance_km,
        }
        _METRO_CACHE[cache_key] = (now, result)
        return result

    except Exception as e:
        print(f"[metro-snapshot] failed: {e}")
        return {
            "available": False,
            "reason": "exception",
        }


async def choose_commute_option_with_override(
    profile,
    effective_arrival_time: str,
    weather_buffer_minutes: int,
    mode_override: str | None = None,
):
    if mode_override == "bus":
        bus_snapshot = await safe_call(get_bus_realtime_snapshot(profile))
        metro_snapshot = None
    elif mode_override == "metro":
        metro_snapshot = await safe_call(get_metro_snapshot(profile))
        bus_snapshot = None
    elif mode_override == "bus_to_metro":
        bus_snapshot, metro_snapshot = await asyncio.gather(
            safe_call(get_bus_realtime_snapshot(profile)),
            safe_call(get_metro_snapshot(profile)),
        )
    else:
        bus_snapshot, metro_snapshot = await asyncio.gather(
            safe_call(get_bus_realtime_snapshot(profile)),
            safe_call(get_metro_snapshot(profile)),
        )

    bus_option = None
    metro_option = None
    bus_to_metro_option = None

    if bus_snapshot and bus_snapshot.get("available"):
        first_stop = bus_snapshot.get("first_stop", {}) or {}
        chosen_bus = bus_snapshot.get("chosen_bus")
        walk_minutes = bus_snapshot.get("walk_minutes")
        arrival_at_stop_min = bus_snapshot.get("arrival_at_stop_min")

        print(f"[bus-debug] first_stop={first_stop.get('stop_name')}, walk_minutes={walk_minutes}, arrival_at_stop_min={arrival_at_stop_min}")
        print(f"[bus-debug] chosen_bus={chosen_bus}")
        print(f"[bus-debug] valid_eta_list={bus_snapshot.get('valid_eta_list')}")

        if chosen_bus and chosen_bus.get("eta_min") is not None:
            wait_minutes = max(0, chosen_bus["eta_min"] - (arrival_at_stop_min or 0))
            chosen_label = chosen_bus.get("route_name", "未知路線")
            subroute_name = chosen_bus.get("subroute_name")
            if subroute_name and subroute_name != chosen_bus.get("route_name"):
                chosen_label += f"({subroute_name})"

            bus_option = {
                "mode": "bus",
                "reason": "catchable_bus_available",
                "summary": f"可搭上公車 {chosen_label}，最近站牌 {first_stop.get('stop_name', '未知站牌')}，步行約 {walk_minutes} 分鐘，等車約 {wait_minutes} 分鐘。",
                "wait_minutes": wait_minutes,
                "reliability_penalty_minutes": 3,
                "snapshot": bus_snapshot,
            }
        else:
            bus_option = {
                "mode": "bus",
                "reason": "no_catchable_bus",
                "summary": f"附近有公車站牌 {first_stop.get('stop_name', '未知站牌')}，但目前沒有明確能趕上的班次。",
                "wait_minutes": 0,
                "reliability_penalty_minutes": 3,
                "snapshot": bus_snapshot,
            }

    if metro_snapshot and metro_snapshot.get("available"):
        station = metro_snapshot.get("station", {}) or {}
        walk_minutes = metro_snapshot.get("walk_minutes")
        station_name = station.get("name", "未知站名")
        metro_option = {
            "mode": "metro",
            "reason": "metro_available",
            "summary": f"可改搭捷運，最近捷運站 {station_name}，步行約 {walk_minutes} 分鐘。",
            "wait_minutes": 3,
            "transfer_minutes": 2,
            "reliability_penalty_minutes": 1,
            "snapshot": metro_snapshot,
        }

    if (
        bus_snapshot and bus_snapshot.get("available")
        and metro_snapshot and metro_snapshot.get("available")
        and bus_snapshot.get("chosen_bus")
    ):
        station = metro_snapshot.get("station", {}) or {}
        station_name = station.get("name", "未知站名")
        bus_to_metro_option = {
            "mode": "bus_to_metro",
            "reason": "catchable_bus_then_transfer_metro",
            "summary": f"可先搭公車再轉捷運，轉乘捷運站 {station_name}。",
            "wait_minutes": 3,
            "transfer_minutes": 5,
            "reliability_penalty_minutes": 2,
            "snapshot": {
                "bus_snapshot": bus_snapshot,
                "metro_snapshot": metro_snapshot,
            },
        }

    selection_source = "auto"

    if mode_override == "bus":
        if bus_option and bus_option.get("reason") == "catchable_bus_available":
            return {"best_option": bus_option, "selection_source": "manual"}
        selection_source = "fallback_auto"

    elif mode_override == "metro":
        if metro_option:
            return {"best_option": metro_option, "selection_source": "manual"}
        selection_source = "fallback_auto"

    elif mode_override == "bus_to_metro":
        if bus_to_metro_option:
            return {"best_option": bus_to_metro_option, "selection_source": "manual"}
        selection_source = "fallback_auto"

    if bus_option and bus_option.get("reason") == "catchable_bus_available":
        return {"best_option": bus_option, "selection_source": selection_source}

    if metro_option:
        return {"best_option": metro_option, "selection_source": selection_source}

    if bus_to_metro_option:
        return {"best_option": bus_to_metro_option, "selection_source": selection_source}

    if bus_option:
        return {"best_option": bus_option, "selection_source": selection_source}

    return {
        "best_option": {
            "mode": "metro",
            "reason": "metro_fallback_no_realtime",
            "summary": "即時乘車資料暫時不足，先依通勤時間與天氣估算，建議改搭捷運或其他大眾運輸並提早出門。",
            "wait_minutes": 3,
            "transfer_minutes": 2,
            "reliability_penalty_minutes": 1,
            "snapshot": {},
        },
        "selection_source": selection_source,
    }


async def _hydrate_option_snapshot(profile, best_option: dict) -> dict:
    mode = best_option.get("mode")
    snapshot = best_option.get("snapshot")

    if mode == "bus":
        if not snapshot or not snapshot.get("first_stop"):
            new_snapshot = await safe_call(get_bus_realtime_snapshot(profile))
            if new_snapshot and new_snapshot.get("available"):
                best_option["snapshot"] = new_snapshot

    elif mode == "metro":
        if not snapshot or not snapshot.get("station"):
            new_snapshot = await safe_call(get_metro_snapshot(profile))
            if new_snapshot and new_snapshot.get("available"):
                best_option["snapshot"] = new_snapshot

    elif mode == "bus_to_metro":
        if not snapshot:
            bus_snapshot, metro_snapshot = await asyncio.gather(
                safe_call(get_bus_realtime_snapshot(profile)),
                safe_call(get_metro_snapshot(profile)),
            )
            best_option["snapshot"] = {
                "bus_snapshot": bus_snapshot or {},
                "metro_snapshot": metro_snapshot or {},
            }

    return best_option


def _build_weather_line(weather_info: dict) -> str:
    weather_text = weather_info.get("weather_text", "未知")
    weather_description = weather_info.get("weather_description")
    temperature = weather_info.get("temperature")
    min_t = weather_info.get("temperature_min")
    max_t = weather_info.get("temperature_max")

    line = f"{weather_text}"
    if weather_description:
        line += f"，{weather_description}"
    if temperature is not None:
        line += f"，{temperature}°C"
    elif min_t is not None and max_t is not None:
        line += f"，{min_t}-{max_t}°C"

    return line


def _build_rain_line(weather_info: dict) -> str:
    pop = weather_info.get("pop")
    return f"{pop}%" if pop is not None else "未知"


def _build_buffer_note(weather_buffer: int) -> str:
    if weather_buffer > 0:
        return f"今日天氣已額外增加 {weather_buffer} 分鐘緩衝。"
    return "今日天氣穩定，未額外增加天氣緩衝。"


def _build_detail_lines(recommended_mode: str, best_option: dict) -> list[str]:
    detail_lines: list[str] = []

    if recommended_mode == "bus":
        bus_data = best_option.get("snapshot", {}) or {}
        first_stop = bus_data.get("first_stop", {}) or {}
        chosen_bus = bus_data.get("chosen_bus")
        walk_minutes = bus_data.get("walk_minutes")
        arrival_at_stop_min = bus_data.get("arrival_at_stop_min")
        valid_eta_list = bus_data.get("valid_eta_list", []) or []

        if first_stop:
            detail_lines.append(f"最近站牌：{first_stop.get('stop_name', '未知站牌')}")
        if walk_minutes is not None:
            detail_lines.append(f"步行到站牌：約 {walk_minutes} 分鐘")
        if arrival_at_stop_min is not None:
            detail_lines.append(f"預估抵達站牌時間：約 {arrival_at_stop_min} 分鐘後")

        if chosen_bus:
            chosen_label = chosen_bus.get("route_name", "未知路線")
            subroute_name = chosen_bus.get("subroute_name")
            if subroute_name and subroute_name != chosen_bus.get("route_name"):
                chosen_label += f"({subroute_name})"
            eta_min = chosen_bus.get("eta_min")
            if eta_min is not None:
                detail_lines.append(f"目前最有機會趕上的車：{chosen_label}，約 {eta_min} 分鐘後到站")

        if valid_eta_list:
            detail_lines.append("即時班次：")
            for eta in valid_eta_list[:3]:
                route_label = eta.get("route_name", "未知路線")
                subroute_name = eta.get("subroute_name")
                if subroute_name and subroute_name != eta.get("route_name"):
                    route_label += f"({subroute_name})"
                eta_min = eta.get("eta_min")
                eta_text = f"{eta_min} 分鐘" if eta_min is not None else "無即時時間"
                detail_lines.append(f"{route_label}：{eta_text}")

    elif recommended_mode == "metro":
        metro_data = best_option.get("snapshot", {}) or {}
        station = metro_data.get("station", {}) or {}
        walk_minutes = metro_data.get("walk_minutes")
        distance_km = metro_data.get("distance_km")

        if station:
            station_name = station.get("name")
            if station_name:
                detail_lines.append(f"最近捷運站：{station_name}")
        if distance_km is not None:
            detail_lines.append(f"直線距離：約 {distance_km:.2f} 公里")
        if walk_minutes is not None:
            detail_lines.append(f"步行到捷運站：約 {walk_minutes} 分鐘")

    elif recommended_mode == "bus_to_metro":
        mixed_data = best_option.get("snapshot", {}) or {}
        bus_data = mixed_data.get("bus_snapshot", {}) or {}
        metro_data = mixed_data.get("metro_snapshot", {}) or {}

        first_stop = bus_data.get("first_stop", {}) or {}
        chosen_bus = bus_data.get("chosen_bus")
        bus_walk_minutes = bus_data.get("walk_minutes")
        station = metro_data.get("station", {}) or {}
        metro_walk_minutes = metro_data.get("walk_minutes")

        if first_stop:
            detail_lines.append(f"第一段公車站牌：{first_stop.get('stop_name', '未知站牌')}")
        if bus_walk_minutes is not None:
            detail_lines.append(f"步行到公車站：約 {bus_walk_minutes} 分鐘")
        if chosen_bus:
            chosen_label = chosen_bus.get("route_name", "未知路線")
            subroute_name = chosen_bus.get("subroute_name")
            if subroute_name and subroute_name != chosen_bus.get("route_name"):
                chosen_label += f"({subroute_name})"
            eta_min = chosen_bus.get("eta_min")
            if eta_min is not None:
                detail_lines.append(f"第一段公車：{chosen_label}，約 {eta_min} 分鐘後到站")
        if station:
            station_name = station.get("name")
            if station_name:
                detail_lines.append(f"轉乘捷運：{station_name}")
        if metro_walk_minutes is not None:
            detail_lines.append(f"捷運步行參考：約 {metro_walk_minutes} 分鐘")
        detail_lines.append("轉乘緩衝：5 分鐘")

    return detail_lines


async def _compute_today_plan(
    db,
    user_id: int,
    target_date: date | None = None,
    force_mode_override: str | None = None,
):
    if target_date is None:
        target_date = date.today()

    profile = get_profile(db, user_id)
    next_step = get_next_setup_step(profile)

    if next_step is not None:
        return {
            "ok": False,
            "reason": "setup_incomplete",
            "next_step": next_step,
        }

    effective_arrival_time = profile.preferred_arrival_time
    override = get_override_for_date(db, user_id, target_date)
    if override and override.target_arrival_time:
        effective_arrival_time = override.target_arrival_time
        used_override = True
    else:
        used_override = False

    weather_info, baseline_minutes = await asyncio.gather(
        get_commute_weather(profile),
        estimate_commute_minutes(profile, target_date, effective_arrival_time),
    )
    weather_buffer = weather_info.get("extra_buffer_minutes", 0)

    stored_mode_override = get_transport_mode_override(db, user_id, target_date)
    mode_override = force_mode_override if force_mode_override is not None else stored_mode_override

    option_choice = await choose_commute_option_with_override(
        profile,
        effective_arrival_time=effective_arrival_time,
        weather_buffer_minutes=weather_buffer,
        mode_override=mode_override,
    )
    best_option = option_choice.get("best_option", {}) or {}
    best_option = await _hydrate_option_snapshot(profile, best_option)
    selection_source = option_choice.get("selection_source", "auto")

    departure_calc = await calculate_departure_time_by_mode_fast(
        target_date=target_date,
        effective_arrival_time=effective_arrival_time,
        baseline_minutes=baseline_minutes,
        weather_buffer_minutes=weather_buffer,
        best_option=best_option,
    )
    final_departure_time = departure_calc["departure_time"]

    recommended_mode = best_option.get("mode", "unknown")
    mode_reason = best_option.get("reason", "unknown")
    option_summary = best_option.get("summary", "目前先以基礎通勤時間估算。")

    weather_line = _build_weather_line(weather_info)
    rain_line = _build_rain_line(weather_info)
    buffer_note = _build_buffer_note(weather_buffer)

    note = (
        f"已套用今天覆蓋到公司時間：{effective_arrival_time}"
        if used_override
        else f"目前使用預設到公司時間：{effective_arrival_time}"
    )

    mode_text = MODE_LABELS.get(recommended_mode, "目前無法明確判斷")
    mode_reason_text = MODE_REASON_LABELS.get(mode_reason, mode_reason)
    mode_override_text = TRANSPORT_MODE_NAME_MAP.get(mode_override, mode_override)
    selection_source_text = SELECTION_SOURCE_NAME_MAP.get(selection_source, selection_source)

    detail_lines = _build_detail_lines(recommended_mode, best_option)

    return {
        "ok": True,
        "profile": profile,
        "target_date": target_date,
        "effective_arrival_time": effective_arrival_time,
        "used_override": used_override,
        "weather_info": weather_info,
        "weather_buffer": weather_buffer,
        "weather_line": weather_line,
        "rain_line": rain_line,
        "baseline_minutes": baseline_minutes,
        "mode_override": mode_override,
        "selection_source": selection_source,
        "selection_source_text": selection_source_text,
        "best_option": best_option,
        "recommended_mode": recommended_mode,
        "mode_text": mode_text,
        "mode_reason": mode_reason,
        "mode_reason_text": mode_reason_text,
        "option_summary": option_summary,
        "mode_override_text": mode_override_text,
        "buffer_note": buffer_note,
        "note": note,
        "departure_calc": departure_calc,
        "final_departure_time": final_departure_time,
        "detail_lines": detail_lines,
    }


def _format_today_commute_text(plan: dict, header: str = "今日通勤建議：") -> str:
    reply_parts = [
        header,
        f"到公司時間：{plan['effective_arrival_time']}",
        f"建議出門時間：{plan['final_departure_time']}",
        f"建議方式：{plan['mode_text']}",
        f"預估通勤時間：{plan['baseline_minutes']} 分鐘",
        f"出門時間依據：{plan['departure_calc'].get('mode_note', '目前先以基礎通勤估算為主')}",
        f"今日天氣：{plan['weather_line']}",
        f"降雨機率：{plan['rain_line']}",
        f"說明：{plan['note']}",
        f"今日模式設定：{plan['mode_override_text']}",
        f"套用方式：{plan['selection_source_text']}",
        f"模式判斷：{plan['mode_reason_text']}",
        f"方案摘要：{plan['option_summary']}",
        f"提醒：{plan['buffer_note']}",
        "",
    ] + plan["detail_lines"]

    return "\n".join(reply_parts)


def _build_reminder_payload_from_plan(plan: dict) -> dict:
    reminder_lines = [
        "出門提醒：",
        f"你今天建議於 {plan['final_departure_time']} 出門",
        f"建議方式：{plan['mode_text']}",
        f"到公司時間：{plan['effective_arrival_time']}",
        f"方案摘要：{plan['option_summary']}",
        f"出門時間依據：{plan['departure_calc'].get('mode_note', '目前先以基礎通勤估算為主')}",
        f"今日模式設定：{plan['mode_override_text']}",
        f"套用方式：{plan['selection_source_text']}",
        f"今天天氣：{plan['weather_line']}，降雨機率 {plan['rain_line']}",
    ]

    recommended_mode = plan["recommended_mode"]
    best_option = plan["best_option"]

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
            station_name = station.get("name")
            if station_name:
                reminder_lines.append(f"最近捷運站：{station_name}")
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
            station_name = station.get("name")
            if station_name:
                reminder_lines.append(f"轉乘捷運：{station_name}")

    text = "\n".join(reminder_lines)
    plan_key = hashlib.sha1(
        f"{plan['target_date'].isoformat()}|{plan['effective_arrival_time']}|{plan['final_departure_time']}|{plan['mode_override']}|{text}".encode("utf-8")
    ).hexdigest()

    return {
        "ok": True,
        "plan_key": plan_key,
        "departure_time": plan["final_departure_time"],
        "recommended_mode": plan["recommended_mode"],
        "text": text,
    }


async def build_today_commute_payload(
    db,
    user_id: int,
    target_date: date | None = None,
    force_mode_override: str | None = None,
    header: str = "今日通勤建議：",
):
    plan = await _compute_today_plan(
        db=db,
        user_id=user_id,
        target_date=target_date,
        force_mode_override=force_mode_override,
    )
    if not plan.get("ok"):
        return plan

    plan["text"] = _format_today_commute_text(plan, header=header)
    return plan


async def build_today_reminder_payload(
    db,
    user_id: int,
    target_date: date | None = None,
    plan: dict | None = None,
):
    if plan is None:
        plan = await _compute_today_plan(
            db=db,
            user_id=user_id,
            target_date=target_date,
            force_mode_override=None,
        )
    if not plan.get("ok"):
        return plan

    return _build_reminder_payload_from_plan(plan)


async def freeze_today_reminder_payload(
    db,
    user_id: int,
    target_date: date | None = None,
    plan: dict | None = None,
):
    payload = await build_today_reminder_payload(
        db=db,
        user_id=user_id,
        target_date=target_date,
        plan=plan,
    )
    if not payload.get("ok"):
        return payload

    save_frozen_reminder(
        db=db,
        user_id=user_id,
        target_date=target_date or date.today(),
        plan_key=payload["plan_key"],
        frozen_departure_time=payload["departure_time"],
        frozen_reminder_text=payload["text"],
        prepared_at=datetime.now(),
    )
    return payload