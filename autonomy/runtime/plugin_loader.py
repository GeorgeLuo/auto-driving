"""Load one step runner from the installed package or a staged controller bundle.

Both vehicle hosts call ``load_runner``. The code source says which copy of
``implementations`` to import. The installed package is the process's own
modules. A staged bundle is swapped in for construction and for later runner
calls, then restored, so ``autonomy`` stays the host's value classes. Callers
do not edit ``sys.modules``.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from autonomy.decision_cycle.activation import StepActivation

# Only this top-level package is swapped. autonomy stays on the host so plugins
# and the cycle share the host's value classes.
BUNDLE_PREFIXES = ("implementations",)
_IMPORT_LOCK = threading.RLock()


@dataclass(frozen=True)
class CodeSource:
    """Where a step's plugins are imported from."""

    bundle_root: Path | None = None
    prefixes: tuple[str, ...] = BUNDLE_PREFIXES
    plugin_sources: tuple[tuple[str, str], ...] = ()

    def executable_activation(self, activation: StepActivation) -> StepActivation:
        """Resolve source assets without changing the recorded activation's identity."""
        if not self.plugin_sources:
            return activation
        sources = dict(self.plugin_sources)
        specs = {}
        for plugin_id, spec in activation.plugin_specs.items():
            path, separator, name = spec.partition(":")
            specs[plugin_id] = f"{sources.get(path, path)}{separator}{name}"
        return replace(activation, plugin_specs=specs)


INSTALLED_PACKAGE = CodeSource()


class StagedBundleImport:
    """Swap one bundle's modules into the process, then restore the host's."""

    def __init__(self, bundle_root: Path, prefixes: tuple[str, ...]) -> None:
        self.bundle_root = str(bundle_root)
        self.prefixes = prefixes
        self.modules: dict[str, Any] = {}

    @contextmanager
    def activate(self) -> Iterator[None]:
        with _IMPORT_LOCK:
            cached = {
                name: module
                for name, module in list(sys.modules.items())
                if self._is_bundle_module(name)
            }
            for name in cached:
                sys.modules.pop(name, None)
            sys.modules.update(self.modules)
            previous_dont_write_bytecode = sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            sys.path.insert(0, self.bundle_root)
            try:
                yield
            finally:
                self.modules = {
                    name: module
                    for name, module in list(sys.modules.items())
                    if self._is_bundle_module(name)
                }
                for name in list(sys.modules):
                    if self._is_bundle_module(name):
                        sys.modules.pop(name, None)
                sys.modules.update(cached)
                try:
                    sys.path.remove(self.bundle_root)
                except ValueError:
                    pass
                sys.dont_write_bytecode = previous_dont_write_bytecode

    def _is_bundle_module(self, name: str) -> bool:
        return any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in self.prefixes
        )


class StagedStep:
    """A step runner whose calls run inside its controller bundle's imports."""

    def __init__(self, runner: Any, import_context: StagedBundleImport) -> None:
        self.runner = runner
        self.import_context = import_context

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        with self.import_context.activate():
            return self.runner(*args, **kwargs)

    @property
    def activation(self) -> StepActivation:
        return self.runner.activation

    @activation.setter
    def activation(self, activation: StepActivation) -> None:
        self.runner.activation = activation

    def __getattr__(self, name: str) -> Any:
        value = getattr(self.runner, name)
        if not callable(value):
            return value

        def invoke_in_bundle(*args: Any, **kwargs: Any) -> Any:
            with self.import_context.activate():
                return value(*args, **kwargs)

        return invoke_in_bundle


def code_source_from_activation(activation: StepActivation) -> CodeSource:
    """The bundle named on the activation, or the installed package."""

    controller_bundle = activation.metadata.get("controller_bundle")
    root = controller_bundle.get("root_dir") if isinstance(controller_bundle, dict) else None
    sources = activation.metadata.get("plugin_sources", {})
    return CodeSource(
        bundle_root=Path(root) if isinstance(root, str) and root else None,
        plugin_sources=tuple(sorted(sources.items())),
    )


def load_runner(activation: StepActivation, *, source: CodeSource | None = None) -> Any:
    """The activation's runner, imported from ``source``.

    Omitting ``source`` uses the activation's recorded or staged code source.
    An explicit source overrides it.
    """

    source = code_source_from_activation(activation) if source is None else source
    from autonomy.decision_cycle.steps import step_runner

    executable = source.executable_activation(activation)
    if source.bundle_root is None:
        runner = step_runner(executable)
        runner.activation = activation
        return runner
    if not source.bundle_root.is_dir():
        raise FileNotFoundError(f"Controller bundle is missing: {source.bundle_root}")
    import_context = StagedBundleImport(source.bundle_root, source.prefixes)
    with import_context.activate():
        runner = step_runner(executable)
        runner.activation = activation
    return StagedStep(runner, import_context)
