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
        self.assertIn("_apply_timeout_voice", dashboard_py)
        self.assertIn("DEPARTURE_TIMEOUT_VOICE_PROMPT", dashboard_py)
        self.assertIn("timeout_voice_silent", dashboard_py)
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
        self.assertIn("result === \"unavailable\"", html)
        self.assertIn("urgent-no-voice", html)
        self.assertIn("return \"started\"", html)
        self.assertIn("return \"duplicate\"", html)
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
        self.assertIn("timeoutVoiceStorageKey", html)
        self.assertIn("dashboardTimeoutVoice", html)
        self.assertIn("timeout_voice_prompt", html)

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

    def test_short_test_reminder_command_is_removed_from_production(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        departure_confirmation_py = self.read_repo_file("backend/app/departure_confirmation.py")

        self.assertNotIn("schedule_" + "test_departure_for_user", webhook_py)
        self.assertNotIn("已啟動" + "測試" + "提醒流程", webhook_py)
        self.assertNotIn("TEST_" + "REMINDER_SECONDS", departure_confirmation_py)
        self.assertNotIn("timedelta(seconds=", departure_confirmation_py)


if __name__ == "__main__":
    unittest.main()
