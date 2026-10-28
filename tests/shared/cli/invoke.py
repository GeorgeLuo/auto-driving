"""Run an Automa command in this process, for tests that need only its result."""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
from contextlib import ExitStack, chdir, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from cli.automa_cli import app
from cli.automa_cli.hosts import (
    commands as hosts_commands,
    deploy,
    memory,
    operations,
    perception,
    runtime_hosts,
    view_discovery,
    viability,
)
from tests.support.cli_runner import WORKSPACE_ROOT, checked_result
from tests.support.offline.sitecustomize import offline_getaddrinfo

# These names read their variable once, when the module is imported. A command
# run in this process sees a test's value only if the name is rebound.
IMPORT_TIME_ENV = {
    "AUTOMA_RUNTIME_ROOT": (
        (hosts_commands, "HOSTS_RUNTIME_ROOT"),
        (deploy, "RUNTIME_ROOT"),
        (memory, "RUNTIME_ROOT"),
        (perception, "RUNTIME_ROOT"),
        (runtime_hosts, "RUNTIME_ROOT"),
        (view_discovery, "RUNTIME_ROOT"),
        (view_discovery.runtime_view_dir.__kwdefaults__, "runtime_root"),
        (view_discovery.discover_runtime_view.__kwdefaults__, "runtime_root"),
    ),
    "AUTOMA_OPERATION_OUTPUT_ROOT": ((operations, "OPERATION_OUTPUT_ROOT"),),
    "AUTOMA_PERCEPTION_VIABILITY_OUTPUT_ROOT": ((viability, "PERCEPTION_VIABILITY_OUTPUT_ROOT"),),
    "AUTOMA_MEMORY_VIABILITY_OUTPUT_ROOT": ((viability, "MEMORY_VIABILITY_OUTPUT_ROOT"),),
}
LIVE_ENV = ("AUTOMA_TEST_LIVE_SIM", "AUTOMA_TEST_LIVE_PI")


def invoke_automa(
    *args: str,
    runtime_root: Path | None = None,
    extra_env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Return what ``run_automa`` would, without starting the executable.

    Starting ``cli/automa`` costs about two seconds per command. Like
    ``run_automa``, the command runs from the workspace root and, outside live
    runs, reaches only local fixture endpoints. Keep ``run_automa`` for a test
    of the executable itself or a command that must run in its own process.
    """

    env = {} if runtime_root is None else {"AUTOMA_RUNTIME_ROOT": str(runtime_root)}
    env.update(extra_env or {})
    stdout, stderr = io.StringIO(), io.StringIO()
    with ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env))
        for name, value in env.items():
            for target, attribute in IMPORT_TIME_ENV.get(name, ()):
                if isinstance(target, dict):
                    stack.enter_context(mock.patch.dict(target, {attribute: Path(value)}))
                else:
                    stack.enter_context(mock.patch.object(target, attribute, Path(value)))
        if not any(os.environ.get(name) == "1" for name in LIVE_ENV):
            stack.enter_context(
                mock.patch.object(socket, "getaddrinfo", offline_getaddrinfo(socket.getaddrinfo))
            )
        stack.enter_context(chdir(WORKSPACE_ROOT))
        stack.enter_context(redirect_stdout(stdout))
        stack.enter_context(redirect_stderr(stderr))
        try:
            code = app.main(list(args))
        except SystemExit as exc:
            code = exc.code
            if isinstance(code, str):
                print(code, file=sys.stderr)
                code = 1
    result = subprocess.CompletedProcess(
        ["automa", *args], 0 if code is None else code, stdout.getvalue(), stderr.getvalue()
    )
    return checked_result(result, args, check)
