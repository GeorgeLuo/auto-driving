"""Run an Automa command in this process, for tests that need only its result."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cli.automa_cli.app import main
from tests.support.cli_runner import checked_result


def invoke_automa(
    *args: str,
    runtime_root: Path | None = None,
    extra_env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Return what ``run_automa`` would, without starting the executable.

    Starting ``cli/automa`` costs about two seconds per command. Keep
    ``run_automa`` for a test of the executable itself or a command that must
    run in its own process.
    """

    env = {} if runtime_root is None else {"AUTOMA_RUNTIME_ROOT": str(runtime_root)}
    env.update(extra_env or {})
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env), redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            code = main(list(args))
        except SystemExit as exc:
            code = exc.code
            if isinstance(code, str):
                print(code, file=sys.stderr)
                code = 1
    result = subprocess.CompletedProcess(
        ["automa", *args], 0 if code is None else code, stdout.getvalue(), stderr.getvalue()
    )
    return checked_result(result, args, check)
