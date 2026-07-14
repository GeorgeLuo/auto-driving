from __future__ import annotations

import hashlib
import json
import os
import select
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import cv2
import requests  # type: ignore[import-untyped]

from autonomy.perception import (
    PERCEPTION_TEXT_SCHEMA,
    PerceptionComponentUnavailable,
    PerceptionRequest,
    PerceptionText,
)
from implementations.perception.components import FRONT_CAMERA_RGB_INPUT, provide_camera_frame

from .paths import ROOT, display_path, safe_path_part


LAB_PLUGIN_SCHEMA = "automa_lab_perception_plugin_v0"
LAB_PERCEPTION_ROOT = Path(
    os.environ.get("AUTOMA_LAB_PERCEPTION_ROOT", ROOT / "lab" / "plugins" / "perception")
)
WORKER_MODULE = "lab.plugins.perception.worker"
STATE_FILENAME = ".candidate-state.json"


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    message: str


@dataclass(frozen=True)
class PerceptionCandidate:
    candidate_id: str
    directory: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def runtime_python(self) -> Path:
        configured = str((self.manifest.get("runtime") or {}).get("python") or ".venv/bin/python")
        if configured == "core":
            return Path(sys.executable)
        path = Path(configured)
        return path if path.is_absolute() else self.directory / path

    @property
    def requirements_path(self) -> Path | None:
        configured = (self.manifest.get("runtime") or {}).get("requirements")
        if not isinstance(configured, str) or not configured:
            return None
        path = Path(configured)
        return path if path.is_absolute() else self.directory / path

    @property
    def model_path(self) -> Path | None:
        model = self.manifest.get("model") or {}
        configured = model.get("filename")
        if not isinstance(configured, str) or not configured:
            return None
        return self.directory / "models" / configured

    @property
    def runs_dir(self) -> Path:
        return self.directory / "runs"


def available_candidate_ids() -> tuple[str, ...]:
    return tuple(candidate.candidate_id for candidate in discover_candidates())


def discover_candidates() -> tuple[PerceptionCandidate, ...]:
    if not LAB_PERCEPTION_ROOT.is_dir():
        return ()
    candidates: list[PerceptionCandidate] = []
    for manifest_path in sorted(LAB_PERCEPTION_ROOT.glob("*/plugin.json")):
        try:
            candidate = _load_manifest(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        candidates.append(candidate)
    return tuple(candidates)


def get_candidate(candidate_id: str | None) -> PerceptionCandidate:
    candidates = discover_candidates()
    if candidate_id is None:
        if len(candidates) == 1:
            return candidates[0]
        available = ", ".join(candidate.candidate_id for candidate in candidates) or "none"
        raise ValueError(f"candidate id is required; available candidates: {available}")
    for candidate in candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    available = ", ".join(item.candidate_id for item in candidates) or "none"
    raise ValueError(f"unknown perception candidate {candidate_id!r}; available candidates: {available}")


def candidate_status(candidate: PerceptionCandidate) -> dict[str, Any]:
    python_ready = candidate.runtime_python.is_file() and os.access(candidate.runtime_python, os.X_OK)
    requirements = candidate.requirements_path
    requirements_ready = requirements is None or requirements.is_file()
    model_path = candidate.model_path
    expected_model_sha256 = (candidate.manifest.get("model") or {}).get("sha256")
    observed_model_sha256 = _file_sha256(model_path)
    model_ready = model_path is None or (
        model_path.is_file()
        and (
            not isinstance(expected_model_sha256, str)
            or not expected_model_sha256
            or observed_model_sha256 == expected_model_sha256
        )
    )
    state_path = candidate.directory / STATE_FILENAME
    state = _read_json(state_path)
    return {
        "schema": "automa_lab_perception_candidate_status_v0",
        "id": candidate.candidate_id,
        "name": str(candidate.manifest.get("name") or candidate.candidate_id),
        "description": str(candidate.manifest.get("description") or ""),
        "directory": display_path(candidate.directory),
        "manifest": display_path(candidate.manifest_path),
        "source_tree_sha256": _candidate_source_hash(candidate.directory),
        "ready": python_ready and requirements_ready and model_ready,
        "runtime": {
            "python": display_path(candidate.runtime_python),
            "python_ready": python_ready,
            "requirements": display_path(requirements) if requirements is not None else None,
            "requirements_ready": requirements_ready,
        },
        "model": {
            "path": display_path(model_path) if model_path is not None else None,
            "ready": model_ready,
            "download_url": (candidate.manifest.get("model") or {}).get("download_url"),
            "expected_sha256": expected_model_sha256,
            "license_review": (candidate.manifest.get("model") or {}).get("license_review"),
            "sha256": observed_model_sha256,
            "size_bytes": state.get("model_size_bytes") if isinstance(state, dict) else None,
        },
        "output": dict(candidate.manifest.get("output") or {}),
        "setup_command": f"./cli/automa vehicles perception setup {candidate.candidate_id}",
    }


def list_perception_candidates(*, json_output: bool = False) -> CommandResult:
    payload = {
        "schema": "automa_lab_perception_candidates_v0",
        "root": display_path(LAB_PERCEPTION_ROOT),
        "candidates": [candidate_status(candidate) for candidate in discover_candidates()],
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    lines = ["Perception candidates", "---------------------"]
    if not payload["candidates"]:
        lines.extend(["No candidates found.", f"Expected under: {display_path(LAB_PERCEPTION_ROOT)}"])
        return CommandResult(0, "\n".join(lines))
    for item in payload["candidates"]:
        state = "ready" if item["ready"] else "setup required"
        lines.append(f"- {item['id']}: {state}")
        if item["description"]:
            lines.append(f"  {item['description']}")
        if not item["ready"]:
            missing: list[str] = []
            if not item["runtime"]["python_ready"]:
                missing.append("runtime")
            if not item["runtime"]["requirements_ready"]:
                missing.append("requirements")
            if not item["model"]["ready"]:
                missing.append("model")
            lines.append(f"  missing: {', '.join(missing)}")
            lines.append(f"  setup: {item['setup_command']}")
    return CommandResult(0, "\n".join(lines))


def setup_perception_candidate(
    candidate_id: str | None,
    *,
    json_output: bool = False,
    output: TextIO | None = None,
) -> CommandResult:
    try:
        candidate = get_candidate(candidate_id)
    except ValueError as exc:
        return CommandResult(2, str(exc))

    steps: list[dict[str, Any]] = []
    try:
        _emit(output, f"Preparing perception candidate {candidate.candidate_id}...")
        runtime_created = _ensure_runtime(candidate, output=output)
        steps.append({"step": "runtime", "changed": runtime_created, "ok": True})
        dependencies_installed = _ensure_dependencies(candidate, output=output, force=runtime_created)
        steps.append({"step": "dependencies", "changed": dependencies_installed, "ok": True})
        model_result = _ensure_model(candidate, output=output)
        steps.append({"step": "model", "changed": model_result["changed"], "ok": True})
        state = {
            "schema": "automa_lab_perception_candidate_state_v0",
            "candidate_id": candidate.candidate_id,
            "prepared_at_ms": int(time.time() * 1000),
            "source_tree_sha256": _candidate_source_hash(candidate.directory),
            "requirements_sha256": _file_sha256(candidate.requirements_path),
            "model_sha256": model_result.get("sha256"),
            "model_size_bytes": model_result.get("size_bytes"),
        }
        (candidate.directory / STATE_FILENAME).write_text(
            json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
        )
        status = candidate_status(candidate)
        if not status["ready"]:
            raise RuntimeError("candidate setup completed but readiness checks still fail")
    except Exception as exc:
        return CommandResult(2, f"Candidate setup failed: {type(exc).__name__}: {exc}")

    payload = {
        "schema": "automa_lab_perception_candidate_setup_v0",
        "candidate": candidate.candidate_id,
        "ready": status["ready"],
        "steps": steps,
        "status": status,
    }
    if json_output:
        return CommandResult(0, json.dumps(payload, indent=2, sort_keys=True))
    changed = [step["step"] for step in steps if step["changed"]]
    summary = f"Candidate {candidate.candidate_id} is ready."
    if changed:
        summary += f" Prepared: {', '.join(changed)}."
    else:
        summary += " Existing runtime and model were reused."
    return CommandResult(0, summary)


class LabPerceptionMapper:
    """Mapper-shaped proxy to a candidate's isolated Python worker."""

    def __init__(self, candidate_id: str, *, timeout_s: float = 180.0) -> None:
        self.candidate = get_candidate(candidate_id)
        self.status = candidate_status(self.candidate)
        if not self.status["ready"]:
            raise RuntimeError(
                f"perception candidate {candidate_id!r} is not ready; "
                f"run {self.status['setup_command']}"
            )
        self.plugin_id = f"lab-candidate:{candidate_id}"
        self.timeout_s = max(1.0, float(timeout_s))
        self._request_index = 0
        self.last_runtime_metrics: dict[str, Any] = {}
        self._scratch = tempfile.TemporaryDirectory(prefix=f"automa_{safe_path_part(candidate_id)}_")
        self._stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            path for path in (str(ROOT), env.get("PYTHONPATH", "")) if path
        )
        self._process: subprocess.Popen[str] | None = subprocess.Popen(
            [
                str(self.candidate.runtime_python),
                "-m",
                WORKER_MODULE,
                "--manifest",
                str(self.candidate.manifest_path),
            ],
            cwd=ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            bufsize=1,
        )

    def __enter__(self) -> "LabPerceptionMapper":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def reset(self) -> None:
        self._request({"command": "reset"})

    def describe_schema(self) -> dict[str, Any]:
        response = self._request({"command": "describe_schema"})
        schema = response.get("schema")
        if not isinstance(schema, dict):
            raise RuntimeError("candidate worker did not return its perception schema")
        return {
            **schema,
            "plugin_id": self.plugin_id,
            "runtime_mapper": f"{self.__class__.__module__}:{self.__class__.__name__}",
            "candidate": self.status,
        }

    def report_descriptor(self) -> dict[str, Any]:
        plugin = self.candidate.manifest.get("plugin") or {}
        return {
            "algorithm": f"candidate:{self.candidate.candidate_id}",
            "candidate": self.candidate.candidate_id,
            "spec": str(plugin.get("entrypoint") or "unknown"),
            "config": dict(plugin.get("config") or {}),
            "source_tree_sha256": self.status["source_tree_sha256"],
            "runtime": self.status["runtime"],
            "model": self.status["model"],
        }

    def perceive(self, request: PerceptionRequest) -> PerceptionText:
        try:
            frame = provide_camera_frame(request, FRONT_CAMERA_RGB_INPUT)
        except PerceptionComponentUnavailable as exc:
            raise RuntimeError(f"front camera unavailable: {exc}") from exc
        image_path = frame.source_path
        if image_path is None or not image_path.is_file():
            image_path = Path(self._scratch.name) / f"{safe_path_part(request.snapshot.read_id)}.png"
            bgr = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR)
            if not cv2.imwrite(str(image_path), bgr):
                raise RuntimeError(f"could not materialize candidate input at {image_path}")
        response = self._request(
            {
                "command": "perceive",
                "image_path": str(image_path),
                "frame_id": request.snapshot.read_id,
                "captured_at_ms": frame.captured_at_ms,
                "output_dir": str(request.output_dir) if request.output_dir is not None else None,
                "metadata": request.metadata,
            }
        )
        perception = response.get("perception")
        if not isinstance(perception, dict):
            raise RuntimeError("candidate worker did not return perception output")
        self.last_runtime_metrics = dict(response.get("runtime") or {})
        return PerceptionText.from_dict(perception)

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None:
            return
        if process.poll() is None:
            try:
                self._request({"command": "stop"}, timeout_s=2.0)
            except Exception:
                process.terminate()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        self._process = None
        self._stderr.close()
        self._scratch.cleanup()

    def _request(self, payload: dict[str, Any], *, timeout_s: float | None = None) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("candidate worker is closed")
        if process.poll() is not None:
            raise RuntimeError(self._worker_failure("candidate worker exited"))
        self._request_index += 1
        request_id = f"request-{self._request_index}"
        command = {**payload, "request_id": request_id}
        process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], timeout_s or self.timeout_s)
        if not ready:
            raise TimeoutError(f"candidate worker timed out after {timeout_s or self.timeout_s:.1f}s")
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(self._worker_failure("candidate worker closed its output"))
        response = json.loads(line)
        if response.get("request_id") != request_id:
            raise RuntimeError("candidate worker returned an unexpected request id")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "candidate worker failed"))
        return response

    def _worker_failure(self, prefix: str) -> str:
        self._stderr.flush()
        self._stderr.seek(0)
        detail = self._stderr.read().strip()
        return f"{prefix}: {detail[-4000:]}" if detail else prefix


def _load_manifest(manifest_path: Path) -> PerceptionCandidate:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"candidate manifest must be an object: {manifest_path}")
    if manifest.get("schema") != LAB_PLUGIN_SCHEMA:
        raise ValueError(f"unsupported candidate schema in {manifest_path}")
    candidate_id = manifest.get("id")
    if not isinstance(candidate_id, str) or not candidate_id or safe_path_part(candidate_id) != candidate_id:
        raise ValueError(f"invalid candidate id in {manifest_path}")
    plugin = manifest.get("plugin")
    if not isinstance(plugin, dict) or not isinstance(plugin.get("entrypoint"), str):
        raise ValueError(f"candidate manifest lacks plugin.entrypoint: {manifest_path}")
    output = manifest.get("output")
    if not isinstance(output, dict) or output.get("schema") != PERCEPTION_TEXT_SCHEMA:
        raise ValueError(f"candidate output must declare {PERCEPTION_TEXT_SCHEMA}: {manifest_path}")
    return PerceptionCandidate(candidate_id, manifest_path.parent, manifest_path, manifest)


def _ensure_runtime(candidate: PerceptionCandidate, *, output: TextIO | None) -> bool:
    configured = str((candidate.manifest.get("runtime") or {}).get("python") or ".venv/bin/python")
    if configured == "core":
        _emit(output, "Runtime: candidate uses the core environment and declares no isolated dependencies.")
        return False
    if candidate.runtime_python.is_file():
        _emit(output, "Runtime: existing isolated environment found.")
        return False
    venv_dir = candidate.runtime_python.parent.parent
    _emit(output, f"Runtime: creating isolated environment at {display_path(venv_dir)}...")
    completed = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(_command_failure("could not create virtual environment", completed))
    _emit(output, "Runtime: ready.")
    return True


def _ensure_dependencies(
    candidate: PerceptionCandidate,
    *,
    output: TextIO | None,
    force: bool = False,
) -> bool:
    requirements = candidate.requirements_path
    if requirements is None:
        _emit(output, "Dependencies: none declared.")
        return False
    if not requirements.is_file():
        raise FileNotFoundError(requirements)
    state = _read_json(candidate.directory / STATE_FILENAME)
    current_hash = _file_sha256(requirements)
    if not force and isinstance(state, dict) and state.get("requirements_sha256") == current_hash:
        _emit(output, "Dependencies: existing installation matches requirements.")
        return False
    _emit(output, "Dependencies: installing candidate requirements...")
    completed = subprocess.run(
        [
            str(candidate.runtime_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(requirements),
        ],
        cwd=candidate.directory,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(_command_failure("dependency installation failed", completed))
    _emit(output, "Dependencies: ready.")
    return True


def _ensure_model(candidate: PerceptionCandidate, *, output: TextIO | None) -> dict[str, Any]:
    model_path = candidate.model_path
    if model_path is None:
        _emit(output, "Model: none declared.")
        return {"changed": False, "sha256": None, "size_bytes": None}
    if model_path.is_file():
        observed_sha256 = _file_sha256(model_path)
        _verify_model_hash(candidate, observed_sha256)
        _emit(output, f"Model: reusing {display_path(model_path)}.")
        return {
            "changed": False,
            "sha256": observed_sha256,
            "size_bytes": model_path.stat().st_size,
        }
    url = (candidate.manifest.get("model") or {}).get("download_url")
    if not isinstance(url, str) or not url:
        raise ValueError("candidate model is missing and no download URL is declared")
    model_path.parent.mkdir(parents=True, exist_ok=True)
    partial = model_path.with_suffix(model_path.suffix + ".partial")
    _emit(output, f"Model: downloading {model_path.name}...")
    digest = hashlib.sha256()
    size = 0
    try:
        with requests.get(url, stream=True, timeout=(10, 120)) as response:
            response.raise_for_status()
            with partial.open("wb") as destination:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    destination.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        partial.replace(model_path)
    finally:
        if partial.exists():
            partial.unlink()
    observed_sha256 = digest.hexdigest()
    try:
        _verify_model_hash(candidate, observed_sha256)
    except Exception:
        model_path.unlink(missing_ok=True)
        raise
    _emit(output, f"Model: ready ({size / (1024 * 1024):.1f} MiB; checksum verified).")
    return {"changed": True, "sha256": observed_sha256, "size_bytes": size}


def _verify_model_hash(candidate: PerceptionCandidate, observed_sha256: str | None) -> None:
    expected = (candidate.manifest.get("model") or {}).get("sha256")
    if isinstance(expected, str) and expected and observed_sha256 != expected:
        raise RuntimeError(
            f"model checksum mismatch: expected {expected}, observed {observed_sha256 or 'none'}"
        )


def _candidate_source_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    ignored_parts = {".venv", "models", "runs", "__pycache__"}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or any(part in ignored_parts for part in path.relative_to(directory).parts):
            continue
        if path.name == STATE_FILENAME:
            continue
        relative = path.relative_to(directory).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _command_failure(prefix: str, completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip()
    return f"{prefix} (exit {completed.returncode}): {detail[-4000:]}"


def _emit(output: TextIO | None, message: str) -> None:
    if output is not None:
        print(message, file=output, flush=True)
