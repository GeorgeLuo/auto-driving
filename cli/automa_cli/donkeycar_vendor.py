from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO


ROOT = Path(__file__).resolve().parents[2]
VENDOR_MANIFEST_PATH = ROOT / "deploy" / "targets" / "donkeycar" / "donkeycar-vendor.json"
VENDOR_STATE_FILE = ".automa-vendor-state.json"


@dataclass(frozen=True)
class DonkeyCarVendorResult:
    checkout_dir: Path
    action: str
    revision: str
    state_path: Path


def load_donkeycar_vendor_manifest() -> dict[str, Any]:
    return json.loads(VENDOR_MANIFEST_PATH.read_text(encoding="utf-8"))


def donkeycar_vendor_source_dir() -> Path:
    manifest = load_donkeycar_vendor_manifest()
    return ROOT / manifest["checkout"]["path"]


def ensure_donkeycar_vendor(*, output: TextIO | None = None, verbose: bool = False) -> DonkeyCarVendorResult:
    manifest = load_donkeycar_vendor_manifest()
    checkout_dir = ROOT / manifest["checkout"]["path"]
    repo_url = manifest["source"]["repo_url"]
    revision = manifest["source"]["revision"]
    state_path = checkout_dir / VENDOR_STATE_FILE
    desired_state = _desired_state(manifest)

    if state_path.exists() and _state_matches(_read_json(state_path), desired_state):
        _emit(output, f"DonkeyCar vendor ready: {_display_path(checkout_dir)}")
        return DonkeyCarVendorResult(checkout_dir, "reused", revision, state_path)

    if checkout_dir.exists():
        existing_head = _git_head(checkout_dir)
        patches = _patch_paths(manifest)
        if existing_head == revision and _patches_are_applied(checkout_dir, patches):
            _write_json(state_path, desired_state)
            _emit(output, f"Registered existing DonkeyCar vendor: {_display_path(checkout_dir)}")
            return DonkeyCarVendorResult(checkout_dir, "registered-existing", revision, state_path)

        if existing_head == revision and _git_tree_is_clean(checkout_dir):
            _apply_patches(checkout_dir, patches, output=output, verbose=verbose)
            _write_json(state_path, desired_state)
            _emit(output, f"Patched existing DonkeyCar vendor: {_display_path(checkout_dir)}")
            return DonkeyCarVendorResult(checkout_dir, "patched-existing", revision, state_path)

        _emit(output, f"Replacing stale DonkeyCar vendor checkout: {_display_path(checkout_dir)}")
        shutil.rmtree(checkout_dir)

    checkout_dir.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", repo_url, str(checkout_dir)], output=output, verbose=verbose)
    _run(["git", "checkout", revision], cwd=checkout_dir, output=output, verbose=verbose)
    _apply_patches(checkout_dir, _patch_paths(manifest), output=output, verbose=verbose)
    _write_json(state_path, desired_state)
    _emit(output, f"Prepared DonkeyCar vendor: {_display_path(checkout_dir)}")
    return DonkeyCarVendorResult(checkout_dir, "created", revision, state_path)


def _desired_state(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "automa_donkeycar_vendor_state_v0",
        "manifest": _display_path(VENDOR_MANIFEST_PATH),
        "repo_url": manifest["source"]["repo_url"],
        "revision": manifest["source"]["revision"],
        "patches": [
            {
                "path": _display_path(path),
                "sha256": _sha256_file(path),
            }
            for path in _patch_paths(manifest)
        ],
    }


def _state_matches(actual: dict[str, Any], desired: dict[str, Any]) -> bool:
    comparable = dict(actual)
    comparable.pop("prepared_at_ms", None)
    return comparable == desired


def _patch_paths(manifest: dict[str, Any]) -> list[Path]:
    return [ROOT / patch["path"] for patch in manifest.get("patches", [])]


def _apply_patches(
    checkout_dir: Path,
    patches: list[Path],
    *,
    output: TextIO | None,
    verbose: bool,
) -> None:
    for patch in patches:
        _run(["git", "apply", "--binary", str(patch)], cwd=checkout_dir, output=output, verbose=verbose)


def _patches_are_applied(checkout_dir: Path, patches: list[Path]) -> bool:
    for patch in patches:
        result = subprocess.run(
            ["git", "apply", "--check", "--reverse", str(patch)],
            cwd=checkout_dir,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            return False
    return True


def _git_head(checkout_dir: Path) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _git_tree_is_clean(checkout_dir: Path) -> bool:
    unstaged = subprocess.run(["git", "diff", "--quiet"], cwd=checkout_dir, check=False)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=checkout_dir, check=False)
    return unstaged.returncode == 0 and staged.returncode == 0


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    output: TextIO | None,
    verbose: bool,
) -> None:
    if verbose:
        _emit(output, "$ " + " ".join(command))
    process = subprocess.run(
        command,
        cwd=cwd or ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if verbose and process.stdout:
        for line in process.stdout.splitlines():
            _emit(output, line)
    if process.returncode != 0:
        raise RuntimeError(
            "\n".join(
                [
                    f"Command failed with exit code {process.returncode}: {' '.join(command)}",
                    process.stdout.strip(),
                ]
            ).strip()
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    payload = {**payload, "prepared_at_ms": int(time.time() * 1000)}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _emit(output: TextIO | None, message: str) -> None:
    if output is not None:
        print(message, file=output, flush=True)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)
