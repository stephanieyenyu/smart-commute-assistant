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
)
from app.google_maps import geocode_address
from app.weather import get_today_weather_by_city
from app.service import calculate_departure_time, estimate_commute_minutes

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


def verify_line_signature(body: bytes, x_line_signature: str | None) -> bool:
    if not x_line_signature:
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
    if override:
        return override.target_arrival_time, True
    return default_arrival_time, False


def infer_city_from_text(text: str | None) -> str | None:
    if not text:
        return None

    if "台北市" in text or "臺北市" in text:
        return "臺北市"
    if "台中市" in text or "臺中市" in text:
        return "臺中市"
    if "台南市" in text or "臺南市" in text:
        return "臺南市"
    if "高雄市" in text:
        return "高雄市"
    if "新北市" in text:
        return "新北市"
    if "桃園市" in text:
        return "桃園市"
    if "基隆市" in text:
        return "基隆市"
    if "新竹市" in text:
        return "新竹市"
    if "新竹縣" in text:
        return "新竹縣"
    if "苗栗縣" in text:
        return "苗栗縣"
    if "彰化縣" in text:
        return "彰化縣"
    if "南投縣" in text:
        return "南投縣"
    if "雲林縣" in text:
        return "雲林縣"
    if "嘉義市" in text:
        return "嘉義市"
    if "嘉義縣" in text:
        return "嘉義縣"
    if "屏東縣" in text:
        return "屏東縣"
    if "宜蘭縣" in text:
        return "宜蘭縣"
    if "台東縣" in text or "臺東縣" in text:
        return "臺東縣"
    if "花蓮縣" in text:
        return "花蓮縣"
    if "澎湖縣" in text:
        return "澎湖縣"
    if "金門縣" in text:
        return "金門縣"
    if "連江縣" in text:
        return "連江縣"

    return None


def select_city_name(profile) -> str | None:
    city_name = profile.home_city
    if not city_name:
        city_name = profile.office_city
    if not city_name:
        city_name = infer_city_from_text(profile.home_address)
    if not city_name:
        city_name = infer_city_from_text(profile.office_address)

    print(f"[weather] selected_city_name={city_name}")
    return city_name


def get_next_setup_step(profile) -> str | None:
    if not profile.home_address:
        return "home_location"
    if not profile.office_address:
        return "office_location"
    if not profile.preferred_arrival_time:
        return "preferred_arrival_time"
    return None


def reset_profile_for_reconfigure(db, user_id: int):
    profile = get_or_create_profile(db, user_id)

    profile.home_address = None
    profile.home_lat = None
    profile.home_lng = None
    profile.home_city = None

    profile.office_address = None
    profile.office_lat = None
    profile.office_lng = None
    profile.office_city = None

    profile.preferred_arrival_time = None

    # 先維持 0，之後你再改成系統自動算到站牌步行時間
    profile.walk_to_bus_stop_min = 0

    if hasattr(profile, "selected_bus_stop_id"):
        profile.selected_bus_stop_id = None
    if hasattr(profile, "selected_bus_stop_name"):
        profile.selected_bus_stop_name = None
    if hasattr(profile, "selected_bus_stop_lat"):
        profile.selected_bus_stop_lat = None
    if hasattr(profile, "selected_bus_stop_lng"):
        profile.selected_bus_stop_lng = None

    profile.pending_field = "home_location"

    db.commit()
    db.refresh(profile)
    return profile


def ensure_profile_defaults_for_calc(db, user_id: int, profile):
    if profile.walk_to_bus_stop_min is None:
        update_profile_field(db, user_id, "walk_to_bus_stop_min", 0)
        profile = get_profile(db, user_id)
    return profile


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

    if geocode_result:
        normalized_address = geocode_result.get("formatted_address") or raw_address
        city = geocode_result.get("city") or city

        # 如果原本沒有 lat/lng，就用 geocode 回來的
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
    )


@router.post("/webhooks/line")
async def line_webhook(
    request: Request,
    x_line_signature: str | None = Header(default=None),
):
    body = await request.body()

    if not verify_line_signature(body, x_line_signature):
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
            profile = get_or_create_profile(db, user.id)

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

            # 1. 先處理 location message
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
                    await reply_text(
                        reply_token,
                        "已儲存公司位置。\n"
                        f"{FIELD_PROMPTS['preferred_arrival_time']}"
                    )
                    continue

                await reply_text(
                    reply_token,
                    "目前不需要位置資訊。\n"
                    "可傳送：查看設定、今天通勤建議、明天幾點出門、修改明天到公司時間、重新設定"
                )
                continue

            # 2. 其他非文字、非位置訊息直接略過
            if message_type != "text":
                continue

            user_text = message.get("text", "").strip()

            today_date = date.today()
            tomorrow_date = today_date + timedelta(days=1)

            tomorrow_override = get_override_for_date(db, user.id, tomorrow_date)
            tomorrow_override_time = (
                tomorrow_override.target_arrival_time if tomorrow_override else None
            )

            # 3. 可直接切回位置設定的指令
            if user_text in ["傳送住家位置", "設定住家位置"]:
                set_pending_field(db, user.id, "home_location")
                await reply_text(reply_token, FIELD_PROMPTS["home_location"])
                continue

            if user_text in ["傳送公司位置", "設定公司位置"]:
                set_pending_field(db, user.id, "office_location")
                await reply_text(reply_token, FIELD_PROMPTS["office_location"])
                continue

            # 4. 重新設定
            if user_text == "重新設定":
                reset_profile_for_reconfigure(db, user.id)
                await reply_text(
                    reply_token,
                    "好的，現在開始重新設定。\n"
                    f"{FIELD_PROMPTS['home_location']}"
                )
                continue

            # 5. 查看設定
            if user_text == "查看設定":
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

            # 6. 今天通勤建議
            if user_text == "今天通勤建議":
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

                estimated_minutes = await estimate_commute_minutes(
                    profile,
                    today_date,
                    effective_arrival_time,
                )

                city_name = select_city_name(profile)
                weather_info = await get_today_weather_by_city(city_name)
                weather_buffer = weather_info["extra_buffer_minutes"]

                departure_time = await calculate_departure_time(
                    profile,
                    today_date,
                    effective_arrival_time,
                    weather_buffer_minutes=weather_buffer,
                )

                note = (
                    f"已套用今天覆蓋到公司時間：{effective_arrival_time}"
                    if used_override
                    else f"目前使用預設到公司時間：{effective_arrival_time}"
                )

                weather_text = weather_info["weather_text"]
                pop = weather_info["pop"]
                min_t = weather_info["temperature_min"]
                max_t = weather_info["temperature_max"]

                weather_line = f"{weather_text}"
                if min_t is not None and max_t is not None:
                    weather_line += f"，{min_t}-{max_t}°C"

                rain_line = f"{pop}%" if pop is not None else "未知"

                buffer_note = (
                    f"今日天氣已額外增加 {weather_buffer} 分鐘緩衝。"
                    if weather_buffer > 0
                    else "今日天氣穩定，未額外增加天氣緩衝。"
                )

                print(
                    "[today] "
                    f"arrival={effective_arrival_time}, "
                    f"estimated_minutes={estimated_minutes}, "
                    f"weather_text={weather_text}, pop={pop}, "
                    f"weather_buffer={weather_buffer}, departure={departure_time}"
                )

                await reply_text(
                    reply_token,
                    "今日通勤建議：\n"
                    f"到公司時間：{effective_arrival_time}\n"
                    f"建議出門時間：{departure_time}\n"
                    "建議方式：目前以 Google 大眾運輸估算為主\n"
                    f"預估通勤時間：{estimated_minutes} 分鐘\n"
                    f"今日天氣：{weather_line}\n"
                    f"降雨機率：{rain_line}\n"
                    f"說明：{note}\n"
                    f"提醒：{buffer_note}"
                )
                continue

            # 7. 明天幾點出門
            if user_text == "明天幾點出門":
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

                city_name = select_city_name(profile)
                weather_info = await get_today_weather_by_city(city_name)
                weather_buffer = weather_info["extra_buffer_minutes"]

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

                weather_text = weather_info["weather_text"]
                pop = weather_info["pop"]
                rain_line = f"{pop}%" if pop is not None else "未知"

                print(
                    "[tomorrow] "
                    f"arrival={effective_arrival_time}, "
                    f"estimated_minutes={estimated_minutes}, "
                    f"weather_text={weather_text}, pop={pop}, "
                    f"weather_buffer={weather_buffer}, departure={departure_time}"
                )

                await reply_text(
                    reply_token,
                    f"明天建議 {departure_time} 出門。\n"
                    f"{note}\n"
                    f"預估通勤時間：{estimated_minutes} 分鐘\n"
                    f"參考天氣：{weather_text}\n"
                    f"降雨機率：{rain_line}\n"
                    f"已套用天氣緩衝：{weather_buffer} 分鐘。"
                )
                continue

            # 8. 修改明天到公司時間
            if user_text == "修改明天到公司時間":
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

            # 9. 若正在等待位置，但使用者傳的是文字地址，也接受
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

            # 10. 若正在等待文字欄位，就把這次輸入當答案
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

                update_profile_field(db, user.id, current_step, value)

                updated_profile = get_profile(db, user.id)
                next_step = get_next_setup_step(updated_profile)

                if next_step is None:
                    set_pending_field(db, user.id, None)
                    await reply_text(
                        reply_token,
                        f"已儲存{FIELD_LABELS[current_step]}。\n\n"
                        f"{format_profile_text(updated_profile, tomorrow_override_time)}"
                    )
                    continue

                set_pending_field(db, user.id, next_step)
                await reply_text(
                    reply_token,
                    f"已儲存{FIELD_LABELS[current_step]}。\n"
                    f"{FIELD_PROMPTS[next_step]}"
                )
                continue

            # 11. 其他未知訊息
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
                    "傳送公司位置"
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