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
from app.commute_schedule import (
    WEEKDAY_NAMES,
    commute_date_is_active,
    normalize_active_weekdays,
    parse_custom_weekdays,
    parse_weekday_preset,
    schedule_label,
)
from app.dashboard_links import build_dashboard_view_url, build_household_dashboard_view_url
from app.db import SessionLocal
from app.departure_confirmation import (
    confirm_departure_for_user,
    format_taipei_hhmm,
    snooze_departure_for_user,
)
from app.line_client import reply_flex_with_quick_reply, reply_text, reply_with_quick_reply, reply_multi_messages_with_quick_reply
from app.crud import (
    get_or_create_user,
    get_or_create_profile,
    get_profile,
    get_override_for_date,
    get_household_id_for_user,
    get_users_for_household,
    ensure_personal_household,
    normalize_household_id,
    set_user_display_name,
    set_user_household_id,
    set_pending_field,
    update_profile_field,
    update_address_and_coords,
    upsert_override,
    get_next_setup_step,
    reset_profile_for_reconfigure,
    upsert_transport_mode_override,
    get_transport_mode_override,
    set_reminder_enabled,
    set_active_weekdays,
    set_commute_disabled_for_date,
)
from app.google_maps import geocode_address
from app.service import (
    calculate_departure_time,
    build_today_commute_payload,
    freeze_today_reminder_payload,
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

# Rich Menu uses these six top-level message actions. Quick Replies below them
# contain only the narrower actions inside that topic.
RICH_MENU_TOPICS = [
    {"type": "message", "label": "通勤選單", "text": "通勤選單"},
    {"type": "message", "label": "時間設定", "text": "時間設定"},
    {"type": "message", "label": "自動提醒", "text": "自動提醒"},
    {"type": "message", "label": "排程設定", "text": "排程設定"},
    {"type": "message", "label": "看板家庭", "text": "看板家庭"},
    {"type": "message", "label": "指令說明", "text": "指令說明"},
]

MAIN_MENU_QUICK_REPLIES = []

COMMUTE_TOPIC_QUICK_REPLIES = [
    {"type": "message", "label": "今日建議", "text": "今日通勤建議"},
    {"type": "message", "label": "最短時間", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "公車優先", "text": "今天搭公車"},
    {"type": "message", "label": "捷運優先", "text": "今天搭捷運"},
    {"type": "message", "label": "公車轉捷運", "text": "今天搭公車轉捷運"},
    {"type": "message", "label": "今天方式", "text": "查看今天交通方式"},
]

TIME_TOPIC_QUICK_REPLIES = [
    {"type": "message", "label": "今天到公司", "text": "修改今天到公司時間"},
    {"type": "message", "label": "明天到公司", "text": "修改明天到公司時間"},
    {"type": "message", "label": "明天出門", "text": "明天幾點出門"},
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

SCHEDULE_QUICK_REPLIES = [
    {"type": "message", "label": "平日啟用", "text": "排程平日"},
    {"type": "message", "label": "每天啟用", "text": "排程每天"},
    {"type": "message", "label": "週末啟用", "text": "排程週末"},
    {"type": "message", "label": "自訂星期", "text": "自訂日曆排程"},
    {"type": "datetimepicker", "label": "選休息日", "data": "action=pause_date", "mode": "date"},
    {"type": "datetimepicker", "label": "選啟用日", "data": "action=enable_date", "mode": "date"},
    {"type": "message", "label": "暫停固定", "text": "暫停固定排程"},
    {"type": "message", "label": "今天休息", "text": "今天休息"},
    {"type": "message", "label": "明天休息", "text": "明天休息"},
    {"type": "message", "label": "今天啟用", "text": "今天啟用"},
    {"type": "message", "label": "明天啟用", "text": "明天啟用"},
]

SCHEDULE_SETUP_QUICK_REPLIES = [
    {"type": "message", "label": "平日啟用", "text": "排程平日"},
    {"type": "message", "label": "每天啟用", "text": "排程每天"},
    {"type": "message", "label": "週末啟用", "text": "排程週末"},
    {"type": "message", "label": "自訂星期", "text": "自訂日曆排程"},
]

CUSTOM_SCHEDULE_QUICK_REPLIES = [
    {"type": "message", "label": "週一三五", "text": "週一週三週五"},
    {"type": "message", "label": "週二四", "text": "週二週四"},
    {"type": "message", "label": "週六日", "text": "週六週日"},
]

HOUSEHOLD_QUICK_REPLIES = [
    {"type": "message", "label": "查看家庭成員", "text": "查看家庭成員"},
    {"type": "message", "label": "取得邀請碼", "text": "取得家庭邀請碼"},
    {"type": "message", "label": "建立家庭", "text": "建立家庭"},
    {"type": "message", "label": "設定我的名稱", "text": "設定我的名稱"},
    {"type": "message", "label": "家庭看板連結", "text": "取得家庭Dashboard連結"},
]

DASHBOARD_TOPIC_QUICK_REPLIES = [
    {"type": "message", "label": "個人看板", "text": "取得Dashboard連結"},
    {"type": "message", "label": "家庭看板", "text": "取得家庭Dashboard連結"},
    {"type": "message", "label": "家庭管理", "text": "家庭成員管理"},
    {"type": "message", "label": "電腦模式", "text": "電腦Dashboard設定"},
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
    "topic_commute": {"通勤選單", "通勤功能"},
    "topic_time": {"時間設定", "到公司時間設定"},
    "topic_reminder": {"自動提醒", "提醒設定"},
    "topic_schedule": {"排程設定", "日程排程", "週間設定"},
    "topic_dashboard": {"看板家庭", "看板與家庭", "Dashboard家庭"},
    "topic_help": {"指令說明", "提示詞說明", "使用說明", "幫助"},
    "view_settings": {"查看設定"},
    "today_commute": {"今天通勤建議", "今日通勤建議", "通勤建議"},
    "dashboard_link": {"取得Dashboard連結", "取得dashboard連結", "Dashboard連結", "dashboard連結", "取得儀表板連結"},
    "household_dashboard_link": {"取得家庭Dashboard連結", "取得家用Dashboard連結", "家庭Dashboard連結", "家用Dashboard連結"},
    "tomorrow_departure": {"明天幾點出門"},
    "edit_today_arrival": {"修改今天到公司時間", "今天改到公司時間", "設定到公司時間", "修改出門時間", "修改到公司時間"},
    "edit_tomorrow_arrival": {"修改明天到公司時間"},
    "reset": {"重新設定"},
    "send_home_location": {"傳送住家位置", "設定住家位置"},
    "send_office_location": {"傳送公司位置", "設定公司位置"},
    "set_mode_auto": {"今天自動判斷", "今天交通自動"},
    "set_mode_shortest": {"優先選擇通勤時間短", "今天最短時間"},
    "set_mode_bus": {"今天搭公車", "今天坐公車"},
    "set_mode_metro": {"今天搭捷運", "今天坐捷運"},
    "set_mode_bus_to_metro": {"今天搭公車轉捷運", "今天公車轉捷運"},
    "view_mode_today": {"查看今天交通方式"},
    "enable_reminder": {"開啟自動提醒"},
    "disable_reminder": {"關閉自動提醒"},
    "view_reminder_setting": {"查看提醒設定"},
    "view_schedule_setting": {"查看排程設定", "LINE排程"},
    "schedule_workdays": {"排程平日", "平日啟用", "只在平日啟用"},
    "schedule_everyday": {"排程每天", "每天啟用", "每天都啟用"},
    "schedule_weekend": {"排程週末", "週末啟用", "只在週末啟用"},
    "schedule_none": {"暫停固定排程", "排程全休"},
    "schedule_custom": {"自訂日曆排程", "自訂排程", "自訂星期", "自訂啟用日"},
    "pause_today": {"今天休息", "今天不用提醒", "今天不用通勤"},
    "pause_tomorrow": {"明天休息", "明天不用提醒", "明天不用通勤"},
    "enable_today": {"今天啟用", "今天恢復提醒", "今天要通勤"},
    "enable_tomorrow": {"明天啟用", "明天恢復提醒", "明天要通勤"},
    "household_management": {"家庭成員管理", "家庭管理", "家人管理"},
    "view_household_members": {"查看家庭成員", "家庭成員", "家人列表"},
    "create_household": {"建立家庭", "建立家用群組", "建立家庭群組"},
    "household_invite_code": {"取得家庭邀請碼", "家庭邀請碼", "取得邀請碼"},
    "join_household": {"加入家庭", "加入家庭群組"},
    "set_display_name": {"設定我的名稱", "設定暱稱", "修改我的名稱", "修改暱稱"},
    "leave_household": {"離開家庭", "退出家庭"},
    "computer_dashboard_guide": {"實體電腦Dashboard操作模式", "電腦Dashboard設定", "Kiosk說明", "外接螢幕設定說明"},
    "departure_left": {"已經出門了"},
    "departure_need_5": {"我還需要五分鐘", "還需要五分鐘"},
}

CANONICAL_PROMPT_GROUPS = {
    "通勤": ["今日通勤建議", "優先選擇通勤時間短", "今天搭公車", "今天搭捷運", "今天搭公車轉捷運", "查看今天交通方式"],
    "時間": ["修改今天到公司時間", "修改明天到公司時間", "明天幾點出門"],
    "提醒": ["查看提醒設定", "開啟自動提醒", "關閉自動提醒"],
    "排程": ["查看排程設定", "排程平日", "排程每天", "排程週末", "自訂日曆排程", "今天休息", "明天休息", "今天啟用", "明天啟用"],
    "看板家庭": ["取得Dashboard連結", "取得家庭Dashboard連結", "家庭成員管理", "建立家庭", "取得家庭邀請碼", "加入家庭 邀請碼", "設定我的名稱 名稱", "電腦Dashboard設定"],
    "基本設定": ["查看設定", "重新設定", "傳送住家位置", "傳送公司位置"],
}


def canonical_prompt_lines() -> list[str]:
    lines = []
    for group, prompts in CANONICAL_PROMPT_GROUPS.items():
        lines.append(f"{group}：" + "、".join(prompts))
    return lines


def unsupported_canonical_prompts() -> list[str]:
    supported = set().union(*COMMAND_ALIASES.values())
    unsupported = []
    for prompts in CANONICAL_PROMPT_GROUPS.values():
        for prompt in prompts:
            if prompt in supported:
                continue
            if prompt.startswith("加入家庭 ") or prompt.startswith("設定我的名稱 "):
                continue
            unsupported.append(prompt)
    return unsupported


READY_MENU_TEXT = (
    "您目前設定已完成。\n"
    "請使用下方 Rich Menu 的 6 個大主題：通勤選單、時間設定、自動提醒、排程設定、看板家庭、指令說明。\n"
    "需要完整提示詞時，請傳送「指令說明」。"
)

def normalize_user_text(text: str) -> str:
    if not text:
        return ""
    return text.strip().replace("\u3000", " ").replace("\n", "").replace("\r", "").replace(" ", "")


def format_profile_text(profile, today_override_time: str | None = None, tomorrow_override_time: str | None = None, today_mode: str | None = None) -> str:
    home_address = profile.home_address or "尚未設定"
    office_address = profile.office_address or "尚未設定"
    preferred_arrival_time = profile.preferred_arrival_time or "尚未設定"
    today_effective_time = today_override_time or preferred_arrival_time
    tomorrow_effective_time = tomorrow_override_time or preferred_arrival_time
    today_note = "（今天臨時調整）" if today_override_time else "（固定時間）"
    tomorrow_note = "（明天臨時調整）" if tomorrow_override_time else "（固定時間）"
    reminder_status = "開啟" if getattr(profile, "reminder_enabled", True) else "關閉"
    schedule_status = schedule_label(getattr(profile, "active_weekdays", None))
    mode_label = TRANSPORT_MODE_NAME_MAP.get(today_mode or "auto", "自動判斷")

    text = (
        "您目前設定如下：\n"
        f"🏠 住家位置：{home_address}\n"
        f"🏢 公司位置：{office_address}\n"
        f"⏰ 固定到公司時間：{preferred_arrival_time}\n"
        f"📍 今天到公司時間：{today_effective_time}{today_note}\n"
        f"📅 明天到公司時間：{tomorrow_effective_time}{tomorrow_note}\n"
        f"📢 自動提醒：{reminder_status}\n"
        f"🗓 啟用日：{schedule_status}\n"
        f"🚇 今天交通方式：{mode_label}"
    )
    return text


def format_schedule_text(profile, today_date, today_override, tomorrow_date, tomorrow_override) -> str:
    today_status = "啟用" if commute_date_is_active(profile, today_date, today_override) else "休息"
    tomorrow_status = "啟用" if commute_date_is_active(profile, tomorrow_date, tomorrow_override) else "休息"
    return (
        "目前通勤排程：\n"
        f"🗓 固定啟用日：{schedule_label(getattr(profile, 'active_weekdays', None))}\n"
        f"📍 今天：{today_status}\n"
        f"📅 明天：{tomorrow_status}\n\n"
        "所有設定都可直接在 LINE 中完成。若要自訂星期，請按「自訂星期」。"
    )


SCHEDULE_PENDING_FIELDS = {"active_weekdays", "custom_active_weekdays"}


def is_schedule_setup_pending(profile) -> bool:
    return getattr(profile, "pending_field", None) in SCHEDULE_PENDING_FIELDS


def schedule_setup_prompt() -> str:
    return (
        "最後一步：請設定哪些日子要啟用通勤提醒。\n"
        "可用下方按鈕選「平日、每天、週末」，也可以按「自訂星期」在 LINE 內逐日切換。"
    )


def custom_schedule_prompt() -> str:
    return (
        "請選擇要固定啟用的星期。\n"
        "下方會顯示一張 LINE 原生選擇卡，點星期可開關；也可直接輸入例如：週一週三週五、週二週四、1,3,5。"
    )


def schedule_saved_text(profile, was_setup: bool = False) -> str:
    text = f"已設定固定排程：{schedule_label(profile.active_weekdays)}。"
    if was_setup:
        text += "\n\n初始設定完成，可以開始使用通勤建議與自動提醒。"
    return text


def parse_line_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def extract_command_value(user_text: str, aliases: set[str]) -> str | None:
    stripped = (user_text or "").strip()
    for alias in sorted(aliases, key=len, reverse=True):
        if stripped.startswith(alias):
            return stripped[len(alias):].strip(" ：:")
    return None


def format_household_management_text(db, user) -> str:
    household_id = get_household_id_for_user(user)
    members = get_users_for_household(db, household_id)
    member_lines = []
    for member in members:
        name = member.display_name or f"成員 {member.id}"
        suffix = "（你）" if member.id == user.id else ""
        member_lines.append(f"- {name}{suffix}")
    members_text = "\n".join(member_lines) if member_lines else "- 目前只有你"
    return (
        "家庭成員管理：\n"
        f"家庭邀請碼：{household_id}\n"
        f"目前成員：\n{members_text}\n\n"
        "可傳送：\n"
        "設定我的名稱 小明\n"
        "取得家庭邀請碼\n"
        "加入家庭 邀請碼\n"
        "取得家庭Dashboard連結"
    )


def format_computer_dashboard_guide(dashboard_url: str, household_url: str) -> str:
    return (
        "實體電腦 Dashboard 操作模式：\n"
        "1. 在要接外接螢幕的電腦打開家庭 Dashboard 連結。\n"
        f"{household_url}\n"
        "2. 全螢幕 Kiosk：Windows 可用 F11；若要固定成看板，Chrome 捷徑目標加上 --kiosk \"連結\"。macOS 可用 Chrome 全螢幕，或用指令 open -na \"Google Chrome\" --args --kiosk \"連結\"。\n"
        "3. 自動開機打開：Windows 把該捷徑放到 shell:startup。macOS 用「登入項目」加入 Chrome 或 Automator App。\n\n"
        "個人 Dashboard 連結：\n"
        f"{dashboard_url}"
    )


def build_weekday_picker_flex(profile) -> dict:
    active_days = set(normalize_active_weekdays(getattr(profile, "active_weekdays", None)))

    def day_button(day: int) -> dict:
        active = day in active_days
        label = f"{WEEKDAY_NAMES[day]} {'✓' if active else ''}".strip()
        return {
            "type": "button",
            "style": "primary" if active else "secondary",
            "height": "sm",
            "action": {
                "type": "postback",
                "label": label,
                "data": f"action=toggle_weekday&day={day}",
                "displayText": f"切換{WEEKDAY_NAMES[day]}",
            },
        }

    def row(days: list[int]) -> dict:
        return {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [day_button(day) for day in days],
        }

    preset_buttons = [
        ("平日", "workdays"),
        ("每天", "everyday"),
        ("週末", "weekend"),
        ("全休", "none"),
    ]
    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "重複提醒日", "weight": "bold", "size": "xl"},
                {"type": "text", "text": f"目前：{schedule_label(getattr(profile, 'active_weekdays', None))}", "size": "sm", "color": "#666666", "wrap": True},
                row([0, 1, 2, 3]),
                row([4, 5, 6]),
                {"type": "separator", "margin": "md"},
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "button",
                            "style": "secondary",
                            "height": "sm",
                            "action": {
                                "type": "postback",
                                "label": label,
                                "data": f"action=schedule_preset&preset={preset}",
                                "displayText": f"排程{label}",
                            },
                        }
                        for label, preset in preset_buttons
                    ],
                },
            ],
        },
    }


async def reply_weekday_picker(reply_token: str, profile, message: str | None = None) -> None:
    alt_text = message or "請在 LINE 中選擇通勤排程"
    await reply_flex_with_quick_reply(
        reply_token,
        alt_text,
        build_weekday_picker_flex(profile),
        SCHEDULE_QUICK_REPLIES,
    )


def build_command_help_carousel() -> dict:
    bubbles = []
    for title, prompts in CANONICAL_PROMPT_GROUPS.items():
        bubbles.append({
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": title, "weight": "bold", "size": "lg"},
                    {"type": "text", "text": "\n".join(prompts), "size": "sm", "wrap": True, "color": "#555555"},
                ],
            },
        })
    return {"type": "carousel", "contents": bubbles}


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
                postback_date = params.get("date") if isinstance(params, dict) else getattr(params, "date", None)
                if postback_action == "departure_check":
                    if postback_choice == "left":
                        confirm_departure_for_user(db, user.id, today_date)
                        await reply_text(
                            reply_token,
                            "收到，今天上班加油！\n已更新為計算明日通勤與提醒時間。",
                        )
                        continue

                    if postback_choice == "need_5":
                        override = snooze_departure_for_user(db, user.id, today_date)
                        snooze_text = format_taipei_hhmm(override.departure_snoozed_until)
                        await reply_text(
                            reply_token,
                            f"好的，{snooze_text} 再提醒您出門。四分鐘後會先提醒一次，時間到會再提醒一次。",
                        )
                        continue

                if postback_action in {"pause_date", "enable_date"}:
                    selected_date = parse_line_date(postback_date)
                    if selected_date is None:
                        await reply_with_quick_reply(reply_token, "日期格式讀取失敗，請再選一次。", SCHEDULE_QUICK_REPLIES)
                        continue

                    disabled = postback_action == "pause_date"
                    set_commute_disabled_for_date(db, user.id, selected_date, disabled)
                    if selected_date == today_date:
                        clear_today_reminder_state_for_user(user.id)
                    status_text = "休息" if disabled else "啟用"
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定 {selected_date.strftime('%Y-%m-%d')} {status_text}。",
                        SCHEDULE_QUICK_REPLIES,
                    )
                    continue

                if postback_action == "toggle_weekday":
                    try:
                        day = int((postback_parts.get("day") or [""])[0])
                    except ValueError:
                        day = -1
                    if not 0 <= day <= 6:
                        await reply_weekday_picker(reply_token, get_profile(db, user.id), "星期讀取失敗，請再選一次。")
                        continue

                    profile = get_profile(db, user.id)
                    active_days = set(normalize_active_weekdays(getattr(profile, "active_weekdays", None)))
                    if day in active_days:
                        active_days.remove(day)
                    else:
                        active_days.add(day)
                    profile = set_active_weekdays(db, user.id, sorted(active_days))
                    set_pending_field(db, user.id, None)
                    clear_today_reminder_state_for_user(user.id)
                    await reply_weekday_picker(reply_token, profile, f"已更新：{schedule_label(profile.active_weekdays)}")
                    continue

                if postback_action == "schedule_preset":
                    preset = (postback_parts.get("preset") or ["everyday"])[0]
                    profile = set_active_weekdays(db, user.id, parse_weekday_preset(preset))
                    set_pending_field(db, user.id, None)
                    clear_today_reminder_state_for_user(user.id)
                    await reply_weekday_picker(reply_token, profile, f"已更新：{schedule_label(profile.active_weekdays)}")
                    continue

                if postback_data == "action=set_preferred_arrival_time" and time_value:
                    profile_before = get_profile(db, user.id)
                    was_initial_arrival = (
                        profile_before.pending_field == "preferred_arrival_time"
                        or get_next_setup_step(profile_before) == "preferred_arrival_time"
                    )
                    update_profile_field(db, user.id, "preferred_arrival_time", time_value)
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-postback-preferred] error={e}")
                    updated_profile = get_profile(db, user.id)
                    if was_initial_arrival:
                        set_pending_field(db, user.id, "active_weekdays")
                        await reply_with_quick_reply(
                            reply_token,
                            f"已設定固定到公司時間：{time_value}\n\n{schedule_setup_prompt()}",
                            SCHEDULE_SETUP_QUICK_REPLIES,
                        )
                        continue

                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已更新固定到公司時間：{time_value}\n\n{format_profile_text(updated_profile, today_override_time, tomorrow_override_time)}",
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
                        (
                            f"已設定今天臨時到公司時間：{time_value}\n"
                            f"明天會自動回到固定到公司時間 {get_profile(db, user.id).preferred_arrival_time}。"
                        ),
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if postback_data == "action=set_tomorrow_arrival_time" and time_value:
                    upsert_override(db, user.id, tomorrow_date, time_value)
                    set_pending_field(db, user.id, None)
                    departure_time = await calculate_departure_time(get_profile(db, user.id), tomorrow_date, time_value)
                    await reply_with_quick_reply(
                        reply_token,
                        (
                            f"已設定明天臨時到公司時間：{time_value}\n"
                            f"明天建議出門：{departure_time}\n"
                            f"後天會自動回到固定到公司時間 {get_profile(db, user.id).preferred_arrival_time}。"
                        ),
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
                    | COMMAND_ALIASES["household_dashboard_link"]
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

            if is_schedule_setup_pending(_profile_for_guard):
                schedule_commands = (
                    COMMAND_ALIASES["schedule_workdays"]
                    | COMMAND_ALIASES["schedule_everyday"]
                    | COMMAND_ALIASES["schedule_weekend"]
                    | COMMAND_ALIASES["schedule_none"]
                    | COMMAND_ALIASES["schedule_custom"]
                    | COMMAND_ALIASES["reset"]
                    | COMMAND_ALIASES["dashboard_link"]
                    | COMMAND_ALIASES["household_dashboard_link"]
                )
                if command_text not in schedule_commands and parse_custom_weekdays(user_text) is None:
                    await reply_with_quick_reply(
                        reply_token,
                        schedule_setup_prompt(),
                        SCHEDULE_SETUP_QUICK_REPLIES,
                    )
                    continue

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

            if command_text in COMMAND_ALIASES["topic_commute"]:
                await reply_with_quick_reply(
                    reply_token,
                    "通勤選單：請選擇今天要怎麼計算。",
                    COMMUTE_TOPIC_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["topic_time"]:
                await reply_with_quick_reply(
                    reply_token,
                    "時間設定：請選擇要調整今天、明天，或查看明天建議出門時間。",
                    TIME_TOPIC_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["topic_reminder"]:
                profile = get_profile(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"自動提醒目前：{'開啟' if profile.reminder_enabled else '關閉'}。\n請選擇是否切換。",
                    REMINDER_SETTING_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["topic_schedule"]:
                profile = get_profile(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    format_schedule_text(profile, today_date, today_override, tomorrow_date, tomorrow_override),
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["topic_dashboard"]:
                await reply_with_quick_reply(
                    reply_token,
                    "看板與家庭：請選擇要取得看板連結、管理家庭成員，或查看電腦外接螢幕模式。",
                    DASHBOARD_TOPIC_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["topic_help"]:
                await reply_flex_with_quick_reply(
                    reply_token,
                    "指令說明",
                    build_command_help_carousel(),
                    [],
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
                    "在要顯示的螢幕上打開這個網址，並把瀏覽器切成全螢幕即可。\n"
                    "若要設定開機自動打開，請傳送「電腦 Dashboard 設定」。",
                )
                continue

            if command_text in COMMAND_ALIASES["household_dashboard_link"]:
                user = ensure_personal_household(db, user.id)
                household_url = build_household_dashboard_view_url(
                    household_id=get_household_id_for_user(user),
                    public_url=PUBLIC_URL,
                    request_base_url=str(request.base_url),
                )
                await reply_text(
                    reply_token,
                    "家庭外接螢幕看板連結：\n"
                    f"{household_url}\n\n"
                    "家人都加入這個 LINE Bot 後，這個看板會一起顯示每位成員的出門狀態。\n"
                    "若要設定開機自動打開，請傳送「電腦 Dashboard 設定」。",
                )
                continue

            if command_text in COMMAND_ALIASES["computer_dashboard_guide"]:
                user = ensure_personal_household(db, user.id)
                dashboard_url = build_dashboard_view_url(
                    user_id=user.id,
                    public_url=PUBLIC_URL,
                    request_base_url=str(request.base_url),
                )
                household_url = build_household_dashboard_view_url(
                    household_id=get_household_id_for_user(user),
                    public_url=PUBLIC_URL,
                    request_base_url=str(request.base_url),
                )
                await reply_text(reply_token, format_computer_dashboard_guide(dashboard_url, household_url))
                continue

            join_household_value = extract_command_value(user_text, COMMAND_ALIASES["join_household"])
            if join_household_value:
                updated_user = set_user_household_id(db, user.id, join_household_value)
                user = updated_user
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    f"已加入家庭：{get_household_id_for_user(user)}。\n可用家庭 Dashboard 看板一起顯示成員狀態。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            display_name_value = extract_command_value(user_text, COMMAND_ALIASES["set_display_name"])
            if display_name_value:
                updated_user = set_user_display_name(db, user.id, display_name_value)
                user = updated_user
                set_pending_field(db, user.id, None)
                display_name = user.display_name or f"成員 {user.id}"
                await reply_with_quick_reply(
                    reply_token,
                    f"已更新你的家庭看板名稱：{display_name}。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["household_management"]:
                user = ensure_personal_household(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    format_household_management_text(db, user),
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["view_household_members"]:
                user = ensure_personal_household(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    format_household_management_text(db, user),
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["create_household"]:
                user = ensure_personal_household(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"已建立家庭。\n家庭邀請碼：{get_household_id_for_user(user)}\n請家人傳送「加入家庭 {get_household_id_for_user(user)}」。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["household_invite_code"]:
                user = ensure_personal_household(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"家庭邀請碼：{get_household_id_for_user(user)}\n請家人傳送「加入家庭 {get_household_id_for_user(user)}」。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["join_household"]:
                set_pending_field(db, user.id, "household_join_code")
                await reply_text(reply_token, "請輸入家庭邀請碼，或直接傳送：加入家庭 邀請碼")
                continue

            if command_text in COMMAND_ALIASES["set_display_name"]:
                set_pending_field(db, user.id, "display_name")
                await reply_text(reply_token, "請輸入要顯示在家庭 Dashboard 上的名稱，例如：設定我的名稱 小明")
                continue

            if command_text in COMMAND_ALIASES["leave_household"]:
                user = set_user_household_id(db, user.id, f"family-{user.id}")
                await reply_with_quick_reply(
                    reply_token,
                    f"已離開原本家庭，現在你的個人家庭邀請碼是：{get_household_id_for_user(user)}。",
                    HOUSEHOLD_QUICK_REPLIES,
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

            if command_text in COMMAND_ALIASES["view_schedule_setting"]:
                profile = get_profile(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    format_schedule_text(profile, today_date, today_override, tomorrow_date, tomorrow_override),
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_workdays"]:
                was_setup_schedule = is_schedule_setup_pending(get_profile(db, user.id))
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("workdays"))
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES if was_setup_schedule else SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_everyday"]:
                was_setup_schedule = is_schedule_setup_pending(get_profile(db, user.id))
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("everyday"))
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES if was_setup_schedule else SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_weekend"]:
                was_setup_schedule = is_schedule_setup_pending(get_profile(db, user.id))
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("weekend"))
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES if was_setup_schedule else SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_none"]:
                was_setup_schedule = is_schedule_setup_pending(get_profile(db, user.id))
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("none"))
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES if was_setup_schedule else SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_custom"]:
                set_pending_field(db, user.id, "custom_active_weekdays")
                await reply_weekday_picker(reply_token, get_profile(db, user.id), custom_schedule_prompt())
                continue

            if command_text in COMMAND_ALIASES["pause_today"]:
                set_commute_disabled_for_date(db, user.id, today_date, True)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    "已設定今天休息，今天不會再推送出門提醒。Dashboard 會改看下一個啟用日。",
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["pause_tomorrow"]:
                set_commute_disabled_for_date(db, user.id, tomorrow_date, True)
                await reply_with_quick_reply(
                    reply_token,
                    "已設定明天休息，明天不會推送通勤提醒。",
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["enable_today"]:
                set_commute_disabled_for_date(db, user.id, today_date, False)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    "已啟用今天通勤提醒，會依今天實際到公司時間重新計算。",
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["enable_tomorrow"]:
                set_commute_disabled_for_date(db, user.id, tomorrow_date, False)
                await reply_with_quick_reply(
                    reply_token,
                    "已啟用明天通勤提醒，會依明天實際到公司時間計算。",
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["departure_left"]:
                confirm_departure_for_user(db, user.id, today_date)
                await reply_text(
                    reply_token,
                    "收到，今天上班加油！\n已更新為計算明日通勤與提醒時間。",
                )
                continue

            if command_text in COMMAND_ALIASES["departure_need_5"]:
                override = snooze_departure_for_user(db, user.id, today_date)
                snooze_text = format_taipei_hhmm(override.departure_snoozed_until)
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

            if command_text in COMMAND_ALIASES["today_commute"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_text(reply_token, FIELD_PROMPTS[next_step])
                    continue
                if not commute_date_is_active(profile, today_date, today_override):
                    await reply_with_quick_reply(
                        reply_token,
                        "今天排程是休息日，不會推送出門提醒。\n若今天臨時要通勤，請按「今天啟用」。",
                        SCHEDULE_QUICK_REPLIES,
                    )
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
                if not commute_date_is_active(profile, tomorrow_date, override):
                    await reply_with_quick_reply(
                        reply_token,
                        "明天排程是休息日，不會推送通勤提醒。\n若明天臨時要通勤，請按「明天啟用」。",
                        SCHEDULE_QUICK_REPLIES,
                    )
                    continue
                if override and override.target_arrival_time:
                    effective_arrival_time = override.target_arrival_time

                departure_time = await calculate_departure_time(profile, tomorrow_date, effective_arrival_time)
                await reply_text(
                    reply_token,
                    f"明天到公司時間：{effective_arrival_time}\n明天建議出門：{departure_time}",
                )
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

            if current_step == "household_join_code":
                household_code = normalize_household_id(user_text)
                user = set_user_household_id(db, user.id, household_code)
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    f"已加入家庭：{get_household_id_for_user(user)}。\n可用家庭 Dashboard 看板一起顯示成員狀態。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if current_step == "display_name":
                user = set_user_display_name(db, user.id, user_text)
                set_pending_field(db, user.id, None)
                display_name = user.display_name or f"成員 {user.id}"
                await reply_with_quick_reply(
                    reply_token,
                    f"已更新你的家庭看板名稱：{display_name}。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            if current_step in SCHEDULE_PENDING_FIELDS:
                weekdays = parse_custom_weekdays(user_text)
                if weekdays is None:
                    set_pending_field(db, user.id, "custom_active_weekdays")
                    await reply_weekday_picker(reply_token, profile, custom_schedule_prompt())
                    continue

                was_setup_schedule = is_schedule_setup_pending(profile)
                profile = set_active_weekdays(db, user.id, weekdays)
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES if was_setup_schedule else SCHEDULE_QUICK_REPLIES,
                )
                continue

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
                        (
                            f"已設定今天臨時到公司時間：{value}\n"
                            f"明天會自動回到固定到公司時間 {get_profile(db, user.id).preferred_arrival_time}。"
                        ),
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if current_step == "override_tomorrow_arrival_time":
                    upsert_override(db, user.id, tomorrow_date, value)
                    set_pending_field(db, user.id, None)
                    departure_time = await calculate_departure_time(get_profile(db, user.id), tomorrow_date, value)
                    await reply_with_quick_reply(
                        reply_token,
                        (
                            f"已設定明天臨時到公司時間：{value}\n"
                            f"明天建議出門：{departure_time}\n"
                            f"後天會自動回到固定到公司時間 {get_profile(db, user.id).preferred_arrival_time}。"
                        ),
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                was_initial_arrival = (
                    profile.pending_field == "preferred_arrival_time"
                    or get_next_setup_step(profile) == "preferred_arrival_time"
                )
                update_profile_field(db, user.id, "preferred_arrival_time", value)
                clear_today_reminder_state_for_user(user.id)

                try:
                    await freeze_today_reminder_payload(db, user.id, today_date)
                except Exception as e:
                    print(f"[freeze-preferred-arrival] error={e}")

                updated_profile = get_profile(db, user.id)
                if was_initial_arrival:
                    set_pending_field(db, user.id, "active_weekdays")
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定固定到公司時間：{value}\n\n{schedule_setup_prompt()}",
                        SCHEDULE_SETUP_QUICK_REPLIES,
                    )
                    continue

                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    f"已更新固定到公司時間：{value}\n\n{format_profile_text(updated_profile, today_override_time, tomorrow_override_time)}",
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
