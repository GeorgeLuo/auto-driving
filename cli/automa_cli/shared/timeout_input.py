"""The ``--timeout-s`` check every status and run command makes before dispatching work."""

from __future__ import annotations

import json
import math
from typing import TextIO

_TIMEOUT_INPUT_ERROR_CODE = "timeout_invalid"
_TIMEOUT_INPUT_CONSTRAINT = "finite number greater than zero"
_TIMEOUT_INPUT_RECOVERY = "Provide a finite --timeout-s greater than zero."


def validate_timeout_input(
    timeout_s: float,
    *,
    command: str,
    json_output: bool,
    human_stream: TextIO,
) -> bool:
    """Reject malformed command-envelope timeouts before dispatching work."""

    if math.isfinite(timeout_s) and timeout_s > 0:
        return True

    message = f"{command}: --timeout-s must be a {_TIMEOUT_INPUT_CONSTRAINT}"
    if json_output:
        print(
            json.dumps(
                {
                    "schema": "automa_cli_error",
                    "error_code": _TIMEOUT_INPUT_ERROR_CODE,
                    "error": message,
                    "layer": "input",
                    "details": {
                        "argument": "--timeout-s",
                        "constraint": _TIMEOUT_INPUT_CONSTRAINT,
                    },
                    "recovery": _TIMEOUT_INPUT_RECOVERY,
                    "exit_code": 2,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(message, file=human_stream)
    return False
