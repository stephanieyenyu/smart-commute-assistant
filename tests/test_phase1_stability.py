import asyncio
import importlib.util
import io
import sys
import time
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase1StabilityTests(unittest.TestCase):
    def read_repo_file(self, relative_path: str) -> str:
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def test_main_only_starts_primary_reminder_scheduler(self):
        main_py = self.read_repo_file("backend/app/main.py")

        self.assertIn("start_reminder_scheduler()", main_py)
        self.assertNotIn("async_check_all_commutes", main_py)
        self.assertNotIn("AsyncIOScheduler", main_py)

    def test_external_api_clients_emit_health_logs(self):
        client_paths = [
            "backend/app/integrations/Maps_client.py",
            "backend/app/tdx_bus.py",
            "backend/app/metro_basic.py",
            "backend/app/weather.py",
        ]

        for relative_path in client_paths:
            with self.subTest(relative_path=relative_path):
                content = self.read_repo_file(relative_path)
                self.assertIn("log_api_health", content)
                self.assertIn("api_timer_start", content)

    def test_api_health_log_format_is_parseable(self):
        module_path = REPO_ROOT / "backend/app/integrations/api_health.py"
        spec = importlib.util.spec_from_file_location("api_health_under_test", module_path)
        api_health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api_health)

        started_at = time.perf_counter() - 0.012
        output = io.StringIO()
        with redirect_stdout(output):
            api_health.log_api_health(
                "unit.test.endpoint",
                started_at,
                status_code=200,
            )

        log_line = output.getvalue()
        self.assertIn("[api-health]", log_line)
        self.assertIn("endpoint=unit.test.endpoint", log_line)
        self.assertIn("latency_ms=", log_line)
        self.assertIn("status_code=200", log_line)

    def test_weather_api_failure_uses_stale_cache(self):
        class FailingAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *args, **kwargs):
                raise RuntimeError("weather timeout")

        fake_httpx = types.ModuleType("httpx")
        fake_httpx.AsyncClient = FailingAsyncClient
        fake_httpx.Timeout = lambda *args, **kwargs: None

        sys.modules["httpx"] = fake_httpx
        sys.modules["app"] = types.ModuleType("app")
        sys.modules["app.address_utils"] = types.ModuleType("app.address_utils")
        sys.modules["app.address_utils"].normalize_city_name = lambda city: city
        sys.modules["app.address_utils"].extract_city_from_text = lambda text: None
        sys.modules["app.integrations"] = types.ModuleType("app.integrations")
        sys.modules["app.integrations.api_health"] = types.ModuleType("app.integrations.api_health")
        sys.modules["app.integrations.api_health"].api_timer_start = lambda: time.perf_counter()
        sys.modules["app.integrations.api_health"].log_api_health = lambda *args, **kwargs: None

        module_path = REPO_ROOT / "backend/app/weather.py"
        spec = importlib.util.spec_from_file_location("weather_under_test", module_path)
        weather = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(weather)

        weather._get_weather_api_key = lambda: "fake-key"
        weather._weather_cache["Taipei"] = (
            0,
            {
                "weather_text": "多雲",
                "pop": 20,
                "extra_buffer_minutes": 0,
                "scope": "city",
                "city": "Taipei",
                "township": None,
            },
        )

        result = asyncio.run(weather.get_today_weather_by_city("Taipei"))

        self.assertEqual(result["weather_text"], "多雲")
        self.assertEqual(result["scope"], "city_stale_cache")

    def test_tdx_bus_eta_failure_uses_stale_cache(self):
        sys.modules["httpx"] = types.ModuleType("httpx")
        sys.modules["app"] = types.ModuleType("app")
        sys.modules["app.config"] = types.ModuleType("app.config")
        sys.modules["app.config"].TDX_CLIENT_ID = "fake-id"
        sys.modules["app.config"].TDX_CLIENT_SECRET = "fake-secret"
        sys.modules["app.integrations"] = types.ModuleType("app.integrations")
        sys.modules["app.integrations.api_health"] = types.ModuleType("app.integrations.api_health")
        sys.modules["app.integrations.api_health"].api_timer_start = lambda: time.perf_counter()
        sys.modules["app.integrations.api_health"].log_api_health = lambda *args, **kwargs: None

        module_path = REPO_ROOT / "backend/app/tdx_bus.py"
        spec = importlib.util.spec_from_file_location("tdx_bus_under_test", module_path)
        tdx_bus = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tdx_bus)

        async def failing_tdx_get(*args, **kwargs):
            raise RuntimeError("tdx timeout")

        tdx_bus.tdx_get = failing_tdx_get
        cache_key = "Taipei|STOP_UID|None"
        stale_eta = [{"RouteName": {"Zh_tw": "307"}, "EstimateTime": 480}]
        tdx_bus._ETA_CACHE[cache_key] = (0, stale_eta)

        result = asyncio.run(tdx_bus.get_estimated_arrivals("Taipei", stop_uid="STOP_UID"))

        self.assertEqual(result, stale_eta)

    def test_line_topic_texts_reply_with_single_flex_cards(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        line_client_py = self.read_repo_file("backend/app/line_client.py")

        self.assertIn("TOPIC_CARD_TITLES = (\"設定通勤路線\", \"通勤建議\", \"交通方式\", \"看板\", \"系統設定\")", webhook_py)
        self.assertIn('"設定通勤路線": "設定通勤路線"', webhook_py)
        self.assertIn('"看板": "看板"', webhook_py)
        self.assertIn('"add_schedule"', webhook_py)
        self.assertIn('"weekly_schedule"', webhook_py)
        self.assertIn('"edit_schedule"', webhook_py)
        self.assertIn('"delete_schedule"', webhook_py)
        self.assertIn("parse_delete_schedule_id", webhook_py)
        self.assertIn("delete_commute_schedule(db, line_user_id, target_schedule.id)", webhook_py)
        self.assertIn("def build_topic_help_card", webhook_py)
        self.assertIn("topic_title = topic_title_for_command(command_text)", webhook_py)
        self.assertIn("await reply_flex_message(reply_token, topic_title, topic_card)", webhook_py)
        self.assertIn('uri_btn("➕ 新增排程設定", create_url)', webhook_py)
        self.assertIn('btn("📅 一週排程設定", "一週排程設定")', webhook_py)
        self.assertIn('btn("✏️ 編輯排程", "編輯排程")', webhook_py)
        self.assertIn('btn("🗑️ 刪除排程", "刪除排程")', webhook_py)
        self.assertIn('btn("📺 個人看板連結", "個人看板連結", "primary")', webhook_py)
        self.assertIn("build_dashboard_url(request, line_user_id, \"personal\")", webhook_py)

        self.assertIn("flex_contents: dict | list[dict]", line_client_py)
        self.assertIn("container_payload = flex_contents", line_client_py)
        self.assertIn("container_payload = {", line_client_py)
        self.assertIn('"type": "carousel"', line_client_py)

    def test_liff_weekday_labels_use_monday_first_indices(self):
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        main_py = self.read_repo_file("backend/app/main.py")
        models_py = self.read_repo_file("backend/app/models.py")
        crud_py = self.read_repo_file("backend/app/crud.py")
        scheduler_py = self.read_repo_file("backend/app/reminder_scheduler.py")
        schedule_summary_py = self.read_repo_file("backend/app/schedule_summary.py")

        self.assertIn("format_commute_setting_text(schedule, profile, mode_label)", webhook_py)
        self.assertIn('WEEKDAY_LABELS = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")', schedule_summary_py)
        self.assertIn("0=週一, 1=週二, ..., 6=週日", main_py)
        self.assertIn('"mode":            payload.mode', main_py)
        self.assertIn("get_commute_schedules(db, userId)", main_py)
        self.assertIn('@app.delete("/api/schedule")', main_py)
        self.assertIn('@app.post("/api/schedule/delete")', main_py)
        self.assertIn("delete_commute_schedule", main_py)
        self.assertIn("DROP CONSTRAINT IF EXISTS ix_commute_schedules_user_id", main_py)
        self.assertIn("DROP INDEX IF EXISTS ix_commute_schedules_user_id", main_py)
        self.assertIn("CREATE INDEX IF NOT EXISTS ix_commute_schedules_user_id", main_py)
        self.assertNotIn('UniqueConstraint("user_id", "dest_name"', models_py)
        self.assertNotIn("_find_schedule_by_destination", crud_py)
        self.assertIn("schedule = CommuteSchedule(user_id=user.id, dest_name=dest_name, is_active=True)", crud_py)
        self.assertIn("schedule.is_active = False", crud_py)
        self.assertIn("schedule.reminder_enabled = False", crud_py)
        self.assertIn('mode == "edit"', crud_py)
        self.assertIn("0=週一，1=週二，...，6=週日", models_py)
        self.assertIn("0=週一, 1=週二, ..., 6=週日", crud_py)
        self.assertIn("day_of_week = now_dt.weekday()", scheduler_py)

    def test_dashboard_routes_and_shared_schedule_summary_are_wired(self):
        main_py = self.read_repo_file("backend/app/main.py")
        dashboard_view_py = self.read_repo_file("backend/app/dashboard_view.py")
        schedule_summary_py = self.read_repo_file("backend/app/schedule_summary.py")

        self.assertIn('@app.get("/dashboard", response_class=HTMLResponse)', main_py)
        self.assertIn('@app.get("/dashboard/family", response_class=HTMLResponse)', main_py)
        self.assertIn('@app.get("/api/dashboard/status")', main_py)
        self.assertIn("build_schedule_status_payload(schedules, profile, mode_label, now_dt=now_dt)", main_py)
        self.assertIn("setInterval(refresh, refreshMs)", dashboard_view_py)
        self.assertIn("setInterval(render, 1000)", dashboard_view_py)
        self.assertIn("AbortController", dashboard_view_py)
        self.assertIn("renderMembers(payload.members)", dashboard_view_py)
        self.assertIn("renderCommute(payload.commute)", dashboard_view_py)
        self.assertIn("displaySchedules", dashboard_view_py)
        self.assertIn("dashboard_target_schedule", schedule_summary_py)
        self.assertIn("dashboard_display_schedule_rows", schedule_summary_py)
        self.assertNotIn("renderWeek", dashboard_view_py)
        self.assertNotIn("weeklySchedule", dashboard_view_py)
        self.assertNotIn("reminderEnabled", dashboard_view_py)
        self.assertIn("format_weekly_schedule_text", schedule_summary_py)
        self.assertIn("format_commute_setting_text", schedule_summary_py)

    def test_family_dashboard_invites_and_status_colors_are_wired(self):
        models_py = self.read_repo_file("backend/app/models.py")
        crud_py = self.read_repo_file("backend/app/crud.py")
        webhook_py = self.read_repo_file("backend/app/webhook.py")
        schedule_summary_py = self.read_repo_file("backend/app/schedule_summary.py")

        self.assertIn("class Household", models_py)
        self.assertIn("invite_code = Column(String, unique=True", models_py)
        self.assertIn("ensure_household_for_user", crud_py)
        self.assertIn("join_household_by_code", crud_py)
        self.assertIn("parse_household_invite_code", webhook_py)
        self.assertIn("加入家庭 {household.invite_code}", webhook_py)
        self.assertIn('"家庭看板連結", "取得家庭看板連結", "家庭看板", "開啟家庭看板"', webhook_py)
        self.assertIn('"green": "#22c55e"', schedule_summary_py)
        self.assertIn('"blue": "#3b82f6"', schedule_summary_py)
        self.assertIn('"orange": "#f97316"', schedule_summary_py)
        self.assertIn('"red": "#ef4444"', schedule_summary_py)

    def test_commute_weather_audio_and_reminders_are_wired(self):
        service_py = self.read_repo_file("backend/app/service.py")
        weather_py = self.read_repo_file("backend/app/weather.py")
        reminder_py = self.read_repo_file("backend/app/reminder_scheduler.py")
        line_client_py = self.read_repo_file("backend/app/line_client.py")
        webhook_py = self.read_repo_file("backend/app/webhook.py")

        self.assertIn("build_transport_detail_lines(plan)", service_py)
        self.assertIn('"apparent_temperature"', weather_py)
        self.assertIn("return 10", weather_py)
        self.assertIn("MORNING_MONITOR_OFFSETS", reminder_py)
        self.assertIn('"one_hour": 60 * 60', reminder_py)
        self.assertIn('"five_min": 5 * 60', reminder_py)
        self.assertIn("departure_question_sent_at", reminder_py)
        self.assertIn("DEPARTURE_CONFIRM_QR", reminder_py)
        self.assertIn("mark_departure_question_sent", reminder_py)
        self.assertIn("mark_monitor_sent", reminder_py)
        self.assertIn("mark_user_departed_for_today", reminder_py)
        self.assertNotIn("last_sent_plan_key == override.frozen_plan_key", reminder_py)
        self.assertIn("AudioMessage", line_client_py)
        self.assertIn("build_tts_audio_url", line_client_py)
        self.assertIn("push_audio_message", reminder_py)
        self.assertIn('"departed"', webhook_py)
        self.assertIn("mark_user_departed_for_today(user.id)", webhook_py)
        self.assertIn("build_commute_advice_flex", webhook_py)
        self.assertIn("今日交通看板", webhook_py)


if __name__ == "__main__":
    unittest.main()
