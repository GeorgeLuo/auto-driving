#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomy.decision.image_memory import build_memory_from_image, now_id
from autonomy.perception.traversability import FloorPlaneConfig
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID, SensorReadRequest
from implementations.vehicle.chase_sim import ChaseSimCar
from implementations.vehicle.chase_sim.metrics_ws import MetricsUiWebSocketError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert one front-view image into the current decision-memory representation.",
    )
    parser.add_argument("image", nargs="?", type=Path, help="Image to inspect.")
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--source-label", default="local-image")
    parser.add_argument("--no-traversability", action="store_true")
    parser.add_argument("--horizon", type=float, default=FloorPlaneConfig.horizon_ratio)
    parser.add_argument("--threshold", type=float, default=FloorPlaneConfig.floor_threshold)
    parser.add_argument("--capture-chase-sim", action="store_true", help="Capture one Chase simulator front-view image first.")
    parser.add_argument(
        "--sim-current",
        action="store_true",
        help="Capture the Chase simulator front-view image at the present moment without changing sim control/play state.",
    )
    parser.add_argument("--ws-url", default=None)
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--no-prepare", action="store_true", help="Do not switch Chase sim into WS control first.")
    return parser


def capture_chase_sim_image(
    args: argparse.Namespace,
    out_dir: Path,
    *,
    prepare: bool,
) -> tuple[Path, dict[str, Any]]:
    car = ChaseSimCar(ws_url=args.ws_url, timeout_s=args.timeout_s)
    preparation = car.prepare_for_external_control() if prepare else None
    snapshot = car.read_sensors(
        SensorReadRequest(
            output_dir=out_dir / "source",
            read_id="chase_current",
            requested_sensors=(FRONT_CAMERA_SENSOR_ID,),
            image_extension="png",
        ),
    )
    front_camera = snapshot.readings[FRONT_CAMERA_SENSOR_ID]
    if front_camera.path is None:
        raise RuntimeError(f"sensor {FRONT_CAMERA_SENSOR_ID!r} did not return an image path")
    return Path(front_camera.path), {
        "mode": "prepared-capture" if prepare else "current-frame-capture",
        "preparation": preparation,
        "sensor_snapshot": snapshot.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = args.run_id or now_id("image-memory")
    out_dir = args.out_dir or ROOT / "lab" / "runs" / "memory-inspection" / run_id
    out_dir = out_dir.resolve()

    capture_meta = None
    if args.capture_chase_sim and args.sim_current:
        raise SystemExit("choose only one of --capture-chase-sim or --sim-current")

    if args.capture_chase_sim or args.sim_current:
        try:
            image_path, capture_meta = capture_chase_sim_image(
                args,
                out_dir,
                prepare=bool(args.capture_chase_sim and not args.no_prepare),
            )
        except MetricsUiWebSocketError as exc:
            print(f"Chase sim capture failed: {exc}", file=sys.stderr)
            return 2
        source_label = "chase-sim-current-front-view" if args.sim_current else "chase-sim-front-view"
    else:
        if args.image is None:
            raise SystemExit("provide an image path, --capture-chase-sim, or --sim-current")
        image_path = args.image
        source_label = args.source_label

    result = build_memory_from_image(
        image_path=image_path,
        out_dir=out_dir,
        run_id=run_id,
        source_label=source_label,
        include_traversability=not args.no_traversability,
        floor_config=FloorPlaneConfig(
            horizon_ratio=args.horizon,
            floor_threshold=args.threshold,
        ),
    )
    if capture_meta is not None:
        result["capture_source"] = capture_meta
        capture_meta_path = out_dir / "capture_source.json"
        capture_meta_path.write_text(json.dumps(capture_meta, indent=2, sort_keys=True), encoding="utf-8")
        result["capture_source_path"] = str(capture_meta_path)

    compact = {
        "out_dir": result["out_dir"],
        "memory": result["memory"],
        "summary": result["summary"],
        "frame": result["frame"],
        "source": source_label,
    }
    print(json.dumps(compact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
