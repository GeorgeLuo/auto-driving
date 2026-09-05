"""Run M008 consumer checks and emit machine-readable execution/line evidence.

Run this file with the historical checkout as cwd. QCA itself is not imported
into that checkout, so the candidate uses its own product modules and tests.
"""

from __future__ import annotations

import contextlib
import json
import sys
import unittest
from pathlib import Path


def _repo_relative(root: Path, filename: str) -> str | None:
    path = Path(filename)
    if not path.is_absolute():
        path = root / path
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return None
    return relative.as_posix()


def main() -> int:
    import coverage

    root = Path.cwd().resolve()
    sys.path[:0] = [str(root), str(root / "cli")]
    # Ignore the checkout .coveragerc (source= plus relative_files=) so this
    # runner can bound measurement to the workbench package regardless of cwd.
    measured = coverage.Coverage(
        data_file=None,
        config_file=False,
        source=[str(root / "cli" / "automa_cli")],
    )
    measured.start()
    with contextlib.redirect_stdout(sys.stderr):
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            "tests.cli.test_workbench",
            "tests.lab.perception.test_floor_continuity_capture",
            "tests.milestones.test_replay_workbench_record_session",
        ])
        result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    measured.stop()
    data = measured.get_data()
    lines = {}
    for filename in sorted(data.measured_files()):
        relative = _repo_relative(root, filename)
        if relative is None or not relative.startswith("cli/automa_cli/"):
            continue
        _, statements, _, missing, _ = measured.analysis2(filename)
        lines[relative] = {
            "statements": statements,
            "executed": sorted(data.lines(filename) or []),
            "missing": missing,
        }
    print(json.dumps({
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "failing_tests": [str(test) for test, _ in result.failures + result.errors],
        "coverage_version": coverage.__version__,
        "coverage_scope": "in-process CLI Python; subprocess coverage is not included",
        "lines": lines,
    }, indent=2, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
