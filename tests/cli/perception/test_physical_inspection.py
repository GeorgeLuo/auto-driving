from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.support.runtime_fixtures import write_runtime_fixture
from cli.automa_cli import perception


ROOT = Path(__file__).resolve().parents[3]


class PerceptionCommandTests(unittest.TestCase):
    def test_piracer_staged_inspection_enriches_with_reachable_live_observation(
        self,
    ) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        publication = {
            "schema": "automa_physical_observation_publication_v0",
            "ok": True,
            "health": "healthy",
            "algorithm": "lightweight_observer",
            "mode": "user",
            "result_age_ms": 120,
            "duration_ms": 280,
            "control": {
                "steering": 0.0,
                "throttle": 0.0,
                "reason": "stable-idle-engine",
            },
            "frame": {"frame_id": "donkey_frame_000011", "has_image": True},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ) as discover, patch.object(
                perception,
                "physical_view_status",
                return_value={"available": True, "url": "http://127.0.0.1:9100"},
            ) as view_status, patch.object(
                perception,
                "fetch_observation_publication",
                return_value=publication,
            ) as fetch:
                json_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=1.25
                )
                text_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=False, timeout_s=1.25
                )

        self.assertEqual(json_result.exit_code, 0)
        payload = json.loads(json_result.message)
        self.assertEqual(payload["activation"]["algorithm"], "sim_debug")
        self.assertTrue(payload["live_observation"]["available"])
        self.assertEqual(payload["live_observation"]["provider"], "picar")
        self.assertEqual(
            payload["live_observation"]["frame"]["frame_id"], "donkey_frame_000011"
        )
        self.assertTrue(payload["published_view"]["available"])
        self.assertIn("Live onboard observation:", text_result.message)
        self.assertIn("local view: http://127.0.0.1:9100", text_result.message)
        self.assertEqual(discover.call_count, 2)
        self.assertEqual(view_status.call_count, 2)
        self.assertEqual(fetch.call_count, 2)
        fetch.assert_called_with("http://piracer.local:8887", timeout_s=1.25)

    def test_staged_inspection_survives_unavailable_live_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": []},
            ) as discover:
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )
                text_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=False, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        payload = json.loads(result.message)
        self.assertEqual(payload["activation"]["algorithm"], "sim_debug")
        self.assertFalse(payload["live_observation"]["available"])
        self.assertIn(
            "not found among discoverable vehicles",
            payload["live_observation"]["reason"],
        )
        self.assertIn("Live onboard observation: unavailable", text_result.message)
        self.assertEqual(discover.call_count, 2)

    def test_staged_inspection_keeps_live_observation_when_view_is_unavailable(
        self,
    ) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        publication = {
            "health": "healthy",
            "frame": {"frame_id": "frame_view_unavailable"},
            "control": {"steering": 0.0, "throttle": 0.0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception,
                "physical_view_status",
                return_value={"available": False, "reason": "view not started"},
            ), patch.object(
                perception,
                "fetch_observation_publication",
                return_value=publication,
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )
                text_result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=False, timeout_s=0.5
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(payload["live_observation"]["available"])
        self.assertFalse(payload["live_observation"]["published_view"]["available"])
        self.assertEqual(
            payload["live_observation"]["published_view"]["reason"], "view not started"
        )
        self.assertIn("local view: unavailable (view not started)", text_result.message)

    def test_staged_inspection_survives_discovery_connection_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                side_effect=ConnectionError("discovery timed out"),
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(payload["live_observation"]["available"])
        self.assertIn("discovery timed out", payload["live_observation"]["reason"])

    def test_staged_inspection_reports_reachable_non_piracer_without_fabrication(
        self,
    ) -> None:
        chase = {
            "vehicle_id": "piracer",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://simulator/ws/control"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [chase]},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        live = json.loads(result.message)["live_observation"]
        self.assertFalse(live["available"])
        self.assertEqual(live["provider"], "chase-sim")
        self.assertIn("not a PiRacer", live["reason"])

    def test_staged_inspection_bounds_unusable_picar_base_url(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "not-a-url"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception, "physical_view_status"
            ) as view_status, patch.object(
                perception, "fetch_observation_publication"
            ) as fetch:
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        live = json.loads(result.message)["live_observation"]
        self.assertFalse(live["available"])
        self.assertIn("invalid picar base_url", live["error"])
        view_status.assert_not_called()
        fetch.assert_not_called()

    def test_staged_inspection_does_not_claim_unavailable_live_health(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        publication = {
            "health": "unavailable",
            "error": "observation warming",
            "frame": None,
            "control": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception,
                "physical_view_status",
                return_value={"available": False, "reason": "view not started"},
            ), patch.object(
                perception,
                "fetch_observation_publication",
                return_value=publication,
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 0)
        live = json.loads(result.message)["live_observation"]
        self.assertFalse(live["available"])
        self.assertEqual(live["error"], "observation warming")

    def test_no_local_staging_preserves_live_only_activation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": []},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No active perception algorithm found", result.message)

    def test_no_local_staging_rejects_reachable_non_piracer(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "chase-sim",
            "connection": {"ws_url": "ws://simulator/ws/control"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("No active perception algorithm found", result.message)

    def test_local_activation_error_is_not_hidden_by_reachable_picar(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            fixture = write_runtime_fixture(
                runtime_root, "piracer", pid=os.getpid(), manifest_bundle_root=ROOT
            )
            (fixture.bundle_root / "runtime" / "perception" / "active.json").write_text(
                "{}", encoding="utf-8"
            )
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("does not define perception.mapper_spec", result.message)

    def test_piracer_without_local_staging_keeps_live_only_inspection(self) -> None:
        vehicle = {
            "vehicle_id": "piracer",
            "provider": "picar",
            "connection": {"base_url": "http://piracer.local:8887"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            runtime_root = Path(tmp) / "vehicles"
            with patch.object(perception, "RUNTIME_ROOT", runtime_root), patch.object(
                perception,
                "discover_active_vehicles",
                return_value={"vehicles": [vehicle]},
            ), patch.object(
                perception,
                "physical_view_status",
                return_value={"available": False, "reason": "view not started"},
            ), patch.object(
                perception,
                "fetch_observation_publication",
                return_value={
                    "health": "healthy",
                    "frame": {"frame_id": "frame_1"},
                    "control": {"steering": 0.0, "throttle": 0.0},
                },
            ):
                result = perception.get_vehicle_perception_info(
                    vehicle_id="piracer", json_output=True, timeout_s=0.5
                )

        payload = json.loads(result.message)
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(payload["activation"])
        self.assertTrue(payload["live_observation"]["available"])
