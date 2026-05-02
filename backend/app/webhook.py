import json
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Request
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, LocationMessageContent, FollowEvent, PostbackEvent

from app.address_utils import looks_like_address, extract_city_from_text
from app.config import LINE_CHANNEL_SECRET, PUBLIC_URL
from app.dashboard_links import build_dashboard_view_url
from app.db import SessionLocal
from app.departure_confirmation import (
    confirm_departure_for_user,
    snooze_departure_for_user,
)
from app.line_client import reply_text, reply_with_quick_reply, reply_multi_messages_with_quick_reply
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
    freeze_today_reminder_payload,
    get_bus_realtime_snapshot,
    get_metro_snapshot,
)
from app.reminder_scheduler import clear_today_reminder_state_for_user

router = APIRouter()
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def today_taipei() -> date:
    return datetime.now(TAIPEI_TZ).date()

FIELD_PROMPTS = {
    "home_location": (
        "請傳送住家位置 📍\n"
        "點下方按鈕開啟地圖，或直接輸入完整地址"
    ),
    "office_location": (
        "請傳送公司位置 🏢\n"
        "點下方按鈕開啟地圖，或直接輸入完整地址"
    ),
    "preferred_arrival_time": "請問您幾點需要到公司？\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 08:30）",
    "override_today_arrival_time": "請問今天幾點需要到公司？\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 15:30）",
    "override_tomorrow_arrival_time": "請問明天幾點需要到公司？\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 09:30）",
}

parser = WebhookParser(LINE_CHANNEL_SECRET)

# ===========================================================
# Quick Reply button sets
# ===========================================================

# Shown for: new users (FollowEvent), reset, setup incomplete
# Contains all 3 setup actions in one bar
SETUP_QUICK_REPLIES = [
    {"type": "location",      "label": "🏠 住家位置"},
    {"type": "location",      "label": "🏢 公司位置"},
    {"type": "datetimepicker","label": "⏰ 到公司時間",
     "data": "action=set_preferred_arrival_time", "mode": "time"},
]

# Shown when waiting for home address
HOME_QUICK_REPLY = [
    {"type": "location", "label": "📍 開啟地圖選位置"},
]

# Shown when waiting for office address
OFFICE_QUICK_REPLY = [
    {"type": "location", "label": "🏢 開啟地圖選位置"},
]

# Shown when waiting for preferred_arrival_time
ARRIVAL_TIME_QUICK_REPLIES = [
    {"type": "datetimepicker", "label": "⏰ 選擇到公司時間",
     "data": "action=set_preferred_arrival_time", "mode": "time"},
    {"type": "message", "label": "07:30", "text": "07:30"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "08:30", "text": "08:30"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "09:30", "text": "09:30"},
]

# Shown when modifying today's arrival time
OVERRIDE_TODAY_TIME_QUICK_REPLIES = [
    {"type": "datetimepicker", "label": "⏰ 選擇今天時間",
     "data": "action=set_today_arrival_time", "mode": "time"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "10:00", "text": "10:00"},
    {"type": "message", "label": "14:00", "text": "14:00"},
    {"type": "message", "label": "17:00", "text": "17:00"},
]

# Shown when modifying tomorrow's arrival time
OVERRIDE_TOMORROW_TIME_QUICK_REPLIES = [
    {"type": "datetimepicker", "label": "⏰ 選擇明天時間",
     "data": "action=set_tomorrow_arrival_time", "mode": "time"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "08:30", "text": "08:30"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "10:00", "text": "10:00"},
    {"type": "message", "label": "09:30", "text": "09:30"},
]

# Shown after setup complete / after commute advice
MAIN_MENU_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",    "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",    "text": "今天搭捷運"},
    {"type": "message", "label": "📅 修改到公司時間", "text": "修改今天到公司時間"},
]

# Shown after commute advice reply
COMMUTE_RESULT_QUICK_REPLIES = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",       "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",       "text": "今天搭捷運"},
    {"type": "message", "label": "📊 查看設定",         "text": "查看設定"},
]

REMINDER_SETTING_QUICK_REPLIES = [
    {"type": "message", "label": "✅ 開啟自動提醒", "text": "開啟自動提醒"},
    {"type": "message", "label": "⏸ 關閉自動提醒", "text": "關閉自動提醒"},
]

TRANSPORT_MODE_NAME_MAP = {
    None: "自動判斷",
    "auto": "自動判斷",
    "shortest": "最短時間優先 (Google)",
    "bus": "公車優先",
    "metro": "捷運優先",
    "bus_to_metro": "公車轉捷運",
}

COMMAND_ALIASES = {
    "view_settings": {"查看設定"},
    "today_commute": {"今天通勤建議", "今日通勤建議", "通勤建議"},
    "dashboard_link": {"取得Dashboard連結", "取得dashboard連結", "Dashboard連結", "dashboard連結", "取得儀表板連結"},
    "tomorrow_departure": {"明天幾點出門"},
    "edit_today_arrival": {"修改今天到公司時間", "今天改到公司時間", "設定到公司時間", "修改出門時間"},
    "edit_tomorrow_arrival": {"修改明天到公司時間"},
    "reset": {"重新設定"},
    "send_home_location": {"傳送住家位置", "設定住家位置"},
    "send_office_location": {"傳送公司位置", "設定公司位置"},
    "test_bus": {"測試公車", "公車測試"},
    "test_metro": {"測試捷運", "捷運測試"},
    "test_reminder": {"測試提醒"},
    "test_quick_reply": {"測試按鈕"},
    "set_mode_auto": {"今天自動判斷", "今天交通自動"},
    "set_mode_shortest": {"優先選擇通勤時間短", "今天最短時間"},
    "set_mode_bus": {"今天搭公車", "今天坐公車"},
    "set_mode_metro": {"今天搭捷運", "今天坐捷運"},
    "set_mode_bus_to_metro": {"今天搭公車轉捷運", "今天公車轉捷運"},
    "view_mode_today": {"查看今天交通方式"},
    "enable_reminder": {"開啟自動提醒"},
    "disable_reminder": {"關閉自動提醒"},
    "view_reminder_setting": {"查看提醒設定"},
    "departure_left": {"已經出門了"},
    "departure_need_5": {"我還需要五分鐘", "還需要五分鐘"},
}

READY_MENU_TEXT = (
    "您目前設定已完成。\n"
    "可傳送：\n"
    "查看設定\n"
    "今天通勤建議\n"
    "取得 Dashboard 連結\n"
    "明天幾點出門\n"
    "修改明天到公司時間\n"
    "重新設定\n"
    "傳送住家位置\n"
    "傳送公司位置\n"
    "測試公車\n"
    "測試捷運\n"
    "今天自動判斷\n"
    "優先選擇通勤時間短\n"
    "今天搭公車\n"
    "今天搭捷運\n"
    "今天搭公車轉捷運\n"
    "查看今天交通方式"
)

def normalize_user_text(text: str) -> str:
    if not text:
        return ""
    return text.strip().replace("\u3000", " ").replace("\n", "").replace("\r", "").replace(" ", "")


def format_profile_text(profile, today_override_time: str | None = None, tomorrow_override_time: str | None = None, today_mode: str | None = None) -> str:
    home_address = profile.home_address or "尚未設定"
    office_address = profile.office_address or "尚未設定"
    preferred_arrival_time = profile.preferred_arrival_time or "尚未設定"
    reminder_status = "開啟" if getattr(profile, "reminder_enabled", True) else "關閉"
    mode_label = TRANSPORT_MODE_NAME_MAP.get(today_mode or "auto", "自動判斷")

    text = (
        "您目前設定如下：\n"
        f"🏠 住家位置：{home_address}\n"
        f"🏢 公司位置：{office_address}\n"
        f"⏰ 到公司時間：{preferred_arrival_time}\n"
        f"📢 自動提醒：{reminder_status}\n"
        f"🚇 今天交通方式：{mode_label}"
    )
    if today_override_time:
        text += f"\n📍 今天覆蓋到公司時間：{today_override_time}"
    if tomorrow_override_time:
        text += f"\n📅 明天覆蓋到公司時間：{tomorrow_override_time}"
    return text


def validate_pending_input(field_name: str, user_text: str):
    if field_name in {"preferred_arrival_time", "override_today_arrival_time", "override_tomorrow_arrival_time"}:
        value = user_text.strip()
        try:
            datetime.strptime(value, "%H:%M")
            return value, None
        except ValueError:
            return None, "時間格式錯誤，請輸入 HH:MM，例如 08:30"
    return None, "未知欄位"


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
        print(f"[geocode] error={e}")

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
    body_str = body.decode("utf-8")

    try:
        events = parser.parse(body_str, x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    db = SessionLocal()

    try:
        for event in events:
            line_user_id = event.source.user_id
            if not line_user_id:
                continue

            user = get_or_create_user(db, line_user_id=line_user_id)
            get_or_create_profile(db, user.id)

            if isinstance(event, FollowEvent):
                set_pending_field(db, user.id, "home_location")
                reply_token = event.reply_token
                await reply_with_quick_reply(
                    reply_token,
                    "歡迎使用智慧通勤助理！👋\n"
                    "請先完成以下設定，點擊下方按鈕可快速完成：",
                    SETUP_QUICK_REPLIES,
                )
                continue

            # --- PostbackEvent: datetime picker time selection ---
            if isinstance(event, PostbackEvent):
                postback_data = event.postback.data if event.postback else ""
                params = event.postback.params or {} if event.postback else {}
                # params is a dict for datetimepicker: {"time": "HH:mm"}
                time_value = params.get("time") if isinstance(params, dict) else getattr(params, "time", None)
                reply_token = event.reply_token
                today_date = today_taipei()
                tomorrow_date = today_date + timedelta(days=1)

                today_override = get_override_for_date(db, user.id, today_date)
                today_override_time = today_override.target_arrival_time if today_override and today_override.target_arrival_time else None
                tomorrow_override = get_override_for_date(db, user.id, tomorrow_date)
                tomorrow_override_time = tomorrow_override.target_arrival_time if tomorrow_override and tomorrow_override.target_arrival_time else None

                print(f"[postback] user_id={user.id} data={postback_data} time={time_value}")

                postback_parts = parse_qs(postback_data)
                postback_action = (postback_parts.get("action") or [""])[0]
                postback_choice = (postback_parts.get("choice") or [""])[0]
                if postback_action == "departure_check":
                    if postback_choice == "left":
                        confirm_departure_for_user(db, user.id, today_date)
                        await reply_text(
                            reply_token,
                            "收到，今天通勤計算已停止。Dashboard 會改看明天的通勤與提醒時間。",
                        )
                        continue

                    if postback_choice == "need_5":
                        override = snooze_departure_for_user(db, user.id, today_date)
                        snooze_text = override.departure_snoozed_until.strftime("%H:%M")
                        await reply_text(
                            reply_token,
                            f"好的，{snooze_text} 再提醒您出門。四分鐘後會先提醒一次，時間到會再提醒一次。",
                        )
                        continue

                if postback_data == "action=set_preferred_arrival_time" and time_value:
                    update_profile_field(db, user.id, "preferred_arrival_time", time_value)
                    clear_today_reminder_state_for_user(user.id)
                    set_pending_field(db, user.id, None)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-postback-preferred] error={e}")
                    updated_profile = get_profile(db, user.id)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已儲存到公司時間：{time_value}\n\n{format_profile_text(updated_profile, today_override_time, tomorrow_override_time)}",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if postback_data == "action=set_today_arrival_time" and time_value:
                    upsert_override(db, user.id, today_date, time_value)
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-postback-today] error={e}")
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已儲存今天到公司時間：{time_value}",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if postback_data == "action=set_tomorrow_arrival_time" and time_value:
                    upsert_override(db, user.id, tomorrow_date, time_value)
                    set_pending_field(db, user.id, None)
                    departure_time = await calculate_departure_time(get_profile(db, user.id), tomorrow_date, time_value)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已儲存明天到公司時間：{time_value}\n明天建議 {departure_time} 出門。",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                # Unknown postback — ignore
                continue

            if not isinstance(event, MessageEvent):
                continue

            message = event.message
            reply_token = event.reply_token

            # 1. 先處理 location message
            if isinstance(message, LocationMessageContent):
                profile = get_profile(db, user.id)
                current_step = profile.pending_field or get_next_setup_step(profile)

                lat = message.latitude
                lng = message.longitude
                title = message.title
                address = message.address
                raw_address = address or title or "未命名位置"

                if current_step == "home_location":
                    await save_location_or_address(db, user.id, "home", raw_address, lat=lat, lng=lng)
                    clear_today_reminder_state_for_user(user.id)
                    set_pending_field(db, user.id, "office_location")
                    await reply_with_quick_reply(
                        reply_token,
                        "已儲存住家位置。\n" + FIELD_PROMPTS["office_location"],
                        OFFICE_QUICK_REPLY,
                    )
                    continue

                if current_step == "office_location":
                    await save_location_or_address(db, user.id, "office", raw_address, lat=lat, lng=lng)
                    clear_today_reminder_state_for_user(user.id)
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        "已儲存公司位置。\n" + FIELD_PROMPTS["preferred_arrival_time"],
                        ARRIVAL_TIME_QUICK_REPLIES,
                    )
                    continue

                await reply_with_quick_reply(reply_token, READY_MENU_TEXT, MAIN_MENU_QUICK_REPLIES)
                continue

            # 2. 其他非文字、非位置訊息直接略過
            if not isinstance(message, TextMessageContent):
                continue

            user_text = message.text.strip()
            command_text = normalize_user_text(user_text)

            today_date = today_taipei()
            tomorrow_date = today_date + timedelta(days=1)

            today_override = get_override_for_date(db, user.id, today_date)
            today_override_time = today_override.target_arrival_time if today_override and today_override.target_arrival_time else None
            tomorrow_override = get_override_for_date(db, user.id, tomorrow_date)
            tomorrow_override_time = tomorrow_override.target_arrival_time if tomorrow_override and tomorrow_override.target_arrival_time else None

            # ===========================================================
            # 全域設定守衛：設定未完成的用戶，任何指令都先引導完成設定
            # ===========================================================
            _profile_for_guard = get_profile(db, user.id)
            _next_step = get_next_setup_step(_profile_for_guard)

            if _next_step is not None:
                # 例外1：允許通過的設定指令（含問候、重設、傳送位置指令）
                _setup_commands = (
                    COMMAND_ALIASES["send_home_location"]
                    | COMMAND_ALIASES["send_office_location"]
                    | COMMAND_ALIASES["reset"]
                    | COMMAND_ALIASES["dashboard_link"]
                    | {"嗨", "你好", "哈囉", "哈喽", "Hi", "Hello", "hello", "hi"}
                )
                # 例外2：正在填寫設定欄位且輸入內容確實像「地址」或「時間」
                _current_pending = _profile_for_guard.pending_field
                _is_valid_setup_input = False
                if _current_pending in {"home_location", "office_location"}:
                    # 只有看起來像地址的文字才放行
                    _is_valid_setup_input = looks_like_address(user_text)
                elif _current_pending == "preferred_arrival_time":
                    # 只有 HH:MM 格式的時間文字才放行
                    _parts = user_text.strip().split(":")
                    if len(_parts) == 2:
                        try:
                            _h, _m = int(_parts[0]), int(_parts[1])
                            _is_valid_setup_input = 0 <= _h <= 23 and 0 <= _m <= 59
                        except ValueError:
                            pass

                if command_text not in _setup_commands and not _is_valid_setup_input:
                    set_pending_field(db, user.id, _next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        "⚙️ 請先完成基本設定，才能使用完整功能！\n點擊下方按鈕快速設定：",
                        SETUP_QUICK_REPLIES,
                    )
                    continue
            # ===========================================================

            if command_text in {"嗨", "你好", "哈囉", "哈喽", "Hi", "Hello", "hello", "hi"}:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is None:
                    await reply_text(reply_token, "你好，我是智慧通勤助理。\n" + READY_MENU_TEXT)
                else:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        "你好，我是智慧通勤助理！\n請先完成以下設定，點擊下方按鈕可快速完成：",
                        SETUP_QUICK_REPLIES,
                    )
                continue

            if command_text in COMMAND_ALIASES["send_home_location"]:
                set_pending_field(db, user.id, "home_location")
                await reply_with_quick_reply(reply_token, FIELD_PROMPTS["home_location"], HOME_QUICK_REPLY)
                continue

            if command_text in COMMAND_ALIASES["send_office_location"]:
                set_pending_field(db, user.id, "office_location")
                await reply_with_quick_reply(reply_token, FIELD_PROMPTS["office_location"], OFFICE_QUICK_REPLY)
                continue

            if command_text in COMMAND_ALIASES["reset"]:
                reset_profile_for_reconfigure(db, user.id)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    "好的，現在開始重新設定。\n" + FIELD_PROMPTS["home_location"],
                    HOME_QUICK_REPLY,
                )
                continue

            if command_text in COMMAND_ALIASES["view_settings"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                today_mode = get_transport_mode_override(db, user.id, today_date)
                if next_step is None:
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token, 
                        format_profile_text(profile, today_override_time, tomorrow_override_time, today_mode),
                        MAIN_MENU_QUICK_REPLIES
                    )
                else:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        format_profile_text(profile, today_override_time, tomorrow_override_time, today_mode) + "\n\n" + FIELD_PROMPTS[next_step],
                        MAIN_MENU_QUICK_REPLIES
                    )
                continue

            if command_text in COMMAND_ALIASES["dashboard_link"]:
                dashboard_url = build_dashboard_view_url(
                    user_id=user.id,
                    public_url=PUBLIC_URL,
                    request_base_url=str(request.base_url),
                )
                await reply_text(
                    reply_token,
                    "外接螢幕看板連結：\n"
                    f"{dashboard_url}\n\n"
                    "在要顯示的螢幕上打開這個網址，並把瀏覽器切成全螢幕即可。",
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
                await reply_with_quick_reply(
                    reply_token,
                    f"目前自動提醒：{'開啟' if profile.reminder_enabled else '關閉'}\n可用下方按鈕切換。",
                    REMINDER_SETTING_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["departure_left"]:
                confirm_departure_for_user(db, user.id, today_date)
                await reply_text(
                    reply_token,
                    "收到，今天通勤計算已停止。Dashboard 會改看明天的通勤與提醒時間。",
                )
                continue

            if command_text in COMMAND_ALIASES["departure_need_5"]:
                override = snooze_departure_for_user(db, user.id, today_date)
                snooze_text = override.departure_snoozed_until.strftime("%H:%M")
                await reply_text(
                    reply_token,
                    f"好的，{snooze_text} 再提醒您出門。四分鐘後會先提醒一次，時間到會再提醒一次。",
                )
                continue

            if command_text in COMMAND_ALIASES["set_mode_auto"]:
                upsert_transport_mode_override(db, user.id, today_date, "auto")
                clear_today_reminder_state_for_user(user.id)

                advice = await build_today_commute_payload(db, user.id, today_date, "auto", "好的，今天交通方式切換為：自動判斷。")
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date, plan=advice)
                except Exception as e:
                    print(f"[freeze-auto] error={e}")
                await reply_multi_messages_with_quick_reply(
                    reply_token,
                    [advice.get("text", "已設定。")],
                    COMMUTE_RESULT_QUICK_REPLIES
                )
                continue

            if command_text in COMMAND_ALIASES["set_mode_shortest"]:
                upsert_transport_mode_override(db, user.id, today_date, "shortest")
                clear_today_reminder_state_for_user(user.id)

                advice = await build_today_commute_payload(db, user.id, today_date, "shortest", "好的，今天優先選擇最短時間：")
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date, plan=advice)
                except Exception as e:
                    print(f"[freeze-shortest] error={e}")
                await reply_multi_messages_with_quick_reply(
                    reply_token,
                    [advice.get("text", "已設定。")],
                    COMMUTE_RESULT_QUICK_REPLIES
                )
                continue

            if command_text in COMMAND_ALIASES["set_mode_bus"]:
                upsert_transport_mode_override(db, user.id, today_date, "bus")
                clear_today_reminder_state_for_user(user.id)

                advice = await build_today_commute_payload(db, user.id, today_date, "bus", "好的，今天切換為：公車優先。")
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date, plan=advice)
                except Exception as e:
                    print(f"[freeze-bus] error={e}")
                await reply_multi_messages_with_quick_reply(
                    reply_token,
                    [advice.get("text", "已設定。")],
                    COMMUTE_RESULT_QUICK_REPLIES
                )
                continue

            if command_text in COMMAND_ALIASES["set_mode_metro"]:
                upsert_transport_mode_override(db, user.id, today_date, "metro")
                clear_today_reminder_state_for_user(user.id)

                advice = await build_today_commute_payload(db, user.id, today_date, "metro", "好的，今天切換為：捷運優先。")
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date, plan=advice)
                except Exception as e:
                    print(f"[freeze-metro] error={e}")
                await reply_multi_messages_with_quick_reply(
                    reply_token,
                    [advice.get("text", "已設定。")],
                    COMMUTE_RESULT_QUICK_REPLIES
                )
                continue

            if command_text in COMMAND_ALIASES["set_mode_bus_to_metro"]:
                upsert_transport_mode_override(db, user.id, today_date, "bus_to_metro")
                clear_today_reminder_state_for_user(user.id)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-bus-to-metro] error={e}")
                await reply_text(reply_token, "已設定今天交通方式為：公車轉捷運")
                continue

            if command_text in COMMAND_ALIASES["view_mode_today"]:
                current_mode = get_transport_mode_override(db, user.id, today_date) or "auto"
                await reply_text(reply_token, f"今天交通方式設定：{TRANSPORT_MODE_NAME_MAP.get(current_mode, '自動判斷')}")
                continue

            if command_text in COMMAND_ALIASES["test_bus"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue

                bus_snapshot = await get_bus_realtime_snapshot(profile)
                if not bus_snapshot.get("available"):
                    await reply_text(reply_token, "附近站牌測試：\n無即時資訊")
                    continue

                nearby_stops = bus_snapshot.get("nearby_stops", []) or []
                first_stop = bus_snapshot.get("first_stop", {}) or {}
                valid_eta_list = bus_snapshot.get("valid_eta_list", []) or []

                lines = ["附近站牌測試："]
                for idx, stop in enumerate(nearby_stops[:5], start=1):
                    lines.append(
                        f"{idx}. {stop.get('stop_name', '無法識別站牌')} | "
                        f"stop_id={stop.get('stop_id', '無即時資訊')} | "
                        f"uid={stop.get('stop_uid', '無即時資訊')}"
                    )

                lines.append("")
                lines.append(f"最近站牌 ETA 測試：{first_stop.get('stop_name', '無法識別站牌')}")

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

                await reply_text(reply_token, "\n".join(lines))
                continue

            if command_text in COMMAND_ALIASES["test_metro"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue

                metro_snapshot = await get_metro_snapshot(profile)
                if not metro_snapshot.get("available"):
                    await reply_text(reply_token, "捷運測試：\n無即時資訊")
                    continue

                station = metro_snapshot.get("station", {}) or {}
                distance_km = metro_snapshot.get("distance_km")
                walk_minutes = metro_snapshot.get("walk_minutes")

                lines = [
                    "捷運測試：",
                    f"最近捷運站：{station.get('name', '無法識別捷運站')}",
                    f"直線距離：約 {distance_km:.2f} 公里" if distance_km is not None else "直線距離：無法估算",
                    f"步行到捷運站：約 {walk_minutes if walk_minutes is not None else '無法估算'} 分鐘",
                ]
                await reply_text(reply_token, "\n".join(lines))
                continue

            if command_text in COMMAND_ALIASES["test_reminder"]:
                await reply_text(reply_token, READY_MENU_TEXT)
                continue

            if command_text in COMMAND_ALIASES["test_quick_reply"]:
                print(f"[test-qr] user_id={user.id} sending quick reply test")
                test_items = [
                    {"type": "message", "label": "✅ 按鈕測試 A", "text": "今天通勤建議"},
                    {"type": "message", "label": "⏰ 按鈕測試 B", "text": "修改今天到公司時間"},
                    {"type": "location", "label": "📍 地圖測試"},
                ]
                await reply_with_quick_reply(
                    reply_token,
                    "🧪 Quick Reply 按鈕測試\n如果您看到下方按鈕，代表功能正常！",
                    test_items,
                )
                continue

            if command_text in COMMAND_ALIASES["today_commute"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue

                payload = await build_today_commute_payload(
                    db=db,
                    user_id=user.id,
                    target_date=today_date,
                    force_mode_override=None,
                    header="今日通勤建議：",
                )
                if not payload.get("ok"):
                    await reply_text(reply_token, "今日通勤建議：\n無法建立通勤建議")
                    continue

                try:
                    await freeze_today_reminder_payload(
                        db=db,
                        user_id=user.id,
                        target_date=today_date,
                        plan=payload,
                    )
                except Exception as e:
                    print(f"[freeze-today-commute] error={e}")

                await reply_with_quick_reply(reply_token, payload["text"], COMMUTE_RESULT_QUICK_REPLIES)
                continue

            if command_text in COMMAND_ALIASES["tomorrow_departure"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue

                effective_arrival_time = profile.preferred_arrival_time
                override = get_override_for_date(db, user.id, tomorrow_date)
                if override and override.target_arrival_time:
                    effective_arrival_time = override.target_arrival_time

                departure_time = await calculate_departure_time(profile, tomorrow_date, effective_arrival_time)
                await reply_text(reply_token, f"明天建議 {departure_time} 出門。")
                continue

            if command_text in COMMAND_ALIASES["edit_today_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue

                set_pending_field(db, user.id, "override_today_arrival_time")
                await reply_with_quick_reply(
                    reply_token,
                    FIELD_PROMPTS["override_today_arrival_time"],
                    OVERRIDE_TODAY_TIME_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["edit_tomorrow_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue

                set_pending_field(db, user.id, "override_tomorrow_arrival_time")
                await reply_with_quick_reply(
                    reply_token,
                    FIELD_PROMPTS["override_tomorrow_arrival_time"],
                    OVERRIDE_TOMORROW_TIME_QUICK_REPLIES,
                )
                continue

            profile = get_profile(db, user.id)
            current_step = profile.pending_field or get_next_setup_step(profile)

            if current_step in {"home_location", "office_location"}:
                typed_address = user_text.strip()
                if not typed_address:
                    await reply_with_quick_reply(reply_token, FIELD_PROMPTS[current_step],
                                                 HOME_QUICK_REPLY if current_step == "home_location" else OFFICE_QUICK_REPLY)
                    continue

                if not looks_like_address(typed_address):
                    await reply_with_quick_reply(reply_token, FIELD_PROMPTS[current_step],
                                                 HOME_QUICK_REPLY if current_step == "home_location" else OFFICE_QUICK_REPLY)
                    continue

                try:
                    if current_step == "home_location":
                        await save_location_or_address(db, user.id, "home", typed_address)
                        clear_today_reminder_state_for_user(user.id)
                        set_pending_field(db, user.id, "office_location")
                        await reply_with_quick_reply(
                            reply_token,
                            "已儲存住家位置。\n" + FIELD_PROMPTS["office_location"],
                            OFFICE_QUICK_REPLY,
                        )
                        continue

                    if current_step == "office_location":
                        await save_location_or_address(db, user.id, "office", typed_address)
                        clear_today_reminder_state_for_user(user.id)
                        set_pending_field(db, user.id, "preferred_arrival_time")
                        await reply_with_quick_reply(
                            reply_token,
                            "已儲存公司位置。\n" + FIELD_PROMPTS["preferred_arrival_time"],
                            ARRIVAL_TIME_QUICK_REPLIES,
                        )
                        continue
                except Exception as e:
                    print(f"[text-address] error={e}")
                    await reply_text(reply_token, "地址辨識失敗，請重新輸入完整地址或直接傳送位置。")
                    continue

            if current_step in {"preferred_arrival_time", "override_today_arrival_time", "override_tomorrow_arrival_time"}:
                value, error_message = validate_pending_input(current_step, user_text)
                if error_message:
                    if current_step == "preferred_arrival_time":
                        qr = ARRIVAL_TIME_QUICK_REPLIES
                    elif current_step == "override_today_arrival_time":
                        qr = OVERRIDE_TODAY_TIME_QUICK_REPLIES
                    else:
                        qr = OVERRIDE_TOMORROW_TIME_QUICK_REPLIES
                    await reply_with_quick_reply(reply_token, error_message + "\n" + FIELD_PROMPTS[current_step], qr)
                    continue

                if current_step == "override_today_arrival_time":
                    upsert_override(db, user.id, today_date, value)
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-today-override] error={e}")
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已儲存今天到公司時間：{value}",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if current_step == "override_tomorrow_arrival_time":
                    upsert_override(db, user.id, tomorrow_date, value)
                    set_pending_field(db, user.id, None)
                    departure_time = await calculate_departure_time(get_profile(db, user.id), tomorrow_date, value)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已儲存明天到公司時間：{value}\n明天建議 {departure_time} 出門。",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                update_profile_field(db, user.id, "preferred_arrival_time", value)
                clear_today_reminder_state_for_user(user.id)
                set_pending_field(db, user.id, None)

                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-preferred-arrival] error={e}")

                updated_profile = get_profile(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"已儲存到公司時間：{value}\n\n{format_profile_text(updated_profile, today_override_time, tomorrow_override_time)}",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            next_step = get_next_setup_step(profile)
            if next_step is None:
                await reply_with_quick_reply(reply_token, READY_MENU_TEXT, MAIN_MENU_QUICK_REPLIES)
            else:
                set_pending_field(db, user.id, next_step)
                if next_step == "home_location":
                    await reply_with_quick_reply(reply_token, FIELD_PROMPTS[next_step], HOME_QUICK_REPLY)
                elif next_step == "office_location":
                    await reply_with_quick_reply(reply_token, FIELD_PROMPTS[next_step], OFFICE_QUICK_REPLY)
                elif next_step == "preferred_arrival_time":
                    await reply_with_quick_reply(reply_token, FIELD_PROMPTS[next_step], ARRIVAL_TIME_QUICK_REPLIES)
                else:
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])

    finally:
        db.close()

    return {"ok": True}
