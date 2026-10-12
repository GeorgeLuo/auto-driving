from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cli.automa_cli.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.deploy import PicarTarget, _verify_picar_autonomy_runtime
from cli.automa_cli.perception import ensure_vehicle_perception_activation
from implementations.decision_cycle.perception.presets import PERCEPTION_PRESETS

TARGET = PicarTarget(
    vehicle_id="piracer",
    vehicle={
        "vehicle_id": "piracer",
        "vehicle_kind": "picar",
        "provider": "picar",
        "connection": {"base_url": "http://piracer.local:8887"},
    },
    provider="picar",
    ssh_target="piracer@piracer.local",
    pi_home="/home/piracer",
)


def _status_response(mode: str, steps: dict[str, list[str]]) -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = json.dumps(
        {
            "ok": True,
            "mode": mode,
            "autonomy": {
                "steps": {step: {"plugin_ids": plugins} for step, plugins in steps.items()},
                "components": {},
            },
        }
    ).encode("utf-8")
    return response


class PhysicalDeployTests(unittest.TestCase):
    def test_named_perception_activation_is_refreshed_from_current_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "runtime" / "vehicles" / "piracer")
            release = sync_controller_bundle(bundle, output=None)
            activation_path = ensure_vehicle_perception_activation(
                vehicle=dict(TARGET.vehicle),
                preset="visual_observer",
                bundle=bundle,
                release=release,
            )
            stale = json.loads(activation_path.read_text(encoding="utf-8"))
            stale["plugins"] = ["frame"]
            activation_path.write_text(json.dumps(stale), encoding="utf-8")

            refreshed_path = ensure_vehicle_perception_activation(
                vehicle=dict(TARGET.vehicle),
                preset="lightweight_observer",
                bundle=bundle,
                release=release,
            )
            refreshed = json.loads(refreshed_path.read_text(encoding="utf-8"))

        self.assertEqual(refreshed["metadata"]["preset"], "visual_observer")
        self.assertEqual(refreshed["plugins"], PERCEPTION_PRESETS["visual_observer"]["plugins"])

    def test_runtime_verification_requires_every_deployed_step_and_manual_mode(self) -> None:
        expected = {"memory": ["bounded_evidence"], "action": ["hold"]}
        cases = (
            ("manual", expected, None),
            ("autonomy", expected, "expected 'manual'"),
            ("manual", {"action": ["hold"]}, "no live memory step"),
            ("manual", {**expected, "action": ["mode"]}, "expected \\['hold'\\]"),
        )
        for mode, reported, error in cases:
            with self.subTest(mode=mode, reported=reported), patch(
                "cli.automa_cli.deploy.urllib_request.urlopen",
                return_value=_status_response(mode, reported),
            ):
                if error is None:
                    verification = _verify_picar_autonomy_runtime(
                        target=TARGET, expected_steps=expected, timeout_s=3.0
                    )
                    self.assertEqual(verification["mode"], "manual")
                    continue
                with self.assertRaisesRegex(RuntimeError, error):
                    _verify_picar_autonomy_runtime(
                        target=TARGET, expected_steps=expected, timeout_s=3.0
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
