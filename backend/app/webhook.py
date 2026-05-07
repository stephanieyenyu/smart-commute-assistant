import json
import os
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Request
from linebot.v3.webhook import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent, FollowEvent, PostbackEvent

from app.config import LINE_CHANNEL_SECRET
from app.db import SessionLocal
from app.line_client import reply_text, reply_with_quick_reply, reply_multi_messages_with_quick_reply, reply_flex_message
from app.crud import (
    get_or_create_user,
    get_or_create_profile,
    get_profile,
    get_override_for_date,
    set_pending_field,
    update_profile_field,
    upsert_override,
    upsert_transport_mode_override,
    get_transport_mode_override,
    set_reminder_enabled,
)
from app.service import (
    calculate_departure_time,
    build_today_commute_payload,
    freeze_today_reminder_payload,
    get_bus_realtime_snapshot,
    get_metro_snapshot,
)
from app.reminder_scheduler import clear_today_reminder_state_for_user
from app.schedule_summary import (
    format_commute_setting_text,
    format_weekdays,
    format_weekly_schedule_text,
    schedule_arrival_time,
    schedule_destination,
)

router = APIRouter()
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
LIFF_URL = "https://liff.line.me/2009982765-aKb3T2ca"


def today_taipei() -> date:
    return datetime.now(TAIPEI_TZ).date()


def normalize(text: str) -> str:
    if not text:
        return ""
    return text.strip().replace("\u3000", " ").replace("\n", "").replace("\r", "").replace(" ", "")


parser = WebhookParser(LINE_CHANNEL_SECRET)

# ── Quick Reply sets ──────────────────────────────────────────────────────────

MAIN_MENU_QR = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",   "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",   "text": "今天搭捷運"},
    {"type": "message", "label": "📺 看板",         "text": "看板"},
    {"type": "message", "label": "⚙️ 系統設定",     "text": "系統設定"},
]

COMMUTE_RESULT_QR = [
    {"type": "message", "label": "🚄 最短時間優先", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "🚌 今天搭公車",   "text": "今天搭公車"},
    {"type": "message", "label": "🚇 今天搭捷運",   "text": "今天搭捷運"},
    {"type": "message", "label": "📊 查看設定",     "text": "查看設定"},
]

SETTINGS_QR = [
    {"type": "uri",     "label": "➕ 新增排程設定",  "uri": f"{LIFF_URL}?mode=create"},
    {"type": "message", "label": "📅 一週排程設定",  "text": "一週排程設定"},
    {"type": "message", "label": "✏️ 編輯排程",      "text": "編輯排程"},
    {"type": "message", "label": "🔔 開啟自動提醒",  "text": "開啟自動提醒"},
    {"type": "message", "label": "🔕 關閉自動提醒",  "text": "關閉自動提醒"},
    {"type": "message", "label": "📋 查看提醒設定",  "text": "查看提醒設定"},
    {"type": "message", "label": "📊 查看設定",      "text": "查看設定"},
]

OVERRIDE_TODAY_QR = [
    {"type": "datetimepicker", "label": "⏰ 選擇今天時間",
     "data": "action=set_today_arrival_time", "mode": "time"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "10:00", "text": "10:00"},
]

OVERRIDE_TOMORROW_QR = [
    {"type": "datetimepicker", "label": "⏰ 選擇明天時間",
     "data": "action=set_tomorrow_arrival_time", "mode": "time"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "09:00", "text": "09:00"},
]

TRANSPORT_MODE_NAME_MAP = {
    None: "自動判斷", "auto": "自動判斷",
    "shortest": "最短時間優先 (Google)",
    "bus": "公車優先", "metro": "捷運優先", "bus_to_metro": "公車轉捷運",
}

COMMAND_ALIASES = {
    "view_settings":        {"查看設定"},
    "today_commute":        {"今天通勤建議", "今日通勤建議", "通勤建議"},
    "tomorrow_departure":   {"明天幾點出門"},
    "edit_today_arrival":   {"修改今天到公司時間", "今天改到公司時間", "設定到公司時間"},
    "edit_tomorrow_arrival":{"修改明天到公司時間"},
    "reset":                {"重新設定"},
    "system_settings":      {"系統設定", "系統設定選單", "設定選單"},
    "help":                 {"指令說明", "說明", "help", "Help"},
    "set_mode_auto":        {"今天自動判斷", "今天交通自動"},
    "set_mode_shortest":    {"優先選擇通勤時間短", "今天最短時間"},
    "set_mode_bus":         {"今天搭公車", "今天坐公車"},
    "set_mode_metro":       {"今天搭捷運", "今天坐捷運"},
    "set_mode_bus_to_metro":{"今天搭公車轉捷運", "今天公車轉捷運"},
    "enable_reminder":      {"開啟自動提醒"},
    "disable_reminder":     {"關閉自動提醒"},
    "view_reminder_setting":{"查看提醒設定"},
    "add_schedule":         {"新增排程設定", "新增排程"},
    "weekly_schedule":      {"一週排程設定", "一周排程設定", "一週排程", "一周排程", "查看一週排程設定"},
    "edit_schedule":        {"編輯排程", "修改排程"},
    "personal_dashboard_link": {"個人看板連結", "取得個人看板連結", "Dashboard連結", "看板連結"},
    "family_dashboard_link": {"家庭看板連結", "取得家庭看板連結"},
}

TOPIC_CARD_TITLES = ("設定通勤路線", "通勤建議", "交通方式", "看板", "系統設定")

TOPIC_CARD_ALIASES = {
    "設定通勤路線": "設定通勤路線",
    "通勤路線": "設定通勤路線",
    "通勤建議": "通勤建議",
    "交通建議": "通勤建議",
    "交通方式": "交通方式",
    "通勤方式": "交通方式",
    "看板": "看板",
    "看板管理": "看板",
    "Dashboard": "看板",
    "dashboard": "看板",
    "系統設定": "系統設定",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def format_profile_text(schedule, profile, today_mode=None):
    mode_label = TRANSPORT_MODE_NAME_MAP.get(today_mode or "auto", "自動判斷")
    return format_commute_setting_text(schedule, profile, mode_label)


def build_liff_url(mode: str | None = None, **params) -> str:
    query = {key: value for key, value in params.items() if value is not None}
    if mode:
        query["mode"] = mode
    if not query:
        return LIFF_URL
    return f"{LIFF_URL}?{urlencode(query)}"


def public_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def build_dashboard_url(request: Request, line_user_id: str, view: str = "personal") -> str:
    query = urlencode({"userId": line_user_id, "view": view})
    return f"{public_base_url(request)}/dashboard?{query}"


def _build_help_cards_by_title() -> dict[str, dict]:
    """Build reusable topic bubbles for 指令說明 and single-topic replies."""
    create_url = build_liff_url("create")

    def btn(label: str, text: str, style: str = "secondary") -> dict:
        return {
            "type": "button",
            "action": {"type": "message", "label": label, "text": text},
            "style": style,
            "height": "sm",
            "margin": "xs",
        }

    def uri_btn(label: str, uri: str) -> dict:
        return {
            "type": "button",
            "action": {"type": "uri", "label": label, "uri": uri},
            "style": "primary",
            "height": "sm",
            "margin": "xs",
        }

    def bubble(header_color: str, icon: str, title: str, rows: list, footer_btns: list) -> dict:
        body_contents = [
            {"type": "text", "text": title, "weight": "bold",
             "size": "lg", "color": "#1a1a2e", "margin": "none"},
        ]
        for label, desc in rows:
            body_contents.append({
                "type": "box", "layout": "vertical", "margin": "md",
                "contents": [
                    {"type": "text", "text": label, "weight": "bold",
                     "size": "sm", "color": header_color},
                    {"type": "text", "text": desc, "size": "xs",
                     "color": "#666666", "wrap": True},
                ]
            })
        return {
            "type": "bubble",
            "size": "kilo",
            "header": {
                "type": "box", "layout": "vertical",
                "backgroundColor": header_color, "paddingAll": "14px",
                "contents": [
                    {"type": "text", "text": f"{icon} {title}",
                     "weight": "bold", "size": "lg", "color": "#ffffff"},
                ]
            },
            "body": {
                "type": "box", "layout": "vertical",
                "paddingAll": "14px", "spacing": "sm",
                "contents": body_contents,
            },
            "footer": {
                "type": "box", "layout": "vertical",
                "spacing": "xs", "paddingAll": "10px",
                "contents": footer_btns,
            }
        }

    return {
        "設定通勤路線": bubble("#4a90d9", "🗺️", "設定通勤路線",
            [
                ("新增排程設定", "新增一組通勤排程；新增流程不會直接覆蓋舊排程"),
                ("一週排程設定", "查看目前一整週的啟用日、到達時間與目的地，無關表單填寫"),
                ("編輯排程", "先選擇要修改的排程，再進行部分修改"),
                ("查看目前設定", "傳送「查看設定」查看已儲存的通勤資訊"),
            ],
            [
                uri_btn("➕ 新增排程設定", create_url),
                btn("📅 一週排程設定", "一週排程設定"),
                btn("✏️ 編輯排程", "編輯排程"),
                btn("📊 查看設定", "查看設定"),
            ]
        ),
        "通勤建議": bubble("#7b68ee", "🚆", "通勤建議",
            [
                ("今天通勤建議", "查看今天最佳通勤方式與建議出發時間"),
                ("明天幾點出門", "預估明天需要幾點出發"),
                ("修改今天到公司時間", "臨時更改今天的到達時間"),
            ],
            [
                btn("🚆 今天通勤建議", "今天通勤建議", "primary"),
                btn("⏰ 明天幾點出門", "明天幾點出門"),
                btn("✏️ 修改今天時間", "修改今天到公司時間"),
            ]
        ),
        "交通方式": bubble("#27ae60", "🚌", "交通方式",
            [
                ("今天搭公車", "強制切換為公車優先模式"),
                ("今天搭捷運", "強制切換為捷運優先模式"),
                ("最短時間優先", "Google 推薦最短路徑"),
                ("今天自動判斷", "恢復自動判斷最佳交通"),
            ],
            [
                btn("🚌 今天搭公車", "今天搭公車"),
                btn("🚇 今天搭捷運", "今天搭捷運"),
                btn("🚄 最短時間優先", "優先選擇通勤時間短"),
            ]
        ),
        "看板": bubble("#0f766e", "📺", "看板",
            [
                ("個人看板連結", "開啟只顯示自己通勤資訊的即時看板"),
                ("家庭看板連結", "開啟家庭檢視看板，方便共用螢幕查看"),
                ("一週排程設定", "看板和 LINE 文字會使用同一份排程資料"),
            ],
            [
                btn("📺 個人看板連結", "個人看板連結", "primary"),
                btn("🏠 家庭看板連結", "家庭看板連結"),
                btn("📅 一週排程設定", "一週排程設定"),
            ]
        ),
        "系統設定": bubble("#e67e22", "⚙️", "系統設定",
            [
                ("系統設定選單", "顯示完整設定選單"),
                ("開啟/關閉自動提醒", "控制每日自動出發提醒"),
                ("查看提醒設定", "查看目前提醒開關狀態"),
            ],
            [
                btn("⚙️ 系統設定選單", "系統設定選單", "primary"),
                btn("🔔 開啟自動提醒", "開啟自動提醒"),
                btn("🔕 關閉自動提醒", "關閉自動提醒"),
            ]
        ),
    }


def build_help_flex() -> list[dict]:
    """Build Flex Message Carousel for 指令說明 — each bubble has footer action buttons."""
    cards_by_title = _build_help_cards_by_title()
    return [cards_by_title[title] for title in TOPIC_CARD_TITLES]


def build_topic_help_card(topic_title: str) -> dict | None:
    return _build_help_cards_by_title().get(topic_title)


def topic_title_for_command(command_text: str) -> str | None:
    return TOPIC_CARD_ALIASES.get(command_text)


# ── Webhook Router ────────────────────────────────────────────────────────────

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

            # ── FollowEvent：歡迎 + 引導開啟 LIFF ─────────────────────────
            if isinstance(event, FollowEvent):
                reply_token = event.reply_token
                await reply_with_quick_reply(
                    reply_token,
                    "👋 歡迎使用智慧通勤助理！\n"
                    "請點擊下方「設定通勤路線」按鈕，在網頁中完成您的通勤設定。\n"
                    "設定完成後，每天將自動提醒您最佳出發時間！",
                    [
                        {"type": "uri",     "label": "📝 設定通勤路線", "uri": LIFF_URL},
                        {"type": "message", "label": "📋 指令說明",     "text": "指令說明"},
                    ],
                )
                continue

            # ── PostbackEvent：日期時間選擇器 ─────────────────────────────
            if isinstance(event, PostbackEvent):
                postback_data = event.postback.data if event.postback else ""
                params = event.postback.params or {} if event.postback else {}
                time_value = params.get("time") if isinstance(params, dict) else getattr(params, "time", None)
                reply_token = event.reply_token
                today_date    = today_taipei()
                tomorrow_date = today_date + timedelta(days=1)

                if postback_data == "action=set_today_arrival_time" and time_value:
                    upsert_override(db, user.id, today_date, time_value)
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-postback-today] error={e}")
                    await reply_with_quick_reply(
                        reply_token, f"✅ 已儲存今天到公司時間：{time_value}", MAIN_MENU_QR)
                    continue

                if postback_data == "action=set_tomorrow_arrival_time" and time_value:
                    upsert_override(db, user.id, tomorrow_date, time_value)
                    profile = get_profile(db, user.id)
                    departure_time = await calculate_departure_time(profile, tomorrow_date, time_value)
                    await reply_with_quick_reply(
                        reply_token,
                        f"✅ 已儲存明天到公司時間：{time_value}\n明天建議 {departure_time} 出門。",
                        MAIN_MENU_QR)
                    continue
                continue

            if not isinstance(event, MessageEvent):
                continue
            message = event.message
            reply_token = event.reply_token
            if not isinstance(message, TextMessageContent):
                continue

            user_text    = message.text.strip()
            command_text = normalize(user_text)
            today_date    = today_taipei()
            tomorrow_date = today_date + timedelta(days=1)

            if command_text in COMMAND_ALIASES["add_schedule"]:
                await reply_with_quick_reply(
                    reply_token,
                    "新增排程設定會建立新的通勤排程，不會直接覆蓋既有排程。",
                    [{"type": "uri", "label": "➕ 新增排程設定", "uri": build_liff_url("create")}],
                )
                continue

            if command_text in COMMAND_ALIASES["weekly_schedule"]:
                schedule = user.schedule
                await reply_with_quick_reply(
                    reply_token,
                    format_weekly_schedule_text(schedule),
                    MAIN_MENU_QR,
                )
                continue

            if command_text in COMMAND_ALIASES["edit_schedule"]:
                schedule = user.schedule
                if not schedule:
                    await reply_with_quick_reply(
                        reply_token,
                        "目前尚未建立排程。請先新增一組通勤排程。",
                        [{"type": "uri", "label": "➕ 新增排程設定", "uri": build_liff_url("create")}],
                    )
                    continue
                edit_url = build_liff_url("edit", scheduleId=schedule.id)
                await reply_with_quick_reply(
                    reply_token,
                    "請選擇要編輯的排程：\n"
                    f"{schedule.id}. {schedule_arrival_time(schedule)} 到{schedule_destination(schedule)}"
                    f"（{format_weekdays(schedule.days)}）\n\n"
                    "進入編輯後可只修改時間、地點、提醒星期或提醒開關。",
                    [{"type": "uri", "label": "✏️ 編輯目前排程", "uri": edit_url}],
                )
                continue

            if command_text in COMMAND_ALIASES["personal_dashboard_link"]:
                dashboard_url = build_dashboard_url(request, line_user_id, "personal")
                await reply_with_quick_reply(
                    reply_token,
                    "個人看板連結：\n"
                    f"{dashboard_url}\n\n"
                    "看板會和 LINE 的目前設定使用同一份排程資料。",
                    [{"type": "uri", "label": "📺 開啟個人看板", "uri": dashboard_url}],
                )
                continue

            if command_text in COMMAND_ALIASES["family_dashboard_link"]:
                dashboard_url = build_dashboard_url(request, line_user_id, "family")
                await reply_with_quick_reply(
                    reply_token,
                    "家庭看板連結：\n"
                    f"{dashboard_url}\n\n"
                    "家庭看板使用同一份排程摘要資料，適合放在共用螢幕查看。",
                    [{"type": "uri", "label": "🏠 開啟家庭看板", "uri": dashboard_url}],
                )
                continue

            topic_title = topic_title_for_command(command_text)
            if topic_title:
                topic_card = build_topic_help_card(topic_title)
                if topic_card:
                    await reply_flex_message(reply_token, topic_title, topic_card)
                    continue

            # ── 系統設定 ──────────────────────────────────────────────────
            if command_text in COMMAND_ALIASES["system_settings"]:
                await reply_with_quick_reply(
                    reply_token,
                    "⚙️ 系統設定選單\n請選擇以下操作：",
                    SETTINGS_QR,
                )
                continue

            # ── 指令說明 → Flex Carousel ──────────────────────────────────
            if command_text in COMMAND_ALIASES["help"]:
                cards = build_help_flex()
                await reply_flex_message(reply_token, "📖 指令說明", cards)
                continue

            # ── 查看設定 ──────────────────────────────────────────────────
            if command_text in COMMAND_ALIASES["view_settings"]:
                profile  = get_profile(db, user.id)
                schedule = user.schedule
                today_mode = get_transport_mode_override(db, user.id, today_date)
                await reply_with_quick_reply(
                    reply_token,
                    format_profile_text(schedule, profile, today_mode),
                    MAIN_MENU_QR,
                )
                continue

            # ── 開啟 / 關閉 / 查看提醒 ───────────────────────────────────
            if command_text in COMMAND_ALIASES["enable_reminder"]:
                set_reminder_enabled(db, user.id, True)
                await reply_with_quick_reply(reply_token, "✅ 已開啟自動提醒。", MAIN_MENU_QR)
                continue

            if command_text in COMMAND_ALIASES["disable_reminder"]:
                set_reminder_enabled(db, user.id, False)
                await reply_with_quick_reply(reply_token, "🔕 已關閉自動提醒。", MAIN_MENU_QR)
                continue

            if command_text in COMMAND_ALIASES["view_reminder_setting"]:
                profile = get_profile(db, user.id)
                schedule = user.schedule
                enabled = (schedule.reminder_enabled if schedule else getattr(profile, "reminder_enabled", True))
                await reply_with_quick_reply(
                    reply_token,
                    f"目前自動提醒：{'🔔 開啟' if enabled else '🔕 關閉'}",
                    SETTINGS_QR,
                )
                continue

            # ── 重新設定 ──────────────────────────────────────────────────
            if command_text in COMMAND_ALIASES["reset"]:
                await reply_with_quick_reply(
                    reply_token,
                    "🔄 請點擊下方按鈕，在設定頁面中重新設定通勤路線。",
                    [{"type": "uri", "label": "📝 重新設定通勤路線", "uri": LIFF_URL}],
                )
                continue

            # ── 交通方式設定 ──────────────────────────────────────────────
            async def set_mode_and_reply(mode: str, header: str):
                upsert_transport_mode_override(db, user.id, today_date, mode)
                clear_today_reminder_state_for_user(user.id)
                advice = await build_today_commute_payload(db, user.id, today_date, mode, header)
                try:
                    await freeze_today_reminder_payload(db, user.id, today_date, plan=advice)
                except Exception as e:
                    print(f"[freeze-{mode}] error={e}")
                await reply_multi_messages_with_quick_reply(
                    reply_token, [advice.get("text", "已設定。")], COMMUTE_RESULT_QR)

            if command_text in COMMAND_ALIASES["set_mode_auto"]:
                await set_mode_and_reply("auto", "好的，今天交通方式切換為：自動判斷。")
                continue
            if command_text in COMMAND_ALIASES["set_mode_shortest"]:
                await set_mode_and_reply("shortest", "好的，今天優先選擇最短時間：")
                continue
            if command_text in COMMAND_ALIASES["set_mode_bus"]:
                await set_mode_and_reply("bus", "好的，今天切換為：公車優先。")
                continue
            if command_text in COMMAND_ALIASES["set_mode_metro"]:
                await set_mode_and_reply("metro", "好的，今天切換為：捷運優先。")
                continue
            if command_text in COMMAND_ALIASES["set_mode_bus_to_metro"]:
                await set_mode_and_reply("bus_to_metro", "好的，今天切換為：公車轉捷運。")
                continue

            # ── 今天通勤建議 ──────────────────────────────────────────────
            if command_text in COMMAND_ALIASES["today_commute"]:
                profile = get_profile(db, user.id)
                schedule = user.schedule
                if not schedule or not schedule.origin_address or not schedule.dest_address:
                    await reply_with_quick_reply(
                        reply_token,
                        "⚠️ 尚未設定通勤路線，請先完成設定。",
                        [{"type": "uri", "label": "📝 設定通勤路線", "uri": LIFF_URL}],
                    )
                    continue
                payload = await build_today_commute_payload(
                    db=db, user_id=user.id, target_date=today_date,
                    force_mode_override=None, header="今日通勤建議：",
                )
                if not payload.get("ok"):
                    await reply_text(reply_token, "今日通勤建議：\n無法建立通勤建議，請確認設定是否正確。")
                    continue
                try:
                    await freeze_today_reminder_payload(db=db, user_id=user.id, target_date=today_date, plan=payload)
                except Exception as e:
                    print(f"[freeze-today-commute] error={e}")
                await reply_with_quick_reply(reply_token, payload["text"], COMMUTE_RESULT_QR)
                continue

            # ── 明天幾點出門 ──────────────────────────────────────────────
            if command_text in COMMAND_ALIASES["tomorrow_departure"]:
                profile  = get_profile(db, user.id)
                schedule = user.schedule
                effective_arrival = getattr(profile, "preferred_arrival_time", None)
                if schedule and schedule.time:
                    effective_arrival = schedule.time
                override = get_override_for_date(db, user.id, tomorrow_date)
                if override and override.target_arrival_time:
                    effective_arrival = override.target_arrival_time
                if not effective_arrival:
                    await reply_with_quick_reply(
                        reply_token, "⚠️ 尚未設定到達時間，請先完成設定。",
                        [{"type": "uri", "label": "📝 設定通勤路線", "uri": LIFF_URL}])
                    continue
                departure_time = await calculate_departure_time(profile, tomorrow_date, effective_arrival)
                await reply_with_quick_reply(
                    reply_token, f"明天建議 {departure_time} 出門（到達時間：{effective_arrival}）。",
                    MAIN_MENU_QR)
                continue

            # ── 修改今天到公司時間 ────────────────────────────────────────
            if command_text in COMMAND_ALIASES["edit_today_arrival"]:
                await reply_with_quick_reply(
                    reply_token, "請問今天幾點需要到公司？", OVERRIDE_TODAY_QR)
                continue

            # ── 修改明天到公司時間 ────────────────────────────────────────
            if command_text in COMMAND_ALIASES["edit_tomorrow_arrival"]:
                await reply_with_quick_reply(
                    reply_token, "請問明天幾點需要到公司？", OVERRIDE_TOMORROW_QR)
                continue

            # ── 時間格式輸入（override_today / override_tomorrow pending） ─
            time_parts = user_text.strip().split(":")
            if len(time_parts) == 2:
                try:
                    h, m = int(time_parts[0]), int(time_parts[1])
                    if 0 <= h <= 23 and 0 <= m <= 59:
                        time_val = f"{h:02d}:{m:02d}"
                        upsert_override(db, user.id, today_date, time_val)
                        clear_today_reminder_state_for_user(user.id)
                        await reply_with_quick_reply(
                            reply_token, f"✅ 已儲存今天到公司時間：{time_val}", MAIN_MENU_QR)
                        continue
                except ValueError:
                    pass

            # ── 問候語 ────────────────────────────────────────────────────
            if command_text in {"嗨", "你好", "哈囉", "哈喽", "Hi", "Hello", "hello", "hi"}:
                schedule = user.schedule
                if schedule and schedule.origin_address:
                    await reply_with_quick_reply(
                        reply_token,
                        "你好！我是智慧通勤助理 🤖\n需要什麼幫助嗎？",
                        MAIN_MENU_QR,
                    )
                else:
                    await reply_with_quick_reply(
                        reply_token,
                        "你好！我是智慧通勤助理 🤖\n請先完成通勤路線設定！",
                        [{"type": "uri", "label": "📝 設定通勤路線", "uri": LIFF_URL}],
                    )
                continue

            # ── 預設回覆 ──────────────────────────────────────────────────
            await reply_with_quick_reply(
                reply_token,
                "不確定您的指令，請點擊下方按鈕或傳送「指令說明」查看所有功能。",
                MAIN_MENU_QR,
            )

    finally:
        db.close()

    return {"ok": True}
