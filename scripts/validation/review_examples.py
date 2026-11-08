"""Replay accepted review examples; reports candidate recovery, not a quality score."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from scripts.validation.inspection import related_candidates, revision_sources, unused_candidates


@dataclass(frozen=True)
class Example:
    pr: int
    before: str
    after: str
    path: str
    symbol: str
    related: str | None = None


EXAMPLES = (
    Example(524, "f494c535^", "f494c535", "cli/automa_cli/workbench/runner.py", "ImageReplayRunner._restore_frame_checkpoint"),
    Example(536, "15fca6f4", "1f86333a", "autonomy/runtime/cycle_host.py", "AutonomyCycleHost.last_result"),
    Example(510, "84b0df32^", "84b0df32", "cli/automa_cli/frontend/perception_view.py", "PerceptionView.publish_perception"),
    Example(510, "84b0df32^", "84b0df32", "cli/automa_cli/frontend/perception_view.py", "PerceptionView.retain_frame"),
    Example(538, "e6993f12", "425490f6", "cli/automa_cli/plugins/catalog/service.py", "upload_collection", "upload_plugin"),
)


@lru_cache(maxsize=4)
def sources(ref: str) -> dict[str, str]:
    return revision_sources(ref)[1]


def measure(example: Example, ref: str) -> dict[str, int | bool | None]:
    tree = sources(ref)
    # Limit what counts as changed, while keeping the whole tree available as context.
    unchanged = {path: source for path, source in tree.items() if path != example.path}
    if example.related is None:
        candidates = unused_candidates(unchanged, tree)
        matches = [item for item in candidates if item.path == example.path and item.symbol == example.symbol]
        return {"found": bool(matches), "candidate_count": len(candidates),
                "test_reference_count": sum(len(item.test_references) for item in matches)}
    candidates = related_candidates(unchanged, tree)
    matches = [item for item in candidates if item.changed.path == example.path
               and item.changed.name == example.symbol]
    rank = next((index for index, item in enumerate(matches, 1) if item.related.name == example.related), None)
    return {"found": rank is not None, "rank": rank, "candidate_count": len(candidates)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    rows = []
    for example in EXAMPLES:
        before, after = measure(example, example.before), measure(example, example.after)
        row = {**asdict(example), "before_result": before, "after_result": after}
        rows.append(row)
        print(f"PR #{example.pr} {example.symbol}: before={before}; after={after}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "examples": rows,
        "limitations": [
            "Selected known positives, not a representative precision/recall benchmark.",
            "Related functions may remain similar after correct consolidation; similarity is not a defect verdict.",
            "Candidate counts include other prompts; they need manual labeling before setting gates.",
        ],
    }, indent=2) + "\n", encoding="utf-8")
    return int(any(not row["before_result"]["found"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
