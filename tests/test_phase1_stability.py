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
        self.assertIn("def build_topic_help_card", webhook_py)
        self.assertIn("topic_title = topic_title_for_command(command_text)", webhook_py)
        self.assertIn("await reply_flex_message(reply_token, topic_title, topic_card)", webhook_py)
        self.assertIn('uri_btn("➕ 新增排程設定", create_url)', webhook_py)
        self.assertIn('btn("📅 一週排程設定", "一週排程設定")', webhook_py)
        self.assertIn('btn("✏️ 編輯排程", "編輯排程")', webhook_py)
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
        self.assertIn('payload.mode == "create"', main_py)
        self.assertIn("已保留既有排程，未覆蓋舊資料", main_py)
        self.assertIn("0=週一，1=週二，...，6=週日", models_py)
        self.assertIn("0=週一, 1=週二, ..., 6=週日", crud_py)
        self.assertIn("day_of_week = now_dt.weekday()", scheduler_py)

    def test_dashboard_routes_and_shared_schedule_summary_are_wired(self):
        main_py = self.read_repo_file("backend/app/main.py")
        dashboard_view_py = self.read_repo_file("backend/app/dashboard_view.py")
        schedule_summary_py = self.read_repo_file("backend/app/schedule_summary.py")

        self.assertIn('@app.get("/dashboard", response_class=HTMLResponse)', main_py)
        self.assertIn('@app.get("/api/dashboard/status")', main_py)
        self.assertIn("build_schedule_status_payload(schedule, profile, mode_label)", main_py)
        self.assertIn("setInterval(refresh, refreshMs)", dashboard_view_py)
        self.assertIn("AbortController", dashboard_view_py)
        self.assertIn("format_weekly_schedule_text", schedule_summary_py)
        self.assertIn("format_commute_setting_text", schedule_summary_py)


if __name__ == "__main__":
    unittest.main()
