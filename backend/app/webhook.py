import base64
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import LINE_CHANNEL_SECRET
from app.line_client import reply_text
from app.db import SessionLocal
from app.crud import (
    get_or_create_user,
    get_or_create_profile,
    get_profile,
    get_override_for_date,
    set_pending_field,
    update_profile_field,
    update_address_and_coords,
    upsert_override,
    get_next_setup_step,
    reset_profile_for_reconfigure,
    upsert_transport_mode_override,
    get_transport_mode_override,
)
from app.google_maps import geocode_address
from app.weather import get_commute_weather
from app.service import (
    calculate_departure_time,
    estimate_commute_minutes,
    get_bus_realtime_snapshot,
    choose_departure_time_with_realtime_bus,
    get_metro_snapshot,
    choose_commute_option_with_override,
)

router = APIRouter()

FIELD_LABELS = {
    "home_location": "住家位置",
    "office_location": "公司位置",
    "preferred_arrival_time": "到公司時間",
    "override_tomorrow_arrival_time": "明天到公司時間",
}

FIELD_PROMPTS = {
    "home_location": (
        "請傳送住家位置。\n"
        "方法：\n"
        "1. 若你有做快捷鍵，請點「傳送住家位置」或「傳送位置」\n"
        "2. 或直接使用 LINE 的位置傳送功能送出住家位置\n"
        "3. 若暫時不方便傳位置，也可以直接輸入完整住家地址"
    ),
    "office_location": (
        "請傳送公司位置。\n"
        "方法：\n"
        "1. 若你有做快捷鍵，請點「傳送公司位置」或「傳送位置」\n"
        "2. 或直接使用 LINE 的位置傳送功能送出公司位置\n"
        "3. 若暫時不方便傳位置，也可以直接輸入完整公司地址"
    ),
    "preferred_arrival_time": "請直接輸入到公司時間，格式 HH:MM，例如 08:30",
    "override_tomorrow_arrival_time": "請直接輸入明天新的到公司時間，格式 HH:MM，例如 09:30",
}

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
    "bus_unavailable": "目前抓不到公車資訊",
    "no_catchable_bus": "目前這班公車大多趕不上",
    "no_catchable_bus_but_no_metro": "公車不容易趕上，捷運資訊也不足",
    "both_unavailable": "目前交通資訊不足，先用基本估算",
    "no_available_options": "目前沒有合適的交通方案",
    "fallback_baseline_only": "目前先用基本通勤時間估算",
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
    "auto": "系統自動幫你選擇",
    "manual": "已依照你的指定方式計算",
    "fallback_auto": "你指定的方式目前不適用，已改用系統自動判斷",
}

def normalize_user_text(text: str) -> str:
    if not text:
        return ""
    return (
        text.strip()
        .replace("\u3000", " ")
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
    )


COMMAND_ALIASES = {
    "view_settings": {"查看設定"},
    "today_commute": {"今天通勤建議", "今日通勤建議", "通勤建議"},
    "tomorrow_departure": {"明天幾點出門"},
    "edit_tomorrow_arrival": {"修改明天到公司時間"},
    "reset": {"重新設定"},
    "send_home_location": {"傳送住家位置", "設定住家位置"},
    "send_office_location": {"傳送公司位置", "設定公司位置"},
    "test_bus": {"測試公車", "公車測試"},
    "test_metro": {"測試捷運", "捷運測試"},
    "set_mode_auto": {"今天自動判斷", "今天交通自動"},
    "set_mode_bus": {"今天搭公車", "今天坐公車"},
    "set_mode_metro": {"今天搭捷運", "今天坐捷運"},
    "set_mode_bus_to_metro": {"今天搭公車轉捷運", "今天公車轉捷運"},
    "view_mode_today": {"查看今天交通方式"},
}


def verify_line_signature(body: bytes, x_line_signature: str | None) -> bool:
    if not x_line_signature:
        return False
    if not LINE_CHANNEL_SECRET:
        return False

    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()

    signature = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(signature, x_line_signature)


def format_profile_text(profile, tomorrow_override_time: str | None = None) -> str:
    home_address = profile.home_address or "尚未設定"
    office_address = profile.office_address or "尚未設定"
    preferred_arrival_time = profile.preferred_arrival_time or "尚未設定"

    text = (
        "您目前設定如下：\n"
        f"住家位置：{home_address}\n"
        f"公司位置：{office_address}\n"
        f"到公司時間：{preferred_arrival_time}"
    )

    if tomorrow_override_time:
        text += f"\n明天覆蓋到公司時間：{tomorrow_override_time}"

    return text


def validate_pending_input(field_name: str, user_text: str):
    if field_name in ["preferred_arrival_time", "override_tomorrow_arrival_time"]:
        value = user_text.strip()
        try:
            datetime.strptime(value, "%H:%M")
            return value, None
        except ValueError:
            return None, "時間格式錯誤，請輸入 HH:MM，例如 08:30"

    return None, "未知的設定欄位。"


def get_effective_arrival_time(db, user_id: int, target_date: date, default_arrival_time: str):
    override = get_override_for_date(db, user_id, target_date)

    if override and override.target_arrival_time:
        return override.target_arrival_time, True

    return default_arrival_time, False


def ensure_profile_defaults_for_calc(db, user_id: int, profile):
    if profile.walk_to_bus_stop_min is None:
        update_profile_field(db, user_id, "walk_to_bus_stop_min", 0)
        profile = get_profile(db, user_id)
    return profile


def infer_city_from_text(text: str | None) -> str | None:
    if not text:
        return None

    mapping = {
        "台北市": "臺北市",
        "臺北市": "臺北市",
        "新北市": "新北市",
        "桃園市": "桃園市",
        "台中市": "臺中市",
        "臺中市": "臺中市",
        "台南市": "臺南市",
        "臺南市": "臺南市",
        "高雄市": "高雄市",
        "基隆市": "基隆市",
        "新竹市": "新竹市",
        "嘉義市": "嘉義市",
        "新竹縣": "新竹縣",
        "苗栗縣": "苗栗縣",
        "彰化縣": "彰化縣",
        "南投縣": "南投縣",
        "雲林縣": "雲林縣",
        "嘉義縣": "嘉義縣",
        "屏東縣": "屏東縣",
        "宜蘭縣": "宜蘭縣",
        "花蓮縣": "花蓮縣",
        "台東縣": "臺東縣",
        "臺東縣": "臺東縣",
        "澎湖縣": "澎湖縣",
        "金門縣": "金門縣",
        "連江縣": "連江縣",
    }

    for key, value in mapping.items():
        if key in text:
            return value

    return None


async def save_location_or_address(
    db,
    user_id: int,
    field_prefix: str,
    raw_address: str,
    lat: float | None = None,
    lng: float | None = None,
):
    geocode_result = None

    try:
        geocode_result = await geocode_address(raw_address)
    except Exception as e:
        print(f"[location/address] geocode failed: {e}")

    normalized_address = raw_address
    city = infer_city_from_text(raw_address)
    township = None
    place_name = None

    if geocode_result:
        normalized_address = geocode_result.get("formatted_address") or raw_address
        city = geocode_result.get("city") or city
        township = geocode_result.get("township")
        place_name = geocode_result.get("place_name")

        if lat is None:
            lat = geocode_result.get("lat")
        if lng is None:
            lng = geocode_result.get("lng")

    update_address_and_coords(
        db,
        user_id,
        field_prefix,
        normalized_address,
        lat,
        lng,
        city,
        township,
        place_name,
    )


@router.post("/webhooks/line")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None),
):
    body = await request.body()

    if not verify_line_signature(body, x_line_signature):
        print(
            f"[signature] invalid | has_header={bool(x_line_signature)} "
            f"| secret_len={len(LINE_CHANNEL_SECRET)} | body_len={len(body)}"
        )
        raise HTTPException(status_code=400, detail="Invalid signature")

    payload = json.loads(body.decode("utf-8"))
    events = payload.get("events", [])

    db = SessionLocal()

    try:
        for event in events:
            event_type = event.get("type")
            source = event.get("source", {})
            line_user_id = source.get("userId")

            if not line_user_id:
                continue

            user = get_or_create_user(db, line_user_id=line_user_id)
            get_or_create_profile(db, user.id)

            if event_type == "follow":
                set_pending_field(db, user.id, "home_location")
                reply_token = event.get("replyToken")
                await reply_text(
                    reply_token,
                    "歡迎使用智慧通勤助理。\n"
                    f"{FIELD_PROMPTS['home_location']}"
                )
                continue

            if event_type != "message":
                continue

            message = event.get("message", {})
            message_type = message.get("type")
            reply_token = event.get("replyToken")

            if message_type == "location":
                profile = get_profile(db, user.id)
                current_step = profile.pending_field or get_next_setup_step(profile)

                lat = message.get("latitude")
                lng = message.get("longitude")
                title = message.get("title")
                address = message.get("address")
                raw_address = address or title or "未命名位置"

                if current_step == "home_location":
                    await save_location_or_address(
                        db,
                        user.id,
                        "home",
                        raw_address,
                        lat=lat,
                        lng=lng,
                    )
                    set_pending_field(db, user.id, "office_location")
                    await reply_text(
                        reply_token,
                        "已儲存住家位置。\n"
                        f"{FIELD_PROMPTS['office_location']}"
                    )
                    continue

                if current_step == "office_location":
                    await save_location_or_address(
                        db,
                        user.id,
                        "office",
                        raw_address,
                        lat=lat,
                        lng=lng,
                    )
                    update_profile_field(db, user.id, "walk_to_bus_stop_min", 0)
                    set_pending_field(db, user.id, "preferred_arrival_time")

                    check_profile = get_profile(db, user.id)
                    print(
                        f"[setup] office saved | office_address={check_profile.office_address} | "
                        f"pending_field={check_profile.pending_field}"
                    )

                    await reply_text(
                        reply_token,
                        "已儲存公司位置。\n"
                        f"{FIELD_PROMPTS['preferred_arrival_time']}"
                    )
                    continue

                await reply_text(
                    reply_token,
                    "目前不需要位置資訊。\n"
                    "可傳送：查看設定、今天通勤建議、明天幾點出門、修改明天到公司時間、重新設定、測試公車、測試捷運、今天自動判斷、今天搭公車、今天搭捷運、今天搭公車轉捷運、查看今天交通方式"
                )
                continue

            if message_type != "text":
                continue

            user_text = message.get("text", "").strip()
            command_text = normalize_user_text(user_text)
            print(f"[debug] user_text={repr(user_text)} | command_text={repr(command_text)}")

            today_date = date.today()
            tomorrow_date = today_date + timedelta(days=1)

            tomorrow_override = get_override_for_date(db, user.id, tomorrow_date)
            tomorrow_override_time = (
                tomorrow_override.target_arrival_time if tomorrow_override else None
            )

            if command_text in COMMAND_ALIASES["send_home_location"]:
                set_pending_field(db, user.id, "home_location")
                await reply_text(reply_token, FIELD_PROMPTS["home_location"])
                continue

            if command_text in COMMAND_ALIASES["send_office_location"]:
                set_pending_field(db, user.id, "office_location")
                await reply_text(reply_token, FIELD_PROMPTS["office_location"])
                continue

            if command_text in COMMAND_ALIASES["reset"]:
                reset_profile_for_reconfigure(db, user.id)
                await reply_text(
                    reply_token,
                    "好的，現在開始重新設定。\n"
                    f"{FIELD_PROMPTS['home_location']}"
                )
                continue

            if command_text in COMMAND_ALIASES["view_settings"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is None:
                    set_pending_field(db, user.id, None)
                    await reply_text(
                        reply_token,
                        format_profile_text(profile, tomorrow_override_time),
                    )
                    continue

                set_pending_field(db, user.id, next_step)
                await reply_text(
                    reply_token,
                    f"{format_profile_text(profile, tomorrow_override_time)}\n\n"
                    "尚未完成設定。\n"
                    f"{FIELD_PROMPTS[next_step]}"
                )
                continue

            if command_text in COMMAND_ALIASES["set_mode_auto"]:
                upsert_transport_mode_override(db, user.id, today_date, "auto")
                await reply_text(reply_token, "已設定今天交通方式為：自動判斷")
                continue

            if command_text in COMMAND_ALIASES["set_mode_bus"]:
                upsert_transport_mode_override(db, user.id, today_date, "bus")
                await reply_text(reply_token, "已設定今天交通方式為：公車優先")
                continue

            if command_text in COMMAND_ALIASES["set_mode_metro"]:
                upsert_transport_mode_override(db, user.id, today_date, "metro")
                await reply_text(reply_token, "已設定今天交通方式為：捷運優先")
                continue

            if command_text in COMMAND_ALIASES["set_mode_bus_to_metro"]:
                upsert_transport_mode_override(db, user.id, today_date, "bus_to_metro")
                await reply_text(reply_token, "已設定今天交通方式為：公車轉捷運")
                continue

            if command_text in COMMAND_ALIASES["view_mode_today"]:
                current_mode = get_transport_mode_override(db, user.id, today_date) or "auto"
                await reply_text(
                    reply_token,
                    f"今天交通方式設定：{TRANSPORT_MODE_NAME_MAP.get(current_mode, current_mode)}"
                )
                continue

            if command_text in COMMAND_ALIASES["test_bus"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(
                        reply_token,
                        "請先完成基本設定。\n"
                        f"{FIELD_PROMPTS[next_step]}"
                    )
                    continue

                try:
                    bus_snapshot = await get_bus_realtime_snapshot(profile)

                    if not bus_snapshot.get("available"):
                        await reply_text(
                            reply_token,
                            "目前無法取得附近公車資訊。"
                        )
                        continue

                    first_stop = bus_snapshot["first_stop"]
                    nearby_stops = bus_snapshot.get("nearby_stops", [])
                    walk_minutes = bus_snapshot.get("walk_minutes")
                    arrival_at_stop_min = bus_snapshot.get("arrival_at_stop_min")
                    chosen_bus = bus_snapshot.get("chosen_bus")
                    valid_eta_list = bus_snapshot.get("valid_eta_list", [])

                    message_lines = ["附近站牌測試："]
                    for idx, stop in enumerate(nearby_stops[:5], start=1):
                        message_lines.append(
                            f"{idx}. {stop['stop_name']} | stop_id={stop['stop_id']} | uid={stop['stop_uid']}"
                        )

                    message_lines.append("")
                    message_lines.append(f"最近站牌：{first_stop['stop_name']}")

                    if walk_minutes is not None:
                        message_lines.append(f"步行到站牌時間：約 {walk_minutes} 分鐘")
                    else:
                        message_lines.append("步行到站牌時間：無法估算")

                    message_lines.append("安全緩衝：1 分鐘")

                    if arrival_at_stop_min is not None:
                        message_lines.append(f"預估抵達站牌時間：約 {arrival_at_stop_min} 分鐘後")

                    message_lines.append("")
                    message_lines.append(f"最近站牌 ETA 測試：{first_stop['stop_name']}")

                    for eta in valid_eta_list[:5]:
                        route_label = eta["route_name"]
                        if eta.get("subroute_name") and eta["subroute_name"] != eta["route_name"]:
                            route_label += f"({eta['subroute_name']})"

                        eta_text = (
                            f"{eta['eta_min']} 分鐘"
                            if eta["eta_min"] is not None
                            else "無即時時間"
                        )

                        message_lines.append(f"{route_label}：{eta_text} | direction={eta.get('direction')}")

                    message_lines.append("")

                    if chosen_bus:
                        chosen_label = chosen_bus["route_name"]
                        if chosen_bus.get("subroute_name") and chosen_bus["subroute_name"] != chosen_bus["route_name"]:
                            chosen_label += f"({chosen_bus['subroute_name']})"

                        message_lines.append(
                            f"你目前最有機會趕上的車：{chosen_label}，約 {chosen_bus['eta_min']} 分鐘後到站"
                        )
                    else:
                        if arrival_at_stop_min is not None and valid_eta_list:
                            first_eta = valid_eta_list[0].get("eta_min")
                            first_route = valid_eta_list[0].get("route_name", "未知路線")

                            if first_eta is not None:
                                message_lines.append(
                                    f"目前最近一班公車 {first_route} 約 {first_eta} 分鐘後到站，"
                                    f"但你預估 {arrival_at_stop_min} 分鐘後才能抵達站牌，這班車大概率趕不上。"
                                )
                            else:
                                message_lines.append("目前清單中沒有明確能趕上的即時班次")
                        else:
                            message_lines.append("目前清單中沒有明確能趕上的即時班次")

                    await reply_text(
                        reply_token,
                        "\n".join(message_lines)
                    )
                    continue

                except Exception as e:
                    print(f"[bus-test] failed: {e}")
                    await reply_text(
                        reply_token,
                        f"測試公車失敗：{e}"
                    )
                    continue

            if command_text in COMMAND_ALIASES["test_metro"]:
                print("[debug] entered 測試捷運 branch")

                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(
                        reply_token,
                        "請先完成基本設定。\n"
                        f"{FIELD_PROMPTS[next_step]}"
                    )
                    continue

                try:
                    metro_snapshot = await get_metro_snapshot(profile)

                    if not metro_snapshot.get("available"):
                        await reply_text(
                            reply_token,
                            "目前無法取得附近捷運資訊。"
                        )
                        continue

                    station = metro_snapshot["station"]
                    walk_minutes = metro_snapshot.get("walk_minutes")
                    distance_km = metro_snapshot.get("distance_km")

                    lines = [
                        "捷運測試：",
                        f"最近捷運站：{station['name']}",
                    ]

                    if distance_km is not None:
                        lines.append(f"直線距離：約 {distance_km:.2f} 公里")

                    if walk_minutes is not None:
                        lines.append(f"步行到捷運站：約 {walk_minutes} 分鐘")
                    else:
                        lines.append("步行到捷運站：無法估算")

                    await reply_text(
                        reply_token,
                        "\n".join(lines)
                    )
                    continue

                except Exception as e:
                    print(f"[metro-test] failed: {e}")
                    await reply_text(
                        reply_token,
                        f"測試捷運失敗：{e}"
                    )
                    continue

            if command_text in COMMAND_ALIASES["today_commute"]:
                print("[debug] entered 今天通勤建議 branch")

                try:
                    profile = get_profile(db, user.id)
                    next_step = get_next_setup_step(profile)

                    if next_step is not None:
                        set_pending_field(db, user.id, next_step)
                        await reply_text(
                            reply_token,
                            "請先完成基本設定。\n"
                            f"{FIELD_PROMPTS[next_step]}"
                        )
                        continue

                    profile = ensure_profile_defaults_for_calc(db, user.id, profile)

                    effective_arrival_time, used_override = get_effective_arrival_time(
                        db,
                        user.id,
                        today_date,
                        profile.preferred_arrival_time,
                    )

                    weather_info = await get_commute_weather(profile)
                    weather_buffer = weather_info.get("extra_buffer_minutes", 0)

                    baseline_minutes = await estimate_commute_minutes(
                        profile,
                        today_date,
                        effective_arrival_time,
                    )

                    departure_time = await calculate_departure_time(
                        profile,
                        today_date,
                        effective_arrival_time,
                        weather_buffer_minutes=weather_buffer,
                    )

                    final_departure_time = departure_time
                    recommended_mode = "unknown"
                    mode_text = MODE_LABELS.get("unknown", "目前無法明確判斷")
                    mode_reason = "fallback_baseline_only"
                    option_summary = "目前先以基礎大眾運輸估算為主"
                    best_option = {}

                    mode_override = get_transport_mode_override(db, user.id, today_date)

                    try:
                        option_choice = await choose_commute_option_with_override(
                            profile,
                            effective_arrival_time=effective_arrival_time,
                            weather_buffer_minutes=weather_buffer,
                            mode_override=mode_override,
                        )
                        best_option = option_choice.get("best_option", {}) or {}
                        selection_source = option_choice.get("selection_source", "auto")

                        recommended_mode = best_option.get("mode", "unknown")
                        mode_text = MODE_LABELS.get(recommended_mode, "目前無法明確判斷")
                        mode_reason = best_option.get("reason", "unknown")
                        option_summary = best_option.get("summary", "無摘要")
                    except Exception:
                        import traceback
                        print("[today] choose_commute_option_with_override failed")
                        print(traceback.format_exc())
                        selection_source = "auto"

                    bus_snapshot = {}
                    realtime_departure = {
                        "final_departure_time": departure_time,
                        "used_realtime_adjustment": False,
                        "latest_leave_time": None,
                        "leave_in_min": None,
                        "reason": "baseline_only",
                    }

                    try:
                        bus_snapshot = await get_bus_realtime_snapshot(profile)
                        realtime_departure = choose_departure_time_with_realtime_bus(
                            baseline_departure_time=departure_time,
                            bus_snapshot=bus_snapshot,
                        )
                        final_departure_time = realtime_departure.get("final_departure_time", departure_time)
                    except Exception:
                        import traceback
                        print("[today] bus realtime failed")
                        print(traceback.format_exc())

                    note = (
                        f"已套用今天覆蓋到公司時間：{effective_arrival_time}"
                        if used_override
                        else f"目前使用預設到公司時間：{effective_arrival_time}"
                    )

                    weather_text = weather_info.get("weather_text", "未知")
                    weather_description = weather_info.get("weather_description")
                    pop = weather_info.get("pop")
                    temperature = weather_info.get("temperature")
                    min_t = weather_info.get("temperature_min")
                    max_t = weather_info.get("temperature_max")

                    weather_line = f"{weather_text}"
                    if weather_description:
                        weather_line += f"，{weather_description}"

                    if temperature is not None:
                        weather_line += f"，{temperature}°C"
                    elif min_t is not None and max_t is not None:
                        weather_line += f"，{min_t}-{max_t}°C"

                    rain_line = f"{pop}%" if pop is not None else "未知"

                    buffer_note = (
                        f"今日天氣已額外增加 {weather_buffer} 分鐘緩衝。"
                        if weather_buffer > 0
                        else "今日天氣穩定，未額外增加天氣緩衝。"
                    )

                    detail_lines = []

                    try:
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
                                    detail_lines.append(
                                        f"目前最有機會趕上的車：{chosen_label}，約 {eta_min} 分鐘後到站"
                                    )

                            if realtime_departure.get("used_realtime_adjustment"):
                                detail_lines.append(
                                    f"即時公車修正：已將建議出門時間調整為 {final_departure_time}"
                                )
                            elif realtime_departure.get("reason") == "baseline_departure_passed":
                                detail_lines.append("目前已超過今日原定上班出門時段，以下即時公車僅供參考。")
                            elif realtime_departure.get("reason") == "baseline_too_far_for_realtime":
                                detail_lines.append("目前距離原定出門時間仍較久，暫不以即時公車調整出門時間。")
                            elif realtime_departure.get("latest_leave_time"):
                                detail_lines.append(
                                    f"依目前即時班次估計，若要搭上這班車，最晚約 {realtime_departure['latest_leave_time']} 出門"
                                )

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
                                detail_lines.append(f"最近捷運站：{station.get('name', '未知站名')}")

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
                                    detail_lines.append(
                                        f"第一段公車：{chosen_label}，約 {eta_min} 分鐘後到站"
                                    )

                            if station:
                                detail_lines.append(f"轉乘捷運：{station.get('name', '未知站名')}")

                            if metro_walk_minutes is not None:
                                detail_lines.append(f"捷運步行參考：約 {metro_walk_minutes} 分鐘")

                            detail_lines.append("轉乘緩衝：5 分鐘")

                    except Exception:
                        import traceback
                        print("[today] detail_lines build failed")
                        print(traceback.format_exc())
                        detail_lines.append("細部交通資訊暫時無法完整顯示。")

                    mode_reason_text = MODE_REASON_LABELS.get(mode_reason, mode_reason)
                    mode_override_text = TRANSPORT_MODE_NAME_MAP.get(mode_override, mode_override)
                    selection_source_text = SELECTION_SOURCE_NAME_MAP.get(selection_source, selection_source)

                    reply_parts = [
                        "今日通勤建議：",
                        f"到公司時間：{effective_arrival_time}",
                        f"建議出門時間：{final_departure_time}",
                        f"建議方式：{mode_text}",
                        f"預估通勤時間：{baseline_minutes} 分鐘",
                        f"今日天氣：{weather_line}",
                        f"降雨機率：{rain_line}",
                        f"說明：{note}",
                        f"今日模式設定：{mode_override_text}",
                        f"套用方式：{selection_source_text}",
                        f"模式判斷：{mode_reason_text}",
                        f"方案摘要：{option_summary}",
                        f"提醒：{buffer_note}",
                        "",
                    ] + detail_lines

                    print(f"[today] reply_parts={reply_parts}")

                    await reply_text(
                        reply_token,
                        "\n".join(reply_parts)
                    )
                    continue

                except Exception as e:
                    import traceback
                    print("[today] exception happened")
                    print(traceback.format_exc())
                    await reply_text(
                        reply_token,
                        f"今天通勤建議執行失敗：{type(e).__name__}: {e}"
                    )
                    continue

            if command_text in COMMAND_ALIASES["tomorrow_departure"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(
                        reply_token,
                        "請先完成基本設定。\n"
                        f"{FIELD_PROMPTS[next_step]}"
                    )
                    continue

                profile = ensure_profile_defaults_for_calc(db, user.id, profile)

                effective_arrival_time, used_override = get_effective_arrival_time(
                    db,
                    user.id,
                    tomorrow_date,
                    profile.preferred_arrival_time,
                )

                estimated_minutes = await estimate_commute_minutes(
                    profile,
                    tomorrow_date,
                    effective_arrival_time,
                )

                weather_info = await get_commute_weather(profile)
                weather_buffer = weather_info.get("extra_buffer_minutes", 0)

                departure_time = await calculate_departure_time(
                    profile,
                    tomorrow_date,
                    effective_arrival_time,
                    weather_buffer_minutes=weather_buffer,
                )

                note = (
                    f"已套用明天覆蓋到公司時間：{effective_arrival_time}"
                    if used_override
                    else f"目前使用預設到公司時間：{effective_arrival_time}"
                )

                weather_text = weather_info.get("weather_text", "未知")
                weather_description = weather_info.get("weather_description")
                pop = weather_info.get("pop")
                temperature = weather_info.get("temperature")
                min_t = weather_info.get("temperature_min")
                max_t = weather_info.get("temperature_max")
                scope = weather_info.get("scope")

                weather_line = f"{weather_text}"
                if weather_description:
                    weather_line += f"，{weather_description}"

                if temperature is not None:
                    weather_line += f"，{temperature}°C"
                elif min_t is not None and max_t is not None:
                    weather_line += f"，{min_t}-{max_t}°C"

                rain_line = f"{pop}%" if pop is not None else "未知"

                print(
                    "[tomorrow-weather] "
                    f"scope={scope}, city={weather_info.get('city')}, township={weather_info.get('township')}, "
                    f"wx={weather_text}, pop={pop}, buffer={weather_buffer}"
                )

                await reply_text(
                    reply_token,
                    f"明天建議 {departure_time} 出門。\n"
                    f"{note}\n"
                    f"預估通勤時間：{estimated_minutes} 分鐘\n"
                    f"參考天氣：{weather_line}\n"
                    f"降雨機率：{rain_line}\n"
                    f"已套用天氣緩衝：{weather_buffer} 分鐘。"
                )
                continue

            if command_text in COMMAND_ALIASES["edit_tomorrow_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(
                        reply_token,
                        "請先完成基本設定，之後才能修改明天到公司時間。\n"
                        f"{FIELD_PROMPTS[next_step]}"
                    )
                    continue

                set_pending_field(db, user.id, "override_tomorrow_arrival_time")
                await reply_text(
                    reply_token,
                    FIELD_PROMPTS["override_tomorrow_arrival_time"]
                )
                continue

            profile = get_profile(db, user.id)
            current_step = profile.pending_field or get_next_setup_step(profile)

            if current_step in ["home_location", "office_location"]:
                typed_address = user_text.strip()

                if not typed_address:
                    await reply_text(
                        reply_token,
                        f"內容不能是空白。\n{FIELD_PROMPTS[current_step]}"
                    )
                    continue

                try:
                    if current_step == "home_location":
                        await save_location_or_address(
                            db,
                            user.id,
                            "home",
                            typed_address,
                        )
                        set_pending_field(db, user.id, "office_location")
                        await reply_text(
                            reply_token,
                            "已儲存住家位置。\n"
                            f"{FIELD_PROMPTS['office_location']}"
                        )
                        continue

                    if current_step == "office_location":
                        await save_location_or_address(
                            db,
                            user.id,
                            "office",
                            typed_address,
                        )
                        update_profile_field(db, user.id, "walk_to_bus_stop_min", 0)
                        set_pending_field(db, user.id, "preferred_arrival_time")

                        check_profile = get_profile(db, user.id)
                        print(
                            f"[setup] office saved | office_address={check_profile.office_address} | "
                            f"pending_field={check_profile.pending_field}"
                        )

                        await reply_text(
                            reply_token,
                            "已儲存公司位置。\n"
                            f"{FIELD_PROMPTS['preferred_arrival_time']}"
                        )
                        continue

                except Exception as e:
                    print(f"[text address] save failed: {e}")
                    await reply_text(
                        reply_token,
                        "地址辨識失敗，請重新輸入更完整地址，或直接傳送位置。"
                    )
                    continue

            if current_step in ["preferred_arrival_time", "override_tomorrow_arrival_time"]:
                value, error_message = validate_pending_input(current_step, user_text)

                if error_message:
                    await reply_text(
                        reply_token,
                        f"{error_message}\n{FIELD_PROMPTS[current_step]}"
                    )
                    continue

                if current_step == "override_tomorrow_arrival_time":
                    upsert_override(
                        db,
                        user.id,
                        tomorrow_date,
                        value,
                    )
                    set_pending_field(db, user.id, None)

                    refreshed_profile = get_profile(db, user.id)
                    refreshed_profile = ensure_profile_defaults_for_calc(db, user.id, refreshed_profile)

                    departure_time = await calculate_departure_time(
                        refreshed_profile,
                        tomorrow_date,
                        value,
                    )

                    await reply_text(
                        reply_token,
                        f"已儲存明天到公司時間：{value}\n"
                        f"明天建議 {departure_time} 出門。"
                    )
                    continue

                update_profile_field(db, user.id, "preferred_arrival_time", value)
                set_pending_field(db, user.id, None)

                updated_profile = get_profile(db, user.id)
                print(
                    f"[setup] arrival saved | office_address={updated_profile.office_address} | "
                    f"preferred_arrival_time={updated_profile.preferred_arrival_time} | "
                    f"pending_field={updated_profile.pending_field}"
                )

                await reply_text(
                    reply_token,
                    f"已儲存到公司時間：{value}\n\n"
                    f"{format_profile_text(updated_profile, tomorrow_override_time)}"
                )
                continue

            next_step = get_next_setup_step(profile)

            if next_step is None:
                await reply_text(
                    reply_token,
                    "您目前設定已完成。\n"
                    "可傳送：\n"
                    "查看設定\n"
                    "今天通勤建議\n"
                    "明天幾點出門\n"
                    "修改明天到公司時間\n"
                    "重新設定\n"
                    "傳送住家位置\n"
                    "傳送公司位置\n"
                    "測試公車\n"
                    "測試捷運\n"
                    "今天自動判斷\n"
                    "今天搭公車\n"
                    "今天搭捷運\n"
                    "今天搭公車轉捷運\n"
                    "查看今天交通方式"
                )
            else:
                set_pending_field(db, user.id, next_step)
                await reply_text(
                    reply_token,
                    f"尚未完成設定。\n{FIELD_PROMPTS[next_step]}"
                )

    finally:
        db.close()

    return {"ok": True}