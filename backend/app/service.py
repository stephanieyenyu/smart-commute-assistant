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

        walk_minutes = None
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

        if walk_minutes is None:
            dist_m = first_stop.get("distance_m")
            if dist_m is not None:
                walk_minutes = max(1, round(dist_m / 80))

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

        walk_minutes = None
        station_lat = station.get("lat")
        station_lng = station.get("lng")

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

        distance_km = None
        if station_lat is not None and station_lng is not None:
            try:
                distance_km = _haversine_km(home_lat, home_lng, station_lat, station_lng)
            except Exception:
                distance_km = None

        if walk_minutes is None and distance_km is not None:
            walk_minutes = max(1, round(distance_km * 1000 / 80))

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
    mode_override: str | None = None,
):
    bus_snapshot, metro_snapshot = await asyncio.gather(
        safe_call(get_bus_realtime_snapshot(profile)),
        safe_call(get_metro_snapshot(profile)),
    )

    chosen_bus = None
    if bus_snapshot and bus_snapshot.get("available"):
        chosen_bus = bus_snapshot.get("chosen_bus")

    metro_available = bool(metro_snapshot and metro_snapshot.get("available"))

    google_option = {
        "mode": "google_transit",
        "reason": "google_transit",
        "summary": "目前以 Google 大眾運輸估算為主",
        "snapshot": {
            "bus_snapshot": bus_snapshot or {},
            "metro_snapshot": metro_snapshot or {},
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
            "summary": f"可搭上公車 {bus_label}，最近站牌 {first_stop.get('stop_name', '無法識別站牌')}，步行約 {walk_minutes or '無法估算'} 分鐘，等車約 {wait_minutes} 分鐘。",
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
            "summary": f"可改搭捷運，最近捷運站 {station.get('name', '無法識別捷運站')}，步行約 {walk_minutes or '無法估算'} 分鐘。",
            "wait_minutes": 3,
            "transfer_minutes": 2,
            "reliability_penalty_minutes": 1,
            "snapshot": metro_snapshot,
        }

    if mode_override == "bus":
        if bus_option:
            return {"best_option": bus_option, "selection_source": "manual"}
        if metro_option:
            return {"best_option": metro_option, "selection_source": "fallback_auto"}
        return {"best_option": google_option, "selection_source": "fallback_auto"}

    if mode_override == "metro":
        if metro_option:
            return {"best_option": metro_option, "selection_source": "manual"}
        if bus_option:
            return {"best_option": bus_option, "selection_source": "fallback_auto"}
        return {"best_option": google_option, "selection_source": "fallback_auto"}

    if mode_override == "bus_to_metro":
        if bus_option and metro_option:
            combo_option = {
                "mode": "bus_to_metro",
                "reason": "bus_to_metro_available",
                "summary": f"可先搭公車再轉捷運，轉乘捷運站 {metro_snapshot.get('station', {}).get('name', '無法識別捷運站')}。",
                "wait_minutes": 3,
                "transfer_minutes": 5,
                "reliability_penalty_minutes": 2,
                "snapshot": {
                    "bus_snapshot": bus_snapshot,
                    "metro_snapshot": metro_snapshot,
                },
            }
            return {"best_option": combo_option, "selection_source": "manual"}
        if metro_option:
            return {"best_option": metro_option, "selection_source": "fallback_auto"}
        if bus_option:
            return {"best_option": bus_option, "selection_source": "fallback_auto"}
        return {"best_option": google_option, "selection_source": "fallback_auto"}

    # auto
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

    weather_info, baseline_minutes = await asyncio.gather(
        get_commute_weather(profile),
        estimate_commute_minutes(profile, target_date, effective_arrival_time),
    )
    weather_buffer = weather_info.get("extra_buffer_minutes", 0)

    stored_mode_override = get_transport_mode_override(db, user_id, target_date)
    mode_override = force_mode_override if force_mode_override is not None else stored_mode_override

    option_choice = await choose_commute_option_with_override(
        profile=profile,
        effective_arrival_time=effective_arrival_time,
        weather_buffer_minutes=weather_buffer,
        mode_override=mode_override,
    )
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


def _format_today_commute_text(plan: dict, header: str = "今日通勤建議：") -> str:
    weather_info = plan["weather_info"]
    best_option = plan["best_option"]
    recommended_mode = plan["recommended_mode"]

    lines = [
        header,
        f"到公司時間：{plan['effective_arrival_time']}",
        f"建議出門時間：{plan['final_departure_time']}",
        f"建議方式：{MODE_LABELS.get(recommended_mode, '目前以 Google 大眾運輸估算為主')}",
        f"預估通勤時間：{plan['baseline_minutes']} 分鐘",
        f"今日天氣：{_weather_line(weather_info)}",
        f"降雨機率：{_rain_line(weather_info)}",
        f"說明：{plan['note']}",
    ]

    if recommended_mode in {"bus", "metro", "bus_to_metro"}:
        lines.append(f"模式判斷：{best_option.get('reason', 'google_transit')}")
        lines.append(f"方案摘要：{best_option.get('summary', '目前以 Google 大眾運輸估算為主')}")

    lines.append(f"提醒：{_buffer_note(plan['weather_buffer'])}")
    lines.append("")

    if recommended_mode == "google_transit":
        snapshot = best_option.get("snapshot", {}) or {}
        bus_snapshot = snapshot.get("bus_snapshot") or {}
        metro_snapshot = snapshot.get("metro_snapshot") or {}
        if bus_snapshot.get("available"):
            lines.extend(_build_bus_detail_lines(bus_snapshot))
        elif metro_snapshot.get("available"):
            lines.extend(_build_metro_detail_lines(metro_snapshot))
        else:
            lines.append("無即時資訊")
    elif recommended_mode == "bus":
        lines.extend(_build_bus_detail_lines(best_option.get("snapshot", {}) or {}))
    elif recommended_mode == "metro":
        lines.extend(_build_metro_detail_lines(best_option.get("snapshot", {}) or {}))
    elif recommended_mode == "bus_to_metro":
        snapshot = best_option.get("snapshot", {}) or {}
        bus_snapshot = snapshot.get("bus_snapshot") or {}
        metro_snapshot = snapshot.get("metro_snapshot") or {}
        if bus_snapshot.get("available"):
            lines.extend(_build_bus_detail_lines(bus_snapshot))
        if metro_snapshot.get("available"):
            lines.append("")
            lines.extend(_build_metro_detail_lines(metro_snapshot))

    return "\n".join(lines)


def _build_reminder_payload_from_plan(plan: dict) -> dict:
    weather_info = plan["weather_info"]
    best_option = plan["best_option"]
    recommended_mode = plan["recommended_mode"]

    lines = [
        "出門提醒：",
        f"你今天建議於 {plan['final_departure_time']} 出門",
        f"建議方式：{MODE_LABELS.get(recommended_mode, '目前以 Google 大眾運輸估算為主')}",
        f"到公司時間：{plan['effective_arrival_time']}",
        f"方案摘要：{best_option.get('summary', '目前以 Google 大眾運輸估算為主')}",
        f"出門時間依據：{plan['departure_calc'].get('mode_note', '目前以 Google 大眾運輸估算為主')}",
        f"今天天氣：{_weather_line(weather_info)}，降雨機率 {_rain_line(weather_info)}",
    ]

    if recommended_mode == "google_transit":
        snapshot = best_option.get("snapshot", {}) or {}
        bus_snapshot = snapshot.get("bus_snapshot") or {}
        metro_snapshot = snapshot.get("metro_snapshot") or {}
        if bus_snapshot.get("available"):
            lines.extend(_build_bus_detail_lines(bus_snapshot))
        elif metro_snapshot.get("available"):
            lines.extend(_build_metro_detail_lines(metro_snapshot))
        else:
            lines.append("無即時資訊")
    elif recommended_mode == "bus":
        lines.extend(_build_bus_detail_lines(best_option.get("snapshot", {}) or {}))
    elif recommended_mode == "metro":
        lines.extend(_build_metro_detail_lines(best_option.get("snapshot", {}) or {}))
    elif recommended_mode == "bus_to_metro":
        snapshot = best_option.get("snapshot", {}) or {}
        bus_snapshot = snapshot.get("bus_snapshot") or {}
        metro_snapshot = snapshot.get("metro_snapshot") or {}
        if bus_snapshot.get("available"):
            lines.extend(_build_bus_detail_lines(bus_snapshot))
        if metro_snapshot.get("available"):
            lines.extend(_build_metro_detail_lines(metro_snapshot))

    text = "\n".join(lines)
    plan_key = hashlib.sha1(
        f"{plan['target_date'].isoformat()}|{plan['effective_arrival_time']}|{plan['final_departure_time']}|{plan['mode_override']}|{text}".encode("utf-8")
    ).hexdigest()

    return {
        "ok": True,
        "plan_key": plan_key,
        "departure_time": plan["final_departure_time"],
        "recommended_mode": recommended_mode,
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