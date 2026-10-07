from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from autonomy.runtime.session import RunConfiguration
from cli.automa_cli import onboard_automation
from cli.automa_cli.run_record import control_application, control_source


VEHICLE_ID = "picar-test"
PERCEPTION = {"activation": "perception/active.json", "preset": "lane-preset", "plugins": ["lanes"]}
DECISION = {
    "generation_id": "gen-1",
    "steps": {"proposal": {"plugins": ["follow"]}},
    "published": True,
    "latest_frame_publish_skips": 0,
    "latest_frame_publish_skip_reason": None,
}


class _FakeClient:
    """An onboard host that runs two frames and then ends the run itself."""

    def __init__(self, sessions: list[dict]) -> None:
        self.sessions = sessions
        self.started_with: RunConfiguration | None = None
        self.stopped = False

    def start(self, configuration: RunConfiguration) -> dict:
        self.started_with = configuration
        return {"ok": True, "host_run_id": "host-1"}

    def status(self) -> dict:
        return {"ok": True, "host_run_id": "host-1", "session": self.sessions.pop(0)}

    def stop(self) -> dict:
        self.stopped = True
        released = {**_session("stopped", 0), "execution": {"mode": "manual", "closed": False}}
        return {"ok": True, "host_run_id": "host-1", "session": released}


class _FakeDecisionView:
    def __init__(self, **kwargs) -> None:
        self.action_policy = kwargs["action_policy"]

    def refresh(self) -> bool:
        return True


def _session(status: str, processed: int) -> dict:
    return {
        "status": status,
        "processed_frames": processed,
        "execution": {"mode": "autonomy", "closed": status != "running"},
    }


def _host(camera: int, skipped: int, *, last_error: str | None = None) -> dict:
    return {
        "autonomy": {
            "components": {"observation": {"camera_frame_count": camera, "skipped_count": skipped}},
            "steps": {"memory": {"plugin_ids": ["recent"], "last_error": None}, "proposal": None},
            "cycle_count": camera,
            "error_count": 0 if last_error is None else 1,
            "last_error": last_error,
            "execution": {"mode": "autonomy"},
        }
    }


def _publication(index: int) -> dict:
    return {
        "frame": {
            "frame_id": f"donkey_frame_{index:06d}",
            "frame_index": index,
            "captured_at_ms": 1_000 + index,
            "completed_at_ms": 1_040 + index,
        },
        "duration_ms": 40,
        "perception": {"signals": [{"id": "lane"}], "things": [], "text": "signal id=lane"},
        "control": {"reason": "steer-left", "steering": -0.2, "throttle": 0.1},
        "generation_id": "gen-1",
        "mode": "autonomy",
    }


class OnboardMonitorTests(unittest.TestCase):
    def _monitor(self, automation_dir: Path, *, sessions: list[dict], hosts: list[dict]):
        jpeg = io.BytesIO()
        Image.new("RGB", (4, 4)).save(jpeg, format="JPEG")
        publications = [_publication(0), _publication(1)]
        current: dict = {}

        def publication(*_args, **_kwargs) -> dict:
            # The latest publication repeats once the host has no newer frame.
            current["publication"] = publications.pop(0) if len(publications) > 1 else publications[0]
            return current["publication"]

        def frame(*_args, **_kwargs) -> tuple[bytes, dict]:
            return jpeg.getvalue(), {"frame_id": current["publication"]["frame"]["frame_id"]}

        client = _FakeClient(sessions)
        output = io.StringIO()
        with (
            mock.patch.object(onboard_automation, "OnboardRuntimeClient", return_value=client),
            mock.patch.object(onboard_automation, "fetch_autonomy_status", side_effect=hosts),
            mock.patch.object(onboard_automation, "fetch_observation_publication", side_effect=publication),
            mock.patch.object(onboard_automation, "fetch_observation_frame", side_effect=frame),
            mock.patch.object(
                onboard_automation, "frame_id_from_headers", side_effect=lambda headers: headers["frame_id"]
            ),
            mock.patch.object(onboard_automation, "PicarDecisionViewAdapter", _FakeDecisionView),
        ):
            code, message = onboard_automation.monitor_onboard_runtime(
                vehicle_id=VEHICLE_ID,
                base_url="http://picar.invalid:8887",
                automation_dir=automation_dir,
                perception=dict(PERCEPTION),
                decision=dict(DECISION),
                step_activations={
                    "memory": automation_dir.parent / "memory" / "active.json",
                    "proposal": automation_dir.parent / "proposal" / "active.json",
                },
                configuration=RunConfiguration(mode="autonomy", interval_s=0.0, frames=2),
                timeout_s=1.0,
                record=False,
                verbose=False,
                output=output,
            )
        state = json.loads((automation_dir / "state.json").read_text(encoding="utf-8"))
        return client, code, message, output.getvalue().splitlines(), state

    def test_onboard_run_prints_and_records_what_a_chase_run_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            client, code, message, lines, state = self._monitor(
                automation_dir,
                sessions=[_session("running", 1), _session("completed", 2)],
                hosts=[_host(10, 3), _host(11, 3), _host(12, 4)],
            )

        self.assertTrue(client.stopped)
        self.assertEqual(client.started_with.mode, "autonomy")
        self.assertEqual(
            [line for line in lines if not line.startswith("Runtime view: ")],
            [
                f"Automation running: {VEHICLE_ID}",
                "Perception: lane-preset",
                "Recording: off; latest frame and perception are overwritten each iteration",
                "Control source: onboard Donkey host",
                "Action policy: autonomy",
                "Decision generation: gen-1",
                "Frames: 2",
                "donkey_frame_000000: signals=1 things=0 action=steer-left",
            ],
        )
        self.assertRegex(lines[6], r"^Runtime view: http://")
        self.assertEqual(code, 0)
        self.assertEqual(
            message.splitlines()[:6],
            [
                f"Automation completed: {VEHICLE_ID}",
                "Frames captured: 2",
                "Frames processed: 2",
                "Frames skipped by perception: 1",
                "Control source: onboard",
                "Action policy: autonomy",
            ],
        )
        self.assertEqual(message.splitlines()[-1], "Ready for: inspect stopped deployment")
        self.assertEqual(
            (state["status"], state["stop_reason"], state["run_id"]),
            ("completed", "completed", "host-1"),
        )
        self.assertEqual(state["readiness"]["ready_for"], "inspect stopped deployment")
        self.assertEqual(state["control_application"], "shared_execution")
        self.assertEqual(state["execution"]["mode"], "manual")
        self.assertEqual(state["steps"]["memory"], {"plugin_ids": ["recent"], "last_error": None})
        self.assertEqual(state["proposal"]["status"], "absent")
        self.assertEqual(state["last_frame"]["frame_id"], "donkey_frame_000001")
        self.assertEqual(state["last_frame"]["capture_to_perception_ms"], 40)
        self.assertEqual(state["last_frame"]["control"]["reason"], "steer-left")
        self.assertEqual(state["published_view"]["available"], False)

    def test_a_host_cycle_error_fails_the_run_with_the_host_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            automation_dir = Path(tmp) / "automation"
            client, code, message, _lines, state = self._monitor(
                automation_dir,
                sessions=[_session("error", 0)],
                hosts=[_host(10, 3), _host(10, 3, last_error="RuntimeError: plan failed")],
            )

        self.assertTrue(client.stopped)
        self.assertEqual(code, 2)
        self.assertEqual(
            message.splitlines()[:2],
            [f"Automation failed for {VEHICLE_ID}.", "Reason: RuntimeError: plan failed"],
        )
        self.assertEqual((state["status"], state["stop_reason"]), ("error", "error"))
        self.assertEqual(state["readiness"]["blocking_layer"], "automation_worker")


class ControlRecordTests(unittest.TestCase):
    def test_each_vehicle_names_its_control_source_and_application(self) -> None:
        autonomy = RunConfiguration(mode="autonomy")
        observe = RunConfiguration(mode="observe_only")

        self.assertEqual(
            [
                control_source(autonomy, onboard=False),
                control_source(observe, onboard=False),
                control_source(autonomy, onboard=True),
                control_source(observe, onboard=True),
            ],
            ["external_ws", "preserved_current", "onboard", "onboard"],
        )
        self.assertEqual(
            [control_application(autonomy), control_application(observe)],
            ["shared_execution", "not_applied"],
        )


if __name__ == "__main__":
    unittest.main()
