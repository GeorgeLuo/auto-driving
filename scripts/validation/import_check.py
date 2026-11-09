"""Run import contracts in a fresh interpreter and retain location evidence."""

import json
import sys
from pathlib import Path

from importlinter import cli  # noqa: F401 - configures the tool
from importlinter.application import rendering, use_cases

from scripts.validation.findings import import_records


def main() -> int:
    sys.path.insert(0, str(Path.cwd()))
    options = use_cases.read_user_options(config_filename="pyproject.toml")
    use_cases._register_contract_types(options)
    report = use_cases.create_report(options, cache_dir=None)
    rendering.render_report(report)
    Path(sys.argv[1]).write_text(json.dumps(import_records(report, Path.cwd())))
    return int(report.contains_failures)


if __name__ == "__main__":
    raise SystemExit(main())
