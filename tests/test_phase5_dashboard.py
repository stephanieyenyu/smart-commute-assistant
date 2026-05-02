import importlib.util
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_APP_DIR = REPO_ROOT / "backend" / "app"


def load_module(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase5DashboardTests(unittest.TestCase):
    def read_repo_file(self, relative_path: str) -> str:
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def setUp(self):
        sys.modules["app"] = types.ModuleType("app")
        sys.modules["app"].__path__ = [str(BACKEND_APP_DIR)]

    def test_departure_reminder_timing_sends_only_at_correct_time(self):
        timing = load_module("reminder_timing_under_test", "backend/app/reminder_timing.py")
        departure = "08:00"

        self.assertEqual(
            timing.evaluate_departure_reminder(timing.hhmm_to_seconds("07:59"), departure),
            timing.ReminderTimingDecision.WAIT,
        )
        self.assertEqual(
            timing.evaluate_departure_reminder(timing.hhmm_to_seconds("08:00"), departure),
            timing.ReminderTimingDecision.SEND,
        )
        self.assertEqual(
            timing.evaluate_departure_reminder(timing.hhmm_to_seconds("08:02"), departure),
            timing.ReminderTimingDecision.SEND,
        )
        self.assertEqual(
            timing.evaluate_departure_reminder(timing.hhmm_to_seconds("08:03"), departure),
            timing.ReminderTimingDecision.SKIP_STALE,
        )
        self.assertEqual(
            timing.evaluate_departure_reminder(
                timing.hhmm_to_seconds("08:00"),
                departure,
                already_sent=True,
            ),
            timing.ReminderTimingDecision.ALREADY_SENT,
        )

    def test_dashboard_state_thresholds(self):
        dashboard_status = load_module("dashboard_status_under_test", "backend/app/dashboard_status.py")

        self.assertEqual(dashboard_status.dashboard_state_for_departure(21 * 60), "safe")
        self.assertEqual(dashboard_status.dashboard_state_for_departure(15 * 60), "warning")
        self.assertEqual(dashboard_status.dashboard_state_for_departure(3 * 60), "urgent")
        self.assertEqual(dashboard_status.dashboard_state_for_departure(30 * 60, degraded=True), "degraded")

    def test_dashboard_uses_target_date_for_countdown(self):
        dashboard_status = load_module("dashboard_status_under_test", "backend/app/dashboard_status.py")

        seconds = dashboard_status.seconds_until_departure_datetime(
            now=datetime(2026, 5, 2, 13, 30, 0),
            target_date=date(2026, 5, 3),
            hhmm="08:00",
        )

        self.assertEqual(seconds, (18 * 60 + 30) * 60)

    def test_dashboard_today_plan_expires_after_one_hour(self):
        dashboard_status = load_module("dashboard_status_under_test", "backend/app/dashboard_status.py")
        plan = {
            "ok": True,
            "target_date": date(2026, 5, 2),
            "final_departure_time": "08:00",
        }

        self.assertFalse(
            dashboard_status.dashboard_plan_is_expired(
                datetime(2026, 5, 2, 9, 0, 0),
                plan,
            )
        )
        self.assertTrue(
            dashboard_status.dashboard_plan_is_expired(
                datetime(2026, 5, 2, 9, 1, 0),
                plan,
            )
        )

    def test_dashboard_payload_contains_kiosk_fields(self):
        dashboard_status = load_module("dashboard_status_under_test", "backend/app/dashboard_status.py")
        plan = {
            "ok": True,
            "target_date": date(2026, 5, 2),
            "effective_arrival_time": "09:00",
            "final_departure_time": "08:30",
            "recommended_mode": "metro",
            "transport_line": "🚇 建議搭捷運！請搭乘 淡水信義線。",
            "baseline_minutes": 30,
            "weather_info": {"weather_text": "多雲", "scope": "city"},
        }

        payload = dashboard_status.build_dashboard_payload(
            user_id=1,
            plan=plan,
            now=datetime(2026, 5, 2, 8, 10, 0),
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "safe")
        self.assertEqual(payload["seconds_until_departure"], 20 * 60)
        self.assertEqual(payload["departure_time"], "08:30")
        self.assertEqual(payload["transport_line"], plan["transport_line"])

    def test_dashboard_payload_prefers_line_message_fields(self):
        dashboard_status = load_module("dashboard_status_line_text_under_test", "backend/app/dashboard_status.py")
        line_text = "\n".join([
            "今日通勤建議：",
            "目標抵達：09:10",
            "建議出門：08:40",
            "通勤方式：🚇 LINE 基準通勤方式",
            "通勤時間：約 30 分鐘",
            "今日天氣：多雲",
        ])
        plan = {
            "ok": True,
            "target_date": date(2026, 5, 2),
            "effective_arrival_time": "09:00",
            "final_departure_time": "08:30",
            "recommended_mode": "metro",
            "transport_line": "舊的 dashboard 通勤方式",
            "text": line_text,
            "baseline_minutes": 30,
            "weather_info": {"weather_text": "多雲", "scope": "city"},
        }

        payload = dashboard_status.build_dashboard_payload(
            user_id=1,
            plan=plan,
            now=datetime(2026, 5, 2, 8, 10, 0),
        )

        self.assertEqual(payload["target_arrival_time"], "09:10")
        self.assertEqual(payload["transport_line"], "🚇 LINE 基準通勤方式")
        self.assertEqual(payload["line_commute_text"], line_text)

    def test_dashboard_payload_prefers_precise_departure_datetime(self):
        dashboard_status = load_module("dashboard_status_under_test", "backend/app/dashboard_status.py")
        plan = {
            "ok": True,
            "target_date": date(2026, 5, 2),
            "effective_arrival_time": "09:00",
            "final_departure_time": "08:30",
            "departure_snoozed_until": "2026-05-02T08:31:10",
            "recommended_mode": "metro",
            "transport_line": "測試通勤方式",
            "baseline_minutes": 30,
            "weather_info": {"weather_text": "多雲", "scope": "city"},
        }

        payload = dashboard_status.build_dashboard_payload(
            user_id=1,
            plan=plan,
            now=datetime(2026, 5, 2, 8, 30, 0),
        )

        self.assertEqual(payload["seconds_until_departure"], 70)
        self.assertEqual(payload["departure_at"], "2026-05-02T08:31:10")

    def test_dashboard_routes_are_registered(self):
        dashboard_py = self.read_repo_file("backend/app/dashboard.py")
        main_py = self.read_repo_file("backend/app/main.py")

        self.assertIn('@router.get("/status/{user_id}")', dashboard_py)
        self.assertIn('@router.get("/view/{user_id}", response_class=HTMLResponse)', dashboard_py)
        self.assertIn('@router.websocket("/ws/{user_id}")', dashboard_py)
        self.assertIn('@router.get("/household/{household_id}/status")', dashboard_py)
        self.assertIn('@router.get("/household/{household_id}/view", response_class=HTMLResponse)', dashboard_py)
        self.assertIn('@router.websocket("/household/{household_id}/ws")', dashboard_py)
        self.assertIn('@router.post("/departure-check/{user_id}")', dashboard_py)
        self.assertIn("get_dashboard_status_payload", dashboard_py)
        self.assertIn("get_household_dashboard_status_payload", dashboard_py)
        self.assertIn("dashboard_should_sleep", dashboard_py)
        self.assertIn("dashboard_plan_is_expired", dashboard_py)
        self.assertIn("today + timedelta(days=1)", dashboard_py)
        self.assertIn('payload["reminder_enabled"]', dashboard_py)
        self.assertIn("departure_confirmed_at", dashboard_py)
        self.assertIn("send_departure_check_for_user", dashboard_py)
        self.assertIn("app.include_router(dashboard_router)", main_py)

    def test_dashboard_view_contains_websocket_and_kiosk_layout(self):
        page = load_module("dashboard_page_under_test", "backend/app/dashboard_page.py")
        html = page.render_dashboard_html(7)

        self.assertIn("通勤提醒看板", html)
        self.assertIn("時間還很充裕", html)
        self.assertIn("系統休息中", html)
        self.assertIn("即時更新中", html)
        self.assertIn('const statusPath = "/api/v1/dashboard/status/7"', html)
        self.assertIn('const wsPath = "/api/v1/dashboard/ws/7"', html)
        self.assertIn("const userId = 7", html)
        self.assertIn("state-urgent", html)
        self.assertIn("white-space: pre-line", html)
        self.assertIn("width: min(100%, 1440px)", html)
        self.assertIn("layout-compact", html)
        self.assertIn("layout-short", html)
        self.assertIn("layout-stack", html)
        self.assertIn("fitDashboardToViewport", html)
        self.assertIn("visualViewport", html)
        self.assertIn("membersBand", html)
        self.assertIn("@media (max-width: 1100px)", html)
        self.assertIn("@media (max-height: 700px) and (min-width: 821px)", html)
        self.assertIn("speechSynthesis", html)
        self.assertIn("請於五分鐘後出門", html)
        self.assertIn("已到出門時間，請準時出門", html)
        self.assertIn("!payload.is_snoozed && seconds <= 300 && seconds > 240", html)
        self.assertIn("notifyDepartureVoiceComplete", html)
        self.assertIn("notifyDepartureCheck(payload, \"urgent\")", html)
        self.assertIn("departureCheckStorageKey", html)
        self.assertIn("activeDepartureCheckKeys", html)
        self.assertIn("/api/v1/dashboard/departure-check/${departureUserId}", html)
        self.assertIn("activeSpeechKeys", html)
        self.assertNotIn("payload.plan_key || \"unknown-plan\"", html)
        self.assertIn("const planVersion = payload.plan_key || departureMoment || \"unknown-plan\"", html)
        self.assertIn("utterance.onend = handleSpeechDone", html)
        self.assertIn("utterance.onerror", html)
        self.assertIn("attempt < 2", html)
        self.assertIn("speechSynthesis.resume()", html)
        self.assertIn("payload.reminder_enabled === false", html)
        self.assertIn("payload.sleeping", html)

    def test_dashboard_link_builder_prefers_public_url(self):
        links = load_module("dashboard_links_under_test", "backend/app/dashboard_links.py")

        self.assertEqual(
            links.build_dashboard_view_url(
                7,
                public_url="https://commute.example.com/",
                request_base_url="https://fallback.example.com/",
            ),
            "https://commute.example.com/api/v1/dashboard/view/7",
        )
        self.assertEqual(
            links.build_dashboard_view_url(
                8,
                public_url="",
                request_base_url="https://fallback.example.com/",
            ),
            "https://fallback.example.com/api/v1/dashboard/view/8",
        )
        self.assertEqual(
            links.build_household_dashboard_view_url(
                "default",
                public_url="https://commute.example.com/",
                request_base_url="https://fallback.example.com/",
            ),
            "https://commute.example.com/api/v1/dashboard/household/default/view",
        )

    def test_commute_schedule_supports_active_days_and_sleep(self):
        schedule = load_module("commute_schedule_under_test", "backend/app/commute_schedule.py")

        profile = types.SimpleNamespace(active_weekdays=[0, 1, 2, 3, 4])
        disabled_override = types.SimpleNamespace(commute_disabled=True, commute_enabled=False)
        enabled_override = types.SimpleNamespace(commute_disabled=False, commute_enabled=True)

        self.assertTrue(schedule.commute_date_is_active(profile, date(2026, 5, 1), None))
        self.assertFalse(schedule.commute_date_is_active(profile, date(2026, 5, 2), None))
        self.assertFalse(schedule.commute_date_is_active(profile, date(2026, 5, 1), disabled_override))
        self.assertTrue(schedule.commute_date_is_active(profile, date(2026, 5, 2), enabled_override))
        self.assertEqual(schedule.parse_custom_weekdays("週一週三週五"), [0, 2, 4])
        self.assertEqual(schedule.parse_custom_weekdays("1,3,5"), [0, 2, 4])
        self.assertEqual(schedule.parse_custom_weekdays("週六週日"), [5, 6])
        should_sleep, sleep_until = schedule.dashboard_should_sleep(
            datetime(2026, 5, 1, 20, 0, 0),
            date(2026, 5, 2),
            "08:30",
            None,
        )
        self.assertTrue(should_sleep)
        self.assertEqual(sleep_until.hour, 0)

    def test_line_webhook_has_dashboard_link_command(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn('"dashboard_link"', webhook_py)
        self.assertIn('"household_dashboard_link"', webhook_py)
        self.assertIn("取得Dashboard連結", webhook_py)
        self.assertIn("取得家庭Dashboard連結", webhook_py)
        self.assertIn("build_dashboard_view_url", webhook_py)
        self.assertIn("build_household_dashboard_view_url", webhook_py)
        self.assertIn("外接螢幕看板連結", webhook_py)
        self.assertIn("家庭外接螢幕看板連結", webhook_py)

    def test_reminder_settings_reply_has_toggle_quick_replies(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn("REMINDER_SETTING_QUICK_REPLIES", webhook_py)
        self.assertIn("✅ 開啟自動提醒", webhook_py)
        self.assertIn("⏸ 關閉自動提醒", webhook_py)
        self.assertIn("可用下方按鈕切換", webhook_py)

    def test_line_webhook_has_schedule_controls(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        scheduler_py = self.read_repo_file("backend/app/reminder_scheduler.py")

        self.assertIn("SCHEDULE_QUICK_REPLIES", webhook_py)
        self.assertIn('"view_schedule_setting"', webhook_py)
        self.assertIn('"schedule_workdays"', webhook_py)
        self.assertIn('"pause_today"', webhook_py)
        self.assertIn('"schedule_custom"', webhook_py)
        self.assertIn("SCHEDULE_SETUP_QUICK_REPLIES", webhook_py)
        self.assertIn("CUSTOM_SCHEDULE_QUICK_REPLIES", webhook_py)
        self.assertIn("parse_custom_weekdays", webhook_py)
        self.assertIn("action=pause_date", webhook_py)
        self.assertIn("action=enable_date", webhook_py)
        self.assertIn('set_pending_field(db, user.id, "active_weekdays")', webhook_py)
        self.assertIn("set_active_weekdays", webhook_py)
        self.assertIn("set_commute_disabled_for_date", webhook_py)
        self.assertIn("commute_date_is_active", scheduler_py)
        self.assertIn("MORNING_WATCHDOG_LOOKAHEAD_HOURS = 8", scheduler_py)

    def test_schedule_repeat_picker_uses_native_line_ui(self):
        main_py = self.read_repo_file("backend/app/main.py")
        links_py = self.read_repo_file("backend/app/dashboard_links.py")
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertNotIn("schedule_router", main_py)
        self.assertNotIn("build_schedule_weekly_url", links_py)
        self.assertNotIn("build_schedule_weekly_url", webhook_py)
        self.assertNotIn("/api/v1/schedule", webhook_py)
        self.assertIn("reply_weekday_picker", webhook_py)
        self.assertIn("build_weekday_picker_flex", webhook_py)
        self.assertIn("action=toggle_weekday", webhook_py)
        self.assertIn("action=schedule_preset", webhook_py)
        self.assertIn("重複提醒日", webhook_py)
        self.assertIn("active_weekdays", webhook_py)
        self.assertIn("LINE 原生選擇卡", webhook_py)

    def test_household_management_and_computer_kiosk_guides_are_wired(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        crud_py = self.read_repo_file("backend/app/crud.py")
        dashboard_py = self.read_repo_file("backend/app/dashboard.py")
        dashboard_page_py = self.read_repo_file("backend/app/dashboard_page.py")
        readme = self.read_repo_file("README.md")

        self.assertIn("HOUSEHOLD_QUICK_REPLIES", webhook_py)
        self.assertIn('"household_management"', webhook_py)
        self.assertIn('"create_household"', webhook_py)
        self.assertIn('"household_invite_code"', webhook_py)
        self.assertIn('"join_household"', webhook_py)
        self.assertIn('"set_display_name"', webhook_py)
        self.assertIn("format_household_management_text", webhook_py)
        self.assertIn("ensure_personal_household", crud_py)
        self.assertIn("set_user_household_id", crud_py)
        self.assertIn("set_user_display_name", crud_py)
        self.assertIn("departure_confirmed_today", dashboard_py)
        self.assertIn("members = sorted(members, key=member_sort_key)", dashboard_py)
        self.assertIn("已出門，改看明天", dashboard_page_py)

        self.assertIn('"computer_dashboard_guide"', webhook_py)
        self.assertIn("format_computer_dashboard_guide", webhook_py)
        self.assertIn("實體電腦 Dashboard 操作模式", webhook_py)
        self.assertIn("Physical computer dashboard mode", readme)
        self.assertIn("--kiosk", readme)
        self.assertIn("shell:startup", readme)
        self.assertIn("Login Items", readme)

    def test_rich_menu_topics_and_prompt_coverage_are_wired(self):
        line_client_py = self.read_repo_file("backend/app/line_client.py")
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn("PERSISTENT_QUICK_REPLIES", line_client_py)
        self.assertIn("PERSISTENT_QUICK_REPLIES = []", line_client_py)
        self.assertIn("_quick_reply_model", line_client_py)
        self.assertNotIn('"label": "今日通勤建議"', line_client_py)
        self.assertNotIn('"label": "修改到公司時間"', line_client_py)
        self.assertNotIn('"label": "修改出門時間"', line_client_py)
        self.assertIn("with_persistent_quick_replies(items)", line_client_py)
        self.assertIn("_quick_reply_model([])", line_client_py)
        self.assertIn("RICH_MENU_TOPICS", webhook_py)
        self.assertIn("BASIC_SETTINGS_QUICK_REPLIES", webhook_py)
        self.assertIn("DONE_QUICK_REPLY", webhook_py)
        self.assertIn("with_done_button", webhook_py)
        self.assertIn('"完成修改設定"', webhook_py)
        self.assertIn('"取得DASHBOARD連結"', webhook_py)
        self.assertIn('"通勤選單"', webhook_py)
        self.assertIn('"時間設定"', webhook_py)
        self.assertIn('"自動提醒"', webhook_py)
        self.assertIn('"排程設定"', webhook_py)
        self.assertIn('"看板家庭"', webhook_py)
        self.assertIn('"指令說明"', webhook_py)
        self.assertIn("COMMUTE_TOPIC_QUICK_REPLIES", webhook_py)
        self.assertIn("TIME_TOPIC_QUICK_REPLIES", webhook_py)
        self.assertIn("DASHBOARD_TOPIC_QUICK_REPLIES", webhook_py)
        self.assertIn("CANONICAL_PROMPT_GROUPS", webhook_py)
        self.assertIn("unsupported_canonical_prompts", webhook_py)
        self.assertIn("build_command_help_carousel", webhook_py)

        self.assertIn("unsupported.append(prompt)", webhook_py)
        self.assertIn("加入家庭 ", webhook_py)
        self.assertIn("設定我的名稱 ", webhook_py)

    def test_departure_confirmation_flow_is_wired(self):
        line_client_py = self.read_repo_file("backend/app/line_client.py")
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        scheduler_py = self.read_repo_file("backend/app/reminder_scheduler.py")
        crud_py = self.read_repo_file("backend/app/crud.py")

        self.assertIn("build_departure_check_flex_message", line_client_py)
        self.assertIn("您出門了嗎？", line_client_py)
        self.assertIn("已經出門了", line_client_py)
        self.assertIn("我還需要五分鐘", line_client_py)
        self.assertIn("action=departure_check&choice=left", line_client_py)
        self.assertIn("action=departure_check&choice=need_5", line_client_py)

        self.assertIn('postback_action == "departure_check"', webhook_py)
        self.assertIn("confirm_departure_for_user", webhook_py)
        self.assertIn("snooze_departure_for_user", webhook_py)
        self.assertIn("format_taipei_hhmm", webhook_py)

        self.assertIn("check_and_send_snoozed_departure_reminders", scheduler_py)
        self.assertIn("距離出門剩下一分鐘", scheduler_py)
        self.assertIn("已到出門時間", scheduler_py)

        self.assertIn("mark_departure_confirmed", crud_py)
        self.assertIn("snooze_departure_confirmation", crud_py)
        departure_confirmation_py = self.read_repo_file("backend/app/departure_confirmation.py")
        self.assertIn("format_taipei_hhmm", departure_confirmation_py)

    def test_short_test_reminder_command_is_removed_from_production(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        departure_confirmation_py = self.read_repo_file("backend/app/departure_confirmation.py")

        self.assertNotIn("schedule_" + "test_departure_for_user", webhook_py)
        self.assertNotIn("已啟動" + "測試" + "提醒流程", webhook_py)
        self.assertNotIn("TEST_" + "REMINDER_SECONDS", departure_confirmation_py)
        self.assertNotIn("timedelta(seconds=", departure_confirmation_py)


if __name__ == "__main__":
    unittest.main()
