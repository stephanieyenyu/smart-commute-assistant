import importlib.util
import importlib.util
import unittest
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class Phase2To4ArchitectureTests(unittest.TestCase):
    def read_repo_file(self, relative_path: str) -> str:
        return (REPO_ROOT / relative_path).read_text(encoding="utf-8")

    def test_phase2_api_health_persists_logs(self):
        api_health_py = self.read_repo_file("backend/app/integrations/api_health.py")

        self.assertIn("_persist_api_health_log", api_health_py)
        self.assertIn("record_api_health_log", api_health_py)

    def test_route_formatter_preserves_bus_and_metro_detail_format(self):
        module_path = REPO_ROOT / "backend/app/route_formatter.py"
        spec = importlib.util.spec_from_file_location("route_formatter_under_test", module_path)
        formatter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(formatter)

        metro_plan = {
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
        bus_plan = {
            "recommended_mode": "bus",
            "best_option": {
                "mode": "bus",
                "snapshot": {
                    "first_stop": {"stop_name": "南京敦化路口"},
                    "arrival_at_stop_min": 4,
                    "chosen_bus": {"route_name": "307", "eta_min": 8},
                    "valid_eta_list": [
                        {"route_name": "307", "eta_min": 8},
                        {"route_name": "652", "eta_min": 12},
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

        metro_line = formatter.format_transport_line(metro_plan)
        bus_line = formatter.format_transport_line(bus_plan)

        self.assertIn("搭乘 淡水信義線", metro_line)
        self.assertIn("在『芝山』下車", metro_line)
        self.assertIn("走『出口 3』出站", metro_line)
        self.assertNotIn("走『出口 1』出站", metro_line)
        self.assertIn("搭乘 307號公車", bus_line)
        self.assertIn("307號公車將於 8 分鐘後抵達『南京敦化路口』", bus_line)
        self.assertNotIn("可選路線", bus_line)

if __name__ == "__main__":
    unittest.main()
