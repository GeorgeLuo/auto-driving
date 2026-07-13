from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from autonomy.perception.interface import (
    PerceptionPluginContract,
    PerceptionPluginResult,
    PerceptionRequest,
)
from autonomy.vehicle import FRONT_CAMERA_SENSOR_ID


@dataclass(frozen=True)
class VlmPrepConfig:
    clahe_clip_limit: float = 2.5
    clahe_tile_grid: int = 8
    blur_kernel: int = 3
    shadow_clahe_clip: float = 2.4
    shadow_gamma: float = 0.78
    bilateral_passes: int = 3
    bilateral_d: int = 9
    bilateral_sigma_color: float = 85.0
    bilateral_sigma_space: float = 85.0
    stylize: bool = False
    stylization_sigma_s: float = 85.0
    stylization_sigma_r: float = 0.45


class VlmPrepPlugin:
    """Emit deterministic image-prep artifacts for downstream VLM/CV observers."""

    plugin_id = "vlm-prep-v0"
    contract = PerceptionPluginContract(
        required_sensors=(FRONT_CAMERA_SENSOR_ID,),
        state_mode="stateless",
        artifact_policy="required",
    )

    def __init__(
        self,
        *,
        write_artifacts: bool = True,
        config: VlmPrepConfig | None = None,
    ) -> None:
        self.write_artifacts = bool(write_artifacts)
        self.config = config or VlmPrepConfig()

    def reset(self) -> None:
        return None

    def describe_schema(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "reads": ["front_camera RGB pixels"],
            "emits": [
                "artifact id=vlm_preprocessed_gray",
                "artifact id=vlm_shadow_lifted",
                "artifact id=vlm_stylized_lifted when enabled",
            ],
            "artifacts": ["preprocessed_gray", "shadow_lifted", "stylized_lifted"],
            "limits": [
                "does not call a VLM",
                "does not identify objects by itself",
            ],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionPluginResult:
        front = request.camera_frame(FRONT_CAMERA_SENSOR_ID)
        if front is None:
            return PerceptionPluginResult(
                status="unavailable",
                lines=("signal id=vlm_prep_available value=false confidence=0.000 reason=no_front_camera",),
                observations={
                    self.plugin_id: {
                        "front_camera_available": False,
                        "input_error": request.input_error(FRONT_CAMERA_SENSOR_ID),
                    }
                },
                limits=("front camera image missing",),
            )

        if request.output_dir is None or not self.write_artifacts:
            return PerceptionPluginResult(
                status="unavailable",
                lines=("signal id=vlm_prep_available value=false confidence=0.000 reason=artifact_writes_disabled",),
                observations={self.plugin_id: {"artifact_writes_enabled": self.write_artifacts}},
                limits=("VLM prep plugin currently emits artifacts only",),
            )

        output_dir = request.output_dir / "vlm_prep"
        output_dir.mkdir(parents=True, exist_ok=True)

        image = np.ascontiguousarray(front.rgb[:, :, ::-1])

        artifacts = prepare_vlm_artifacts(
            image,
            output_dir=output_dir,
            config=self.config,
            artifact_id_prefix="vlm_",
        )

        summary = {
            "image": str(front.source_path) if front.source_path is not None else None,
            "artifact_writes_enabled": self.write_artifacts,
            "config": asdict(self.config),
            "artifacts": artifacts,
        }
        summary_path = output_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        artifacts["vlm_prep_summary"] = str(summary_path)

        lines = [
            "signal id=vlm_prep_available value=true confidence=1.000",
            f"artifact id=vlm_preprocessed_gray path={artifacts['vlm_preprocessed_gray']}",
            f"artifact id=vlm_shadow_lifted path={artifacts['vlm_shadow_lifted']}",
        ]
        if "vlm_stylized_lifted" in artifacts:
            lines.append(f"artifact id=vlm_stylized_lifted path={artifacts['vlm_stylized_lifted']}")

        return PerceptionPluginResult(
            lines=tuple(lines),
            observations={
                self.plugin_id: {
                    "image_width_px": int(image.shape[1]),
                    "image_height_px": int(image.shape[0]),
                    "artifacts": artifacts,
                    "config": asdict(self.config),
                }
            },
            artifacts=artifacts,
            limits=("VLM prep artifacts are inputs for later observers, not object detections",),
        )


def prepare_vlm_artifacts(
    image_bgr: np.ndarray,
    *,
    output_dir: Path,
    config: VlmPrepConfig | None = None,
    filename_prefix: str = "",
    artifact_id_prefix: str = "",
) -> dict[str, str]:
    active_config = config or VlmPrepConfig()
    output_dir.mkdir(parents=True, exist_ok=True)
    file_prefix = f"{filename_prefix}_" if filename_prefix else ""
    gray = _preprocess_gray(image_bgr, active_config)
    shadow_lifted = _lift_shadows(image_bgr, active_config)

    artifacts = {
        f"{artifact_id_prefix}preprocessed_gray": str(output_dir / f"{file_prefix}preprocessed_gray.png"),
        f"{artifact_id_prefix}shadow_lifted": str(output_dir / f"{file_prefix}shadow_lifted.png"),
    }
    cv2.imwrite(artifacts[f"{artifact_id_prefix}preprocessed_gray"], gray)
    cv2.imwrite(artifacts[f"{artifact_id_prefix}shadow_lifted"], shadow_lifted)

    if active_config.stylize:
        stylized = cv2.stylization(
            shadow_lifted,
            sigma_s=active_config.stylization_sigma_s,
            sigma_r=active_config.stylization_sigma_r,
        )
        artifacts[f"{artifact_id_prefix}stylized_lifted"] = str(
            output_dir / f"{file_prefix}stylized_lifted.png"
        )
        cv2.imwrite(artifacts[f"{artifact_id_prefix}stylized_lifted"], stylized)
    return artifacts


def _odd(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 == 1 else value + 1


def _preprocess_gray(image_bgr: np.ndarray, config: VlmPrepConfig) -> np.ndarray:
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip_limit,
        tileGridSize=(config.clahe_tile_grid, config.clahe_tile_grid),
    )
    gray = clahe.apply(gray)
    if config.blur_kernel > 1:
        kernel = _odd(config.blur_kernel)
        gray = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    return gray


def _smooth_image(image_bgr: np.ndarray, config: VlmPrepConfig) -> np.ndarray:
    out = image_bgr.copy()
    for _ in range(max(0, config.bilateral_passes)):
        out = cv2.bilateralFilter(
            out,
            d=_odd(config.bilateral_d),
            sigmaColor=config.bilateral_sigma_color,
            sigmaSpace=config.bilateral_sigma_space,
        )
    return out


def _lift_shadows(image_bgr: np.ndarray, config: VlmPrepConfig) -> np.ndarray:
    smoothed = _smooth_image(image_bgr, config)
    lab = cv2.cvtColor(smoothed, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=config.shadow_clahe_clip, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    gamma = max(0.1, float(config.shadow_gamma))
    lookup = np.array([((i / 255.0) ** gamma) * 255.0 for i in range(256)], dtype=np.uint8)
    l_channel = cv2.LUT(l_channel, lookup)
    lifted = cv2.merge([l_channel, a_channel, b_channel])
    return cv2.cvtColor(lifted, cv2.COLOR_LAB2BGR)
