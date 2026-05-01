import asyncio
import hashlib
import math
import time
from datetime import date, datetime, timedelta

from app.address_utils import extract_city_from_text
from app.google_maps import estimate_transit_minutes, estimate_transit_minutes_detailed
from app.metro_basic import get_nearest_metro_station_async
from app.tdx_bus import (
    get_nearby_stops,
    get_estimated_arrivals,
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
BUS_CACHE_SECONDS = 60
METRO_CACHE_SECONDS = 180

MODE_LABELS = {
    "google_transit": "目前以 Google 大眾運輸估算為主",
    "bus": "今天搭公車",
    "metro": "建議改搭捷運",
    "bus_to_metro": "今天搭公車轉捷運",
}


def combine_date_hhmm(target_date: date, hhmm: str) -> datetime:
    t = datetime.strptime(hhmm, "%H:%M").time()
    return datetime.combine(target_date, t)


async def safe_call(coro):
    try:
        return await coro
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

    if mode == "bus":
        # Formula: Reminder Time = (Bus Arrival at Stop - 3 mins) - Walking Time to Stop
        # Wait, the 'departure_time' we return here is what the scheduler uses.
        # Scheduler notifies 5 mins before 'departure_time' currently.
        # But user wants "3 mins before (Bus Arrival - Walking)".
        # So we should set departure_time = Bus Arrival - Walking.
        # AND we will adjust the scheduler to notify at EXACTLY departure_time - 3 mins.
        
        snapshot = best_option.get("snapshot", {})
        chosen_bus = snapshot.get("chosen_bus", {})
        eta_min = chosen_bus.get("eta_min") if chosen_bus else None
        walk_minutes = snapshot.get("walk_minutes") or 0
        
        if eta_min is not None:
            # Departure DT = Now + (ETA - Walk)
            # But we need a fixed HH:MM for the scheduler.
            # Usually ETA is real-time.
            pass

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

    total_minutes = baseline_minutes + weather_buffer_minutes + mode_extra_minutes
    departure_dt = arrival_dt - timedelta(minutes=total_minutes)

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

        if home_lat is None or home_lng is None:
            return {"available": False, "reason": "coords_missing"}

        cache_key = f"{home_lat}|{home_lng}"
        now = time.time()
        cached = _METRO_CACHE.get(cache_key)
        if cached and now - cached[0] <= METRO_CACHE_SECONDS:
            return cached[1]

        station = await get_nearest_metro_station_async(home_lat, home_lng)
        if not station:
            result = {"available": False, "reason": "no_station"}
            _METRO_CACHE[cache_key] = (now, result)
            return result

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
    
    bus_snapshot, metro_snapshot, google_detailed = await asyncio.gather(
        safe_call(get_bus_realtime_snapshot(profile)),
        safe_call(get_metro_snapshot(profile)),
        safe_call(estimate_transit_minutes_detailed(
            profile.home_lat, profile.home_lng,
            profile.office_lat, profile.office_lng,
            arrival_dt
        )),
    )

    chosen_bus = None
    if bus_snapshot and bus_snapshot.get("available"):
        chosen_bus = bus_snapshot.get("chosen_bus")

    metro_available = bool(metro_snapshot and metro_snapshot.get("available"))

    google_option = {
        "mode": "google_transit",
        "reason": "google_transit",
        "summary": "目前以 Google 大眾運輸建議為主",
        "snapshot": {
            "bus_snapshot": bus_snapshot or {},
            "metro_snapshot": metro_snapshot or {},
            "google_detailed": google_detailed or {},
        },
    }

    bus_option = None
    if bus_snapshot and bus_snapshot.get("available") and chosen_bus:
        first_stop = bus_snapshot.get("first_stop", {}) or {}
        walk_minutes = bus_snapshot.get("walk_minutes")
        eta_min = chosen_bus.get("eta_min")
        bus_label = chosen_bus.get("route_name", "無路線資訊")
        subroute = chosen_bus.get("subroute_name")
        if subroute and subroute != chosen_bus.get("route_name"):
            bus_label += f"({subroute})"

        wait_minutes = max(0, (eta_min or 0) - (bus_snapshot.get("arrival_at_stop_min") or 0))
        bus_option = {
            "mode": "bus",
            "reason": "bus_available",
            "summary": f"可搭公車 {bus_label}，於『{first_stop.get('stop_name', '最近站牌')}』上車。",
            "wait_minutes": wait_minutes,
            "reliability_penalty_minutes": 3,
            "snapshot": bus_snapshot,
        }

    metro_option = None
    if metro_available:
        station = metro_snapshot.get("station", {}) or {}
        walk_minutes = metro_snapshot.get("walk_minutes")
        metro_option = {
            "mode": "metro",
            "reason": "metro_available",
            "summary": f"搭乘捷運，最近站牌『{station.get('name', '無法識別捷運站')}』，步行約 {walk_minutes or '無法估算'} 分鐘。",
            "wait_minutes": 3,
            "transfer_minutes": 2,
            "reliability_penalty_minutes": 1,
            "snapshot": metro_snapshot,
        }

    if mode_override == "shortest":
        return {"best_option": google_option, "selection_source": "manual"}

    if mode_override == "bus":
        if bus_option:
            return {"best_option": bus_option, "selection_source": "manual"}
        return {"best_option": google_option, "selection_source": "fallback_auto"}

    if mode_override == "metro":
        if metro_option:
            return {"best_option": metro_option, "selection_source": "manual"}
        return {"best_option": google_option, "selection_source": "fallback_auto"}

    if mode_override == "bus_to_metro":
        # Simplified for now, can be expanded if we have specific bus_to_metro logic
        return {"best_option": google_option, "selection_source": "manual"}

    # auto priority: Metro > Bus > Google
    if metro_option:
        return {"best_option": metro_option, "selection_source": "auto"}
    if bus_option:
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
        return {"ok": False, "reason": "setup_incomplete", "next_step": next_step}

    effective_arrival_time = profile.preferred_arrival_time
    override = get_override_for_date(db, user_id, target_date)
    used_override = False
    if override and override.target_arrival_time:
        effective_arrival_time = override.target_arrival_time
        used_override = True

    stored_mode_override = get_transport_mode_override(db, user_id, target_date)
    mode_override = force_mode_override if force_mode_override is not None else stored_mode_override

    # 三路並行：天氣、通勤時間估算、公車+捷運快照同時發出
    weather_info, baseline_minutes, option_choice = await asyncio.gather(
        safe_call(get_commute_weather(profile)),
        safe_call(estimate_commute_minutes(profile, target_date, effective_arrival_time)),
        safe_call(choose_commute_option_with_override(
            profile=profile,
            effective_arrival_time=effective_arrival_time,
            weather_buffer_minutes=0,
            target_date=target_date,
            mode_override=mode_override,
        )),
    )
    weather_info = weather_info or {"extra_buffer_minutes": 0, "weather_text": "未知"}
    baseline_minutes = baseline_minutes or DEFAULT_COMMUTE_MINUTES
    weather_buffer = weather_info.get("extra_buffer_minutes", 0)
    option_choice = option_choice or {"best_option": {"mode": "google_transit"}, "selection_source": "auto"}
    best_option = option_choice.get("best_option", {}) or {}
    selection_source = option_choice.get("selection_source", "auto")

    departure_calc = await calculate_departure_time_by_mode_fast(
        target_date=target_date,
        effective_arrival_time=effective_arrival_time,
        baseline_minutes=baseline_minutes,
        weather_buffer_minutes=weather_buffer,
        best_option=best_option,
    )

    final_departure_time = departure_calc["departure_time"]
    recommended_mode = best_option.get("mode", "google_transit")

    note = (
        f"已套用今天覆蓋到公司時間：{effective_arrival_time}"
        if used_override
        else f"目前使用預設到公司時間：{effective_arrival_time}"
    )

    return {
        "ok": True,
        "profile": profile,
        "target_date": target_date,
        "effective_arrival_time": effective_arrival_time,
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


def _format_transport_line(plan: dict) -> str:
    best_option = plan["best_option"]
    recommended_mode = plan["recommended_mode"]
    snapshot = best_option.get("snapshot") or {}

    # If it's a specific mode, try to find a matching transit step from Google for more details (like get-off station)
    google_detailed = snapshot.get("google_detailed") if recommended_mode != "google_transit" else snapshot.get("google_detailed")
    if not google_detailed and recommended_mode == "google_transit":
        google_detailed = snapshot.get("google_detailed")

    steps = (google_detailed or {}).get("steps", [])
    matched_step = None
    if recommended_mode == "bus":
        # Search for any step that looks like a bus
        matched_step = next((s for s in steps if "BUS" in str(s.get("vehicle_type")).upper() or "公車" in str(s.get("line_name"))), None)
    elif recommended_mode == "metro":
        # Search for any step that looks like a subway/metro/rail
        matched_step = next((s for s in steps if any(kw in str(s.get("vehicle_type")).upper() for kw in ["SUBWAY", "METRO", "RAIL", "TRAM"]) or "捷運" in str(s.get("line_name"))), None)

    if matched_step:
        line = matched_step.get("line_name") or "大眾運輸"
        dep_stop = matched_step.get("departure_stop") or "最近站點"
        arr_stop = matched_step.get("arrival_stop") or "目的地站點"
        v_emoji = "🚌" if recommended_mode == "bus" else "🚇"
        mode_text = "搭公車" if recommended_mode == "bus" else "搭捷運"
        
        # Try to find exit info in instructions
        exit_info = ""
        instr = matched_step.get("instructions") or ""
        import re
        exit_match = re.search(r"(出口\s*\d+|Exit\s*\d+)", instr)
        if exit_match:
            exit_info = f"從『{exit_match.group(1)}』走"

        # For bus, we still want the TDX real-time ETA if available
        eta_str = ""
        if recommended_mode == "bus":
            bus_snap = snapshot
            chosen = bus_snap.get("chosen_bus") or {}
            eta = chosen.get("eta_min")
            if eta is not None:
                eta_str = f"（約 {eta} 分鐘後到站）"

        return f"{v_emoji} 建議{mode_text}！請搭乘 {line}，於『{dep_stop}』上車，並在『{arr_stop}』下車{(' ' + exit_info) if exit_info else ''}{eta_str}。"

    if recommended_mode == "metro":
        metro_snap = snapshot
        station = metro_snap.get("station") or {}
        walk_min = metro_snap.get("walk_minutes")
        name = station.get("name", "最近捷運站")
        walk_str = f"步行約 {walk_min} 分鐘" if walk_min else "步行距離未知"
        return f"🚇 建議搭捷運！{walk_str}抵達『{name}』，於目的地車站下車。"

    if recommended_mode == "bus":
        bus_snap = snapshot
        first_stop = bus_snap.get("first_stop") or {}
        walk_min = bus_snap.get("walk_minutes")
        chosen = bus_snap.get("chosen_bus") or {}
        stop_name = first_stop.get("stop_name", "最近站牌")
        route = chosen.get("route_name", "")
        eta = chosen.get("eta_min")
        walk_str = f"步行約 {walk_min} 分鐘" if walk_min else "步行距離未知"
        route_str = f"搭乘 {route} 路線" if route else "搭乘公車"
        eta_str = f"（約 {eta} 分鐘後到站）" if eta is not None else ""
        return f"🚌 建議搭公車！{walk_str}抵達『{stop_name}』，{route_str}{eta_str}。"

    # google_transit detailed result
    google_detailed = snapshot.get("google_detailed") or {}
    steps = google_detailed.get("steps", [])
    if steps:
        # Take the first transit step for a more detailed summary
        transit_step = next((s for s in steps if s["type"] == "TRANSIT"), None)
        if transit_step:
            line = transit_step["line_name"]
            dep_stop = transit_step["departure_stop"]
            arr_stop = transit_step["arrival_stop"]
            v_type = transit_step["vehicle_type"]
            v_emoji = "🚌" if "BUS" in str(v_type).upper() else "🚇"
            return f"{v_emoji} 建議搭乘 {line}，於『{dep_stop}』上車，並在『{arr_stop}』下車。"

    return "🚶 建議參考 Google 地圖最快路徑。"


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
    transport_line = f"通勤方式：{_format_transport_line(plan)}"

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
        f"📍 通勤方式：{_format_transport_line(plan)}",
        f"📅 目標 {plan['effective_arrival_time']} 抵達公司 (通勤約 {baseline_minutes} 分鐘)",
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