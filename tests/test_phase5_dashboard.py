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

    def test_dashboard_routes_are_registered(self):
        dashboard_py = self.read_repo_file("backend/app/dashboard.py")
        main_py = self.read_repo_file("backend/app/main.py")

        self.assertIn('@router.get("/status/{user_id}")', dashboard_py)
        self.assertIn('@router.get("/view/{user_id}", response_class=HTMLResponse)', dashboard_py)
        self.assertIn('@router.websocket("/ws/{user_id}")', dashboard_py)
        self.assertIn("get_dashboard_status_payload", dashboard_py)
        self.assertIn("dashboard_plan_is_expired", dashboard_py)
        self.assertIn("today + timedelta(days=1)", dashboard_py)
        self.assertIn('payload["reminder_enabled"]', dashboard_py)
        self.assertIn("app.include_router(dashboard_router)", main_py)

    def test_dashboard_view_contains_websocket_and_kiosk_layout(self):
        page = load_module("dashboard_page_under_test", "backend/app/dashboard_page.py")
        html = page.render_dashboard_html(7)

        self.assertIn("通勤提醒看板", html)
        self.assertIn("時間還很充裕", html)
        self.assertIn("即時更新中", html)
        self.assertIn("/api/v1/dashboard/status/${userId}", html)
        self.assertIn("/api/v1/dashboard/ws/${userId}", html)
        self.assertIn("const userId = 7", html)
        self.assertIn("state-urgent", html)
        self.assertIn("white-space: pre-line", html)
        self.assertIn("speechSynthesis", html)
        self.assertIn("請於五分鐘後出門", html)
        self.assertIn("已到出門時間，請準時出門", html)
        self.assertIn("payload.reminder_enabled === false", html)

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

    def test_line_webhook_has_dashboard_link_command(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn('"dashboard_link"', webhook_py)
        self.assertIn("取得Dashboard連結", webhook_py)
        self.assertIn("build_dashboard_view_url", webhook_py)
        self.assertIn("外接螢幕看板連結", webhook_py)

    def test_reminder_settings_reply_has_toggle_quick_replies(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn("REMINDER_SETTING_QUICK_REPLIES", webhook_py)
        self.assertIn("✅ 開啟自動提醒", webhook_py)
        self.assertIn("⏸ 關閉自動提醒", webhook_py)
        self.assertIn("可用下方按鈕切換", webhook_py)


    def test_line_replies_have_persistent_commute_quick_replies(self):
        line_client_py = self.read_repo_file("backend/app/line_client.py")
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn("PERSISTENT_QUICK_REPLIES", line_client_py)
        self.assertIn('"label": "今日通勤建議"', line_client_py)
        self.assertIn('"label": "修改出門時間"', line_client_py)
        self.assertIn("with_persistent_quick_replies(items)", line_client_py)
        self.assertIn("with_persistent_quick_replies([])", line_client_py)
        self.assertIn("修改出門時間", webhook_py)


if __name__ == "__main__":
    unittest.main()
