import hashlib
import re


BUS_VEHICLE_TYPES = {"BUS", "INTERCITY_BUS", "TROLLEYBUS"}
METRO_VEHICLE_TYPES = {"SUBWAY", "METRO_RAIL"}
LIGHT_RAIL_VEHICLE_TYPES = {"LIGHT_RAIL", "TRAM", "MONORAIL"}
RAIL_VEHICLE_TYPES = {
    "RAIL",
    "HEAVY_RAIL",
    "COMMUTER_TRAIN",
    "LONG_DISTANCE_TRAIN",
    "HIGH_SPEED_TRAIN",
    "TRAIN",
}


def _plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", "", str(value)).strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def normalize_exit_label(text: str | None) -> str | None:
    compact = _plain_text(text)
    if not compact:
        return None

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


def vehicle_type(step: dict) -> str:
    return str(step.get("vehicle_type") or "").upper()


def is_bus_step(step: dict) -> bool:
    value = vehicle_type(step)
    if value in BUS_VEHICLE_TYPES or "BUS" in value:
        return True
    line_text = f"{step.get('line_name', '')} {step.get('line_full_name', '')}".upper()
    return "BUS" in line_text or "公車" in line_text


def is_metro_step(step: dict) -> bool:
    value = vehicle_type(step)
    return value in METRO_VEHICLE_TYPES


def is_light_rail_step(step: dict) -> bool:
    return vehicle_type(step) in LIGHT_RAIL_VEHICLE_TYPES


def is_rail_step(step: dict) -> bool:
    return vehicle_type(step) in RAIL_VEHICLE_TYPES


def is_station_exit_step(step: dict) -> bool:
    return is_metro_step(step) or is_light_rail_step(step) or is_rail_step(step)


def transit_steps(steps: list[dict]) -> list[dict]:
    return [step for step in steps if step.get("type") == "TRANSIT"]


def vehicle_category(step: dict) -> str:
    if is_bus_step(step):
        return "bus"
    if is_metro_step(step):
        return "metro"
    if is_light_rail_step(step):
        return "light_rail"
    if is_rail_step(step):
        return "rail"

    value = vehicle_type(step)
    if value == "FERRY":
        return "ferry"
    if value in {"CABLE_CAR", "GONDOLA_LIFT", "FUNICULAR"}:
        return "other_transit"
    return "google_transit"


def route_mode_from_steps(steps: list[dict]) -> str:
    categories = [vehicle_category(step) for step in transit_steps(steps)]
    if not categories:
        return "google_transit"

    unique = set(categories)
    if unique == {"bus"}:
        return "bus"
    if unique == {"metro"}:
        return "metro"
    if unique == {"light_rail"}:
        return "light_rail"
    if unique == {"rail"}:
        return "rail"
    if "bus" in unique and any(kind in unique for kind in ("metro", "light_rail", "rail")):
        return "mixed_transit"
    if len(unique) == 1:
        return next(iter(unique))
    return "mixed_transit"


def select_transit_step(steps: list[dict], recommended_mode: str) -> dict | None:
    candidates = transit_steps(steps)
    if not candidates:
        return None

    if recommended_mode == "bus":
        return next((step for step in candidates if is_bus_step(step)), None)
    if recommended_mode == "metro":
        return next((step for step in candidates if is_metro_step(step)), None)
    if recommended_mode == "rail":
        return next((step for step in candidates if is_rail_step(step)), None)
    if recommended_mode == "light_rail":
        return next((step for step in candidates if is_light_rail_step(step)), None)
    return candidates[0]


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


def normalize_route_name(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).upper()
    text = re.sub(r"\s+", "", text)
    text = text.replace("號公車", "").replace("路線公車", "").replace("公車", "").replace("BUS", "")
    return text


def route_names_match(left: str | None, right: str | None) -> bool:
    normalized_left = normalize_route_name(left)
    normalized_right = normalize_route_name(right)
    return bool(normalized_left and normalized_right and normalized_left == normalized_right)


def _raw_line_label_from_step(step: dict) -> str | None:
    return _plain_text(
        step.get("line_short_name")
        or step.get("line_name")
        or step.get("line_full_name")
        or step.get("vehicle_name")
    )


def bus_display_label(route: str | None) -> str:
    label = _plain_text(route)
    if not label:
        return "Google Maps 未提供公車路線名稱"
    if "公車" in label or "BUS" in label.upper():
        return label
    if any(char.isdigit() for char in label):
        return f"{label}號公車"
    return f"{label}公車"


def line_label_from_step(step: dict) -> str:
    label = _raw_line_label_from_step(step)
    category = vehicle_category(step)
    if not label:
        if category == "bus":
            return "Google Maps 未提供公車路線名稱"
        if category == "metro":
            return "Google Maps 未提供捷運線名稱"
        if category == "light_rail":
            return "Google Maps 未提供輕軌線名稱"
        if category == "rail":
            return "Google Maps 未提供鐵路車次或路線名稱"
        return "Google Maps 未提供路線名稱"
    if category == "bus":
        return bus_display_label(label)
    return label


def bus_arrival_text(route: str | None, eta_min, stop_name: str | None) -> str:
    if eta_min is None or not route or not stop_name:
        return ""

    route_label = bus_display_label(route)
    return f"{route_label}將於 {eta_min} 分鐘後抵達『{stop_name}』。"


def metro_line_from_station_ids(origin_station: dict, destination_station: dict) -> str:
    return "Google Maps 未提供捷運線名稱"


def exit_info_from_snapshot(snapshot: dict) -> str:
    return ""


def _stop_label(value: str | None, missing_label: str) -> str:
    return _plain_text(value) or missing_label


def _exit_instruction_after_step(steps: list[dict], final_transit_step: dict) -> str:
    if not is_station_exit_step(final_transit_step):
        return ""

    try:
        matched_index = steps.index(final_transit_step)
    except ValueError:
        matched_index = -1

    for step in steps[matched_index + 1:]:
        exit_label = normalize_exit_label(step.get("instructions"))
        if exit_label:
            return f" ➔ 走『{exit_label}』出站"
    return " ➔ 依站內指標出站"


def _format_transit_step(step: dict, is_first: bool) -> str:
    category = vehicle_category(step)
    dep_stop = _stop_label(step.get("departure_stop"), "Google Maps 未提供上車站名")
    arr_stop = _stop_label(step.get("arrival_stop"), "Google Maps 未提供下車站名")
    line = line_label_from_step(step)

    if category == "bus":
        if is_first:
            return f"於『{dep_stop}』上車 ➔ 搭乘 {line} ➔ 於『{arr_stop}』下車"
        return f"➔ (轉乘) 步行至『{dep_stop}』改搭乘 {line} ➔ 並於『{arr_stop}』下車"

    if is_first:
        return f"搭乘 {line} ➔ 於『{dep_stop}』上車並在『{arr_stop}』下車"
    return f"➔ (轉乘) 於『{dep_stop}』改乘 {line} ➔ 並於『{arr_stop}』下車"


def route_title_from_steps(steps: list[dict]) -> str:
    mode = route_mode_from_steps(steps)
    if mode == "bus":
        return "🚌 建議搭公車！"
    if mode == "metro":
        return "🚇 建議搭捷運！"
    if mode == "light_rail":
        return "🚈 建議搭輕軌！"
    if mode == "rail":
        return "🚆 建議搭鐵路！"
    if mode == "mixed_transit":
        return "🚉 建議搭大眾運輸！"
    return "🚉 建議參考 Google Maps 大眾運輸路線！"


def format_google_transit_steps(steps: list[dict], bus_snapshot: dict | None = None) -> str:
    transit = transit_steps(steps)
    if not transit:
        return "🚶 Google Maps 目前未提供大眾運輸步驟，請依 Google Maps 路線前往。"

    phrases = []
    for index, step in enumerate(transit):
        phrase = _format_transit_step(step, index == 0)
        next_transit = transit[index + 1] if index + 1 < len(transit) else None
        if is_station_exit_step(step) and (next_transit is None or is_bus_step(next_transit)):
            phrase += _exit_instruction_after_step(steps, step)
        phrases.append(phrase)
    body = " ".join(phrases)

    realtime_text = ""
    bus_snapshot = bus_snapshot or {}
    first_bus = next((step for step in transit if is_bus_step(step)), None)
    if first_bus:
        google_route = _raw_line_label_from_step(first_bus)
        chosen = bus_snapshot.get("chosen_bus") or {}
        chosen_route = chosen.get("route_name") or chosen.get("subroute_name")
        if route_names_match(chosen_route, google_route):
            realtime_text = bus_arrival_text(
                google_route,
                chosen.get("eta_min"),
                first_bus.get("departure_stop"),
            )

    suffix = realtime_text if realtime_text else ""
    return f"{route_title_from_steps(transit)}{body}。{suffix}"


def format_transport_line(plan: dict) -> str:
    best_option = plan["best_option"]
    snapshot = best_option.get("snapshot") or {}

    google_detailed = snapshot.get("google_detailed") or {}
    steps = (google_detailed or {}).get("steps", []) or []
    if steps:
        bus_snapshot = snapshot if best_option.get("mode") == "bus" else snapshot.get("bus_snapshot", {})
        return format_google_transit_steps(steps, bus_snapshot=bus_snapshot)

    return "🚉 Google Maps 目前未提供可用的大眾運輸路線；請開啟 Google Maps 確認最新路線。"


def get_transport_line(plan: dict) -> str:
    if plan.get("transport_line"):
        return plan["transport_line"]
    plan["transport_line"] = format_transport_line(plan)
    return plan["transport_line"]


def format_today_commute_text(plan: dict, header: str = "今日通勤建議：") -> str:
    weather_info = plan["weather_info"]
    weather_buffer = plan["weather_buffer"]
    baseline_minutes = plan["baseline_minutes"]
    target_label = plan.get("target_label_text") or "抵達目的地"

    arrival_line = f"目標{target_label}：{plan['effective_arrival_time']}"

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

    # 完整天氣資訊：氣溫、天氣狀況描述、降雨機率
    wx_text = weather_info.get("weather_text", "未知")
    temp_min = weather_info.get("temperature_min")
    temp_max = weather_info.get("temperature_max")
    temp = weather_info.get("temperature")
    pop = weather_info.get("pop")
    wx_description = weather_info.get("wx_description", "")  # 天氣狀況詳細描述

    if temp is not None:
        temp_str = f"，氣溫 {temp}°C"
    elif temp_min is not None and temp_max is not None:
        temp_str = f"，氣溫 {temp_min}-{temp_max}°C"
    else:
        temp_str = ""
    
    pop_str = f"，降雨機率 {pop}%" if pop is not None else ""
    desc_str = f"（{wx_description}）" if wx_description else ""
    weather_line = f"今日天氣：{wx_text}{temp_str}{pop_str}{desc_str}"

    # 完整交通方式詳細說明（從 API 回傳的 Transit Details 解析）
    detailed_transport = ""
    best_option = plan.get("best_option", {})
    if best_option and isinstance(best_option, dict):
        google_steps = best_option.get("google_steps", [])
        if google_steps and isinstance(google_steps, list):
            transport_details = []
            for step in google_steps:
                step_mode = step.get("travel_mode", "")
                if step_mode == "WALK":
                    duration = step.get("duration", {})
                    if isinstance(duration, dict):
                        duration_text = duration.get("text", "")
                        if duration_text:
                            transport_details.append(f"步行 {duration_text}")
                elif step_mode in ["TRANSIT", "BUS", "SUBWAY", "RAIL"]:
                    transit_details = step.get("transit_details", {})
                    if isinstance(transit_details, dict):
                        line_name = transit_details.get("line", {}).get("name", "")
                        num_stops = transit_details.get("num_stops", 0)
                        departure_stop = transit_details.get("departure_stop", {}).get("name", "")
                        arrival_stop = transit_details.get("arrival_stop", {}).get("name", "")
                        
                        if line_name:
                            if departure_stop and arrival_stop:
                                transport_details.append(f"搭乘 {line_name}（{departure_stop} → {arrival_stop}）")
                            else:
                                transport_details.append(f"搭乘 {line_name}")
            
            if transport_details:
                detailed_transport = "\n詳細路線：" + " → ".join(transport_details)

    return "\n".join([header, arrival_line, departure_line, transport_line, commute_line, weather_line, detailed_transport])


def build_reminder_payload_from_plan(plan: dict) -> dict:
    weather_info = plan["weather_info"]
    weather_buffer = plan["weather_buffer"]
    baseline_minutes = plan["baseline_minutes"]
    target_label = plan.get("target_label_text") or "抵達目的地"

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
        f"📅 目標 {plan['effective_arrival_time']} {target_label} (通勤約 {baseline_minutes} 分鐘)",
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
