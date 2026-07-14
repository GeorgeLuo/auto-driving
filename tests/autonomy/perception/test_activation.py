from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from autonomy.perception import read_perception_activation


class PerceptionActivationTests(unittest.TestCase):
    def test_perception_activation_requires_an_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            activation_path = Path(tmp) / "active.json"
            activation_path.write_text("[]", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                read_perception_activation(activation_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
