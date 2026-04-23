import base64
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Request

from app.address_utils import looks_like_address, extract_city_from_text
from app.config import LINE_CHANNEL_SECRET
from app.db import SessionLocal
from app.line_client import reply_text
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
    set_reminder_enabled,
)
from app.google_maps import geocode_address
from app.service import (
    calculate_departure_time,
    build_today_commute_payload,
    build_today_reminder_payload,
    freeze_today_reminder_payload,
    get_metro_snapshot,
)
from app.reminder_scheduler import clear_today_reminder_state_for_user

router = APIRouter()

FIELD_PROMPTS = {
    "home_location": (
        "請傳送住家位置。\n"
        "方法：\n"
        "1. 點「傳送住家位置」或使用 LINE 的位置傳送功能\n"
        "2. 或直接輸入住家完整地址\n"
        "3. 中英文地址都可以"
    ),
    "office_location": (
        "請傳送公司位置。\n"
        "方法：\n"
        "1. 點「傳送公司位置」或使用 LINE 的位置傳送功能\n"
        "2. 或直接輸入公司完整地址\n"
        "3. 中英文地址都可以"
    ),
    "preferred_arrival_time": "請直接輸入到公司時間，格式 HH:MM，例如 08:30",
    "override_today_arrival_time": "請直接輸入今天新的到公司時間，格式 HH:MM，例如 15:30",
    "override_tomorrow_arrival_time": "請直接輸入明天新的到公司時間，格式 HH:MM，例如 09:30",
}

TRANSPORT_MODE_NAME_MAP = {
    None: "未指定，系統自動判斷",
    "auto": "自動判斷",
    "bus": "公車優先",
    "metro": "捷運優先",
    "bus_to_metro": "公車轉捷運",
}

COMMAND_ALIASES = {
    "view_settings": {"查看設定"},
    "today_commute": {"今天通勤建議", "今日通勤建議", "通勤建議"},
    "tomorrow_departure": {"明天幾點出門"},
    "edit_today_arrival": {"修改今天到公司時間", "今天改到公司時間"},
    "edit_tomorrow_arrival": {"修改明天到公司時間"},
    "reset": {"重新設定"},
    "send_home_location": {"傳送住家位置", "設定住家位置"},
    "send_office_location": {"傳送公司位置", "設定公司位置"},
    "test_bus": {"測試公車", "公車測試"},
    "test_metro": {"測試捷運", "捷運測試"},
    "test_reminder": {"測試提醒"},
    "set_mode_auto": {"今天自動判斷", "今天交通自動"},
    "set_mode_bus": {"今天搭公車", "今天坐公車"},
    "set_mode_metro": {"今天搭捷運", "今天坐捷運"},
    "set_mode_bus_to_metro": {"今天搭公車轉捷運", "今天公車轉捷運"},
    "view_mode_today": {"查看今天交通方式"},
    "enable_reminder": {"開啟自動提醒"},
    "disable_reminder": {"關閉自動提醒"},
    "view_reminder_setting": {"查看提醒設定"},
}

READY_MENU_TEXT = (
    "您目前設定已完成。\n"
    "可傳送：\n"
    "查看設定\n"
    "今天通勤建議\n"
    "明天幾點出門\n"
    "修改今天到公司時間\n"
    "修改明天到公司時間\n"
    "重新設定\n"
    "傳送住家位置\n"
    "傳送公司位置\n"
    "測試公車\n"
    "測試捷運\n"
    "測試提醒\n"
    "開啟自動提醒\n"
    "關閉自動提醒\n"
    "查看提醒設定\n"
    "今天自動判斷\n"
    "今天搭公車\n"
    "今天搭捷運\n"
    "今天搭公車轉捷運\n"
    "查看今天交通方式"
)


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


def format_profile_text(
    profile,
    today_override_time: str | None = None,
    tomorrow_override_time: str | None = None,
) -> str:
    home_address = profile.home_address or "尚未設定"
    office_address = profile.office_address or "尚未設定"
    preferred_arrival_time = profile.preferred_arrival_time or "尚未設定"
    reminder_status = "開啟" if getattr(profile, "reminder_enabled", True) else "關閉"

    text = (
        "您目前設定如下：\n"
        f"住家位置：{home_address}\n"
        f"公司位置：{office_address}\n"
        f"到公司時間：{preferred_arrival_time}\n"
        f"自動提醒：{reminder_status}"
    )

    if today_override_time:
        text += f"\n今天覆蓋到公司時間：{today_override_time}"
    if tomorrow_override_time:
        text += f"\n明天覆蓋到公司時間：{tomorrow_override_time}"

    return text


def validate_pending_input(field_name: str, user_text: str):
    if field_name in ["preferred_arrival_time", "override_today_arrival_time", "override_tomorrow_arrival_time"]:
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
    return extract_city_from_text(text)


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
                    await save_location_or_address(db, user.id, "home", raw_address, lat=lat, lng=lng)
                    clear_today_reminder_state_for_user(user.id)
                    set_pending_field(db, user.id, "office_location")
                    await reply_text(reply_token, f"已儲存住家位置。\n{FIELD_PROMPTS['office_location']}")
                    continue

                if current_step == "office_location":
                    await save_location_or_address(db, user.id, "office", raw_address, lat=lat, lng=lng)
                    clear_today_reminder_state_for_user(user.id)
                    update_profile_field(db, user.id, "walk_to_bus_stop_min", 0)
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_text(reply_token, f"已儲存公司位置。\n{FIELD_PROMPTS['preferred_arrival_time']}")
                    continue

                await reply_text(reply_token, READY_MENU_TEXT)
                continue

            if message_type != "text":
                continue

            user_text = message.get("text", "").strip()
            command_text = normalize_user_text(user_text)
            print(f"[debug] user_text={repr(user_text)} | command_text={repr(command_text)}")

            today_date = date.today()
            tomorrow_date = today_date + timedelta(days=1)

            today_override = get_override_for_date(db, user.id, today_date)
            today_override_time = (
                today_override.target_arrival_time if today_override and today_override.target_arrival_time else None
            )

            tomorrow_override = get_override_for_date(db, user.id, tomorrow_date)
            tomorrow_override_time = (
                tomorrow_override.target_arrival_time if tomorrow_override and tomorrow_override.target_arrival_time else None
            )

            if command_text.lower() in {"hi", "hello", "hey"} or command_text in {"嗨", "你好", "哈囉", "哈喽"}:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is None:
                    await reply_text(reply_token, "你好，我是智慧通勤助理。\n" + READY_MENU_TEXT)
                else:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, "你好，我是智慧通勤助理。\n你目前還沒完成設定。\n" + FIELD_PROMPTS[next_step])
                continue

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
                clear_today_reminder_state_for_user(user.id)
                await reply_text(reply_token, "好的，現在開始重新設定。\n" + FIELD_PROMPTS["home_location"])
                continue

            if command_text in COMMAND_ALIASES["view_settings"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is None:
                    set_pending_field(db, user.id, None)
                    await reply_text(reply_token, format_profile_text(profile, today_override_time, tomorrow_override_time))
                    continue

                set_pending_field(db, user.id, next_step)
                await reply_text(
                    reply_token,
                    f"{format_profile_text(profile, today_override_time, tomorrow_override_time)}\n\n"
                    "尚未完成設定。\n"
                    f"{FIELD_PROMPTS[next_step]}"
                )
                continue

            if command_text in COMMAND_ALIASES["enable_reminder"]:
                set_reminder_enabled(db, user.id, True)
                await reply_text(reply_token, "已開啟自動提醒。")
                continue

            if command_text in COMMAND_ALIASES["disable_reminder"]:
                set_reminder_enabled(db, user.id, False)
                await reply_text(reply_token, "已關閉自動提醒。")
                continue

            if command_text in COMMAND_ALIASES["view_reminder_setting"]:
                profile = get_profile(db, user.id)
                status_text = "開啟" if profile.reminder_enabled else "關閉"
                await reply_text(reply_token, f"目前自動提醒：{status_text}")
                continue

            if command_text in COMMAND_ALIASES["set_mode_auto"]:
                upsert_transport_mode_override(db, user.id, today_date, "auto")
                clear_today_reminder_state_for_user(user.id)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-after-mode-auto] failed: {e}")
                await reply_text(reply_token, "已設定今天交通方式為：自動判斷")
                continue

            if command_text in COMMAND_ALIASES["set_mode_bus"]:
                upsert_transport_mode_override(db, user.id, today_date, "bus")
                clear_today_reminder_state_for_user(user.id)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-after-mode-bus] failed: {e}")
                await reply_text(reply_token, "已設定今天交通方式為：公車優先")
                continue

            if command_text in COMMAND_ALIASES["set_mode_metro"]:
                upsert_transport_mode_override(db, user.id, today_date, "metro")
                clear_today_reminder_state_for_user(user.id)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-after-mode-metro] failed: {e}")
                await reply_text(reply_token, "已設定今天交通方式為：捷運優先")
                continue

            if command_text in COMMAND_ALIASES["set_mode_bus_to_metro"]:
                upsert_transport_mode_override(db, user.id, today_date, "bus_to_metro")
                clear_today_reminder_state_for_user(user.id)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-after-mode-bus-to-metro] failed: {e}")
                await reply_text(reply_token, "已設定今天交通方式為：公車轉捷運")
                continue

            if command_text in COMMAND_ALIASES["view_mode_today"]:
                current_mode = get_transport_mode_override(db, user.id, today_date) or "auto"
                await reply_text(reply_token, f"今天交通方式設定：{TRANSPORT_MODE_NAME_MAP.get(current_mode, current_mode)}")
                continue

            if command_text in COMMAND_ALIASES["test_bus"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, "請先完成基本設定。\n" + FIELD_PROMPTS[next_step])
                    continue

                try:
                    payload = await build_today_commute_payload(
                        db=db,
                        user_id=user.id,
                        target_date=today_date,
                        force_mode_override="bus",
                        header="公車測試：",
                    )
                    if not payload.get("ok"):
                        await reply_text(reply_token, "目前無法建立公車測試資料。")
                        continue

                    await reply_text(reply_token, payload["text"])
                    continue

                except Exception as e:
                    import traceback
                    print("[test-bus] failed")
                    print(traceback.format_exc())
                    await reply_text(reply_token, f"測試公車失敗：{type(e).__name__}: {e}")
                    continue

            if command_text in COMMAND_ALIASES["test_metro"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, "請先完成基本設定。\n" + FIELD_PROMPTS[next_step])
                    continue

                try:
                    metro_snapshot = await get_metro_snapshot(profile)
                    if not metro_snapshot.get("available"):
                        await reply_text(reply_token, "目前無法取得附近捷運資訊。")
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

                    await reply_text(reply_token, "\n".join(lines))
                    continue

                except Exception as e:
                    print(f"[metro-test] failed: {e}")
                    await reply_text(reply_token, f"測試捷運失敗：{e}")
                    continue

            if command_text in COMMAND_ALIASES["test_reminder"]:
                try:
                    plan = await build_today_commute_payload(
                        db=db,
                        user_id=user.id,
                        target_date=today_date,
                        force_mode_override=None,
                    )
                    if not plan.get("ok"):
                        next_step = plan.get("next_step")
                        if next_step:
                            set_pending_field(db, user.id, next_step)
                            await reply_text(reply_token, "請先完成基本設定後，才能測試提醒。\n" + FIELD_PROMPTS[next_step])
                        else:
                            await reply_text(reply_token, "目前無法建立提醒。")
                        continue

                    frozen_payload = await freeze_today_reminder_payload(
                        db=db,
                        user_id=user.id,
                        target_date=today_date,
                        plan=plan,
                    )
                    if not frozen_payload.get("ok"):
                        await reply_text(reply_token, "目前無法建立提醒。")
                        continue

                    await reply_text(reply_token, frozen_payload["text"].replace("出門提醒：", "出門提醒測試：", 1))
                    continue

                except Exception as e:
                    import traceback
                    print("[test-reminder] failed")
                    print(traceback.format_exc())
                    await reply_text(reply_token, f"測試提醒失敗：{type(e).__name__}: {e}")
                    continue

            if command_text in COMMAND_ALIASES["today_commute"]:
                try:
                    profile = get_profile(db, user.id)
                    next_step = get_next_setup_step(profile)

                    if next_step is not None:
                        set_pending_field(db, user.id, next_step)
                        await reply_text(reply_token, "請先完成基本設定。\n" + FIELD_PROMPTS[next_step])
                        continue

                    profile = ensure_profile_defaults_for_calc(db, user.id, profile)

                    plan = await build_today_commute_payload(
                        db=db,
                        user_id=user.id,
                        target_date=today_date,
                        force_mode_override=None,
                    )
                    if not plan.get("ok"):
                        await reply_text(reply_token, "今天通勤建議目前無法建立。")
                        continue

                    await freeze_today_reminder_payload(
                        db=db,
                        user_id=user.id,
                        target_date=today_date,
                        plan=plan,
                    )

                    await reply_text(reply_token, plan["text"])
                    continue

                except Exception as e:
                    import traceback
                    print("[today] exception happened")
                    print(traceback.format_exc())
                    await reply_text(reply_token, f"今天通勤建議執行失敗：{type(e).__name__}: {e}")
                    continue

            if command_text in COMMAND_ALIASES["tomorrow_departure"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)

                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, "請先完成基本設定。\n" + FIELD_PROMPTS[next_step])
                    continue

                profile = ensure_profile_defaults_for_calc(db, user.id, profile)

                effective_arrival_time, used_override = get_effective_arrival_time(
                    db, user.id, tomorrow_date, profile.preferred_arrival_time
                )

                tomorrow_plan = await build_today_commute_payload(
                    db=db,
                    user_id=user.id,
                    target_date=tomorrow_date,
                    force_mode_override=None,
                )

                if not tomorrow_plan.get("ok"):
                    await reply_text(reply_token, "明天出門時間目前無法估算。")
                    continue

                await reply_text(
                    reply_token,
                    f"明天建議 {tomorrow_plan['final_departure_time']} 出門。\n"
                    f"到公司時間：{tomorrow_plan['effective_arrival_time']}\n"
                    f"預估通勤時間：{tomorrow_plan['baseline_minutes']} 分鐘\n"
                    f"參考天氣：{tomorrow_plan['weather_line']}\n"
                    f"降雨機率：{tomorrow_plan['rain_line']}\n"
                    f"說明：{'已套用明天覆蓋到公司時間：' + effective_arrival_time if used_override else '目前使用預設到公司時間：' + effective_arrival_time}\n"
                    f"已套用天氣緩衝：{tomorrow_plan['weather_buffer']} 分鐘。"
                )
                continue

            if command_text in COMMAND_ALIASES["edit_today_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, "請先完成基本設定，之後才能修改今天到公司時間。\n" + FIELD_PROMPTS[next_step])
                    continue

                set_pending_field(db, user.id, "override_today_arrival_time")
                await reply_text(reply_token, FIELD_PROMPTS["override_today_arrival_time"])
                continue

            if command_text in COMMAND_ALIASES["edit_tomorrow_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, "請先完成基本設定，之後才能修改明天到公司時間。\n" + FIELD_PROMPTS[next_step])
                    continue

                set_pending_field(db, user.id, "override_tomorrow_arrival_time")
                await reply_text(reply_token, FIELD_PROMPTS["override_tomorrow_arrival_time"])
                continue

            profile = get_profile(db, user.id)
            current_step = profile.pending_field or get_next_setup_step(profile)

            if current_step in ["home_location", "office_location"]:
                typed_address = user_text.strip()

                if not typed_address:
                    await reply_text(reply_token, f"內容不能是空白。\n{FIELD_PROMPTS[current_step]}")
                    continue

                if not looks_like_address(typed_address):
                    field_name = "住家位置" if current_step == "home_location" else "公司位置"
                    await reply_text(
                        reply_token,
                        f"你目前正在設定{field_name}。\n"
                        "請直接輸入完整地址，或使用 LINE 位置傳送功能。\n"
                        f"{FIELD_PROMPTS[current_step]}"
                    )
                    continue

                try:
                    if current_step == "home_location":
                        await save_location_or_address(db, user.id, "home", typed_address)
                        clear_today_reminder_state_for_user(user.id)
                        set_pending_field(db, user.id, "office_location")
                        await reply_text(reply_token, f"已儲存住家位置。\n{FIELD_PROMPTS['office_location']}")
                        continue

                    if current_step == "office_location":
                        await save_location_or_address(db, user.id, "office", typed_address)
                        clear_today_reminder_state_for_user(user.id)
                        update_profile_field(db, user.id, "walk_to_bus_stop_min", 0)
                        set_pending_field(db, user.id, "preferred_arrival_time")
                        await reply_text(reply_token, f"已儲存公司位置。\n{FIELD_PROMPTS['preferred_arrival_time']}")
                        continue

                except Exception as e:
                    print(f"[text address] save failed: {e}")
                    await reply_text(reply_token, "地址辨識失敗，請重新輸入更完整地址，或直接傳送位置。")
                    continue

            if current_step in ["preferred_arrival_time", "override_today_arrival_time", "override_tomorrow_arrival_time"]:
                value, error_message = validate_pending_input(current_step, user_text)

                if error_message:
                    await reply_text(reply_token, f"{error_message}\n{FIELD_PROMPTS[current_step]}")
                    continue

                if current_step == "override_today_arrival_time":
                    upsert_override(db, user.id, today_date, value)
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-after-edit-today] failed: {e}")
                    set_pending_field(db, user.id, None)
                    await reply_text(reply_token, f"已儲存今天到公司時間：{value}\n你現在可以傳送：今天通勤建議 或 測試提醒")
                    continue

                if current_step == "override_tomorrow_arrival_time":
                    upsert_override(db, user.id, tomorrow_date, value)
                    set_pending_field(db, user.id, None)

                    refreshed_profile = get_profile(db, user.id)
                    refreshed_profile = ensure_profile_defaults_for_calc(db, user.id, refreshed_profile)

                    departure_time = await calculate_departure_time(
                        refreshed_profile,
                        tomorrow_date,
                        value,
                    )

                    await reply_text(reply_token, f"已儲存明天到公司時間：{value}\n明天建議 {departure_time} 出門。")
                    continue

                update_profile_field(db, user.id, "preferred_arrival_time", value)
                clear_today_reminder_state_for_user(user.id)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-after-save-arrival] failed: {e}")
                set_pending_field(db, user.id, None)

                updated_profile = get_profile(db, user.id)
                await reply_text(
                    reply_token,
                    f"已儲存到公司時間：{value}\n\n"
                    f"{format_profile_text(updated_profile, today_override_time, tomorrow_override_time)}"
                )
                continue

            next_step = get_next_setup_step(profile)

            if next_step is None:
                await reply_text(reply_token, READY_MENU_TEXT)
            else:
                set_pending_field(db, user.id, next_step)
                await reply_text(reply_token, f"尚未完成設定。\n{FIELD_PROMPTS[next_step]}")

    finally:
        db.close()

    return {"ok": True}