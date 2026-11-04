"""A report from another controller version names its schema before its key set."""
import unittest

from autonomy.runtime.report import REPORT_SCHEMA, HostReport


class SchemaVersionTests(unittest.TestCase):
    def test_schema_mismatch_precedes_missing_and_extra_keys(self):
        with self.assertRaisesRegex(ValueError, "schema must be"):
            HostReport.from_dict({"schema": "previous-version", "obsolete": {}})
        with self.assertRaisesRegex(ValueError, "missing=.*extra=.*obsolete"):
            HostReport.from_dict({"schema": REPORT_SCHEMA, "obsolete": {}})


if __name__ == "__main__":
    unittest.main()
