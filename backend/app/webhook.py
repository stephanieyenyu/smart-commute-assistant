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
    arrival_label,
    commute_date_is_active,
    destination_label_for_identity,
    destination_label_for_profile,
    identity_display_name,
    normalize_active_weekdays,
    parse_custom_weekdays,
    parse_weekday_preset,
    schedule_label,
    template_weekdays,
    week_schedule_overview,
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
    create_schedule_template,
    effective_commute_setting_for_date,
    get_schedule_conflicts,
    get_schedule_template,
    get_schedule_templates,
    get_household_id_for_user,
    get_users_for_household,
    ensure_personal_household,
    normalize_household_id,
    remove_user_from_household,
    set_identity_and_destination_label,
    set_user_display_name,
    set_user_household_id,
    user_is_household_owner,
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
        "請傳送目的地位置 🏢\n"
        "點下方按鈕開啟地圖，或直接輸入完整地址"
    ),
    "identity_type": "請先選擇您的身份，系統會依身份顯示「到學校時間」、「到公司時間」或自訂目的地。",
    "preferred_arrival_time": "請問您幾點需要抵達目的地？\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 08:30）",
    "override_today_arrival_time": "請問今天幾點需要抵達目的地？\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 15:30）",
    "override_tomorrow_arrival_time": "請問明天幾點需要抵達目的地？\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 09:30）",
}

parser = WebhookParser(LINE_CHANNEL_SECRET)

# ===========================================================
# Quick Reply button sets
# ===========================================================

# Shown for: new users (FollowEvent), reset, setup incomplete
# Contains all 3 setup actions in one bar
SETUP_QUICK_REPLIES = [
    {"type": "location",      "label": "🏠 住家位置"},
    {"type": "location",      "label": "🏢 目的地位置"},
    {"type": "datetimepicker","label": "⏰ 抵達時間",
     "data": "action=set_preferred_arrival_time", "mode": "time"},
    {"type": "message", "label": "學生", "text": "身份 學生"},
    {"type": "message", "label": "上班族", "text": "身份 上班族"},
    {"type": "message", "label": "自訂標籤", "text": "自訂目的地標籤"},
]

DONE_QUICK_REPLY = {"type": "message", "label": "完成修改設定", "text": "完成修改設定"}

BASIC_SETTINGS_QUICK_REPLIES = [
    {"type": "message", "label": "查看設定", "text": "查看設定"},
    {"type": "message", "label": "重新設定", "text": "重新設定"},
    {"type": "message", "label": "傳送住家地址", "text": "傳送住家地址"},
    {"type": "message", "label": "傳送目的地地址", "text": "傳送目的地地址"},
]


def with_done_button(items: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in items:
        key = item.get("text") or item.get("data") or item.get("label")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    if DONE_QUICK_REPLY["text"] in seen:
        return deduped
    return [*deduped, DONE_QUICK_REPLY]


# Shown when waiting for home address
HOME_QUICK_REPLY = with_done_button([
    {"type": "location", "label": "📍 開啟地圖選位置"},
])

# Shown when waiting for office address
OFFICE_QUICK_REPLY = with_done_button([
    {"type": "location", "label": "🏢 開啟地圖選位置"},
])

# Shown when waiting for preferred_arrival_time
ARRIVAL_TIME_QUICK_REPLIES = with_done_button([
    {"type": "datetimepicker", "label": "⏰ 選擇抵達時間",
     "data": "action=set_preferred_arrival_time", "mode": "time"},
    {"type": "message", "label": "07:30", "text": "07:30"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "08:30", "text": "08:30"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "09:30", "text": "09:30"},
])

# Shown when modifying today's arrival time
OVERRIDE_TODAY_TIME_QUICK_REPLIES = with_done_button([
    {"type": "datetimepicker", "label": "⏰ 選擇今天時間",
     "data": "action=set_today_arrival_time", "mode": "time"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "10:00", "text": "10:00"},
    {"type": "message", "label": "14:00", "text": "14:00"},
    {"type": "message", "label": "17:00", "text": "17:00"},
])

# Shown when modifying tomorrow's arrival time
OVERRIDE_TOMORROW_TIME_QUICK_REPLIES = with_done_button([
    {"type": "datetimepicker", "label": "⏰ 選擇明天時間",
     "data": "action=set_tomorrow_arrival_time", "mode": "time"},
    {"type": "message", "label": "08:00", "text": "08:00"},
    {"type": "message", "label": "08:30", "text": "08:30"},
    {"type": "message", "label": "09:00", "text": "09:00"},
    {"type": "message", "label": "10:00", "text": "10:00"},
    {"type": "message", "label": "09:30", "text": "09:30"},
])

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

MAIN_MENU_QUICK_REPLIES = BASIC_SETTINGS_QUICK_REPLIES

COMMUTE_TOPIC_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "今日建議", "text": "今日通勤建議"},
    {"type": "message", "label": "最短時間", "text": "優先選擇通勤時間短"},
    {"type": "message", "label": "公車優先", "text": "今天搭公車"},
    {"type": "message", "label": "捷運優先", "text": "今天搭捷運"},
    {"type": "message", "label": "公車轉捷運", "text": "今天搭公車轉捷運"},
    {"type": "message", "label": "今天方式", "text": "查看今天交通方式"},
])

TIME_TOPIC_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "設定嚮導", "text": "身份設定"},
    {"type": "message", "label": "新增排程", "text": "新增常用排程"},
    {"type": "message", "label": "複製排程", "text": "複製常用排程"},
    {"type": "message", "label": "今天時間", "text": "修改今天到達時間"},
    {"type": "message", "label": "明天時間", "text": "修改明天到達時間"},
    {"type": "message", "label": "明天出門", "text": "明天幾點出門"},
])

IDENTITY_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "學生", "text": "身份 學生"},
    {"type": "message", "label": "上班族", "text": "身份 上班族"},
    {"type": "message", "label": "斜槓族", "text": "身份 斜槓族"},
    {"type": "message", "label": "自訂標籤", "text": "自訂目的地標籤"},
])


def setup_quick_replies_for_step(step: str | None) -> list[dict]:
    if step == "home_location":
        return HOME_QUICK_REPLY
    if step == "identity_type":
        return IDENTITY_QUICK_REPLIES
    if step == "office_location":
        return OFFICE_QUICK_REPLY
    if step == "preferred_arrival_time":
        return ARRIVAL_TIME_QUICK_REPLIES
    return MAIN_MENU_QUICK_REPLIES


WIZARD_WEEKDAY_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "平日", "text": "排程平日"},
    {"type": "message", "label": "每天", "text": "排程每天"},
    {"type": "message", "label": "假日", "text": "排程假日"},
    {"type": "message", "label": "自訂", "text": "自訂日曆排程"},
    {"type": "message", "label": "下一步：時間", "text": "下一步：設定時間"},
])

SWITCH_SETTING_QUICK_REPLIES = [
    {"type": "message", "label": "繼續設定", "text": "繼續設定"},
    {"type": "message", "label": "取消並切換", "text": "取消並切換"},
]

WIZARD_WEEKDAY_PENDING_FIELDS = {"wizard_active_weekdays", "wizard_custom_active_weekdays"}
SETTING_FLOW_PENDING_FIELDS = {
    "identity_type",
    "destination_label",
    "home_location",
    "office_location",
    "preferred_arrival_time",
    "active_weekdays",
    "custom_active_weekdays",
    "template_time",
    "template_label",
    "template_days",
    "template_conflict",
    *WIZARD_WEEKDAY_PENDING_FIELDS,
}


def arrival_time_quick_replies_for_profile(profile) -> list[dict]:
    label = arrival_label(destination_label_for_profile(profile))
    return with_done_button([
        {"type": "datetimepicker", "label": f"⏰ 選擇{label}",
         "data": "action=set_preferred_arrival_time", "mode": "time"},
        {"type": "message", "label": "07:30", "text": "07:30"},
        {"type": "message", "label": "08:00", "text": "08:00"},
        {"type": "message", "label": "08:30", "text": "08:30"},
        {"type": "message", "label": "09:00", "text": "09:00"},
        {"type": "message", "label": "09:30", "text": "09:30"},
    ])


def field_prompt_for_profile(field_name: str, profile) -> str:
    destination_label = destination_label_for_profile(profile)
    label = arrival_label(destination_label)
    if field_name == "office_location":
        return f"請傳送{destination_label}位置 🏢\n點下方按鈕開啟地圖，或直接輸入完整地址"
    if field_name == "identity_type":
        return "請先選擇您的身份。選完後，我會接著帶您設定提醒星期與時間。"
    if field_name == "preferred_arrival_time":
        return f"請設定固定{label}。\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 08:30）"
    if field_name == "override_today_arrival_time":
        return f"請設定今天臨時{label}。\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 15:30）"
    if field_name == "override_tomorrow_arrival_time":
        return f"請設定明天臨時{label}。\n點下方按鈕快速選擇，或直接輸入 HH:MM（例如 09:30）"
    return FIELD_PROMPTS.get(field_name, "請依照下方選項完成設定。")


def setting_step_label(step: str | None) -> str:
    pending = parse_schedule_template_pending(step)
    if pending.get("action"):
        return "常用排程"
    if step and step.startswith("copy_template_time:"):
        return "複製排程"
    return {
        "identity_type": "身份",
        "destination_label": "目的地標籤",
        "home_location": "住家位置",
        "office_location": "目的地位置",
        "preferred_arrival_time": "固定到達時間",
        "active_weekdays": "提醒星期",
        "custom_active_weekdays": "自訂星期",
        "wizard_active_weekdays": "提醒星期",
        "wizard_custom_active_weekdays": "自訂星期",
        "template_time": "常用排程時間",
    }.get(step or "", "目前設定")


def is_setting_flow_pending(step: str | None) -> bool:
    if not step:
        return False
    if step.startswith("confirm_switch|") or step.startswith("copy_template_time:"):
        return True
    if parse_schedule_template_pending(step).get("action"):
        return True
    return step in SETTING_FLOW_PENDING_FIELDS


def topic_key_for_command(command_text: str) -> str | None:
    for key in ("topic_commute", "topic_time", "topic_reminder", "topic_schedule", "topic_dashboard", "topic_help"):
        if command_text in COMMAND_ALIASES[key]:
            return key
    return None

SCHEDULE_TEMPLATE_LABEL_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "學校", "text": "學校"},
    {"type": "message", "label": "公司", "text": "公司"},
    {"type": "message", "label": "兼職公司", "text": "兼職公司"},
    {"type": "message", "label": "其他", "text": "自訂目的地"},
])

SCHEDULE_CONFLICT_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "以新排程為準", "text": "以新排程為準"},
    {"type": "message", "label": "保留原排程", "text": "保留原排程"},
    {"type": "message", "label": "取消", "text": "取消新增排程"},
])

# Shown after commute advice reply: return to the clean default state.
COMMUTE_RESULT_QUICK_REPLIES = MAIN_MENU_QUICK_REPLIES

REMINDER_SETTING_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "✅ 開啟自動提醒", "text": "開啟自動提醒"},
    {"type": "message", "label": "⏸ 關閉自動提醒", "text": "關閉自動提醒"},
])

SCHEDULE_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "平日", "text": "排程平日"},
    {"type": "message", "label": "每天", "text": "排程每天"},
    {"type": "message", "label": "假日", "text": "排程假日"},
    {"type": "message", "label": "自訂", "text": "自訂日曆排程"},
    {"type": "datetimepicker", "label": "休息日", "data": "action=pause_date", "mode": "date"},
    {"type": "datetimepicker", "label": "啟用日", "data": "action=enable_date", "mode": "date"},
    {"type": "message", "label": "暫停", "text": "暫停固定排程"},
])

SCHEDULE_SETUP_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "平日", "text": "排程平日"},
    {"type": "message", "label": "每天", "text": "排程每天"},
    {"type": "message", "label": "假日", "text": "排程假日"},
    {"type": "message", "label": "自訂", "text": "自訂日曆排程"},
])

CUSTOM_SCHEDULE_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "週一三五", "text": "週一週三週五"},
    {"type": "message", "label": "週二四", "text": "週二週四"},
    {"type": "message", "label": "週六日", "text": "週六週日"},
])

HOUSEHOLD_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "查看家庭成員", "text": "查看家庭成員"},
    {"type": "message", "label": "移除成員", "text": "移除家庭成員"},
    {"type": "message", "label": "取得邀請碼", "text": "取得家庭邀請碼"},
    {"type": "message", "label": "建立家庭", "text": "建立家庭"},
    {"type": "message", "label": "設定我的名稱", "text": "設定我的名稱"},
    {"type": "message", "label": "家庭看板連結", "text": "取得家庭Dashboard連結"},
])

DASHBOARD_TOPIC_QUICK_REPLIES = with_done_button([
    {"type": "message", "label": "個人看板", "text": "取得Dashboard連結"},
    {"type": "message", "label": "家庭看板", "text": "取得家庭Dashboard連結"},
    {"type": "message", "label": "家庭管理", "text": "家庭成員管理"},
    {"type": "message", "label": "移除成員", "text": "移除家庭成員"},
    {"type": "message", "label": "電腦模式", "text": "電腦Dashboard設定"},
])

TRANSPORT_MODE_NAME_MAP = {
    None: "自動判斷",
    "auto": "自動判斷",
    "shortest": "最短時間優先 (Google)",
    "bus": "公車優先",
    "metro": "捷運優先",
    "bus_to_metro": "公車轉捷運",
    "mixed_transit": "大眾運輸轉乘",
    "rail": "鐵路",
    "light_rail": "輕軌",
}

COMMAND_ALIASES = {
    "topic_commute": {"通勤選單", "通勤功能"},
    "topic_time": {"時間設定", "到公司時間設定"},
    "topic_reminder": {"自動提醒", "提醒設定"},
    "topic_schedule": {"排程設定", "日程排程", "週間設定"},
    "topic_dashboard": {"看板家庭", "看板與家庭", "Dashboard家庭"},
    "topic_help": {"指令說明", "提示詞說明", "使用說明", "幫助"},
    "finish_settings": {"完成修改設定", "完成設定", "結束設定"},
    "view_settings": {"查看設定"},
    "today_commute": {"今天通勤建議", "今日通勤建議", "通勤建議"},
    "dashboard_link": {"取得Dashboard連結", "取得DASHBOARD連結", "取得dashboard連結", "Dashboard連結", "DASHBOARD連結", "dashboard連結", "取得儀表板連結", "看板連結"},
    "household_dashboard_link": {"取得家庭Dashboard連結", "取得家庭DASHBOARD連結", "取得家用Dashboard連結", "家庭Dashboard連結", "家庭DASHBOARD連結", "家用Dashboard連結"},
    "tomorrow_departure": {"明天幾點出門"},
    "edit_today_arrival": {"修改今天到達時間", "修改今天到公司時間", "今天改到公司時間", "設定到公司時間", "修改出門時間", "修改到公司時間"},
    "edit_tomorrow_arrival": {"修改明天到達時間", "修改明天到公司時間"},
    "identity_settings": {"身份設定", "設定身份", "目的地標籤設定"},
    "set_identity_student": {"身份 學生", "身份學生", "我是學生", "學生"},
    "set_identity_worker": {"身份 上班族", "身份上班族", "我是上班族", "上班族"},
    "set_identity_slash": {"身份 斜槓族", "身份斜槓族", "我是斜槓族", "斜槓族"},
    "custom_destination_label": {"自訂目的地標籤", "設定目的地標籤", "自訂標籤"},
    "next_wizard_time": {"下一步：設定時間", "下一步設定時間", "設定時間"},
    "continue_setting": {"繼續設定"},
    "cancel_and_switch": {"取消並切換"},
    "add_schedule_template": {"新增常用排程", "新增排程", "新增週間排程"},
    "copy_schedule_template": {"複製常用排程", "複製排程"},
    "replace_schedule_conflict": {"以新排程為準"},
    "keep_schedule_conflict": {"保留原排程"},
    "cancel_schedule_template": {"取消新增排程", "取消排程設定"},
    "reset": {"重新設定"},
    "send_home_location": {"傳送住家位置", "設定住家位置", "傳送住家地址", "設定住家地址"},
    "send_office_location": {"傳送目的地位置", "設定目的地位置", "傳送目的地地址", "設定目的地地址", "傳送公司位置", "設定公司位置", "傳送公司地址", "設定公司地址"},
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
    "schedule_weekend": {"排程週末", "排程假日", "週末啟用", "假日啟用", "只在週末啟用"},
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
    "remove_household_member": {"移除家庭成員", "移除成員", "刪除家庭成員", "刪除成員"},
    "confirm_remove_household_member": {"確認移除家庭成員", "確認移除成員", "確認移除"},
    "cancel_remove_household_member": {"取消移除成員", "取消移除"},
    "leave_household": {"離開家庭", "退出家庭"},
    "computer_dashboard_guide": {"實體電腦Dashboard操作模式", "電腦Dashboard設定", "Kiosk說明", "外接螢幕設定說明"},
    "departure_left": {"已經出門了"},
    "departure_need_5": {"我還需要五分鐘", "還需要五分鐘"},
}

CANONICAL_PROMPT_GROUPS = {
    "通勤": ["今日通勤建議", "優先選擇通勤時間短", "今天搭公車", "今天搭捷運", "今天搭公車轉捷運", "查看今天交通方式"],
    "時間": ["身份設定", "新增常用排程", "複製常用排程", "修改今天到達時間", "修改明天到達時間", "明天幾點出門"],
    "提醒": ["查看提醒設定", "開啟自動提醒", "關閉自動提醒"],
    "排程": ["查看排程設定", "排程平日", "排程每天", "排程週末", "自訂日曆排程", "今天休息", "明天休息", "今天啟用", "明天啟用"],
    "看板家庭": ["取得Dashboard連結", "取得家庭Dashboard連結", "家庭成員管理", "移除家庭成員", "建立家庭", "取得家庭邀請碼", "加入家庭 邀請碼", "設定我的名稱 名稱", "電腦Dashboard設定"],
    "基本設定": ["查看設定", "重新設定", "傳送住家地址", "傳送目的地地址"],
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


def format_profile_text(profile, today_override_time: str | None = None, tomorrow_override_time: str | None = None, today_mode: str | None = None, db=None) -> str:
    home_address = profile.home_address or "尚未設定"
    office_address = profile.office_address or "尚未設定"
    preferred_arrival_time = profile.preferred_arrival_time or "尚未設定"
    destination_label = destination_label_for_profile(profile)
    arrival_label_text = arrival_label(destination_label)
    today_effective_text = f"{today_override_time or preferred_arrival_time}{'（今天臨時調整）' if today_override_time else '（固定時間）'}"
    tomorrow_effective_text = f"{tomorrow_override_time or preferred_arrival_time}{'（明天臨時調整）' if tomorrow_override_time else '（固定時間）'}"
    reminder_status = "開啟" if getattr(profile, "reminder_enabled", True) else "關閉"
    schedule_status = schedule_label(getattr(profile, "active_weekdays", None))
    mode_label = TRANSPORT_MODE_NAME_MAP.get(today_mode or "auto", "自動判斷")
    identity_text = identity_display_name(getattr(profile, "identity_type", None))
    multi_schedule_text = ""
    if db is not None:
        today_date = today_taipei()
        tomorrow_date = today_date + timedelta(days=1)
        today_setting = effective_commute_setting_for_date(db, profile, today_date, get_override_for_date(db, profile.user_id, today_date))
        tomorrow_setting = effective_commute_setting_for_date(db, profile, tomorrow_date, get_override_for_date(db, profile.user_id, tomorrow_date))
        today_effective_text = (
            f"{today_setting['arrival_time']} 到{today_setting['destination_label']}（今天生效）"
            if today_setting
            else "休息"
        )
        tomorrow_effective_text = (
            f"{tomorrow_setting['arrival_time']} 到{tomorrow_setting['destination_label']}（明天生效）"
            if tomorrow_setting
            else "休息"
        )
        multi_schedule_text = "\n📆 本週排程：\n" + week_schedule_overview(get_schedule_templates(db, profile.user_id, active_only=True), profile)

    text = (
        "您目前設定如下：\n"
        f"🏠 住家位置：{home_address}\n"
        f"🏢 {destination_label}位置：{office_address}\n"
        f"👤 身份：{identity_text}（目的地顯示為「{destination_label}」）\n"
        f"⏰ 固定{arrival_label_text}：{preferred_arrival_time}\n"
        f"📍 今天：{today_effective_text}\n"
        f"📅 明天：{tomorrow_effective_text}\n"
        f"📢 自動提醒：{reminder_status}\n"
        f"🗓 啟用日：{schedule_status}\n"
        f"🚇 今天交通方式：{mode_label}"
        f"{multi_schedule_text}"
    )
    return text


def format_schedule_text(db, profile, today_date, today_override, tomorrow_date, tomorrow_override) -> str:
    today_setting = effective_commute_setting_for_date(db, profile, today_date, today_override)
    tomorrow_setting = effective_commute_setting_for_date(db, profile, tomorrow_date, tomorrow_override)
    today_status = f"{today_setting['arrival_time']} 到{today_setting['destination_label']}" if today_setting else "休息"
    tomorrow_status = f"{tomorrow_setting['arrival_time']} 到{tomorrow_setting['destination_label']}" if tomorrow_setting else "休息"
    return (
        "目前通勤排程：\n"
        f"🗓 固定啟用日：{schedule_label(getattr(profile, 'active_weekdays', None))}\n"
        f"📍 今天：{today_status}\n"
        f"📅 明天：{tomorrow_status}\n\n"
        f"{schedule_templates_text(db, profile)}\n\n"
        "所有設定都可直接在 LINE 中完成。若要新增多組常用排程，請傳送「新增常用排程」。"
    )


SCHEDULE_PENDING_FIELDS = {"active_weekdays", "custom_active_weekdays", *WIZARD_WEEKDAY_PENDING_FIELDS}


def is_schedule_setup_pending(profile) -> bool:
    return getattr(profile, "pending_field", None) in SCHEDULE_PENDING_FIELDS


def hhmm_is_valid(value: str | None) -> bool:
    if not value:
        return False
    try:
        datetime.strptime(value.strip(), "%H:%M")
        return True
    except ValueError:
        return False


def encode_pending_part(value: str) -> str:
    return str(value or "").replace("|", "／").replace(":", "：").strip()


def parse_schedule_template_pending(value: str | None) -> dict:
    parts = (value or "").split("|")
    if len(parts) < 5:
        return {}
    action, time_value, label, days_text, copy_from_text = parts[:5]
    days = []
    if days_text:
        for item in days_text.split(","):
            try:
                day = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6 and day not in days:
                days.append(day)
    copy_from = None
    try:
        copy_from = int(copy_from_text) if copy_from_text else None
    except ValueError:
        copy_from = None
    return {
        "action": action,
        "time": time_value,
        "label": label,
        "days": sorted(days),
        "copy_from": copy_from,
    }


def build_schedule_template_pending(action: str, time_value: str, label: str, days: list[int] | None = None, copy_from: int | None = None) -> str:
    days_text = ",".join(str(day) for day in sorted(days or []))
    copy_text = "" if copy_from is None else str(copy_from)
    return "|".join([
        action,
        encode_pending_part(time_value),
        encode_pending_part(label),
        days_text,
        copy_text,
    ])


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


def schedule_templates_text(db, profile) -> str:
    templates = get_schedule_templates(db, profile.user_id, active_only=True)
    return (
        "週間常用排程：\n"
        f"{week_schedule_overview(templates, profile)}"
    )


def format_schedule_conflict_text(db, user_id: int, weekdays: list[int]) -> str:
    conflicts = get_schedule_conflicts(db, user_id, weekdays)
    if not conflicts:
        return ""
    lines = []
    for template in conflicts:
        overlap = sorted(set(weekdays).intersection(template_weekdays(template)))
        day_text = "、".join(WEEKDAY_NAMES[day] for day in overlap)
        lines.append(f"- {day_text} 已有 {template.target_arrival_time} 到{template.destination_label}")
    return "這些星期已經有排程：\n" + "\n".join(lines)


def save_schedule_template_from_pending(db, user_id: int, pending: dict, *, replace_conflicts: bool = False):
    return create_schedule_template(
        db,
        user_id=user_id,
        target_arrival_time=pending["time"],
        destination_label=pending["label"],
        active_weekdays=pending["days"],
        name=f"{pending['label']} {pending['time']}",
        replace_conflicts=replace_conflicts,
    )


def schedule_saved_text(profile, was_setup: bool = False) -> str:
    text = f"已設定固定排程：{schedule_label(profile.active_weekdays)}。"
    if was_setup:
        text += "\n\n接下來請設定固定到達時間。"
    return text


def setting_overview_text(db, profile, today_override_time: str | None = None, tomorrow_override_time: str | None = None) -> str:
    return "設定完成，請確認以下內容：\n" + format_profile_text(
        profile,
        today_override_time=today_override_time,
        tomorrow_override_time=tomorrow_override_time,
        db=db,
    )


def wizard_weekday_prompt(profile) -> str:
    destination_label = destination_label_for_profile(profile)
    label = arrival_label(destination_label)
    return (
        f"已設定身份：{identity_display_name(getattr(profile, 'identity_type', None))}\n"
        f"後續會顯示「{label}」。\n\n"
        "下一步：請選擇每週哪些日子要啟用提醒。"
    )


def wizard_time_prompt(profile) -> str:
    return field_prompt_for_profile("preferred_arrival_time", profile)


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


def household_member_name(member) -> str:
    return member.display_name or f"成員 {member.id}"


def household_member_label(member) -> str:
    name = household_member_name(member)
    label = f"移除 {name}"
    return label if len(label) <= 20 else f"移除 {name[:16]}..."


def removable_household_members(db, user) -> list:
    household_id = get_household_id_for_user(user)
    return [member for member in get_users_for_household(db, household_id) if member.id != user.id]


def find_household_member_for_removal(db, user, raw_value: str | None):
    value = (raw_value or "").strip()
    if not value:
        return None

    members = removable_household_members(db, user)
    for member in members:
        names = {
            str(member.id),
            f"成員 {member.id}",
            household_member_name(member),
        }
        if value in names:
            return member
    return None


def remove_member_quick_replies(db, user) -> list[dict]:
    items = [
        {"type": "message", "label": household_member_label(member), "text": f"移除家庭成員 {member.id}"}
        for member in removable_household_members(db, user)
    ]
    items.append({"type": "message", "label": "取消", "text": "取消移除成員"})
    return with_done_button(items)


def confirm_remove_member_quick_replies(member_id: int) -> list[dict]:
    return with_done_button([
        {"type": "message", "label": "確認移除", "text": f"確認移除家庭成員 {member_id}"},
        {"type": "message", "label": "取消", "text": "取消移除成員"},
    ])


def format_remove_member_prompt(db, user) -> str:
    if not user_is_household_owner(user):
        return (
            "只有家庭建立者可以移除家庭成員。\n"
            "如果你想離開目前家庭，請傳送「離開家庭」。"
        )

    members = removable_household_members(db, user)
    if not members:
        return "目前沒有其他家庭成員可以移除。"

    member_lines = [f"- {household_member_name(member)}（編號 {member.id}）" for member in members]
    return (
        "請選擇要移除的家庭成員：\n"
        + "\n".join(member_lines)
        + "\n\n被移除的成員會回到自己的個人家庭群組，原本的通勤設定會保留。"
    )


def format_remove_member_confirm_text(member) -> str:
    return (
        f"確定要將「{household_member_name(member)}」移出家庭嗎？\n"
        "對方的帳號和通勤設定會保留，只是不再顯示於這個家庭 Dashboard。"
    )


def format_household_management_text(db, user) -> str:
    household_id = get_household_id_for_user(user)
    members = get_users_for_household(db, household_id)
    member_lines = []
    for member in members:
        name = household_member_name(member)
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
        "移除家庭成員\n"
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
        label = WEEKDAY_NAMES[day]
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
        ("假日", "weekend"),
        ("暫停", "none"),
    ]

    def preset_button(label: str, preset: str) -> dict:
        return {
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

    def preset_row(items: list[tuple[str, str]]) -> dict:
        return {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [preset_button(label, preset) for label, preset in items],
        }

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
                row([0, 1, 2]),
                row([3, 4, 5]),
                row([6]),
                {"type": "separator", "margin": "md"},
                preset_row(preset_buttons[:2]),
                preset_row(preset_buttons[2:]),
            ],
        },
    }


async def reply_weekday_picker(reply_token: str, profile, message: str | None = None, quick_replies: list[dict] | None = None) -> None:
    alt_text = message or "請在 LINE 中選擇通勤排程"
    await reply_flex_with_quick_reply(
        reply_token,
        alt_text,
        build_weekday_picker_flex(profile),
        quick_replies or SCHEDULE_QUICK_REPLIES,
    )


def schedule_template_summary(time_value: str, label: str, days: list[int]) -> str:
    day_text = "、".join(WEEKDAY_NAMES[day] for day in sorted(days)) if days else "暫停排程"
    return f"{time_value} 到{label}｜{day_text}"


def build_schedule_template_weekday_picker_flex(time_value: str, label: str, selected_days: list[int]) -> dict:
    active_days = set(selected_days or [])

    def day_button(day: int) -> dict:
        active = day in active_days
        return {
            "type": "button",
            "style": "primary" if active else "secondary",
            "height": "sm",
            "action": {
                "type": "postback",
                "label": WEEKDAY_NAMES[day],
                "data": f"action=template_toggle_weekday&day={day}",
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
        ("假日", "weekend"),
        ("暫停", "none"),
    ]

    def preset_button(label: str, preset: str) -> dict:
        return {
            "type": "button",
            "style": "secondary",
            "height": "sm",
            "action": {
                "type": "postback",
                "label": label,
                "data": f"action=template_preset&preset={preset}",
            },
        }

    def preset_row(items: list[tuple[str, str]]) -> dict:
        return {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [preset_button(label, preset) for label, preset in items],
        }

    return {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "這組排程適用星期", "weight": "bold", "size": "xl"},
                {"type": "text", "text": schedule_template_summary(time_value, label, sorted(active_days)), "size": "sm", "color": "#666666", "wrap": True},
                {"type": "text", "text": "點選星期不會逐一回覆；選好後按「儲存排程」一次完成。", "size": "xs", "color": "#888888", "wrap": True},
                row([0, 1, 2]),
                row([3, 4, 5]),
                row([6]),
                {"type": "separator", "margin": "md"},
                preset_row(preset_buttons[:2]),
                preset_row(preset_buttons[2:]),
                {"type": "separator", "margin": "md"},
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {
                        "type": "postback",
                        "label": "儲存排程",
                        "data": "action=template_save",
                        "displayText": "儲存排程",
                    },
                },
            ],
        },
    }


async def reply_schedule_template_weekday_picker(reply_token: str, time_value: str, label: str, selected_days: list[int], message: str | None = None) -> None:
    await reply_flex_with_quick_reply(
        reply_token,
        message or "請選擇這組排程適用的星期",
        build_schedule_template_weekday_picker_flex(time_value, label, selected_days),
        with_done_button([{"type": "message", "label": "取消", "text": "取消新增排程"}]),
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


async def reply_topic_menu(reply_token: str, topic_key: str, db, user, today_date, tomorrow_date, today_override, tomorrow_override) -> None:
    if topic_key == "topic_commute":
        await reply_with_quick_reply(reply_token, "通勤選單：請選擇今天要怎麼計算。", COMMUTE_TOPIC_QUICK_REPLIES)
        return
    if topic_key == "topic_time":
        await reply_with_quick_reply(
            reply_token,
            "時間設定：可用一條引導式流程設定身份、提醒星期與固定到達時間，也可臨時調整今天或明天。",
            TIME_TOPIC_QUICK_REPLIES,
        )
        return
    if topic_key == "topic_reminder":
        profile = get_profile(db, user.id)
        await reply_with_quick_reply(
            reply_token,
            f"自動提醒目前：{'開啟' if profile.reminder_enabled else '關閉'}。\n請選擇是否切換。",
            REMINDER_SETTING_QUICK_REPLIES,
        )
        return
    if topic_key == "topic_schedule":
        profile = get_profile(db, user.id)
        await reply_with_quick_reply(
            reply_token,
            format_schedule_text(db, profile, today_date, today_override, tomorrow_date, tomorrow_override),
            SCHEDULE_QUICK_REPLIES,
        )
        return
    if topic_key == "topic_dashboard":
        await reply_with_quick_reply(
            reply_token,
            "看板與家庭：請選擇要取得看板連結、管理家庭成員，或查看電腦外接螢幕模式。",
            DASHBOARD_TOPIC_QUICK_REPLIES,
        )
        return
    if topic_key == "topic_help":
        await reply_flex_with_quick_reply(reply_token, "指令說明", build_command_help_carousel(), [])


async def reply_current_setting_prompt(reply_token: str, db, user, step: str | None) -> None:
    profile = get_profile(db, user.id)
    pending = parse_schedule_template_pending(step)
    if step == "identity_type":
        await reply_with_quick_reply(reply_token, field_prompt_for_profile("identity_type", profile), IDENTITY_QUICK_REPLIES)
        return
    if step == "destination_label":
        await reply_with_quick_reply(reply_token, "請輸入目的地名稱，例如：學校、公司、實驗室、兼職公司。", IDENTITY_QUICK_REPLIES)
        return
    if step in WIZARD_WEEKDAY_PENDING_FIELDS:
        await reply_weekday_picker(reply_token, profile, wizard_weekday_prompt(profile), WIZARD_WEEKDAY_QUICK_REPLIES)
        return
    if step in {"home_location", "office_location", "preferred_arrival_time"}:
        await reply_with_quick_reply(
            reply_token,
            field_prompt_for_profile(step, profile),
            arrival_time_quick_replies_for_profile(profile) if step == "preferred_arrival_time" else setup_quick_replies_for_step(step),
        )
        return
    if pending.get("action") in {"template_days", "template_conflict"}:
        await reply_schedule_template_weekday_picker(reply_token, pending["time"], pending["label"], pending["days"], "請繼續完成常用排程設定。")
        return
    await reply_with_quick_reply(reply_token, "請繼續完成目前設定，或按「完成修改設定」離開。", with_done_button([]))


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
                        await reply_with_quick_reply(
                            reply_token,
                            "收到，今天上班加油！\n已更新為計算明日通勤與提醒時間。",
                            MAIN_MENU_QUICK_REPLIES,
                        )
                        continue

                    if postback_choice == "need_5":
                        if today_override and today_override.departure_timeout_at:
                            await reply_with_quick_reply(
                                reply_token,
                                "今日通勤追蹤已自動暫停，Dashboard 會改看下一個排程。",
                                MAIN_MENU_QUICK_REPLIES,
                            )
                            continue
                        override = snooze_departure_for_user(db, user.id, today_date)
                        snooze_text = format_taipei_hhmm(override.departure_snoozed_until)
                        await reply_with_quick_reply(
                            reply_token,
                            f"好的，{snooze_text} 再提醒您出門。四分鐘後會先提醒一次，時間到會再提醒一次。",
                            MAIN_MENU_QUICK_REPLIES,
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
                        MAIN_MENU_QUICK_REPLIES,
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
                    was_wizard_weekday = profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS
                    active_days = set(normalize_active_weekdays(getattr(profile, "active_weekdays", None)))
                    if day in active_days:
                        active_days.remove(day)
                    else:
                        active_days.add(day)
                    profile = set_active_weekdays(db, user.id, sorted(active_days))
                    set_pending_field(db, user.id, "wizard_active_weekdays" if was_wizard_weekday else None)
                    clear_today_reminder_state_for_user(user.id)
                    await reply_weekday_picker(
                        reply_token,
                        profile,
                        f"已更新：{schedule_label(profile.active_weekdays)}" + ("。\n選好後請按「下一步：時間」。" if was_wizard_weekday else ""),
                        WIZARD_WEEKDAY_QUICK_REPLIES if was_wizard_weekday else None,
                    )
                    continue

                if postback_action == "schedule_preset":
                    preset = (postback_parts.get("preset") or ["everyday"])[0]
                    was_wizard_weekday = get_profile(db, user.id).pending_field in WIZARD_WEEKDAY_PENDING_FIELDS
                    profile = set_active_weekdays(db, user.id, parse_weekday_preset(preset))
                    clear_today_reminder_state_for_user(user.id)
                    if was_wizard_weekday:
                        set_pending_field(db, user.id, "preferred_arrival_time")
                        await reply_with_quick_reply(
                            reply_token,
                            f"已設定提醒日：{schedule_label(profile.active_weekdays)}。\n\n下一步：{wizard_time_prompt(profile)}",
                            arrival_time_quick_replies_for_profile(profile),
                        )
                    else:
                        set_pending_field(db, user.id, None)
                        await reply_weekday_picker(reply_token, profile, f"已更新：{schedule_label(profile.active_weekdays)}")
                    continue

                if postback_action in {"template_toggle_weekday", "template_preset", "template_save"}:
                    profile = get_profile(db, user.id)
                    pending = parse_schedule_template_pending(profile.pending_field)
                    if pending.get("action") not in {"template_days", "template_conflict"}:
                        await reply_with_quick_reply(reply_token, "目前沒有正在新增的常用排程，請先傳送「新增常用排程」。", TIME_TOPIC_QUICK_REPLIES)
                        continue

                    if postback_action == "template_preset":
                        preset = (postback_parts.get("preset") or ["everyday"])[0]
                        pending["days"] = parse_weekday_preset(preset)
                        set_pending_field(
                            db,
                            user.id,
                            build_schedule_template_pending("template_days", pending["time"], pending["label"], pending["days"], pending.get("copy_from")),
                        )
                        continue

                    if postback_action == "template_toggle_weekday":
                        try:
                            day = int((postback_parts.get("day") or [""])[0])
                        except ValueError:
                            day = -1
                        if not 0 <= day <= 6:
                            await reply_schedule_template_weekday_picker(reply_token, pending["time"], pending["label"], pending["days"], "星期讀取失敗，請再選一次。")
                            continue
                        days = set(pending["days"])
                        if day in days:
                            days.remove(day)
                        else:
                            days.add(day)
                        pending["days"] = sorted(days)
                        set_pending_field(
                            db,
                            user.id,
                            build_schedule_template_pending("template_days", pending["time"], pending["label"], pending["days"], pending.get("copy_from")),
                        )
                        continue

                    conflicts = get_schedule_conflicts(db, user.id, pending["days"]) if pending["days"] else []
                    if conflicts:
                        set_pending_field(
                            db,
                            user.id,
                            build_schedule_template_pending("template_conflict", pending["time"], pending["label"], pending["days"], pending.get("copy_from")),
                        )
                        await reply_with_quick_reply(
                            reply_token,
                            f"{format_schedule_conflict_text(db, user.id, pending['days'])}\n\n要以哪一組為準？",
                            SCHEDULE_CONFLICT_QUICK_REPLIES,
                        )
                        continue

                    save_schedule_template_from_pending(db, user.id, pending)
                    set_pending_field(db, user.id, None)
                    clear_today_reminder_state_for_user(user.id)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已新增常用排程：{schedule_template_summary(pending['time'], pending['label'], pending['days'])}\n\n{schedule_templates_text(db, get_profile(db, user.id))}",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if postback_data == "action=set_preferred_arrival_time" and time_value:
                    profile_before = get_profile(db, user.id)
                    if profile_before.pending_field == "template_time":
                        set_pending_field(db, user.id, build_schedule_template_pending("template_label", time_value, ""))
                        await reply_with_quick_reply(
                            reply_token,
                            f"這組排程是 {time_value} 要到哪裡？請選擇或輸入目的地標籤。",
                            SCHEDULE_TEMPLATE_LABEL_QUICK_REPLIES,
                        )
                        continue
                    if profile_before.pending_field and profile_before.pending_field.startswith("copy_template_time:"):
                        template_id_text = profile_before.pending_field.split(":", 1)[1]
                        try:
                            template_id = int(template_id_text)
                        except ValueError:
                            template_id = None
                        template = get_schedule_template(db, user.id, template_id) if template_id else None
                        if template is None:
                            set_pending_field(db, user.id, None)
                            await reply_with_quick_reply(reply_token, "找不到原本排程，請重新選擇。", TIME_TOPIC_QUICK_REPLIES)
                            continue
                        days = template_weekdays(template)
                        set_pending_field(db, user.id, build_schedule_template_pending("template_days", time_value, template.destination_label, days, copy_from=template.id))
                        await reply_schedule_template_weekday_picker(reply_token, time_value, template.destination_label, days, "已複製原排程，請確認星期後按「儲存排程」。")
                        continue
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
                        next_step = get_next_setup_step(updated_profile)
                        if next_step is not None and next_step != "preferred_arrival_time":
                            set_pending_field(db, user.id, next_step)
                            await reply_with_quick_reply(
                                reply_token,
                                f"已設定固定{arrival_label(destination_label_for_profile(updated_profile))}：{time_value}\n\n下一步：{field_prompt_for_profile(next_step, updated_profile)}",
                                setup_quick_replies_for_step(next_step),
                            )
                        else:
                            set_pending_field(db, user.id, None)
                            await reply_with_quick_reply(
                                reply_token,
                                f"已設定固定{arrival_label(destination_label_for_profile(updated_profile))}：{time_value}\n\n{setting_overview_text(db, updated_profile, today_override_time, tomorrow_override_time)}",
                                MAIN_MENU_QUICK_REPLIES,
                            )
                        continue

                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已更新固定{arrival_label(destination_label_for_profile(updated_profile))}：{time_value}\n系統已開始重新計算今日提醒。\n\n{format_profile_text(updated_profile, today_override_time, tomorrow_override_time, db=db)}",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if postback_data == "action=set_today_arrival_time" and time_value:
                    upsert_override(db, user.id, today_date, time_value)
                    profile = get_profile(db, user.id)
                    destination_label = destination_label_for_profile(profile)
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-postback-today] error={e}")
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        (
                            f"已設定今天臨時{arrival_label(destination_label)}：{time_value}\n"
                            "已為您調整時間並開始重新計算今天提醒。\n"
                            f"明天會自動回到固定{arrival_label(destination_label)} {profile.preferred_arrival_time}。"
                        ),
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if postback_data == "action=set_tomorrow_arrival_time" and time_value:
                    upsert_override(db, user.id, tomorrow_date, time_value)
                    profile = get_profile(db, user.id)
                    destination_label = destination_label_for_profile(profile)
                    set_pending_field(db, user.id, None)
                    departure_time = await calculate_departure_time(get_profile(db, user.id), tomorrow_date, time_value)
                    await reply_with_quick_reply(
                        reply_token,
                        (
                            f"已設定明天臨時{arrival_label(destination_label)}：{time_value}\n"
                            "已為您調整時間並開始重新計算明天提醒。\n"
                            f"明天建議出門：{departure_time}\n"
                            f"後天會自動回到固定{arrival_label(destination_label)} {profile.preferred_arrival_time}。"
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
                    profile = get_profile(db, user.id)
                    next_step = get_next_setup_step(profile)
                    if next_step is None:
                        set_pending_field(db, user.id, None)
                        await reply_with_quick_reply(reply_token, "已儲存住家位置，設定已更新。", MAIN_MENU_QUICK_REPLIES)
                        continue
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        "已儲存住家位置。\n" + field_prompt_for_profile(next_step, profile),
                        setup_quick_replies_for_step(next_step),
                    )
                    continue

                if current_step == "office_location":
                    await save_location_or_address(db, user.id, "office", raw_address, lat=lat, lng=lng)
                    clear_today_reminder_state_for_user(user.id)
                    profile = get_profile(db, user.id)
                    next_step = get_next_setup_step(profile)
                    if next_step is None:
                        set_pending_field(db, user.id, None)
                        await reply_with_quick_reply(reply_token, f"已儲存{destination_label_for_profile(profile)}位置，設定已更新。", MAIN_MENU_QUICK_REPLIES)
                        continue
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已儲存{destination_label_for_profile(profile)}位置。\n" + field_prompt_for_profile(next_step, profile),
                        setup_quick_replies_for_step(next_step),
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
                    | COMMAND_ALIASES["topic_commute"]
                    | COMMAND_ALIASES["topic_time"]
                    | COMMAND_ALIASES["topic_reminder"]
                    | COMMAND_ALIASES["topic_schedule"]
                    | COMMAND_ALIASES["topic_dashboard"]
                    | COMMAND_ALIASES["topic_help"]
                    | COMMAND_ALIASES["identity_settings"]
                    | COMMAND_ALIASES["set_identity_student"]
                    | COMMAND_ALIASES["set_identity_worker"]
                    | COMMAND_ALIASES["set_identity_slash"]
                    | COMMAND_ALIASES["custom_destination_label"]
                    | COMMAND_ALIASES["schedule_workdays"]
                    | COMMAND_ALIASES["schedule_everyday"]
                    | COMMAND_ALIASES["schedule_weekend"]
                    | COMMAND_ALIASES["schedule_none"]
                    | COMMAND_ALIASES["schedule_custom"]
                    | COMMAND_ALIASES["next_wizard_time"]
                    | COMMAND_ALIASES["reset"]
                    | COMMAND_ALIASES["finish_settings"]
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
                elif _current_pending == "destination_label":
                    _is_valid_setup_input = bool(user_text.strip())
                elif _current_pending in WIZARD_WEEKDAY_PENDING_FIELDS:
                    _is_valid_setup_input = parse_custom_weekdays(user_text) is not None

                if command_text not in _setup_commands and not _is_valid_setup_input:
                    set_pending_field(db, user.id, _next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        "⚙️ 請先完成基本設定，才能使用完整功能！\n點擊下方按鈕快速設定：",
                        SETUP_QUICK_REPLIES,
                    )
                    continue
            # ===========================================================

            current_pending_for_switch = get_profile(db, user.id).pending_field
            if current_pending_for_switch and current_pending_for_switch.startswith("confirm_switch|"):
                _, topic_key, original_step = (current_pending_for_switch.split("|", 2) + ["", ""])[:3]
                if command_text in COMMAND_ALIASES["cancel_and_switch"]:
                    set_pending_field(db, user.id, None)
                    await reply_topic_menu(reply_token, topic_key, db, user, today_date, tomorrow_date, today_override, tomorrow_override)
                    continue
                if command_text in COMMAND_ALIASES["continue_setting"]:
                    set_pending_field(db, user.id, original_step)
                    await reply_current_setting_prompt(reply_token, db, user, original_step)
                    continue
                await reply_with_quick_reply(
                    reply_token,
                    "請選擇要繼續目前設定，或取消並切換到剛剛點選的選單。",
                    SWITCH_SETTING_QUICK_REPLIES,
                )
                continue

            switch_topic_key = topic_key_for_command(command_text)
            if switch_topic_key and is_setting_flow_pending(current_pending_for_switch):
                set_pending_field(db, user.id, f"confirm_switch|{switch_topic_key}|{current_pending_for_switch}")
                await reply_with_quick_reply(
                    reply_token,
                    f"您正在設定「{setting_step_label(current_pending_for_switch)}」，是否要取消並切換？",
                    SWITCH_SETTING_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["finish_settings"]:
                profile = get_profile(db, user.id)
                if profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS:
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        wizard_time_prompt(profile),
                        arrival_time_quick_replies_for_profile(profile),
                    )
                    continue
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        "基本設定尚未完成，先完成這一步就能使用完整功能：\n" + field_prompt_for_profile(next_step, profile),
                        setup_quick_replies_for_step(next_step),
                    )
                    continue

                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    "已完成修改設定，已回到常用選單。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if is_schedule_setup_pending(_profile_for_guard):
                schedule_commands = (
                    COMMAND_ALIASES["schedule_workdays"]
                    | COMMAND_ALIASES["schedule_everyday"]
                    | COMMAND_ALIASES["schedule_weekend"]
                    | COMMAND_ALIASES["schedule_none"]
                    | COMMAND_ALIASES["schedule_custom"]
                    | COMMAND_ALIASES["next_wizard_time"]
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
                    await reply_with_quick_reply(reply_token, "你好，我是智慧通勤助理。\n" + READY_MENU_TEXT, MAIN_MENU_QUICK_REPLIES)
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
                    "時間設定：可設定身份標籤、新增週間常用排程，也可臨時調整今天或明天的到達時間。",
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
                    format_schedule_text(db, profile, today_date, today_override, tomorrow_date, tomorrow_override),
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
                await reply_with_quick_reply(reply_token, field_prompt_for_profile("office_location", get_profile(db, user.id)), OFFICE_QUICK_REPLY)
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
                        format_profile_text(profile, today_override_time, tomorrow_override_time, today_mode, db=db),
                        MAIN_MENU_QUICK_REPLIES
                    )
                else:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(
                        reply_token,
                        format_profile_text(profile, today_override_time, tomorrow_override_time, today_mode, db=db) + "\n\n" + field_prompt_for_profile(next_step, profile),
                        setup_quick_replies_for_step(next_step)
                    )
                continue

            if command_text in COMMAND_ALIASES["identity_settings"]:
                profile = get_profile(db, user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"目前身份：{identity_display_name(getattr(profile, 'identity_type', None))}\n目前目的地顯示為：{destination_label_for_profile(profile)}\n請選擇身份，或自訂目的地標籤。",
                    IDENTITY_QUICK_REPLIES,
                )
                continue

            identity_map = {
                "set_identity_student": ("student", destination_label_for_identity("student")),
                "set_identity_worker": ("worker", destination_label_for_identity("worker")),
                "set_identity_slash": ("slash", destination_label_for_identity("slash")),
            }
            handled_identity = False
            for alias_key, (identity_type, label) in identity_map.items():
                if command_text in COMMAND_ALIASES[alias_key]:
                    profile = set_identity_and_destination_label(db, user.id, identity_type, label)
                    clear_today_reminder_state_for_user(user.id)
                    set_pending_field(db, user.id, "wizard_active_weekdays")
                    await reply_weekday_picker(
                        reply_token,
                        profile,
                        wizard_weekday_prompt(profile),
                        WIZARD_WEEKDAY_QUICK_REPLIES,
                    )
                    handled_identity = True
                    break
            if handled_identity:
                continue

            if command_text in COMMAND_ALIASES["custom_destination_label"]:
                set_pending_field(db, user.id, "destination_label")
                await reply_with_quick_reply(
                    reply_token,
                    "請輸入想顯示的目的地名稱，例如：學校、公司、實驗室、兼職公司。",
                    with_done_button([
                        {"type": "message", "label": "學校", "text": "學校"},
                        {"type": "message", "label": "公司", "text": "公司"},
                        {"type": "message", "label": "兼職公司", "text": "兼職公司"},
                    ]),
                )
                continue

            if command_text in COMMAND_ALIASES["add_schedule_template"]:
                set_pending_field(db, user.id, "template_time")
                await reply_with_quick_reply(
                    reply_token,
                    "請輸入這組常用排程的目標到達時間，例如：09:00。",
                    ARRIVAL_TIME_QUICK_REPLIES,
                )
                continue

            copy_schedule_value = extract_command_value(user_text, COMMAND_ALIASES["copy_schedule_template"])
            if copy_schedule_value:
                template_id_text = copy_schedule_value.replace("排程", "").strip()
                try:
                    template_id = int(template_id_text)
                except ValueError:
                    template_id = None
                template = get_schedule_template(db, user.id, template_id) if template_id else None
                if template is None:
                    await reply_with_quick_reply(reply_token, "找不到這組排程，請重新選擇。", TIME_TOPIC_QUICK_REPLIES)
                    continue
                set_pending_field(db, user.id, f"copy_template_time:{template.id}")
                await reply_with_quick_reply(
                    reply_token,
                    f"要複製「{template.target_arrival_time} 到{template.destination_label}」。\n請輸入新的到達時間，例如：08:30。",
                    ARRIVAL_TIME_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["copy_schedule_template"]:
                templates = get_schedule_templates(db, user.id, active_only=True)
                if not templates:
                    await reply_with_quick_reply(reply_token, "目前還沒有可複製的常用排程，請先新增一組。", TIME_TOPIC_QUICK_REPLIES)
                    continue
                items = [
                    {
                        "type": "message",
                        "label": f"複製 {template.id}",
                        "text": f"複製常用排程 {template.id}",
                    }
                    for template in templates[:10]
                ]
                await reply_with_quick_reply(
                    reply_token,
                    "請選擇要複製的排程：\n" + "\n".join(
                        f"{template.id}. {template.target_arrival_time} 到{template.destination_label}（{schedule_label(template.active_weekdays)}）"
                        for template in templates
                    ),
                    with_done_button(items),
                )
                continue

            if command_text in COMMAND_ALIASES["cancel_schedule_template"]:
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(reply_token, "已取消新增常用排程。", TIME_TOPIC_QUICK_REPLIES)
                continue

            if command_text in COMMAND_ALIASES["replace_schedule_conflict"] or command_text in COMMAND_ALIASES["keep_schedule_conflict"]:
                profile = get_profile(db, user.id)
                pending = parse_schedule_template_pending(profile.pending_field)
                if pending.get("action") != "template_conflict":
                    await reply_with_quick_reply(reply_token, "目前沒有待處理的排程衝突。", TIME_TOPIC_QUICK_REPLIES)
                    continue
                replace_conflicts = command_text in COMMAND_ALIASES["replace_schedule_conflict"]
                if not replace_conflicts:
                    conflict_days = set()
                    for template in get_schedule_conflicts(db, user.id, pending["days"]):
                        conflict_days.update(set(pending["days"]).intersection(template_weekdays(template)))
                    pending["days"] = [day for day in pending["days"] if day not in conflict_days]
                    if not pending["days"]:
                        set_pending_field(db, user.id, None)
                        await reply_with_quick_reply(reply_token, "已保留原排程，這次沒有新增任何星期。", TIME_TOPIC_QUICK_REPLIES)
                        continue
                save_schedule_template_from_pending(db, user.id, pending, replace_conflicts=replace_conflicts)
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"已新增常用排程：{schedule_template_summary(pending['time'], pending['label'], pending['days'])}\n\n{schedule_templates_text(db, get_profile(db, user.id))}",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["dashboard_link"]:
                dashboard_url = build_dashboard_view_url(
                    user_id=user.id,
                    public_url=PUBLIC_URL,
                    request_base_url=str(request.base_url),
                )
                await reply_with_quick_reply(
                    reply_token,
                    "外接螢幕看板連結：\n"
                    f"{dashboard_url}\n\n"
                    "在要顯示的螢幕上打開這個網址，並把瀏覽器切成全螢幕即可。\n"
                    "若要設定開機自動打開，請傳送「電腦 Dashboard 設定」。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["household_dashboard_link"]:
                user = ensure_personal_household(db, user.id)
                household_url = build_household_dashboard_view_url(
                    household_id=get_household_id_for_user(user),
                    public_url=PUBLIC_URL,
                    request_base_url=str(request.base_url),
                )
                await reply_with_quick_reply(
                    reply_token,
                    "家庭外接螢幕看板連結：\n"
                    f"{household_url}\n\n"
                    "家人都加入這個 LINE Bot 後，這個看板會一起顯示每位成員的出門狀態。\n"
                    "若要設定開機自動打開，請傳送「電腦 Dashboard 設定」。",
                    MAIN_MENU_QUICK_REPLIES,
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
                await reply_with_quick_reply(reply_token, format_computer_dashboard_guide(dashboard_url, household_url), MAIN_MENU_QUICK_REPLIES)
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

            if command_text in COMMAND_ALIASES["cancel_remove_household_member"]:
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(reply_token, "已取消移除家庭成員。", HOUSEHOLD_QUICK_REPLIES)
                continue

            confirm_remove_value = extract_command_value(user_text, COMMAND_ALIASES["confirm_remove_household_member"])
            if confirm_remove_value:
                user = ensure_personal_household(db, user.id)
                member = find_household_member_for_removal(db, user, confirm_remove_value)
                if not user_is_household_owner(user):
                    await reply_with_quick_reply(reply_token, format_remove_member_prompt(db, user), HOUSEHOLD_QUICK_REPLIES)
                    continue
                if member is None:
                    await reply_with_quick_reply(reply_token, "找不到這位家庭成員，請重新選擇。", remove_member_quick_replies(db, user))
                    continue
                removed_name = household_member_name(member)
                remove_user_from_household(db, user.id, member.id)
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    f"已將「{removed_name}」移出家庭。\n對方會回到自己的個人家庭群組，原本通勤設定仍會保留。",
                    HOUSEHOLD_QUICK_REPLIES,
                )
                continue

            remove_member_value = extract_command_value(user_text, COMMAND_ALIASES["remove_household_member"])
            if remove_member_value:
                user = ensure_personal_household(db, user.id)
                if not user_is_household_owner(user):
                    await reply_with_quick_reply(reply_token, format_remove_member_prompt(db, user), HOUSEHOLD_QUICK_REPLIES)
                    continue
                member = find_household_member_for_removal(db, user, remove_member_value)
                if member is None:
                    await reply_with_quick_reply(reply_token, "找不到這位家庭成員，請重新選擇。", remove_member_quick_replies(db, user))
                    continue
                set_pending_field(db, user.id, f"confirm_remove_household_member:{member.id}")
                await reply_with_quick_reply(
                    reply_token,
                    format_remove_member_confirm_text(member),
                    confirm_remove_member_quick_replies(member.id),
                )
                continue

            if command_text in COMMAND_ALIASES["remove_household_member"]:
                user = ensure_personal_household(db, user.id)
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    format_remove_member_prompt(db, user),
                    remove_member_quick_replies(db, user) if user_is_household_owner(user) and removable_household_members(db, user) else HOUSEHOLD_QUICK_REPLIES,
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
                await reply_with_quick_reply(reply_token, "請輸入家庭邀請碼，或直接傳送：加入家庭 邀請碼", HOUSEHOLD_QUICK_REPLIES)
                continue

            if command_text in COMMAND_ALIASES["set_display_name"]:
                set_pending_field(db, user.id, "display_name")
                await reply_with_quick_reply(reply_token, "請輸入要顯示在家庭 Dashboard 上的名稱，例如：設定我的名稱 小明", HOUSEHOLD_QUICK_REPLIES)
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
                profile = set_reminder_enabled(db, user.id, True)
                await reply_with_quick_reply(reply_token, f"已開啟自動提醒，系統會依目前生效的{arrival_label(destination_label_for_profile(profile))}重新計算提醒。", MAIN_MENU_QUICK_REPLIES)
                continue

            if command_text in COMMAND_ALIASES["disable_reminder"]:
                set_reminder_enabled(db, user.id, False)
                await reply_with_quick_reply(reply_token, "已關閉自動提醒。", MAIN_MENU_QUICK_REPLIES)
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
                    format_schedule_text(db, profile, today_date, today_override, tomorrow_date, tomorrow_override),
                    SCHEDULE_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["next_wizard_time"]:
                profile = get_profile(db, user.id)
                if profile.pending_field not in WIZARD_WEEKDAY_PENDING_FIELDS:
                    await reply_with_quick_reply(reply_token, "目前沒有正在進行的設定嚮導。", TIME_TOPIC_QUICK_REPLIES)
                    continue
                set_pending_field(db, user.id, "preferred_arrival_time")
                await reply_with_quick_reply(
                    reply_token,
                    wizard_time_prompt(profile),
                    arrival_time_quick_replies_for_profile(profile),
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_workdays"]:
                current_profile = get_profile(db, user.id)
                was_wizard_schedule = current_profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS
                was_setup_schedule = is_schedule_setup_pending(current_profile)
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("workdays"))
                clear_today_reminder_state_for_user(user.id)
                if was_wizard_schedule:
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定提醒日：{schedule_label(profile.active_weekdays)}。\n\n下一步：{wizard_time_prompt(profile)}",
                        arrival_time_quick_replies_for_profile(profile),
                    )
                    continue
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_everyday"]:
                current_profile = get_profile(db, user.id)
                was_wizard_schedule = current_profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS
                was_setup_schedule = is_schedule_setup_pending(current_profile)
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("everyday"))
                clear_today_reminder_state_for_user(user.id)
                if was_wizard_schedule:
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定提醒日：{schedule_label(profile.active_weekdays)}。\n\n下一步：{wizard_time_prompt(profile)}",
                        arrival_time_quick_replies_for_profile(profile),
                    )
                    continue
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_weekend"]:
                current_profile = get_profile(db, user.id)
                was_wizard_schedule = current_profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS
                was_setup_schedule = is_schedule_setup_pending(current_profile)
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("weekend"))
                clear_today_reminder_state_for_user(user.id)
                if was_wizard_schedule:
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定提醒日：{schedule_label(profile.active_weekdays)}。\n\n下一步：{wizard_time_prompt(profile)}",
                        arrival_time_quick_replies_for_profile(profile),
                    )
                    continue
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_none"]:
                current_profile = get_profile(db, user.id)
                was_wizard_schedule = current_profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS
                was_setup_schedule = is_schedule_setup_pending(current_profile)
                profile = set_active_weekdays(db, user.id, parse_weekday_preset("none"))
                clear_today_reminder_state_for_user(user.id)
                if was_wizard_schedule:
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定提醒日：{schedule_label(profile.active_weekdays)}。\n\n下一步：{wizard_time_prompt(profile)}",
                        arrival_time_quick_replies_for_profile(profile),
                    )
                    continue
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["schedule_custom"]:
                current_profile = get_profile(db, user.id)
                if current_profile.pending_field in WIZARD_WEEKDAY_PENDING_FIELDS:
                    set_pending_field(db, user.id, "wizard_custom_active_weekdays")
                    await reply_weekday_picker(reply_token, current_profile, custom_schedule_prompt(), WIZARD_WEEKDAY_QUICK_REPLIES)
                else:
                    set_pending_field(db, user.id, "custom_active_weekdays")
                    await reply_weekday_picker(reply_token, current_profile, custom_schedule_prompt())
                continue

            if command_text in COMMAND_ALIASES["pause_today"]:
                set_commute_disabled_for_date(db, user.id, today_date, True)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    "已設定今天休息，今天不會再推送出門提醒。Dashboard 會改看下一個啟用日。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["pause_tomorrow"]:
                set_commute_disabled_for_date(db, user.id, tomorrow_date, True)
                await reply_with_quick_reply(
                    reply_token,
                    "已設定明天休息，明天不會推送通勤提醒。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["enable_today"]:
                set_commute_disabled_for_date(db, user.id, today_date, False)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    "已啟用今天通勤提醒，會依今天實際到達時間重新計算。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["enable_tomorrow"]:
                set_commute_disabled_for_date(db, user.id, tomorrow_date, False)
                await reply_with_quick_reply(
                    reply_token,
                    "已啟用明天通勤提醒，會依明天實際到達時間計算。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["departure_left"]:
                confirm_departure_for_user(db, user.id, today_date)
                await reply_with_quick_reply(
                    reply_token,
                    "收到，今天上班加油！\n已更新為計算明日通勤與提醒時間。",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["departure_need_5"]:
                today_override = get_override_for_date(db, user.id, today_date)
                if today_override and today_override.departure_timeout_at:
                    await reply_with_quick_reply(
                        reply_token,
                        "今日通勤追蹤已自動暫停，Dashboard 會改看下一個排程。",
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue
                override = snooze_departure_for_user(db, user.id, today_date)
                snooze_text = format_taipei_hhmm(override.departure_snoozed_until)
                await reply_with_quick_reply(
                    reply_token,
                    f"好的，{snooze_text} 再提醒您出門。四分鐘後會先提醒一次，時間到會再提醒一次。",
                    MAIN_MENU_QUICK_REPLIES,
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
                await reply_with_quick_reply(reply_token, "已設定今天交通方式為：公車轉捷運。已開始重新計算今天提醒。", MAIN_MENU_QUICK_REPLIES)
                continue

            if command_text in COMMAND_ALIASES["view_mode_today"]:
                current_mode = get_transport_mode_override(db, user.id, today_date) or "auto"
                await reply_with_quick_reply(reply_token, f"今天交通方式設定：{TRANSPORT_MODE_NAME_MAP.get(current_mode, '自動判斷')}", MAIN_MENU_QUICK_REPLIES)
                continue

            if command_text in COMMAND_ALIASES["today_commute"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(reply_token, field_prompt_for_profile(next_step, profile), setup_quick_replies_for_step(next_step))
                    continue
                today_setting = effective_commute_setting_for_date(db, profile, today_date, today_override)
                if today_setting is None:
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
                    await reply_with_quick_reply(reply_token, field_prompt_for_profile(next_step, profile), setup_quick_replies_for_step(next_step))
                    continue

                override = get_override_for_date(db, user.id, tomorrow_date)
                tomorrow_setting = effective_commute_setting_for_date(db, profile, tomorrow_date, override)
                if tomorrow_setting is None:
                    await reply_with_quick_reply(
                        reply_token,
                        "明天排程是休息日，不會推送通勤提醒。\n若明天臨時要通勤，請按「明天啟用」。",
                        SCHEDULE_QUICK_REPLIES,
                    )
                    continue
                effective_arrival_time = tomorrow_setting["arrival_time"]
                destination_label = tomorrow_setting["destination_label"]

                departure_time = await calculate_departure_time(profile, tomorrow_date, effective_arrival_time)
                await reply_with_quick_reply(
                    reply_token,
                    f"明天{arrival_label(destination_label)}：{effective_arrival_time}\n明天建議出門：{departure_time}",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["edit_today_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(reply_token, field_prompt_for_profile(next_step, profile), setup_quick_replies_for_step(next_step))
                    continue

                set_pending_field(db, user.id, "override_today_arrival_time")
                await reply_with_quick_reply(
                    reply_token,
                    field_prompt_for_profile("override_today_arrival_time", profile),
                    OVERRIDE_TODAY_TIME_QUICK_REPLIES,
                )
                continue

            if command_text in COMMAND_ALIASES["edit_tomorrow_arrival"]:
                profile = get_profile(db, user.id)
                next_step = get_next_setup_step(profile)
                if next_step is not None:
                    set_pending_field(db, user.id, next_step)
                    await reply_with_quick_reply(reply_token, field_prompt_for_profile(next_step, profile), setup_quick_replies_for_step(next_step))
                    continue

                set_pending_field(db, user.id, "override_tomorrow_arrival_time")
                await reply_with_quick_reply(
                    reply_token,
                    field_prompt_for_profile("override_tomorrow_arrival_time", profile),
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

            if current_step and current_step.startswith("confirm_remove_household_member:"):
                member_id_text = current_step.split(":", 1)[1]
                if command_text in COMMAND_ALIASES["cancel_remove_household_member"] or command_text in COMMAND_ALIASES["finish_settings"]:
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(reply_token, "已取消移除家庭成員。", HOUSEHOLD_QUICK_REPLIES)
                    continue

                if command_text in COMMAND_ALIASES["confirm_remove_household_member"]:
                    user = ensure_personal_household(db, user.id)
                    member = find_household_member_for_removal(db, user, member_id_text)
                    if not user_is_household_owner(user):
                        set_pending_field(db, user.id, None)
                        await reply_with_quick_reply(reply_token, format_remove_member_prompt(db, user), HOUSEHOLD_QUICK_REPLIES)
                        continue
                    if member is None:
                        set_pending_field(db, user.id, None)
                        await reply_with_quick_reply(reply_token, "找不到這位家庭成員，請重新選擇。", HOUSEHOLD_QUICK_REPLIES)
                        continue
                    removed_name = household_member_name(member)
                    remove_user_from_household(db, user.id, member.id)
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        f"已將「{removed_name}」移出家庭。\n對方會回到自己的個人家庭群組，原本通勤設定仍會保留。",
                        HOUSEHOLD_QUICK_REPLIES,
                    )
                    continue

                member = find_household_member_for_removal(db, user, member_id_text)
                if member is not None:
                    await reply_with_quick_reply(
                        reply_token,
                        format_remove_member_confirm_text(member),
                        confirm_remove_member_quick_replies(member.id),
                    )
                    continue

            if current_step == "destination_label":
                label = user_text.strip()
                if not label or label in COMMAND_ALIASES["custom_destination_label"]:
                    await reply_with_quick_reply(reply_token, "請輸入目的地名稱，例如：學校、公司、實驗室、兼職公司。", IDENTITY_QUICK_REPLIES)
                    continue
                identity_type = getattr(profile, "identity_type", None) or "slash"
                profile = set_identity_and_destination_label(db, user.id, identity_type, label)
                clear_today_reminder_state_for_user(user.id)
                set_pending_field(db, user.id, "wizard_active_weekdays")
                await reply_weekday_picker(
                    reply_token,
                    profile,
                    wizard_weekday_prompt(profile),
                    WIZARD_WEEKDAY_QUICK_REPLIES,
                )
                continue

            if current_step == "template_time":
                value = user_text.strip()
                if not hhmm_is_valid(value):
                    await reply_with_quick_reply(reply_token, "時間格式錯誤，請輸入 HH:MM，例如 09:00。", ARRIVAL_TIME_QUICK_REPLIES)
                    continue
                set_pending_field(db, user.id, build_schedule_template_pending("template_label", value, ""))
                await reply_with_quick_reply(
                    reply_token,
                    f"這組排程是 {value} 要到哪裡？請選擇或輸入目的地標籤。",
                    SCHEDULE_TEMPLATE_LABEL_QUICK_REPLIES,
                )
                continue

            if current_step and current_step.startswith("copy_template_time:"):
                template_id_text = current_step.split(":", 1)[1]
                try:
                    template_id = int(template_id_text)
                except ValueError:
                    template_id = None
                template = get_schedule_template(db, user.id, template_id) if template_id else None
                value = user_text.strip()
                if template is None:
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(reply_token, "找不到原本排程，請重新選擇。", TIME_TOPIC_QUICK_REPLIES)
                    continue
                if not hhmm_is_valid(value):
                    await reply_with_quick_reply(reply_token, "時間格式錯誤，請輸入 HH:MM，例如 08:30。", ARRIVAL_TIME_QUICK_REPLIES)
                    continue
                days = template_weekdays(template)
                set_pending_field(db, user.id, build_schedule_template_pending("template_days", value, template.destination_label, days, copy_from=template.id))
                await reply_schedule_template_weekday_picker(reply_token, value, template.destination_label, days, "已複製原排程，請確認星期後按「儲存排程」。")
                continue

            pending_template = parse_schedule_template_pending(current_step)
            if pending_template.get("action") == "template_label":
                label = user_text.strip()
                if not label or label == "自訂目的地":
                    await reply_with_quick_reply(reply_token, "請直接輸入目的地標籤，例如：學校、公司、兼職公司。", SCHEDULE_TEMPLATE_LABEL_QUICK_REPLIES)
                    continue
                set_pending_field(db, user.id, build_schedule_template_pending("template_days", pending_template["time"], label, []))
                await reply_schedule_template_weekday_picker(reply_token, pending_template["time"], label, [], "請批次勾選這組時間適用的星期。")
                continue

            if pending_template.get("action") == "template_days":
                weekdays = parse_custom_weekdays(user_text)
                if weekdays is None:
                    await reply_schedule_template_weekday_picker(reply_token, pending_template["time"], pending_template["label"], pending_template["days"], "請用下方卡片勾選星期，或輸入例如：週一週三週五。")
                    continue
                pending_template["days"] = weekdays
                conflicts = get_schedule_conflicts(db, user.id, pending_template["days"])
                if conflicts:
                    set_pending_field(db, user.id, build_schedule_template_pending("template_conflict", pending_template["time"], pending_template["label"], pending_template["days"], pending_template.get("copy_from")))
                    await reply_with_quick_reply(
                        reply_token,
                        f"{format_schedule_conflict_text(db, user.id, pending_template['days'])}\n\n要以哪一組為準？",
                        SCHEDULE_CONFLICT_QUICK_REPLIES,
                    )
                    continue
                save_schedule_template_from_pending(db, user.id, pending_template)
                set_pending_field(db, user.id, None)
                clear_today_reminder_state_for_user(user.id)
                await reply_with_quick_reply(
                    reply_token,
                    f"已新增常用排程：{schedule_template_summary(pending_template['time'], pending_template['label'], pending_template['days'])}\n\n{schedule_templates_text(db, get_profile(db, user.id))}",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if current_step in SCHEDULE_PENDING_FIELDS:
                weekdays = parse_custom_weekdays(user_text)
                if weekdays is None:
                    if current_step in WIZARD_WEEKDAY_PENDING_FIELDS:
                        set_pending_field(db, user.id, "wizard_custom_active_weekdays")
                        await reply_weekday_picker(reply_token, profile, custom_schedule_prompt(), WIZARD_WEEKDAY_QUICK_REPLIES)
                    else:
                        set_pending_field(db, user.id, "custom_active_weekdays")
                        await reply_weekday_picker(reply_token, profile, custom_schedule_prompt())
                    continue

                was_wizard_schedule = current_step in WIZARD_WEEKDAY_PENDING_FIELDS
                was_setup_schedule = is_schedule_setup_pending(profile)
                profile = set_active_weekdays(db, user.id, weekdays)
                clear_today_reminder_state_for_user(user.id)
                if was_wizard_schedule:
                    set_pending_field(db, user.id, "preferred_arrival_time")
                    await reply_with_quick_reply(
                        reply_token,
                        f"已設定提醒日：{schedule_label(profile.active_weekdays)}。\n\n下一步：{wizard_time_prompt(profile)}",
                        arrival_time_quick_replies_for_profile(profile),
                    )
                    continue
                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    schedule_saved_text(profile, was_setup_schedule),
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            if current_step in {"home_location", "office_location"}:
                typed_address = user_text.strip()
                if not typed_address:
                    await reply_with_quick_reply(reply_token, field_prompt_for_profile(current_step, profile),
                                                 HOME_QUICK_REPLY if current_step == "home_location" else OFFICE_QUICK_REPLY)
                    continue

                if not looks_like_address(typed_address):
                    await reply_with_quick_reply(reply_token, field_prompt_for_profile(current_step, profile),
                                                 HOME_QUICK_REPLY if current_step == "home_location" else OFFICE_QUICK_REPLY)
                    continue

                try:
                    if current_step == "home_location":
                        await save_location_or_address(db, user.id, "home", typed_address)
                        clear_today_reminder_state_for_user(user.id)
                        profile = get_profile(db, user.id)
                        next_step = get_next_setup_step(profile)
                        if next_step is None:
                            set_pending_field(db, user.id, None)
                            await reply_with_quick_reply(reply_token, "已儲存住家位置，設定已更新。", MAIN_MENU_QUICK_REPLIES)
                            continue
                        set_pending_field(db, user.id, next_step)
                        await reply_with_quick_reply(
                            reply_token,
                            "已儲存住家位置。\n" + field_prompt_for_profile(next_step, profile),
                            setup_quick_replies_for_step(next_step),
                        )
                        continue

                    if current_step == "office_location":
                        await save_location_or_address(db, user.id, "office", typed_address)
                        clear_today_reminder_state_for_user(user.id)
                        profile = get_profile(db, user.id)
                        next_step = get_next_setup_step(profile)
                        if next_step is None:
                            set_pending_field(db, user.id, None)
                            await reply_with_quick_reply(reply_token, f"已儲存{destination_label_for_profile(profile)}位置，設定已更新。", MAIN_MENU_QUICK_REPLIES)
                            continue
                        set_pending_field(db, user.id, next_step)
                        await reply_with_quick_reply(
                            reply_token,
                            f"已儲存{destination_label_for_profile(profile)}位置。\n" + field_prompt_for_profile(next_step, profile),
                            setup_quick_replies_for_step(next_step),
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
                        qr = arrival_time_quick_replies_for_profile(profile)
                    elif current_step == "override_today_arrival_time":
                        qr = OVERRIDE_TODAY_TIME_QUICK_REPLIES
                    else:
                        qr = OVERRIDE_TOMORROW_TIME_QUICK_REPLIES
                    await reply_with_quick_reply(reply_token, error_message + "\n" + field_prompt_for_profile(current_step, profile), qr)
                    continue

                if current_step == "override_today_arrival_time":
                    upsert_override(db, user.id, today_date, value)
                    destination_label = destination_label_for_profile(get_profile(db, user.id))
                    clear_today_reminder_state_for_user(user.id)
                    try:
                        await freeze_today_reminder_payload(db, user.id, today_date)
                    except Exception as e:
                        print(f"[freeze-today-override] error={e}")
                    set_pending_field(db, user.id, None)
                    await reply_with_quick_reply(
                        reply_token,
                        (
                            f"已設定今天臨時{arrival_label(destination_label)}：{value}\n"
                            "已為您調整時間並開始重新計算今天提醒。\n"
                            f"明天會自動回到固定{arrival_label(destination_label)} {get_profile(db, user.id).preferred_arrival_time}。"
                        ),
                        MAIN_MENU_QUICK_REPLIES,
                    )
                    continue

                if current_step == "override_tomorrow_arrival_time":
                    upsert_override(db, user.id, tomorrow_date, value)
                    destination_label = destination_label_for_profile(get_profile(db, user.id))
                    set_pending_field(db, user.id, None)
                    departure_time = await calculate_departure_time(get_profile(db, user.id), tomorrow_date, value)
                    await reply_with_quick_reply(
                        reply_token,
                        (
                            f"已設定明天臨時{arrival_label(destination_label)}：{value}\n"
                            "已為您調整時間並開始重新計算明天提醒。\n"
                            f"明天建議出門：{departure_time}\n"
                            f"後天會自動回到固定{arrival_label(destination_label)} {get_profile(db, user.id).preferred_arrival_time}。"
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
                    next_step = get_next_setup_step(updated_profile)
                    if next_step is not None and next_step != "preferred_arrival_time":
                        set_pending_field(db, user.id, next_step)
                        await reply_with_quick_reply(
                            reply_token,
                            f"已設定固定{arrival_label(destination_label_for_profile(updated_profile))}：{value}\n\n下一步：{field_prompt_for_profile(next_step, updated_profile)}",
                            setup_quick_replies_for_step(next_step),
                        )
                    else:
                        set_pending_field(db, user.id, None)
                        await reply_with_quick_reply(
                            reply_token,
                            f"已設定固定{arrival_label(destination_label_for_profile(updated_profile))}：{value}\n\n{setting_overview_text(db, updated_profile, today_override_time, tomorrow_override_time)}",
                            MAIN_MENU_QUICK_REPLIES,
                        )
                    continue

                set_pending_field(db, user.id, None)
                await reply_with_quick_reply(
                    reply_token,
                    f"已更新固定{arrival_label(destination_label_for_profile(updated_profile))}：{value}\n系統已開始重新計算今日提醒。\n\n{format_profile_text(updated_profile, today_override_time, tomorrow_override_time, db=db)}",
                    MAIN_MENU_QUICK_REPLIES,
                )
                continue

            next_step = get_next_setup_step(profile)
            if next_step is None:
                await reply_with_quick_reply(reply_token, READY_MENU_TEXT, MAIN_MENU_QUICK_REPLIES)
            else:
                set_pending_field(db, user.id, next_step)
                await reply_with_quick_reply(
                    reply_token,
                    field_prompt_for_profile(next_step, profile),
                    setup_quick_replies_for_step(next_step),
                )

    finally:
        db.close()

    return {"ok": True}
