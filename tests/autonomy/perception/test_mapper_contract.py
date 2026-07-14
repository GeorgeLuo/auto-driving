from __future__ import annotations

import unittest

from autonomy.perception.mappers import PluginPerceptionMapper


class MapperContractTests(unittest.TestCase):
    def test_mapper_rejects_removed_configuration_option(self) -> None:
        with self.assertRaises(TypeError):
            PluginPerceptionMapper(include_traversability=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
