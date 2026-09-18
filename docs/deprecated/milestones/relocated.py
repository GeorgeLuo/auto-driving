"""Resolve parked milestone files without rewriting sealed path strings."""

from __future__ import annotations

from pathlib import Path

_OLD = "/docs/milestones"
_NEW = "/docs/deprecated/milestones"


def repo_root(start: Path) -> Path:
    start = Path(start).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "AGENTS.md").is_file():
            return candidate
        if (candidate / "docs" / "deprecated" / "milestones").is_dir():
            return candidate
        if (candidate / "docs" / "milestones").is_dir():
            return candidate
    raise RuntimeError(f"cannot locate repository root from {start}")


def prefer(path: Path) -> Path:
    """If docs/milestones was moved, follow it. Leave an existing old tree alone."""

    path = Path(path)
    posix = path.as_posix()
    if _NEW in posix:
        return path
    marker = _OLD + "/"
    if marker in posix:
        relocated = Path(posix.replace(marker, _NEW + "/", 1))
    elif posix.endswith(_OLD):
        relocated = Path(posix[: -len(_OLD)] + _NEW)
    else:
        return path
    if path.exists():
        return path
    if relocated.exists():
        return relocated
    return path


def join(root: Path, *parts: str | Path) -> Path:
    return prefer(Path(root).joinpath(*parts))


class Root(type(Path())):
    """Path that follows the parked milestones tree when the old path is gone."""

    def __truediv__(self, key):
        return Root(prefer(Path(self) / key))

    def joinpath(self, *a):
        return Root(prefer(Path(self).joinpath(*a)))

    def resolve(self, *args, **kwargs):
        return Root(Path.resolve(self, *args, **kwargs))


def wrap(path: Path) -> Root:
    return Root(path)
