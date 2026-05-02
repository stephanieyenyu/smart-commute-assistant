import hashlib
import re


def normalize_exit_label(text: str | None) -> str | None:
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


def exit_info_from_steps(steps: list[dict], matched_step: dict) -> str:
    try:
        matched_index = steps.index(matched_step)
    except ValueError:
        matched_index = 0

    search_steps = steps[matched_index + 1:] + [matched_step]
    for step in search_steps:
        exit_label = normalize_exit_label(step.get("instructions"))
        if exit_label:
            return f"從『{exit_label}』走"
    return ""


def is_bus_step(step: dict) -> bool:
    vehicle_type = str(step.get("vehicle_type", "")).upper()
    line_text = f"{step.get('line_name', '')} {step.get('line_full_name', '')}".upper()
    return "BUS" in vehicle_type or "BUS" in line_text or "公車" in line_text


def is_metro_step(step: dict) -> bool:
    vehicle_type = str(step.get("vehicle_type", "")).upper()
    return any(kind in vehicle_type for kind in ("SUBWAY", "RAIL", "LIGHT_RAIL", "TRAM"))


def select_transit_step(steps: list[dict], recommended_mode: str) -> dict | None:
    transit_steps = [step for step in steps if step.get("type") == "TRANSIT"]
    if not transit_steps:
        return None

    if recommended_mode == "bus":
        return next((step for step in transit_steps if is_bus_step(step)), None)
    if recommended_mode == "metro":
        return next((step for step in transit_steps if is_metro_step(step)), None)
    return transit_steps[0]


def bus_route_label(bus_snapshot: dict) -> str | None:
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


def bus_route_options_text(bus_snapshot: dict, fallback_route: str | None = None) -> str:
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


def bus_arrival_text(route: str | None, eta_min, stop_name: str | None) -> str:
    if eta_min is None or not route or route == "公車" or not stop_name:
        return ""

    route_label = str(route).strip()
    bus_label = f"{route_label}號公車" if any(char.isdigit() for char in route_label) else f"{route_label}公車"
    return f"{bus_label}將於 {eta_min} 分鐘後抵達『{stop_name}』。"


def metro_line_from_station_ids(origin_station: dict, destination_station: dict) -> str:
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


def exit_info_from_snapshot(snapshot: dict) -> str:
    suggested_exit = snapshot.get("suggested_exit") or {}
    exit_id = suggested_exit.get("exit_id")
    exit_name = suggested_exit.get("name")
    exit_label = normalize_exit_label(exit_name) if exit_name else None
    if not exit_label and exit_id:
        exit_label = f"出口 {exit_id}"
    return f"從『{exit_label}』走" if exit_label else ""


def format_transport_line(plan: dict) -> str:
    best_option = plan["best_option"]
    recommended_mode = plan["recommended_mode"]
    snapshot = best_option.get("snapshot") or {}

    google_detailed = snapshot.get("google_detailed") or {}
    steps = (google_detailed or {}).get("steps", [])
    matched_step = select_transit_step(steps, recommended_mode)

    if matched_step:
        is_bus = is_bus_step(matched_step)
        bus_snap = snapshot if recommended_mode == "bus" else snapshot.get("bus_snapshot", {})
        if is_bus:
            line = bus_route_label(bus_snap) or matched_step.get("line_short_name") or matched_step.get("line_name") or "公車"
        else:
            line = matched_step.get("line_name") or matched_step.get("line_short_name") or "捷運"

        dep_stop = matched_step.get("departure_stop") or "最近站點"
        arr_stop = matched_step.get("arrival_stop") or "目的地站點"
        v_emoji = "🚌" if is_bus else "🚇"
        mode_text = "搭公車" if is_bus else "搭捷運"
        exit_info = "" if is_bus else (exit_info_from_steps(steps, matched_step) or exit_info_from_snapshot(snapshot))

        arrival_text = ""
        route_options = ""
        if is_bus:
            chosen = bus_snap.get("chosen_bus") or {}
            eta = chosen.get("eta_min")
            arrival_text = bus_arrival_text(line, eta, dep_stop)
            route_options = bus_route_options_text(bus_snap, line)

        options_text = f"\n{route_options}。" if route_options else ""
        realtime_text = f"{arrival_text}" if arrival_text else ""
        return f"{v_emoji} 建議{mode_text}！請搭乘 {line}，於『{dep_stop}』上車，並在『{arr_stop}』下車{(' ' + exit_info) if exit_info else ''}。{realtime_text}{options_text}"

    if recommended_mode == "metro":
        metro_snap = snapshot
        station = metro_snap.get("station") or {}
        destination_station = metro_snap.get("destination_station") or {}
        walk_min = metro_snap.get("walk_minutes")
        dep_stop = station.get("name", "最近捷運站")
        arr_stop = destination_station.get("name", "目的地附近捷運站")
        line = metro_line_from_station_ids(station, destination_station)
        exit_info = exit_info_from_snapshot(metro_snap)
        walk_str = f"步行約 {walk_min} 分鐘抵達『{dep_stop}』，" if walk_min else ""
        return f"🚇 建議搭捷運！{walk_str}請搭乘 {line}，於『{dep_stop}』上車，並在『{arr_stop}』下車{(' ' + exit_info) if exit_info else ''}。"

    if recommended_mode == "bus":
        bus_snap = snapshot
        first_stop = bus_snap.get("first_stop") or {}
        chosen = bus_snap.get("chosen_bus") or {}
        stop_name = first_stop.get("stop_name", "最近站牌")
        route = bus_route_label(bus_snap)
        eta = chosen.get("eta_min")
        route_str = route or "公車"
        arrival_text = bus_arrival_text(route_str, eta, stop_name)
        route_options = bus_route_options_text(bus_snap, route_str)
        options_text = f"\n{route_options}。" if route_options else ""
        realtime_text = f"{arrival_text}" if arrival_text else ""
        return f"🚌 建議搭公車！請搭乘 {route_str}，於『{stop_name}』上車，並在『目的地附近站牌』下車。{realtime_text}{options_text}"

    return "🚶 建議參考 Google 地圖最快路徑。"


def get_transport_line(plan: dict) -> str:
    if plan.get("transport_line"):
        return plan["transport_line"]
    plan["transport_line"] = format_transport_line(plan)
    return plan["transport_line"]


def format_today_commute_text(plan: dict, header: str = "今日通勤建議：") -> str:
    weather_info = plan["weather_info"]
    weather_buffer = plan["weather_buffer"]
    baseline_minutes = plan["baseline_minutes"]

    arrival_line = f"目標抵達：{plan['effective_arrival_time']}"

    departure_note = ""
    if weather_buffer > 0:
        wx = weather_info.get("weather_text", "")
        if "雨" in wx or "雷" in wx:
            departure_note = f"（已包含雨天 {weather_buffer} 分鐘緩衝）"
        else:
            departure_note = f"（已包含天氣緩衝 {weather_buffer} 分鐘）"
    departure_line = f"建議出門：{plan['final_departure_time']}{departure_note}"

    commute_line = f"通勤時間：約 {baseline_minutes} 分鐘"
    transport_line = f"通勤方式：{get_transport_line(plan)}"

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


def build_reminder_payload_from_plan(plan: dict) -> dict:
    weather_info = plan["weather_info"]
    weather_buffer = plan["weather_buffer"]
    baseline_minutes = plan["baseline_minutes"]

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
        f"📍 通勤方式：{get_transport_line(plan)}",
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
