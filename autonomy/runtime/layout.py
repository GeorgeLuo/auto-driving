"""Host-side controller paths, independent of installation and transport."""

from dataclasses import dataclass
from pathlib import Path

from autonomy.decision_cycle.activation import STEPS, step_activation_path


@dataclass(frozen=True)
class RuntimeLayout:
    root: Path

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    def activation(self, step: str) -> Path:
        return step_activation_path(self.runtime, step)

    def bundle_paths(self) -> dict[str, str]:
        implementations = self.root / "implementations"
        return {
            "root_dir": str(self.root),
            "autonomy_dir": str(self.root / "autonomy"),
            "implementations_dir": str(implementations),
            "perception_dir": str(implementations / "decision_cycle" / "perception"),
            "runtime_dir": str(self.runtime),
            **{f"{step}_runtime_dir": str(self.runtime / step) for step in STEPS},
        }
