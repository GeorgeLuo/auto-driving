from __future__ import annotations

import argparse
import contextlib
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomy.perception import build_perception_request  # noqa: E402
from autonomy.perception.mappers import PluginPerceptionMapper  # noqa: E402
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReading, SensorSnapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated lab perception plugin.")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    candidate_id = str(manifest["id"])
    plugin = manifest["plugin"]
    config = _resolved_config(dict(plugin.get("config") or {}), manifest_path.parent)
    frame_spec = "implementations.perception.observation.plugin:FrameObservationPlugin"
    candidate_spec = str(plugin["entrypoint"])
    if candidate_spec == frame_spec:
        plugin_ids = [candidate_id]
        plugin_specs = {candidate_id: candidate_spec}
    else:
        plugin_ids = ["frame", candidate_id]
        plugin_specs = {"frame": frame_spec, candidate_id: candidate_spec}

    with contextlib.redirect_stdout(sys.stderr):
        mapper = PluginPerceptionMapper(
            plugins=plugin_ids,
            plugin_specs=plugin_specs,
            plugin_configs={candidate_id: config},
        )

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        request_id = None
        try:
            command = json.loads(raw_line)
            request_id = command.get("request_id")
            action = command.get("command")
            if action == "stop":
                _write({"request_id": request_id, "ok": True, "stopped": True})
                return 0
            if action == "reset":
                mapper.reset()
                _write({"request_id": request_id, "ok": True, "reset": True})
                continue
            if action == "describe_schema":
                with contextlib.redirect_stdout(sys.stderr):
                    schema = mapper.describe_schema()
                _write({"request_id": request_id, "ok": True, "schema": schema})
                continue
            if action != "perceive":
                raise ValueError(f"unsupported worker command {action!r}")
            result = _perceive(mapper, command)
            _write({
                "request_id": request_id,
                "ok": True,
                "perception": result.to_dict(),
                "runtime": _runtime_metrics(),
            })
        except Exception as exc:
            _write({
                "request_id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return 0


def _perceive(mapper: PluginPerceptionMapper, command: dict[str, Any]):
    image_path = Path(str(command["image_path"])).resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    captured_at_ms = int(command.get("captured_at_ms") or time.time() * 1000)
    frame_id = str(command.get("frame_id") or image_path.stem)
    output_dir_value = command.get("output_dir")
    output_dir = Path(output_dir_value).resolve() if isinstance(output_dir_value, str) else None
    snapshot = SensorSnapshot(
        read_id=frame_id,
        readings={
            FRONT_CAMERA_SENSOR_ID: SensorReading(
                sensor_id=FRONT_CAMERA_SENSOR_ID,
                sensor_kind="camera",
                captured_at_ms=captured_at_ms,
                path=str(image_path),
                metadata={"source": "lab_worker"},
            )
        },
        started_at_ms=captured_at_ms,
        completed_at_ms=captured_at_ms,
        metadata={"runtime": "lab_worker"},
    )
    with contextlib.redirect_stdout(sys.stderr):
        return mapper.perceive(
            build_perception_request(
                snapshot,
                output_dir=output_dir,
                metadata=dict(command.get("metadata") or {}),
            )
        )


def _resolved_config(config: dict[str, Any], candidate_dir: Path) -> dict[str, Any]:
    model_path = config.get("model_path")
    if isinstance(model_path, str) and not Path(model_path).is_absolute():
        config["model_path"] = str((candidate_dir / model_path).resolve())
    return config


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _runtime_metrics() -> dict[str, float]:
    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return {"peak_rss_mb": round(peak / divisor, 3)}


if __name__ == "__main__":
    raise SystemExit(main())
