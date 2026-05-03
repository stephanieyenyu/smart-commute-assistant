import asyncio
import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import date, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
SERVICE_PATH = BACKEND_DIR / "app" / "service.py"


def load_service_module():
    """Load service.py with external integrations stubbed out."""
    for module_name in list(sys.modules):
        if module_name == "service_under_test" or module_name.startswith("app."):
            sys.modules.pop(module_name, None)

    sys.modules["app"] = types.ModuleType("app")
    sys.modules["app"].__path__ = [str(BACKEND_DIR / "app")]

    stubs = {
        "app.address_utils": {"extract_city_from_text": lambda text: None},
        "app.google_maps": {
            "estimate_transit_minutes": None,
            "estimate_transit_minutes_detailed": None,
        },
        "app.metro_basic": {
            "get_nearest_metro_station_async": None,
            "get_station_exits_async": None,
        },
        "app.tdx_bus": {
            "get_nearby_stops": None,
            "get_estimated_arrivals": None,
            "simplify_eta_list": None,
        },
        "app.weather": {"get_commute_weather": None},
        "app.crud": {
            "get_profile": None,
            "get_next_setup_step": None,
            "get_override_for_date": None,
            "get_transport_mode_override": None,
            "save_frozen_reminder": None,
            "record_commute_plan_log": None,
        },
    }

    for name, attrs in stubs.items():
        module = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location("service_under_test", SERVICE_PATH)
    service = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(service)
    return service


class Phase0RegressionTests(unittest.TestCase):
    def setUp(self):
        self.service = load_service_module()

    def metro_plan(self):
        return {
            "recommended_mode": "metro",
            "best_option": {
                "mode": "metro",
                "snapshot": {
                    "station": {"id": "R18", "name": "明德"},
                    "destination_station": {"id": "R17", "name": "芝山"},
                    "suggested_exit": {"exit_id": "1", "name": "出口 1"},
                    "walk_minutes": 12,
                    "google_detailed": {
                        "steps": [
                            {
                                "type": "TRANSIT",
                                "vehicle_type": "SUBWAY",
                                "line_name": "淡水信義線",
                                "departure_stop": "明德",
                                "arrival_stop": "芝山",
                            },
                            {
                                "type": "WALK",
                                "instructions": "從出口 3 離開車站並步行前往目的地",
                            },
                        ]
                    },
                },
            },
            "target_date": date(2026, 5, 2),
            "effective_arrival_time": "22:34",
            "weather_info": {"weather_text": "多雲時陰", "temperature_min": 20, "temperature_max": 21, "pop": 20},
            "weather_buffer": 0,
            "baseline_minutes": 18,
            "final_departure_time": "22:16",
            "mode_override": "metro",
        }

    def bus_plan(self):
        return {
            "recommended_mode": "bus",
            "best_option": {
                "mode": "bus",
                "snapshot": {
                    "first_stop": {"stop_name": "南京敦化路口"},
                    "arrival_at_stop_min": 4,
                    "walk_minutes": 3,
                    "chosen_bus": {"route_name": "307", "eta_min": 8},
                    "valid_eta_list": [
                        {"route_name": "307", "eta_min": 8},
                        {"route_name": "652", "eta_min": 12},
                        {"route_name": "12", "eta_min": 2},
                    ],
                    "google_detailed": {
                        "steps": [
                            {
                                "type": "TRANSIT",
                                "vehicle_type": "BUS",
                                "line_short_name": "307",
                                "departure_stop": "南京敦化路口",
                                "arrival_stop": "捷運西門站",
                            }
                        ]
                    },
                },
            },
        }

    def test_metro_commute_advice_and_reminder_share_enriched_transport_line(self):
        plan = self.metro_plan()

        commute_text = self.service._format_today_commute_text(plan, header="好的，今天切換為：捷運優先。")
        reminder_text = self.service._build_reminder_payload_from_plan(plan)["text"]

        commute_transport = next(line for line in commute_text.splitlines() if line.startswith("通勤方式："))
        reminder_transport = next(line for line in reminder_text.splitlines() if line.startswith("📍 通勤方式："))
        commute_detail = commute_transport.removeprefix("通勤方式：")
        reminder_detail = reminder_transport.removeprefix("📍 通勤方式：")

        self.assertEqual(commute_detail, reminder_detail)
        self.assertIn("搭乘 淡水信義線", commute_detail)
        self.assertIn("於『明德』上車", commute_detail)
        self.assertIn("在『芝山』下車", commute_detail)
        self.assertIn("走『出口 3』出站", commute_detail)
        self.assertNotIn("走『出口 1』出站", commute_detail)
        self.assertNotIn("目的地附近捷運站", commute_detail)

    def test_forced_metro_uses_google_vehicle_type_without_local_metro_fallback(self):
        plan = self.metro_plan()
        plan["best_option"]["snapshot"]["google_detailed"] = {
            "steps": [
                {
                    "type": "TRANSIT",
                    "vehicle_type": "BUS",
                    "line_short_name": "307",
                    "departure_stop": "A",
                    "arrival_stop": "B",
                }
            ]
        }

        transport_line = self.service._format_transport_line(plan)

        self.assertIn("🚌 建議搭公車", transport_line)
        self.assertIn("307號公車", transport_line)
        self.assertIn("於『A』上車", transport_line)
        self.assertIn("於『B』下車", transport_line)
        self.assertNotIn("淡水信義線", transport_line)
        self.assertNotIn("芝山", transport_line)

    def test_bus_transport_line_contains_primary_and_viable_route_numbers(self):
        transport_line = self.service._format_transport_line(self.bus_plan())

        self.assertIn("🚌 建議搭公車", transport_line)
        self.assertIn("搭乘 307號公車", transport_line)
        self.assertIn("於『南京敦化路口』上車", transport_line)
        self.assertIn("於『捷運西門站』下車", transport_line)
        self.assertIn("307號公車將於 8 分鐘後抵達『南京敦化路口』", transport_line)
        self.assertNotIn("可選路線", transport_line)
        self.assertNotIn("12（約 2 分鐘後到站）", transport_line)

    def test_bus_primary_route_comes_from_google_step(self):
        plan = self.bus_plan()
        snapshot = plan["best_option"]["snapshot"]
        snapshot["chosen_bus"] = {"route_name": "652", "eta_min": 12}
        snapshot["valid_eta_list"] = [{"route_name": "652", "eta_min": 12}]

        transport_line = self.service._format_transport_line(plan)

        self.assertIn("搭乘 307號公車", transport_line)
        self.assertNotIn("搭乘 652號公車", transport_line)
        self.assertNotIn("307號公車將於 12 分鐘後抵達", transport_line)

    def test_metro_without_google_exit_uses_station_signage(self):
        plan = self.metro_plan()
        plan["best_option"]["snapshot"]["google_detailed"]["steps"] = [
            {
                "type": "TRANSIT",
                "vehicle_type": "SUBWAY",
                "line_name": "淡水信義線",
                "departure_stop": "明德",
                "arrival_stop": "芝山",
            },
            {"type": "WALK", "instructions": "步行前往目的地"},
        ]

        transport_line = self.service._format_transport_line(plan)

        self.assertIn("依站內指標出站", transport_line)
        self.assertNotIn("出口 1", transport_line)

    def test_bus_transfer_template_lists_every_google_step(self):
        plan = self.bus_plan()
        plan["best_option"]["snapshot"]["google_detailed"]["steps"] = [
            {
                "type": "TRANSIT",
                "vehicle_type": "BUS",
                "line_short_name": "307",
                "departure_stop": "南京敦化路口",
                "arrival_stop": "捷運西門站",
            },
            {"type": "WALK", "instructions": "步行至西門國小"},
            {
                "type": "TRANSIT",
                "vehicle_type": "BUS",
                "line_short_name": "235",
                "departure_stop": "西門國小",
                "arrival_stop": "植物園",
            },
        ]

        transport_line = self.service._format_transport_line(plan)

        self.assertIn("搭乘 307號公車", transport_line)
        self.assertIn("(轉乘) 步行至『西門國小』改搭乘 235號公車", transport_line)
        self.assertIn("並於『植物園』下車", transport_line)

    def test_heavy_rail_is_not_labeled_as_metro(self):
        plan = self.metro_plan()
        plan["best_option"]["snapshot"]["google_detailed"]["steps"] = [
            {
                "type": "TRANSIT",
                "vehicle_type": "HEAVY_RAIL",
                "line_name": "區間車",
                "departure_stop": "新竹",
                "arrival_stop": "竹北",
            }
        ]

        transport_line = self.service._format_transport_line(plan)

        self.assertIn("🚆 建議搭鐵路", transport_line)
        self.assertNotIn("建議搭捷運", transport_line)

    def test_metro_to_bus_transfer_keeps_google_exit_instruction(self):
        plan = self.metro_plan()
        plan["best_option"]["snapshot"]["google_detailed"]["steps"] = [
            {
                "type": "TRANSIT",
                "vehicle_type": "SUBWAY",
                "line_name": "板南線",
                "departure_stop": "忠孝敦化",
                "arrival_stop": "台北車站",
            },
            {"type": "WALK", "instructions": "從出口 M4 離開車站"},
            {
                "type": "TRANSIT",
                "vehicle_type": "BUS",
                "line_short_name": "307",
                "departure_stop": "台北車站",
                "arrival_stop": "捷運西門站",
            },
        ]

        transport_line = self.service._format_transport_line(plan)

        self.assertIn("走『出口 M4』出站", transport_line)
        self.assertIn("(轉乘) 步行至『台北車站』改搭乘 307號公車", transport_line)

    def test_bus_departure_uses_realtime_eta_walk_and_three_minute_buffer(self):
        self.service._now_taipei_naive = lambda: datetime(2026, 5, 2, 8, 0)
        best_option = {
            "mode": "bus",
            "snapshot": {
                "walk_minutes": 4,
                "chosen_bus": {"route_name": "307", "eta_min": 12},
            },
        }

        result = asyncio.run(
            self.service.calculate_departure_time_by_mode_fast(
                target_date=date(2026, 5, 2),
                effective_arrival_time="09:00",
                baseline_minutes=35,
                weather_buffer_minutes=0,
                best_option=best_option,
            )
        )

        self.assertEqual(result["departure_time"], "08:05")

    def test_bus_departure_never_later_than_latest_on_time_departure(self):
        self.service._now_taipei_naive = lambda: datetime(2026, 5, 2, 8, 0)
        best_option = {
            "mode": "bus",
            "snapshot": {
                "walk_minutes": 4,
                "chosen_bus": {"route_name": "307", "eta_min": 50},
            },
        }

        result = asyncio.run(
            self.service.calculate_departure_time_by_mode_fast(
                target_date=date(2026, 5, 2),
                effective_arrival_time="09:00",
                baseline_minutes=35,
                weather_buffer_minutes=0,
                best_option=best_option,
            )
        )

        self.assertEqual(result["departure_time"], "08:25")

    def test_shortest_bus_result_uses_bus_mode_fastest_google_route(self):
        calls = []

        async def fake_estimate_transit_minutes_detailed(*args, allowed_travel_modes=None, **kwargs):
            calls.append(allowed_travel_modes)
            return {
                "duration_minutes": 24,
                "steps": [
                    {
                        "type": "TRANSIT",
                        "vehicle_type": "BUS",
                        "line_short_name": "307",
                        "line_name": "307",
                        "departure_stop": "南京敦化路口",
                        "arrival_stop": "捷運西門站",
                    }
                ],
            }

        async def fake_bus_snapshot(profile):
            return {
                "available": True,
                "first_stop": {"stop_name": "南京敦化路口"},
                "walk_minutes": 4,
                "arrival_at_stop_min": 5,
                "chosen_bus": {"route_name": "652", "eta_min": 12},
                "valid_eta_list": [{"route_name": "652", "eta_min": 12}],
            }

        self.service.estimate_transit_minutes_detailed = fake_estimate_transit_minutes_detailed
        self.service.get_bus_realtime_snapshot = fake_bus_snapshot

        profile = types.SimpleNamespace(
            home_lat=25.0,
            home_lng=121.0,
            office_lat=25.1,
            office_lng=121.1,
        )
        result = asyncio.run(
            self.service.choose_commute_option_with_override(
                profile=profile,
                effective_arrival_time="09:00",
                weather_buffer_minutes=0,
                target_date=date(2026, 5, 2),
                mode_override="shortest",
            )
        )

        self.assertEqual(result["best_option"]["mode"], "bus")
        self.assertEqual(result["best_option"]["snapshot"]["google_detailed"]["duration_minutes"], 24)
        self.assertIsNone(calls[0])

    def test_metro_departure_uses_transit_duration_without_extra_wait_penalty(self):
        result = asyncio.run(
            self.service.calculate_departure_time_by_mode_fast(
                target_date=date(2026, 5, 2),
                effective_arrival_time="09:00",
                baseline_minutes=18,
                weather_buffer_minutes=0,
                best_option={"mode": "metro", "snapshot": {"walk_minutes": 12}},
            )
        )

        self.assertEqual(result["departure_time"], "08:42")


if __name__ == "__main__":
    unittest.main()
