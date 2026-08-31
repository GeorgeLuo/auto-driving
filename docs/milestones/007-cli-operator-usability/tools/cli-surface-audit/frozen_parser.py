"""Resolve the M007-08 parser from its exact historical Git authority."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable


FROZEN_PARSER_SOURCE_COMMIT = "a989324470e6fc04c9d9678e2337daf47828100b"
FROZEN_APP_SHA256 = "32d76546dd59815202bc71c33f2115683b179adc06d0c7d36849636767e63b89"
FROZEN_SOURCE_ROOTS = ("autonomy", "cli", "implementations")
FROZEN_SOURCE_FILE_COUNT = 100
FROZEN_SOURCE_SET_SHA256 = "12e36a600e2b88a1e800d745fd5dabb645e00b0197aad4afd0b36a17644c74ff"
SOURCE_SET_DIGEST_PREFIX = b"m007_frozen_parser_source_v1\0"


class FrozenParserError(Exception):
    """Historical parser source could not be proven or executed."""


def _bounded_output(value: object, *, limit: int = 2000) -> str:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value or "")
    text = text.strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


class FrozenParserSource:
    """Materialize parser dependencies from one exact local Git commit."""

    def __init__(
        self,
        repo_root: Path,
        *,
        git_runner: Callable[..., Any] | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.commit = FROZEN_PARSER_SOURCE_COMMIT
        self._git_runner = git_runner or subprocess.run

    def _run_git(self, args: list[str]) -> Any:
        command = ["git", "--no-replace-objects", *args]
        try:
            return self._git_runner(
                command,
                cwd=self.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise FrozenParserError(
                f"cannot invoke Git for frozen parser resolution: {exc}"
            ) from exc

    def _archive_bytes(self) -> bytes:
        commit_result = self._run_git(["cat-file", "-e", f"{self.commit}^{{commit}}"])
        if getattr(commit_result, "returncode", 1) != 0:
            raise FrozenParserError(
                f"frozen parser commit is missing or unusable: {self.commit}"
            )
        if getattr(commit_result, "stdout", None) != b"" or getattr(
            commit_result, "stderr", None
        ) != b"":
            raise FrozenParserError(
                f"frozen parser commit resolution was not exact: {self.commit}"
            )

        archive_result = self._run_git(
            [
                "archive",
                "--format=tar",
                self.commit,
                "--",
                *FROZEN_SOURCE_ROOTS,
            ]
        )
        archive = getattr(archive_result, "stdout", None)
        if (
            getattr(archive_result, "returncode", 1) != 0
            or getattr(archive_result, "stderr", None) != b""
            or not isinstance(archive, bytes)
            or not archive
        ):
            detail = _bounded_output(getattr(archive_result, "stderr", b""))
            suffix = f": {detail}" if detail else ""
            raise FrozenParserError(
                f"frozen parser archive is unreadable for {self.commit}{suffix}"
            )
        return archive

    @staticmethod
    def _member_path(member: tarfile.TarInfo, destination: Path) -> Path:
        name = member.name
        if not name or "\x00" in name or "\\" in name or name.startswith("/"):
            raise FrozenParserError(
                f"frozen parser archive path is unsafe: {name!r}"
            )
        trimmed = name.rstrip("/")
        parts = trimmed.split("/")
        if not trimmed or any(part in {"", ".", ".."} for part in parts):
            raise FrozenParserError(
                f"frozen parser archive path is unsafe: {name!r}"
            )
        pure = PurePosixPath(trimmed)
        if pure.is_absolute() or pure.parts[0] not in FROZEN_SOURCE_ROOTS:
            raise FrozenParserError(
                f"frozen parser archive path is outside admitted roots: {name!r}"
            )
        target = destination.joinpath(*pure.parts).resolve()
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise FrozenParserError(
                f"frozen parser archive path escapes destination: {name!r}"
            ) from exc
        return target

    def materialize(self, destination: Path) -> Path:
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()):
            raise FrozenParserError(
                f"frozen parser destination must be empty: {destination}"
            )

        try:
            archive = tarfile.open(fileobj=io.BytesIO(self._archive_bytes()), mode="r:")
        except (tarfile.TarError, OSError) as exc:
            raise FrozenParserError(f"frozen parser archive is malformed: {exc}") from exc

        archive_members: set[str] = set()
        extracted_files: set[str] = set()
        try:
            for member in archive.getmembers():
                target = self._member_path(member, destination)
                normalized_name = PurePosixPath(member.name.rstrip("/")).as_posix()
                if normalized_name in archive_members:
                    raise FrozenParserError(
                        f"frozen parser archive has duplicate member: {normalized_name}"
                    )
                archive_members.add(normalized_name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise FrozenParserError(
                        f"frozen parser archive member is not a regular file: {member.name}"
                    )
                source = archive.extractfile(member)
                if source is None:
                    raise FrozenParserError(
                        f"frozen parser archive member is unreadable: {member.name}"
                    )
                raw = source.read()
                if len(raw) != member.size:
                    raise FrozenParserError(
                        f"frozen parser archive member is truncated: {member.name}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                extracted_files.add(normalized_name)
        finally:
            archive.close()

        app_rel = "cli/automa_cli/app.py"
        if app_rel not in extracted_files:
            raise FrozenParserError(
                f"frozen parser archive is missing required source: {app_rel}"
            )
        app_digest = hashlib.sha256((destination / app_rel).read_bytes()).hexdigest()
        if app_digest != FROZEN_APP_SHA256:
            raise FrozenParserError(
                f"frozen parser source hash mismatch for {app_rel}: "
                f"expected {FROZEN_APP_SHA256}, got {app_digest}"
            )
        if len(extracted_files) != FROZEN_SOURCE_FILE_COUNT:
            raise FrozenParserError(
                "frozen parser source set file count mismatch: "
                f"expected {FROZEN_SOURCE_FILE_COUNT}, got {len(extracted_files)}"
            )
        source_set_digest = hashlib.sha256()
        source_set_digest.update(SOURCE_SET_DIGEST_PREFIX)
        for relative in sorted(extracted_files):
            source_set_digest.update(relative.encode("utf-8"))
            source_set_digest.update(b"\0")
            source_set_digest.update(
                hashlib.sha256((destination / relative).read_bytes()).digest()
            )
        actual_source_set_digest = source_set_digest.hexdigest()
        if actual_source_set_digest != FROZEN_SOURCE_SET_SHA256:
            raise FrozenParserError(
                "frozen parser source set hash mismatch: "
                f"expected {FROZEN_SOURCE_SET_SHA256}, got {actual_source_set_digest}"
            )
        return destination


_CHILD_BOOTSTRAP = r"""
import importlib.util
import json
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
repo_root = Path(sys.argv[2]).resolve()
validator_path = Path(sys.argv[3]).resolve()
sys.path.insert(0, str(source_root))
sys.path.insert(1, str(validator_path.parent))

spec = importlib.util.spec_from_file_location("m007_cli_surface_audit_child", validator_path)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load current CLI-surface validator")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from cli.automa_cli.app import build_parser

try:
    result = module._run_audit_with_parser(repo_root=repo_root, parser=build_parser())
except module.AuditError as exc:
    sys.stderr.write(str(exc))
    raise SystemExit(2)
sys.stdout.write(json.dumps(result, sort_keys=True))
"""


def run_frozen_parser_audit(
    *,
    repo_root: Path,
    validator_path: Path,
    source: FrozenParserSource | None = None,
    child_runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run current artifact validation against the frozen parser in a child process."""

    repo_root = Path(repo_root).resolve()
    validator_path = Path(validator_path).resolve()
    source = source or FrozenParserSource(repo_root)
    runner = child_runner or subprocess.run

    with tempfile.TemporaryDirectory(prefix="m007-cli-parser-") as directory:
        source_root = source.materialize(Path(directory))
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment.pop("PYTHONPATH", None)
        for key in tuple(environment):
            if key.startswith("COVERAGE_"):
                environment.pop(key, None)
        command = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _CHILD_BOOTSTRAP,
            str(source_root),
            str(repo_root),
            str(validator_path),
        ]
        try:
            completed = runner(
                command,
                cwd=repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=120,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise FrozenParserError("frozen parser audit child timed out") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise FrozenParserError(
                f"cannot execute frozen parser audit child: {exc}"
            ) from exc

    stdout = getattr(completed, "stdout", None)
    stderr = getattr(completed, "stderr", None)
    if (
        getattr(completed, "returncode", 1) != 0
        or stderr != b""
        or not isinstance(stdout, bytes)
    ):
        detail = _bounded_output(stderr or stdout)
        suffix = f": {detail}" if detail else ""
        raise FrozenParserError(f"frozen parser audit child failed{suffix}")
    try:
        result = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenParserError("frozen parser audit child returned malformed JSON") from exc
    if not isinstance(result, dict) or set(result) != {"report", "rollup"}:
        raise FrozenParserError("frozen parser audit child returned an invalid result")
    return result
