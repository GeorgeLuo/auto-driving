"""Private state transport between the CLI and its trusted local Python worker.

SharedMemory intentionally permits Python values (including arrays and plugin
records). Pickle preserves those values without a second, framework-owned
history schema. These payloads are only for the child process launched from the
same checkout: never load them from manifests, recordings, or remote clients.
This is not a persistence format or a sandbox boundary.
"""
from __future__ import annotations

import base64
import pickle

from autonomy.memory import SharedMemory


def encode_shared_memory(shared_memory: SharedMemory | None) -> str | None:
    if shared_memory is None:
        return None
    return base64.b64encode(pickle.dumps(dict(shared_memory), protocol=5)).decode("ascii")


def decode_shared_memory(payload: str | None) -> dict | None:
    if payload is None:
        return None
    return pickle.loads(base64.b64decode(payload, validate=True))
