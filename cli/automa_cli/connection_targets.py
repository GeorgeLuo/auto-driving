"""Exact, round-trippable vehicle targets for status and connection commands."""
from __future__ import annotations

import base64
import json
import hashlib
import os
import shlex
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from . import runtime_hosts
from .vehicles import is_chase_vehicle_id, normalize_chase_url, chase_operator_url
from implementations.vehicle.chase_sim.defaults import CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL


@dataclass(frozen=True)
class ConnectionTarget:
    vehicle_id: str
    provider: str
    endpoint: str

    def __post_init__(self) -> None:
        if not self.vehicle_id or any(c.isspace() for c in self.vehicle_id):
            raise ValueError("A target needs a nonempty vehicle id without whitespace.")
        if self.provider not in {"chase-sim", "picar"}:
            raise ValueError("Target provider must be chase-sim or picar.")
        if is_chase_vehicle_id(self.vehicle_id) != (self.provider == "chase-sim"):
            raise ValueError("Vehicle id and target provider disagree.")
        parsed = urlparse(self.endpoint)
        allowed = {"ws", "wss"} if self.provider == "chase-sim" else {"http", "https"}
        if parsed.scheme not in allowed or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Target needs an absolute endpoint without embedded credentials.")
        _ = parsed.port

    @property
    def reference(self) -> str:
        encoded = json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        return "v1." + base64.urlsafe_b64encode(encoded).decode().rstrip("=")

    def to_dict(self) -> dict:
        return {**asdict(self), "reference": self.reference}

    def arguments(self) -> list[str]:
        flag = "--chase-url" if self.provider == "chase-sim" else "--picar-url"
        endpoint = chase_operator_url(self.endpoint) if self.provider == "chase-sim" else self.endpoint
        # Preserve explicit WS paths; an HTTP origin cannot represent them.
        if self.provider == "chase-sim" and normalize_chase_url(endpoint) != self.endpoint:
            endpoint = self.endpoint
        return ["--id", self.vehicle_id, flag, endpoint]

    def command(self, *extra: str, verb: str = "connect") -> str:
        return shlex.join(["./cli/automa", "vehicles", verb, *self.arguments(), *extra])

    def same_endpoint(self, other: str) -> bool:
        """True when ``other`` is this target's endpoint, including loopback aliases."""

        candidate = other.strip()
        if not candidate:
            return False
        if self.provider == "chase-sim":
            try:
                candidate = normalize_chase_url(candidate)
            except ValueError:
                return False
        else:
            candidate = candidate.rstrip("/")
        return _endpoint_identity(candidate) == _endpoint_identity(self.endpoint)


def _endpoint_identity(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host in {"localhost", "127.0.0.1", "::1"}:
        host = "loopback"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme in {"https", "wss"} else 80
    return host, port, parsed.path.rstrip("/")


def decode_target(reference: str) -> ConnectionTarget:
    try:
        if not reference.startswith("v1.") or len(reference) > 8192:
            raise ValueError()
        value = reference[3:]
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if not isinstance(data, dict) or set(data) != {"vehicle_id", "provider", "endpoint"}:
            raise ValueError()
        if not all(isinstance(value, str) for value in data.values()):
            raise ValueError()
        return ConnectionTarget(**data)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("Invalid --target reference; copy it from vehicles status --json.") from exc


def configured_targets() -> list[ConnectionTarget]:
    targets = []
    root = runtime_hosts.RUNTIME_ROOT
    for directory in sorted(root.iterdir()) if root.exists() else []:
        if not directory.is_dir() or directory.name.startswith("."):
            continue
        connection = runtime_hosts.staged_connection(directory.name)
        chase = is_chase_vehicle_id(directory.name)
        endpoint = connection.get("ws_url" if chase else "base_url")
        if endpoint:
            targets.append(ConnectionTarget(directory.name, "chase-sim" if chase else "picar", endpoint.rstrip("/")))
    if not any(t.provider == "chase-sim" for t in targets):
        targets.append(ConnectionTarget("chase-sim-chaser", "chase-sim", normalize_chase_url(
            os.environ.get(CHASE_UI_WS_URL_ENV, DEFAULT_CHASE_UI_WS_URL))))
    return targets


def resolve_target(*, reference: str | None = None, vehicle_id: str | None = None,
                   chase_url: str | None = None, picar_url: str | None = None) -> ConnectionTarget:
    if reference:
        if vehicle_id or chase_url or picar_url:
            raise ValueError("Use --target alone, or --id with an endpoint; do not combine them.")
        return decode_target(reference)
    if chase_url and picar_url:
        raise ValueError("--chase-url and --picar-url cannot be combined.")
    if chase_url:
        return ConnectionTarget(vehicle_id or "chase-sim-chaser", "chase-sim", normalize_chase_url(chase_url))
    if picar_url:
        if not vehicle_id:
            raise ValueError("--picar-url requires --id to identify the selected vehicle.")
        return ConnectionTarget(vehicle_id, "picar", picar_url.rstrip("/"))
    candidates = configured_targets()
    matches = [t for t in candidates if vehicle_id is None or t.vehicle_id == vehicle_id]
    if len(matches) == 1:
        return matches[0]
    choices = "\n".join(t.command() for t in candidates)
    raise ValueError(f"{'Ambiguous' if matches else 'Unknown'} target. Select a listed target:\n{choices}")


def scenario_record_path(target: ConnectionTarget):
    return runtime_hosts.RUNTIME_ROOT / ".connections" / (hashlib.sha256(target.reference.encode()).hexdigest() + ".json")


def selected_scenario(target: ConnectionTarget, status: dict) -> str:
    capture = status.get("layers", {}).get("environment", {}).get("capture", {})
    current = (capture.get("environment") or {}).get("scenario_id")
    if isinstance(current, str) and current:
        return current
    try:
        record = json.loads(scenario_record_path(target).read_text())
        if record.get("target") == target.reference and isinstance(record.get("scenario"), str) and record["scenario"]:
            return record["scenario"]
    except (OSError, ValueError, AttributeError):
        pass
    return "chaser-depth-obstacles"


def remember_scenario(target: ConnectionTarget, status: dict) -> None:
    capture = status.get("layers", {}).get("environment", {}).get("capture", {})
    current = (capture.get("environment") or {}).get("scenario_id")
    if isinstance(current, str) and current:
        path = scenario_record_path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"target": target.reference, "scenario": current}))
        temporary.replace(path)
