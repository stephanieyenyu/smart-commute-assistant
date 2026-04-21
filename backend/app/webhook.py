import base64
import hashlib
import hmac
import json
from app.weather import get_today_weather_by_city
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import LINE_CHANNEL_SECRET
from app.line_client import reply_text
from app.db import SessionLocal
from app.crud import (
    get_or_create_user,
    get_or_create_profile,
    get_profile,
    get_next_missing_field,
    get_override_for_date,
    set_pending_field,
    update_profile_field,
    update_address_and_coords,
    upsert_override,
)
from app.google_maps import geocode_address
from app.service import calculate_departure_time, estimate_commute_minutes

router = APIRouter()

FIELD_LABELS = {
    "home_address": "家裡地址",
    "office_address": "公司地址",
    "preferred_arrival_time": "到公司時間",
    "walk_to_bus_stop_min": "走到站牌時間",
    "override_tomorrow_arrival_time": "明天到公司時間",
}

FIELD_PROMPTS = {
    "home_address": "請直接輸入家裡地址",
    "office_address": "請直接輸入公司地址",
    "preferred_arrival_time": "請直接輸入到公司時間，格式 HH:MM，例如 09:00",
    "walk_to_bus_stop_min": "請直接輸入走到站牌時間，請輸入整數分鐘，例如 8",
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
    walk_to_bus_stop_min = (
        f"{profile.walk_to_bus_stop_min} 分鐘"
        if profile.walk_to_bus_stop_min is not None
        else "尚未設定"
    )

    text = (
        "您目前設定如下：\n"
        f"家裡地址：{home_address}\n"
        f"公司地址：{office_address}\n"
        f"到公司時間：{preferred_arrival_time}\n"
        f"走到站牌時間：{walk_to_bus_stop_min}"
    )

    if tomorrow_override_time:
        text += f"\n明天覆蓋到公司時間：{tomorrow_override_time}"

    return text


def validate_pending_input(field_name: str, user_text: str):
    if field_name in ["home_address", "office_address"]:
        value = user_text.strip()
        if not value:
            return None, "內容不能是空白，請重新輸入。"
        return value, None

    if field_name in ["preferred_arrival_time", "override_tomorrow_arrival_time"]:
        value = user_text.strip()
        try:
            datetime.strptime(value, "%H:%M")
            return value, None
        except ValueError:
            return None, "時間格式錯誤，請輸入 HH:MM，例如 09:00"

    if field_name == "walk_to_bus_stop_min":
        value = user_text.strip()
        try:
            minutes = int(value)
            if minutes < 0:
                raise ValueError
            return minutes, None
        except ValueError:
            return None, "請輸入整數分鐘，例如 8"

    return None, "未知的設定欄位。"


def get_effective_arrival_time(db, user_id: int, target_date: date, default_arrival_time: str):
    override = get_override_for_date(db, user_id, target_date)
    if override:
        return override.target_arrival_time, True
    return default_arrival_time, False


def select_city_name(profile) -> str | None:
    city_name = profile.home_city
    if not city_name:
        city_name = profile.office_city

    if not city_name and profile.home_address:
        if "台北市" in profile.home_address or "臺北市" in profile.home_address:
            city_name = "臺北市"
        elif "台中市" in profile.home_address or "臺中市" in profile.home_address:
            city_name = "臺中市"
        elif "台南市" in profile.home_address or "臺南市" in profile.home_address:
            city_name = "臺南市"
        elif "高雄市" in profile.home_address:
            city_name = "高雄市"

    print(f"[weather] selected city_name={city_name}")
    return city_name


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
                reply_token = event.get("replyToken")
                next_missing = get_next_missing_field(profile) or "home_address"
                set_pending_field(db, user.id, next_missing)

                await reply_text(
                    reply_token,
                    "請進行傳送「查看設定」以確認設定情形。\n\n"
                    "尚未完成設定。\n"
                    f"{FIELD_PROMPTS[next_missing]}"
                )
                continue

            if event_type != "message":
                continue

            message = event.get("message", {})
            if message.get("type") != "text":
                continue

            reply_token = event.get("replyToken")
            user_text = message.get("text", "").strip()

            today_date = date.today()
            tomorrow_date = today_date + timedelta(days=1)

            tomorrow_override = get_override_for_date(db, user.id, tomorrow_date)
            tomorrow_override_time = (
                tomorrow_override.target_arrival_time if tomorrow_override else None
            )

            if user_text == "查看設定":
                profile = get_profile(db, user.id)
                next_missing = get_next_missing_field(profile)

                if next_missing is None:
                    set_pending_field(db, user.id, None)
                    await reply_text(
                        reply_token,
                        format_profile_text(profile, tomorrow_override_time),
                    )
                    continue

                set_pending_field(db, user.id, next_missing)
                await reply_text(
                    reply_token,
                    f"{format_profile_text(profile, tomorrow_override_time)}\n\n"
                    "尚未完成設定。\n"
                    f"{FIELD_PROMPTS[next_missing]}"
                )
                continue

            if user_text == "今天通勤建議":
                profile = get_profile(db, user.id)
                next_missing = get_next_missing_field(profile)

                if next_missing is not None:
                    set_pending_field(db, user.id, next_missing)
                    await reply_text(
                        reply_token,
                        "請先完成基本設定。\n"
                        f"{FIELD_PROMPTS[next_missing]}"
                    )
                    continue

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
                    "建議方式：公車直達優先\n"
                    f"預估通勤時間：{estimated_minutes} 分鐘\n"
                    f"今日天氣：{weather_line}\n"
                    f"降雨機率：{rain_line}\n"
                    f"說明：{note}\n"
                    f"提醒：{buffer_note}"
                )
                continue

            if user_text == "明天幾點出門":
                profile = get_profile(db, user.id)
                next_missing = get_next_missing_field(profile)

                if next_missing is not None:
                    set_pending_field(db, user.id, next_missing)
                    await reply_text(
                        reply_token,
                        "請進行傳送「查看設定」以確認設定情形。\n"
                        f"目前尚未完成設定。\n{FIELD_PROMPTS[next_missing]}"
                    )
                    continue

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

            if user_text == "修改明天到公司時間":
                profile = get_profile(db, user.id)
                next_missing = get_next_missing_field(profile)

                if next_missing is not None:
                    set_pending_field(db, user.id, next_missing)
                    await reply_text(
                        reply_token,
                        "請先完成基本設定，之後才能修改明天到公司時間。\n"
                        f"{FIELD_PROMPTS[next_missing]}"
                    )
                    continue

                set_pending_field(db, user.id, "override_tomorrow_arrival_time")
                await reply_text(
                    reply_token,
                    FIELD_PROMPTS["override_tomorrow_arrival_time"]
                )
                continue

            if profile.pending_field:
                field_name = profile.pending_field
                value, error_message = validate_pending_input(field_name, user_text)

                if error_message:
                    await reply_text(
                        reply_token,
                        f"{error_message}\n{FIELD_PROMPTS[field_name]}"
                    )
                    continue

                if field_name == "override_tomorrow_arrival_time":
                    upsert_override(
                        db,
                        user.id,
                        tomorrow_date,
                        value,
                    )
                    set_pending_field(db, user.id, None)

                    refreshed_profile = get_profile(db, user.id)
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

                if field_name in ["home_address", "office_address"]:
                    geocode_result = await geocode_address(value)

                    if field_name == "home_address":
                        if geocode_result:
                            update_address_and_coords(
                                db,
                                user.id,
                                "home",
                                value,
                                geocode_result["lat"],
                                geocode_result["lng"],
                                geocode_result["city"],
                            )
                        else:
                            update_address_and_coords(
                                db,
                                user.id,
                                "home",
                                value,
                                None,
                                None,
                                None,
                            )

                    elif field_name == "office_address":
                        if geocode_result:
                            update_address_and_coords(
                                db,
                                user.id,
                                "office",
                                value,
                                geocode_result["lat"],
                                geocode_result["lng"],
                                geocode_result["city"],
                            )
                        else:
                            update_address_and_coords(
                                db,
                                user.id,
                                "office",
                                value,
                                None,
                                None,
                                None,
                            )
                else:
                    update_profile_field(db, user.id, field_name, value)

                updated_profile = get_profile(db, user.id)
                next_missing = get_next_missing_field(updated_profile)

                if next_missing is None:
                    set_pending_field(db, user.id, None)
                    await reply_text(
                        reply_token,
                        f"已儲存{FIELD_LABELS[field_name]}。\n\n"
                        f"{format_profile_text(updated_profile, tomorrow_override_time)}"
                    )
                    continue

                set_pending_field(db, user.id, next_missing)
                await reply_text(
                    reply_token,
                    f"已儲存{FIELD_LABELS[field_name]}。\n"
                    f"{FIELD_PROMPTS[next_missing]}"
                )
                continue

            next_missing = get_next_missing_field(profile)
            if next_missing is None:
                await reply_text(
                    reply_token,
                    "您目前設定已完成。\n"
                    "可傳送：\n"
                    "查看設定\n"
                    "今天通勤建議\n"
                    "明天幾點出門\n"
                    "修改明天到公司時間"
                )
            else:
                await reply_text(
                    reply_token,
                    "請進行傳送「查看設定」以確認設定情形。"
                )

    finally:
        db.close()

    return {"ok": True}