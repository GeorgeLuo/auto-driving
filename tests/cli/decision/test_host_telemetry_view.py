"""Compatibility tests for the additive host-telemetry view projection."""

from __future__ import annotations

import copy
import unittest

from cli.automa_cli.decision_live import project_live_decision_payload
from cli.automa_cli.decision_view import (
    build_decision_host_telemetry_capture,
    project_host_telemetry_panel,
    render_host_telemetry_panel_html,
)
from cli.automa_cli.physical_observation import HOST_TELEMETRY_PANEL_SCHEMA


def _panel() -> dict:
    return {
        "schema": HOST_TELEMETRY_PANEL_SCHEMA,
        "status": "healthy",
        "reason": "",
        "joined": True,
        "identity": {"source_frame": {"frame_id": "frame-1"}},
        "source_frame": {"frame_id": "frame-1"},
        "host_tick": {"sequence": 1},
        "mode": "user",
        "user_input": {"steering": 0.0, "throttle": 0.0},
        "pilot_output": {"steering": 0.2, "throttle": 0.0},
        "host_selected_output": {"steering": 0.0, "throttle": 0.0},
        "application": {
            "boundary": "post_drive_mode_pre_drivetrain",
            "actuator_feedback": "unavailable",
        },
        "limits": {},
        "freshness": {},
        "coverage": {"interval_covered": False},
        "record": {},
        "details": {},
        "decision": {"frame_id": "frame-1", "frame_index": 1},
    }


class HostTelemetryViewTests(unittest.TestCase):
    def test_projection_adds_sibling_without_mutating_authority(self) -> None:
        authority = {
            "proposed": {"steering": 0.5, "throttle": 0.0},
            "authorized_output": {"steering": 0.0, "throttle": 0.0},
            "proposed_applied": False,
            "host_application": {"status": "unavailable"},
        }
        decision = {
            "schema": "provider_decision_stream_frame_v0",
            "frame_id": "frame-1",
            "authority": authority,
        }
        before = copy.deepcopy(decision)
        projected = project_live_decision_payload(decision, _panel())

        self.assertEqual(decision, before)
        self.assertEqual(projected["authority"], authority)
        self.assertEqual(projected["host_telemetry"]["host_selected_output"]["steering"], 0.0)
        self.assertNotIn("authority", projected["host_telemetry"])
        self.assertNotIn("host_application", projected["host_telemetry"])

    def test_malicious_panel_becomes_explicit_failure(self) -> None:
        invalid = _panel()
        invalid["authority"] = {"authorized_output": {"steering": 0.0}}
        result = project_host_telemetry_panel(invalid)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "field_invalid")
        self.assertFalse(result["joined"])
        self.assertNotIn("authority", result)

    def test_capture_keeps_decision_and_telemetry_separate(self) -> None:
        decision = {
            "schema": "provider_decision_stream_frame_v0",
            "frame_id": "frame-1",
            "authority": {"proposed_applied": False},
        }
        capture = build_decision_host_telemetry_capture(
            decision_payload=decision,
            panel=_panel(),
            vehicle_id="piracer",
        )

        self.assertEqual(capture["schema"], "automa_physical_decision_capture_v0")
        self.assertEqual(capture["decision"], decision)
        self.assertEqual(capture["host_telemetry"]["schema"], "automa_host_boundary_telemetry_capture_v0")
        self.assertIn("host_telemetry", capture["host_telemetry"])
        self.assertNotIn("authority", capture["host_telemetry"])

    def test_panel_html_is_additive_and_escaped(self) -> None:
        html = render_host_telemetry_panel_html(_panel())
        self.assertIn('id="host_telemetry"', html)
        self.assertIn("Host telemetry", html)
        self.assertIn("post_drive_mode_pre_drivetrain", html)


if __name__ == "__main__":
    unittest.main()
