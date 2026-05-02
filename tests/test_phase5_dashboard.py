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
        self.assertIn('@router.websocket("/ws/{user_id}")', dashboard_py)
        self.assertIn("get_dashboard_status_payload", dashboard_py)
        self.assertIn("app.include_router(dashboard_router)", main_py)


if __name__ == "__main__":
    unittest.main()
