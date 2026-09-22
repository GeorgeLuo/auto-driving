from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
AUTOMA_PATH = WORKSPACE_ROOT / "cli" / "automa"


def run_automa(
    *args: str,
    runtime_root: Path | None = None,
    extra_env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the public Automa executable in an isolated subprocess."""
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if runtime_root is not None:
        env["AUTOMA_RUNTIME_ROOT"] = str(runtime_root)
    if extra_env is not None:
        env.update(extra_env)
    if not any(
        env.get(name) == "1" for name in ("AUTOMA_TEST_LIVE_SIM", "AUTOMA_TEST_LIVE_PI")
    ):
        offline_path = str(Path(__file__).parent / "offline")
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (offline_path, env.get("PYTHONPATH")))
        )

    result = subprocess.run(
        [sys.executable, str(AUTOMA_PATH), *args],
        cwd=WORKSPACE_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            "\n".join(
                [
                    f"automa {' '.join(args)} failed with exit code {result.returncode}",
                    "stdout:",
                    result.stdout,
                    "stderr:",
                    result.stderr,
                ]
            )
        )
    return result
