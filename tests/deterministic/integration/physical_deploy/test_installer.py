from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.hosts.bundles import controller_bundle_paths, sync_controller_bundle
from cli.automa_cli.hosts.deploy import PicarTarget, _verify_picar_autonomy_runtime
from cli.automa_cli.hosts.step_activations import ensure_step_activations, stage_activation
from implementations.decision_cycle.catalog import (
    DEFAULT_STEP_PLUGINS,
    DEFAULT_STEP_PRESETS,
    PRESET_STEPS,
    selection_activation,
)

TARGET = PicarTarget(
    host_name="piracer",
    configuration={
        "host_name": "piracer",
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
            "host": {
                "steps": {step: {"plugin_ids": plugins} for step, plugins in steps.items()},
                "components": {},
            },
        }
    ).encode("utf-8")
    return response


class PhysicalDeployTests(unittest.TestCase):
    def test_deploy_stages_every_step_default_over_what_was_staged(self) -> None:
        """Regression: deploy staged no proposal, so a fresh vehicle held instead of proposing."""

        def staged(paths: dict[str, Path], step: str) -> dict:
            return json.loads(paths[step].read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as tmp:
            bundle = controller_bundle_paths(Path(tmp) / "runtime" / "hosts" / "piracer")
            release = sync_controller_bundle(bundle, output=None)
            ensure = lambda: ensure_step_activations(  # noqa: E731
                host_name="piracer", bundle=bundle, release=release, vehicle=dict(TARGET.configuration),
            )
            paths = ensure()
            self.assertEqual(set(paths), set(STEPS))
            for step in STEPS:
                with self.subTest(step=step, staged="default"):
                    self.assertEqual(staged(paths, step)["plugins"], list(DEFAULT_STEP_PLUGINS[step]))
                    self.assertEqual(
                        staged(paths, step)["metadata"].get("preset"), DEFAULT_STEP_PRESETS.get(step),
                    )

            for step in PRESET_STEPS:
                stale = staged(paths, step)
                stale["plugins"] = []
                paths[step].write_text(json.dumps(stale), encoding="utf-8")
            stage_activation(bundle, selection_activation("action", plugins=["hold"]), host_name="piracer")
            paths["memory"].write_text("{", encoding="utf-8")
            paths = ensure()
            # A deploy replaces whatever each step had staged with its default.
            for step in STEPS:
                with self.subTest(step=step, staged="replaced"):
                    self.assertEqual(staged(paths, step)["plugins"], list(DEFAULT_STEP_PLUGINS[step]))
            for step in STEPS:
                with self.subTest(step=step, staged="release"):
                    self.assertEqual(
                        staged(paths, step)["metadata"]["controller_bundle"]["release"]["archive_sha256"],
                        release["archive"]["sha256"],
                    )

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
                "cli.automa_cli.hosts.deploy.urllib_request.urlopen",
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
