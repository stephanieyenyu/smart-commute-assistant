import asyncio
import hashlib
import math
import re
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.address_utils import extract_city_from_text
from app.google_maps import estimate_transit_minutes, estimate_transit_minutes_detailed
from app.metro_basic import get_nearest_metro_station_async, get_station_exits_async
from app.tdx_bus import (
    get_nearby_stops,
    get_estimated_arrivals,
    simplify_eta_list,
)
from app.weather import get_commute_weather
from app import route_formatter
from app.commute_schedule import arrival_label, target_label_text
from app.crud import (
    effective_commute_setting_for_date,
    get_profile,
    get_next_setup_step,
    get_override_for_date,
    get_transport_mode_override,
    save_frozen_reminder,
    record_commute_plan_log,
)

DEFAULT_COMMUTE_MINUTES = 56
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

_TRANSIT_CACHE = {}
_BUS_CACHE = {}
_METRO_CACHE = {}

TRANSIT_CACHE_SECONDS = 180
BUS_CACHE_SECONDS = 60
METRO_CACHE_SECONDS = 180

MODE_LABELS = {
    "google_transit": "目前以 Google 大眾運輸估算為主",
    "bus": "今天搭公車",
    "metro": "建議改搭捷運",
    "bus_to_metro": "今天搭公車轉捷運",
    "mixed_transit": "今天搭大眾運輸轉乘",
    "rail": "今天搭鐵路",
    "light_rail": "今天搭輕軌",
}


def combine_date_hhmm(target_date: date, hhmm: str) -> datetime:
    t = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(target_date, t)


def _now_taipei_naive() -> datetime:
    return datetime.now(TAIPEI_TZ).replace(tzinfo=None)


async def safe_call(coro, timeout_seconds: float | None = None):
    try:
        if timeout_seconds is not None:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        return await coro
    except asyncio.TimeoutError:
        print(f"[safe-call] timeout={timeout_seconds}s")
        return None
    except Exception as e:
        print(f"[safe-call] error={e}")
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
        name = stop.get("stop_name") or stop.get("stop_id") or stop.get("stop_uid")
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

    raw_eta = await get_estimated_arrivals(
        city_name=city_name,
        stop_uid=stop_uid,
        stop_id=stop_id,
    )
    return simplify_eta_list(raw_eta)


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
    baseline_minutes = await estimate_commute_minutes(profile, target_date, effective_arrival_time)
    arrival_dt = combine_date_hhmm(target_date, effective_arrival_time)
    departure_dt = arrival_dt - timedelta(minutes=baseline_minutes + weather_buffer_minutes)
    return departure_dt.strftime("%H:%M")


async def calculate_departure_time_by_mode_fast(
    target_date: date,
    effective_arrival_time: str,
    baseline_minutes: int,
    weather_buffer_minutes: int,
    best_option: dict,
):
    arrival_dt = combine_date_hhmm(target_date, effective_arrival_time)

    mode = best_option.get("mode", "google_transit")
    mode_extra_minutes = 0
    mode_note = "目前先以 Google 大眾運輸估算為主"
    latest_on_time_departure = arrival_dt - timedelta(minutes=baseline_minutes + weather_buffer_minutes)

    if mode == "bus":
        snapshot = best_option.get("snapshot", {})
        chosen_bus = snapshot.get("chosen_bus", {})
        eta_min = chosen_bus.get("eta_min") if chosen_bus else None
        walk_minutes = snapshot.get("walk_minutes") or 0

        if eta_min is not None:
            leave_in_minutes = max(0, eta_min - 3 - walk_minutes)
            realtime_departure = _now_taipei_naive() + timedelta(minutes=leave_in_minutes)
            departure_dt = min(realtime_departure, latest_on_time_departure)
            total_minutes = max(0, round((arrival_dt - departure_dt).total_seconds() / 60))
            mode_note = (
                f"以公車即時到站 {eta_min} 分鐘、步行到站 {walk_minutes} 分鐘計算，"
                "並確保不晚於目標抵達時間"
            )
            return {
                "departure_time": departure_dt.strftime("%H:%M"),
                "baseline_minutes": baseline_minutes,
                "mode_extra_minutes": max(0, total_minutes - baseline_minutes - weather_buffer_minutes),
                "total_minutes": total_minutes,
                "mode_note": mode_note,
            }

        wait_minutes = best_option.get("wait_minutes", 0) or 0
        reliability_penalty = best_option.get("reliability_penalty_minutes", 3) or 0
        mode_extra_minutes = wait_minutes + reliability_penalty
        mode_note = f"已加入公車等待 {wait_minutes} 分鐘與公車穩定緩衝 {reliability_penalty} 分鐘"

    elif mode == "metro":
        snapshot = best_option.get("snapshot", {})
        walk_minutes = snapshot.get("walk_minutes")
        departure_dt = latest_on_time_departure
        total_minutes = baseline_minutes + weather_buffer_minutes
        walk_note = f"、步行到捷運站約 {walk_minutes} 分鐘" if walk_minutes is not None else ""
        mode_note = f"以捷運通勤總時間 {baseline_minutes} 分鐘{walk_note}回推，確保不晚於目標抵達時間"
        return {
            "departure_time": departure_dt.strftime("%H:%M"),
            "baseline_minutes": baseline_minutes,
            "mode_extra_minutes": 0,
            "total_minutes": total_minutes,
            "mode_note": mode_note,
        }

    total_minutes = baseline_minutes + weather_buffer_minutes + mode_extra_minutes
    departure_dt = latest_on_time_departure - timedelta(minutes=mode_extra_minutes)

    return {
        "departure_time": departure_dt.strftime("%H:%M"),
        "baseline_minutes": baseline_minutes,
        "mode_extra_minutes": mode_extra_minutes,
        "total_minutes": total_minutes,
        "mode_note": mode_note,
    }


async def get_bus_realtime_snapshot(profile):
    try:
        home_lat = getattr(profile, "home_lat", None)
        home_lng = getattr(profile, "home_lng", None)
        city_name = _city_from_profile(profile)

        if home_lat is None or home_lng is None or not city_name:
            return {"available": False, "reason": "city_or_coords_missing"}

        cache_key = f"{home_lat}|{home_lng}|{city_name}"
        now = time.time()
        cached = _BUS_CACHE.get(cache_key)
        if cached and now - cached[0] <= BUS_CACHE_SECONDS:
            return cached[1]

        nearby_stops = await get_nearby_stops(
            city_name=city_name,
            lat=home_lat,
            lng=home_lng,
            distance_m=500,
            top=8,
        )
        nearby_stops = _dedupe_stops_by_name(nearby_stops)

        if not nearby_stops:
            result = {"available": False, "reason": "no_nearby_stops"}
            _BUS_CACHE[cache_key] = (now, result)
            return result

        first_stop = nearby_stops[0]
        stop_lat = first_stop.get("lat")
        stop_lng = first_stop.get("lng")

        # 步行時間：優先使用站點距離，次之用 Haversine 計算（省去 Google API call）
        walk_minutes = None
        dist_m = first_stop.get("distance_m")
        if dist_m is not None:
            walk_minutes = max(1, round(dist_m / 80))
        elif stop_lat is not None and stop_lng is not None:
            dist_km = _haversine_km(home_lat, home_lng, stop_lat, stop_lng)
            walk_minutes = max(1, round(dist_km * 1000 / 80))

        arrival_at_stop_min = (walk_minutes or 0) + 1
        valid_eta_list = await _fetch_arrivals_for_stop(city_name, first_stop)
        valid_eta_list = [eta for eta in valid_eta_list if eta.get("eta_min") is not None]

        chosen_bus = None
        for eta in valid_eta_list:
            eta_min = eta.get("eta_min")
            if eta_min is not None and eta_min >= arrival_at_stop_min:
                chosen_bus = eta
                break

        print(f"[bus-debug] first_stop={first_stop.get('stop_name')}, walk_minutes={walk_minutes}, arrival_at_stop_min={arrival_at_stop_min}")
        print(f"[bus-debug] chosen_bus={chosen_bus}")
        print(f"[bus-debug] valid_eta_list={valid_eta_list}")

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
        return {"available": False, "reason": "exception"}


async def get_metro_snapshot(profile):
    try:
        home_lat = getattr(profile, "home_lat", None)
        home_lng = getattr(profile, "home_lng", None)
        office_lat = getattr(profile, "office_lat", None)
        office_lng = getattr(profile, "office_lng", None)

        if home_lat is None or home_lng is None:
            return {"available": False, "reason": "coords_missing"}

        cache_key = f"{home_lat}|{home_lng}|{office_lat}|{office_lng}"
        now = time.time()
        cached = _METRO_CACHE.get(cache_key)
        if cached and now - cached[0] <= METRO_CACHE_SECONDS:
            return cached[1]

        station_task = asyncio.create_task(get_nearest_metro_station_async(home_lat, home_lng))
        destination_station_task = (
            asyncio.create_task(get_nearest_metro_station_async(office_lat, office_lng))
            if office_lat is not None and office_lng is not None
            else None
        )

        station = await station_task
        if not station:
            if destination_station_task:
                destination_station_task.cancel()
            result = {"available": False, "reason": "no_station"}
            _METRO_CACHE[cache_key] = (now, result)
            return result

        destination_station = None
        suggested_exit = None
        if destination_station_task:
            destination_station = await destination_station_task
            exits = await get_station_exits_async((destination_station or {}).get("id"))
            exits_with_distance = []
            for exit_info in exits:
                exit_lat = exit_info.get("lat")
                exit_lng = exit_info.get("lng")
                if exit_lat is None or exit_lng is None:
                    continue
                exits_with_distance.append((
                    _haversine_km(office_lat, office_lng, exit_lat, exit_lng),
                    exit_info,
                ))
            if exits_with_distance:
                suggested_exit = min(exits_with_distance, key=lambda item: item[0])[1]

        station_lat = station.get("lat")
        station_lng = station.get("lng")

        # 步行時間：直接用 Haversine 計算（省去 Google API call）
        distance_km = None
        walk_minutes = None
        if station_lat is not None and station_lng is not None:
            try:
                distance_km = _haversine_km(home_lat, home_lng, station_lat, station_lng)
                walk_minutes = max(1, round(distance_km * 1000 / 80))
            except Exception:
                pass

        result = {
            "available": True,
            "station": station,
            "destination_station": destination_station,
            "suggested_exit": suggested_exit,
            "walk_minutes": walk_minutes,
            "distance_km": distance_km,
        }
        _METRO_CACHE[cache_key] = (now, result)
        return result

    except Exception as e:
        print(f"[metro-snapshot] failed: {e}")
        return {"available": False, "reason": "exception"}


async def choose_commute_option_with_override(
    profile,
    effective_arrival_time: str,
    weather_buffer_minutes: int,
    target_date: date,
    mode_override: str | None = None,
):
    arrival_dt = combine_date_hhmm(target_date, effective_arrival_time)
    requested_mode = mode_override or "auto"
    allowed_travel_modes = None
    if requested_mode == "bus":
        allowed_travel_modes = ["BUS"]
    elif requested_mode == "metro":
        allowed_travel_modes = ["SUBWAY", "TRAIN", "RAIL", "LIGHT_RAIL"]

    google_task = asyncio.create_task(
        safe_call(estimate_transit_minutes_detailed(
            profile.home_lat, profile.home_lng,
            profile.office_lat, profile.office_lng,
            arrival_dt,
            allowed_travel_modes=allowed_travel_modes,
        ), timeout_seconds=3.0)
    )
    bus_task = (
        asyncio.create_task(safe_call(get_bus_realtime_snapshot(profile), timeout_seconds=2.0))
        if requested_mode in {"auto", "shortest", "bus"}
        else None
    )
    metro_task = (
        asyncio.create_task(safe_call(get_metro_snapshot(profile), timeout_seconds=2.5))
        if requested_mode in {"auto", "metro"}
        else None
    )

    google_detailed = await google_task
    bus_snapshot = await bus_task if bus_task else None
    metro_snapshot = await metro_task if metro_task else None

    chosen_bus = None
    if bus_snapshot and bus_snapshot.get("available"):
        chosen_bus = bus_snapshot.get("chosen_bus")

    google_detailed = google_detailed or {}
    google_steps = google_detailed.get("steps", []) or []
    google_bus_step = route_formatter.select_transit_step(google_steps, "bus")
    google_metro_step = route_formatter.select_transit_step(google_steps, "metro")
    google_mode = route_formatter.route_mode_from_steps(google_steps)

    bus_snapshot_with_details = dict(bus_snapshot or {})
    metro_snapshot_with_details = dict(metro_snapshot or {})
    if bus_snapshot_with_details:
        bus_snapshot_with_details["google_detailed"] = google_detailed
    if metro_snapshot_with_details:
        metro_snapshot_with_details["google_detailed"] = google_detailed

    google_option = {
        "mode": google_mode,
        "reason": "google_transit",
        "summary": "目前以 Google 大眾運輸建議為主",
        "snapshot": {
            "bus_snapshot": bus_snapshot or {},
            "metro_snapshot": metro_snapshot or {},
            "google_detailed": google_detailed,
        },
    }

    bus_option = None
    if google_bus_step:
        bus_snapshot_dict = bus_snapshot or {}
        bus_label = route_formatter.line_label_from_step(google_bus_step)
        google_bus_route = google_bus_step.get("line_short_name") or google_bus_step.get("line_name")
        chosen_route = (chosen_bus or {}).get("route_name") or (chosen_bus or {}).get("subroute_name")
        chosen_bus_matches_google = route_formatter.route_names_match(chosen_route, google_bus_route)
        eta_min = (chosen_bus or {}).get("eta_min") if chosen_bus_matches_google else None

        wait_minutes = max(0, (eta_min or 0) - (bus_snapshot_dict.get("arrival_at_stop_min") or 0))
        bus_snapshot_for_option = dict(bus_snapshot_with_details or {"google_detailed": google_detailed})
        if not chosen_bus_matches_google:
            bus_snapshot_for_option["chosen_bus"] = {}
        bus_option = {
            "mode": "bus",
            "reason": "google_bus_route",
            "summary": f"可搭公車 {bus_label}，於『{google_bus_step.get('departure_stop') or 'Google Maps 未提供上車站名'}』上車。",
            "wait_minutes": wait_minutes,
            "reliability_penalty_minutes": 3,
            "snapshot": bus_snapshot_for_option,
        }

    metro_option = None
    if google_metro_step:
        metro_option = {
            "mode": "metro",
            "reason": "google_metro_route",
            "summary": f"搭乘 {route_formatter.line_label_from_step(google_metro_step)}，於『{google_metro_step.get('departure_stop') or 'Google Maps 未提供上車站名'}』上車。",
            "wait_minutes": 3,
            "transfer_minutes": 2,
            "reliability_penalty_minutes": 1,
            "snapshot": metro_snapshot_with_details or {"google_detailed": google_detailed},
        }

    if requested_mode == "shortest":
        if google_mode == "bus" and bus_option:
            return {"best_option": bus_option, "selection_source": "manual"}
        if google_mode == "metro" and metro_option:
            return {"best_option": metro_option, "selection_source": "manual"}
        return {"best_option": google_option, "selection_source": "manual"}

    if requested_mode == "bus":
        if bus_option:
            return {"best_option": bus_option, "selection_source": "manual"}
        return {
            "best_option": {
                "mode": "google_transit",
                "reason": "google_bus_route_unavailable",
                "summary": "Google Maps 目前未提供公車路線，系統不會自行猜測公車站名或路線。",
                "wait_minutes": 0,
                "reliability_penalty_minutes": 0,
                "snapshot": {"google_detailed": google_detailed},
            },
            "selection_source": "manual",
        }

    if requested_mode == "metro":
        if metro_option:
            return {"best_option": metro_option, "selection_source": "manual"}
        return {
            "best_option": {
                "mode": google_mode,
                "reason": "google_metro_route_unavailable",
                "summary": "Google Maps 目前未提供捷運路線，系統不會自行猜測捷運站名或出口。",
                "wait_minutes": 0,
                "transfer_minutes": 0,
                "reliability_penalty_minutes": 0,
                "snapshot": {"google_detailed": google_detailed},
            },
            "selection_source": "manual",
        }

    if requested_mode == "bus_to_metro":
        if google_mode == "mixed_transit":
            return {"best_option": google_option, "selection_source": "manual"}
        return {
            "best_option": {
                "mode": google_mode,
                "reason": "google_mixed_route_unavailable",
                "summary": "Google Maps 目前未提供公車轉乘捷運的路線，系統不會自行拼湊轉乘。",
                "wait_minutes": 0,
                "transfer_minutes": 0,
                "reliability_penalty_minutes": 0,
                "snapshot": {"google_detailed": google_detailed},
            },
            "selection_source": "manual",
        }

    if google_mode == "bus" and bus_option:
        return {"best_option": bus_option, "selection_source": "auto"}
    return {"best_option": google_option, "selection_source": "auto"}


def _weather_line(weather_info: dict) -> str:
    text = weather_info.get("weather_text") or "無即時資訊"
    temp = weather_info.get("temperature")
    temp_min = weather_info.get("temperature_min")
    temp_max = weather_info.get("temperature_max")

    if temp is not None:
        return f"{text}，{temp}°C"
    if temp_min is not None and temp_max is not None:
        return f"{text}，{temp_min}-{temp_max}°C"
    return text


def _rain_line(weather_info: dict) -> str:
    pop = weather_info.get("pop")
    return f"{pop}%" if pop is not None else "無即時資訊"


def _buffer_note(weather_buffer: int) -> str:
    if weather_buffer > 0:
        return f"今日天氣已額外增加 {weather_buffer} 分鐘緩衝。"
    return "今日天氣穩定，未額外增加天氣緩衝。"


def _build_bus_detail_lines(bus_snapshot: dict) -> list[str]:
    lines: list[str] = []
    first_stop = bus_snapshot.get("first_stop", {}) or {}
    stop_name = first_stop.get("stop_name") or "無法識別站牌"
    walk_minutes = bus_snapshot.get("walk_minutes")
    arrival_at_stop_min = bus_snapshot.get("arrival_at_stop_min")
    chosen_bus = bus_snapshot.get("chosen_bus")
    valid_eta_list = bus_snapshot.get("valid_eta_list", []) or []

    lines.append(f"最近站牌：{stop_name}")
    lines.append(f"步行到站牌：約 {walk_minutes if walk_minutes is not None else '無法估算'} 分鐘")
    lines.append(f"預估抵達站牌時間：約 {arrival_at_stop_min if arrival_at_stop_min is not None else '無法估算'} 分鐘後")

    if chosen_bus:
        route_label = chosen_bus.get("route_name", "無路線資訊")
        subroute_name = chosen_bus.get("subroute_name")
        if subroute_name and subroute_name != chosen_bus.get("route_name"):
            route_label += f"({subroute_name})"
        lines.append(f"目前最有機會趕上的車：{route_label}，約 {chosen_bus.get('eta_min', '無即時資訊')} 分鐘後到站")
    else:
        lines.append("目前清單中沒有明確能趕上的即時班次")

    lines.append("即時班次：")
    if valid_eta_list:
        for eta in valid_eta_list[:5]:
            route_label = eta.get("route_name", "無路線資訊")
            subroute_name = eta.get("subroute_name")
            if subroute_name and subroute_name != eta.get("route_name"):
                route_label += f"({subroute_name})"
            eta_text = f"{eta['eta_min']} 分鐘" if eta.get("eta_min") is not None else "無即時資訊"
            lines.append(f"{route_label}：{eta_text}")
    else:
        lines.append("無即時資訊")

    return lines


def _build_metro_detail_lines(metro_snapshot: dict) -> list[str]:
    lines: list[str] = []
    station = metro_snapshot.get("station", {}) or {}
    station_name = station.get("name") or "無法識別捷運站"
    distance_km = metro_snapshot.get("distance_km")
    walk_minutes = metro_snapshot.get("walk_minutes")

    lines.append(f"最近捷運站：{station_name}")
    if distance_km is not None:
        lines.append(f"直線距離：約 {distance_km:.2f} 公里")
    else:
        lines.append("直線距離：無法估算")
    lines.append(f"步行到捷運站：約 {walk_minutes if walk_minutes is not None else '無法估算'} 分鐘")
    return lines


def _google_duration_from_option(best_option: dict) -> int | None:
    snapshot = best_option.get("snapshot") or {}
    google_detailed = snapshot.get("google_detailed") or {}
    if not google_detailed and isinstance(snapshot.get("google_transit"), dict):
        google_detailed = snapshot["google_transit"].get("google_detailed") or {}

    duration = google_detailed.get("duration_minutes")
    try:
        return int(duration) if duration is not None else None
    except (TypeError, ValueError):
        return None


async def _compute_today_plan(
    db,
    user_id: int,
    target_date: date | None = None,
    force_mode_override: str | None = None,
):
    if target_date is None:
        target_date = datetime.now(TAIPEI_TZ).date()

    profile = get_profile(db, user_id)
    next_step = get_next_setup_step(profile)
    if next_step is not None:
        return {"ok": False, "reason": "setup_incomplete", "next_step": next_step}

    override = get_override_for_date(db, user_id, target_date)
    effective_setting = effective_commute_setting_for_date(db, profile, target_date, override)
    if effective_setting is None:
        return {"ok": False, "reason": "schedule_inactive", "target_date": target_date}

    effective_arrival_time = effective_setting["arrival_time"]
    destination_label = effective_setting["destination_label"]
    destination = effective_setting.get("destination")
    effective_schedule_source = effective_setting["source"]
    if destination is not None and getattr(destination, "lat", None) is not None and getattr(destination, "lng", None) is not None:
        profile = SimpleNamespace(**profile.__dict__)
        profile.office_address = getattr(destination, "address", None) or profile.office_address
        profile.office_lat = getattr(destination, "lat", None)
        profile.office_lng = getattr(destination, "lng", None)
        profile.office_city = getattr(destination, "city", None) or profile.office_city

    schedule_template_id = effective_setting.get("schedule_template_id")
    used_override = False
    if override and override.target_arrival_time:
        effective_arrival_time = override.target_arrival_time
        used_override = True

    stored_mode_override = get_transport_mode_override(db, user_id, target_date)
    mode_override = force_mode_override if force_mode_override is not None else stored_mode_override

    # Keep user-facing advice fast: route details already include the duration, so
    # do not make a second Google Routes call just to calculate baseline minutes.
    weather_info, option_choice = await asyncio.gather(
        safe_call(get_commute_weather(profile), timeout_seconds=1.5),
        safe_call(choose_commute_option_with_override(
            profile=profile,
            effective_arrival_time=effective_arrival_time,
            weather_buffer_minutes=0,
            target_date=target_date,
            mode_override=mode_override,
        ), timeout_seconds=3.5),
    )
    weather_info = weather_info or {"extra_buffer_minutes": 0, "weather_text": "未知"}
    weather_buffer = weather_info.get("extra_buffer_minutes", 0)
    option_choice = option_choice or {"best_option": {"mode": "google_transit"}, "selection_source": "auto"}
    best_option = option_choice.get("best_option", {}) or {}
    selection_source = option_choice.get("selection_source", "auto")
    baseline_minutes = _google_duration_from_option(best_option) or DEFAULT_COMMUTE_MINUTES

    departure_calc = await calculate_departure_time_by_mode_fast(
        target_date=target_date,
        effective_arrival_time=effective_arrival_time,
        baseline_minutes=baseline_minutes,
        weather_buffer_minutes=weather_buffer,
        best_option=best_option,
    )

    final_departure_time = departure_calc["departure_time"]
    recommended_mode = best_option.get("mode", "google_transit")

    today = datetime.now(TAIPEI_TZ).date()
    if target_date == today:
        target_label = "今天"
    elif target_date == today + timedelta(days=1):
        target_label = "明天"
    else:
        target_label = target_date.isoformat()
    note = (
        f"{target_label}使用臨時{arrival_label(destination_label)}：{effective_arrival_time}"
        if used_override
        else f"{target_label}使用固定{arrival_label(destination_label)}：{effective_arrival_time}"
    )

    return {
        "ok": True,
        "profile": profile,
        "target_date": target_date,
        "effective_arrival_time": effective_arrival_time,
        "destination_label": destination_label,
        "arrival_label": arrival_label(destination_label),
        "target_label_text": target_label_text(destination_label),
        "effective_schedule_source": effective_schedule_source,
        "schedule_template_id": schedule_template_id,
        "weather_info": weather_info,
        "weather_buffer": weather_buffer,
        "baseline_minutes": baseline_minutes,
        "best_option": best_option,
        "selection_source": selection_source,
        "recommended_mode": recommended_mode,
        "final_departure_time": final_departure_time,
        "departure_calc": departure_calc,
        "note": note,
        "mode_override": mode_override,
    }


def _walk_metres(walk_minutes) -> str | None:
    if walk_minutes is None:
        return None
    metres = round(walk_minutes * 80 / 10) * 10  # round to nearest 10m
    return f"{metres} 公尺"


def _normalize_exit_label(text: str | None) -> str | None:
    if not text:
        return None

    compact = re.sub(r"\s+", " ", str(text).strip())
    patterns = [
        r"(?:出口|Exit)\s*([0-9A-Za-z]+)",
        r"([0-9A-Za-z]+)\s*(?:號)?\s*出口",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            return f"出口 {match.group(1).upper()}"
    return None


def _exit_info_from_steps(steps: list[dict], matched_step: dict) -> str:
    try:
        matched_index = steps.index(matched_step)
    except ValueError:
        matched_index = 0

    search_steps = steps[matched_index + 1:] + [matched_step]
    for step in search_steps:
        exit_label = _normalize_exit_label(step.get("instructions"))
        if exit_label:
            return f"從『{exit_label}』走"
    return ""


def _is_bus_step(step: dict) -> bool:
    vehicle_type = str(step.get("vehicle_type", "")).upper()
    line_text = f"{step.get('line_name', '')} {step.get('line_full_name', '')}".upper()
    return "BUS" in vehicle_type or "BUS" in line_text or "公車" in line_text


def _is_metro_step(step: dict) -> bool:
    vehicle_type = str(step.get("vehicle_type", "")).upper()
    return any(kind in vehicle_type for kind in ("SUBWAY", "RAIL", "LIGHT_RAIL", "TRAM"))


def _select_transit_step(steps: list[dict], recommended_mode: str) -> dict | None:
    transit_steps = [step for step in steps if step.get("type") == "TRANSIT"]
    if not transit_steps:
        return None

    if recommended_mode == "bus":
        return next((step for step in transit_steps if _is_bus_step(step)), None)
    if recommended_mode == "metro":
        return next((step for step in transit_steps if _is_metro_step(step)), None)
    return transit_steps[0]


def _bus_route_label(bus_snapshot: dict) -> str | None:
    chosen = bus_snapshot.get("chosen_bus") or {}
    if chosen.get("route_name") and chosen.get("route_name") != "無路線資訊":
        return chosen.get("route_name")
    if chosen.get("subroute_name") and chosen.get("subroute_name") != "無路線資訊":
        return chosen.get("subroute_name")

    for eta in bus_snapshot.get("valid_eta_list", []) or []:
        route_name = eta.get("route_name") or eta.get("subroute_name")
        if route_name and route_name != "無路線資訊":
            return route_name
    return None


def _bus_route_options_text(bus_snapshot: dict, fallback_route: str | None = None) -> str:
    options: list[str] = []
    seen = set()
    arrival_at_stop_min = bus_snapshot.get("arrival_at_stop_min")

    for eta in bus_snapshot.get("valid_eta_list", []) or []:
        route_name = eta.get("route_name") or eta.get("subroute_name")
        if not route_name or route_name == "無路線資訊" or route_name in seen:
            continue
        eta_min = eta.get("eta_min")
        if (
            arrival_at_stop_min is not None
            and eta_min is not None
            and eta_min < arrival_at_stop_min
        ):
            continue
        seen.add(route_name)
        if eta_min is not None:
            options.append(f"{route_name}（約 {eta_min} 分鐘後到站）")
        else:
            options.append(route_name)

    if fallback_route and fallback_route != "公車" and fallback_route not in seen:
        options.insert(0, fallback_route)

    return f"可選路線：{'、'.join(options)}" if options else ""


def _metro_line_from_station_ids(origin_station: dict, destination_station: dict) -> str:
    line_names = {
        "BL": "板南線",
        "BR": "文湖線",
        "R": "淡水信義線",
        "G": "松山新店線",
        "O": "中和新蘆線",
        "Y": "環狀線",
    }

    station_id = str(origin_station.get("id") or destination_station.get("id") or "")
    for prefix in ("BR", "BL", "R", "G", "O", "Y"):
        if station_id.startswith(prefix):
            return line_names[prefix]
    return "捷運"


def _exit_info_from_snapshot(snapshot: dict) -> str:
    suggested_exit = snapshot.get("suggested_exit") or {}
    exit_id = suggested_exit.get("exit_id")
    exit_name = suggested_exit.get("name")
    exit_label = _normalize_exit_label(exit_name) if exit_name else None
    if not exit_label and exit_id:
        exit_label = f"出口 {exit_id}"
    return f"從『{exit_label}』走" if exit_label else ""


def _format_transport_line(plan: dict) -> str:
    best_option = plan["best_option"]
    recommended_mode = plan["recommended_mode"]
    snapshot = best_option.get("snapshot") or {}

    google_detailed = snapshot.get("google_detailed") or {}
    steps = (google_detailed or {}).get("steps", [])
    matched_step = _select_transit_step(steps, recommended_mode)

    if matched_step:
        is_bus = _is_bus_step(matched_step)
        bus_snap = snapshot if recommended_mode == "bus" else snapshot.get("bus_snapshot", {})
        if is_bus:
            line = matched_step.get("line_short_name") or _bus_route_label(bus_snap) or matched_step.get("line_name") or "公車"
        else:
            line = matched_step.get("line_name") or matched_step.get("line_short_name") or "捷運"

        dep_stop = matched_step.get("departure_stop") or "最近站點"
        arr_stop = matched_step.get("arrival_stop") or "目的地站點"
        v_emoji = "🚌" if is_bus else "🚇"
        mode_text = "搭公車" if is_bus else "搭捷運"
        exit_info = "" if is_bus else (_exit_info_from_steps(steps, matched_step) or _exit_info_from_snapshot(snapshot))

        eta_str = ""
        route_options = ""
        if is_bus:
            chosen = bus_snap.get("chosen_bus") or {}
            eta = chosen.get("eta_min")
            if eta is not None:
                eta_str = f"（約 {eta} 分鐘後到站）"
            route_options = _bus_route_options_text(bus_snap, line)

        options_text = f" {route_options}。" if route_options else ""
        return f"{v_emoji} 建議{mode_text}！請搭乘 {line}，於『{dep_stop}』上車，並在『{arr_stop}』下車{(' ' + exit_info) if exit_info else ''}{eta_str}。{options_text}"

    if recommended_mode == "metro":
        metro_snap = snapshot
        station = metro_snap.get("station") or {}
        destination_station = metro_snap.get("destination_station") or {}
        walk_min = metro_snap.get("walk_minutes")
        dep_stop = station.get("name", "最近捷運站")
        arr_stop = destination_station.get("name", "目的地附近捷運站")
        line = _metro_line_from_station_ids(station, destination_station)
        exit_info = _exit_info_from_snapshot(metro_snap)
        walk_str = f"步行約 {walk_min} 分鐘抵達『{dep_stop}』，" if walk_min else ""
        return f"🚇 建議搭捷運！{walk_str}請搭乘 {line}，於『{dep_stop}』上車，並在『{arr_stop}』下車{(' ' + exit_info) if exit_info else ''}。"

    if recommended_mode == "bus":
        bus_snap = snapshot
        first_stop = bus_snap.get("first_stop") or {}
        walk_min = bus_snap.get("walk_minutes")
        chosen = bus_snap.get("chosen_bus") or {}
        stop_name = first_stop.get("stop_name", "最近站牌")
        route = _bus_route_label(bus_snap)
        eta = chosen.get("eta_min")
        route_str = route or "公車"
        eta_str = f"（約 {eta} 分鐘後到站）" if eta is not None else ""
        route_options = _bus_route_options_text(bus_snap, route_str)
        options_text = f" {route_options}。" if route_options else ""
        return f"🚌 建議搭公車！請搭乘 {route_str}，於『{stop_name}』上車，並在『目的地附近站牌』下車{eta_str}。{options_text}"

    return "🚶 建議參考 Google 地圖最快路徑。"


def _get_transport_line(plan: dict) -> str:
    if plan.get("transport_line"):
        return plan["transport_line"]
    plan["transport_line"] = _format_transport_line(plan)
    return plan["transport_line"]


def _format_today_commute_text(plan: dict, header: str = "今日通勤建議：") -> str:
    weather_info = plan["weather_info"]
    weather_buffer = plan["weather_buffer"]
    baseline_minutes = plan["baseline_minutes"]

    # Line 1: 目標抵達
    arrival_line = f"目標抵達：{plan['effective_arrival_time']}"

    # Line 2: 建議出門（含緩衝說明）
    departure_note = ""
    if weather_buffer > 0:
        wx = weather_info.get("weather_text", "")
        if "雨" in wx or "雷" in wx:
            departure_note = f"（已包含雨天 {weather_buffer} 分鐘緩衝）"
        else:
            departure_note = f"（已包含天氣緩衝 {weather_buffer} 分鐘）"
    departure_line = f"建議出門：{plan['final_departure_time']}{departure_note}"

    # Line 3: 通勤時間
    commute_line = f"通勤時間：約 {baseline_minutes} 分鐘"

    # Line 4: 交通方式 (Moved up for visibility)
    transport_line = f"通勤方式：{_get_transport_line(plan)}"

    # Line 5: 天氣
    wx_text = weather_info.get("weather_text", "未知")
    temp_min = weather_info.get("temperature_min")
    temp_max = weather_info.get("temperature_max")
    temp = weather_info.get("temperature")
    pop = weather_info.get("pop")

    if temp is not None:
        temp_str = f"，{temp}°C"
    elif temp_min is not None and temp_max is not None:
        temp_str = f"，{temp_min}-{temp_max}°C"
    else:
        temp_str = ""
    pop_str = f"。降雨機率 {pop}%" if pop is not None else ""
    weather_line = f"今日天氣：{wx_text}{temp_str}{pop_str}"

    return "\n".join([header, arrival_line, departure_line, transport_line, commute_line, weather_line])


def _build_reminder_payload_from_plan(plan: dict) -> dict:
    weather_info = plan["weather_info"]
    weather_buffer = plan["weather_buffer"]
    baseline_minutes = plan["baseline_minutes"]

    # Same concise format as commute text, but with reminder header
    departure_note = ""
    if weather_buffer > 0:
        wx = weather_info.get("weather_text", "")
        if "雨" in wx or "雷" in wx:
            departure_note = f"（已包含雨天 {weather_buffer} 分鐘緩衝）"
        else:
            departure_note = f"（已包含天氣緩衝 {weather_buffer} 分鐘）"

    wx_text = weather_info.get("weather_text", "未知")
    temp_min = weather_info.get("temperature_min")
    temp_max = weather_info.get("temperature_max")
    temp = weather_info.get("temperature")
    pop = weather_info.get("pop")

    if temp is not None:
        temp_str = f"，{temp}°C"
    elif temp_min is not None and temp_max is not None:
        temp_str = f"，{temp_min}-{temp_max}°C"
    else:
        temp_str = ""
    pop_str = f"。降雨機率 {pop}%" if pop is not None else ""

    lines = [
        f"🔔 出門提醒：您預計在 {plan['final_departure_time']} 出門{departure_note}",
        f"📍 通勤方式：{_get_transport_line(plan)}",
        f"📅 目標 {plan['effective_arrival_time']} {plan.get('target_label_text', '抵達目的地')} (通勤約 {baseline_minutes} 分鐘)",
        f"🌤 天氣：{wx_text}{temp_str}{pop_str}",
    ]

    text = "\n".join(lines)
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


_normalize_exit_label = route_formatter.normalize_exit_label
_exit_info_from_steps = route_formatter.exit_info_from_steps
_is_bus_step = route_formatter.is_bus_step
_is_metro_step = route_formatter.is_metro_step
_select_transit_step = route_formatter.select_transit_step
_bus_route_label = route_formatter.bus_route_label
_bus_route_options_text = route_formatter.bus_route_options_text
_metro_line_from_station_ids = route_formatter.metro_line_from_station_ids
_exit_info_from_snapshot = route_formatter.exit_info_from_snapshot
_format_transport_line = route_formatter.format_transport_line
_get_transport_line = route_formatter.get_transport_line
_format_today_commute_text = route_formatter.format_today_commute_text
_build_reminder_payload_from_plan = route_formatter.build_reminder_payload_from_plan



async def build_today_commute_payload(
    db,
    user_id: int,
    target_date: date | None = None,
    force_mode_override: str | None = None,
    header: str = "今日通勤建議：",
    log_plan: bool = True,
):
    plan = await _compute_today_plan(
        db=db,
        user_id=user_id,
        target_date=target_date,
        force_mode_override=force_mode_override,
    )
    if not plan.get("ok"):
        return plan

    plan["text"] = route_formatter.format_today_commute_text(plan, header=header)
    plan["plan_key"] = route_formatter.build_reminder_payload_from_plan(plan).get("plan_key")
    if log_plan:
        try:
            record_commute_plan_log(db, user_id, plan)
        except Exception as e:
            print(f"[commute-log] skipped user_id={user_id} error={e}")
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

    return route_formatter.build_reminder_payload_from_plan(plan)


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
        target_date=target_date or datetime.now(TAIPEI_TZ).date(),
        plan_key=payload["plan_key"],
        frozen_departure_time=payload["departure_time"],
        frozen_reminder_text=payload["text"],
        prepared_at=datetime.now(TAIPEI_TZ),
    )
    return payload
