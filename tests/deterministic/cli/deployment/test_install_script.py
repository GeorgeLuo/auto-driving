"""The script ``automa hosts create`` and ``update`` run on a PiCar to install a release."""

from __future__ import annotations

import ast
import unittest

from autonomy.decision_cycle.activation import STEPS
from cli.automa_cli.hosts import deploy


class RemoteInstallScriptTests(unittest.TestCase):
    def test_script_compiles_and_names_the_activation_steps(self) -> None:
        script = deploy._REMOTE_AUTONOMY_INSTALL_SCRIPT
        compile(script, "<remote install>", "exec")
        assignments = [
            node.value
            for node in ast.parse(script).body
            if isinstance(node, ast.Assign)
            and [getattr(target, "id", None) for target in node.targets] == ["STEPS"]
        ]
        self.assertEqual([ast.literal_eval(value) for value in assignments], [STEPS])


if __name__ == "__main__":
    unittest.main(verbosity=2)
