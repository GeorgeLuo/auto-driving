"""Labels and diagnostics for supplied step activation records."""
from __future__ import annotations


def format_activation_problems(problems: list[dict[str, str]]) -> str:
    """Render the same step-specific recovery for startup and status callers."""

    return "\n".join(
        line
        for problem in problems
        for line in (
            f"Invalid {problem['step']} activation: {problem['activation']}",
            f"Reason: {problem['reason']}",
            f"Replace it with the step's default: {problem['command']}",
        )
    )


def selection_label(preset: str | None, plugins: list[str]) -> str:
    """A selection as deploy, run and status print it: its preset and plugins."""

    listed = ", ".join(plugins)
    return f"{preset} ({listed or 'no plugins'})" if preset else listed or "(no plugins)"


def step_lines(steps: dict[str, list[str]], presets: dict[str, str]) -> list[str]:
    return [
        f"{step.capitalize()}: {selection_label(presets.get(step), plugins)}"
        for step, plugins in steps.items()
    ]
