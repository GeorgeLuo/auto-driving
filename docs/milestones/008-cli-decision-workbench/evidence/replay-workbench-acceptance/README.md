# Replay workbench POC acceptance evidence

Status: **incomplete** — first-cut packet. The live session and operator
verdict are not recorded yet.

Accepted contract:
[Replay workbench POC acceptance](../../proposals/replay-workbench-acceptance.md)
([PR #190](https://github.com/GeorgeLuo/auto-driving/pull/190)).

This directory is the only product of the evidence implementation PR. It does
not change the workbench. It does not rewrite
[the M008 assessment](../../assessment/perception-memory-workbench.md).

## Verdict

`incomplete` — environment receipt, procedure log, screenshot, and named
operator judgment are still open.

Fill this packet during one guided local session against a clean milestone
checkout that contains merged PR #174. Hands-on comments from #174 are
context, not this verdict.

## Environment receipt

| Field | Value |
| --- | --- |
| Operator | _pending_ |
| Started (UTC) | _pending_ |
| Ended (UTC) | _pending_ |
| OS | _pending_ |
| Browser | _pending_ |
| auto-driving commit | _pending_ |
| Worktree | _pending_ |
| Image source (redacted) | _pending_ |
| Plugin root | `lab/plugins/perception` or packaged default |
| Loopback URL | _pending_ |

## How to record a session

The operator still drives the page. This script launches the same CLI, prompts
after each checklist step, snapshots `/api/state`, and writes the packet. It
does not click the page or infer a visual pass.

From the repository root:

```sh
python3 docs/milestones/008-cli-decision-workbench/evidence/replay-workbench-acceptance/record_session.py \
  --source-dir /path/to/real-capture \
  --operator "$USER" \
  --browser-name Chrome \
  --browser-version "paste from chrome://version"
```

Optional: `--screenshot /path/to/cropped.png`, `--packaged` for the default
catalog, `--plugin-dir` / `--plugin` to match the accepted launch.

After each printed `do` block, use the workbench, then answer `y` / `n` / `u`
and optional notes. At the end, give `accepted`, `blocked`, or `incomplete`.
The script overwrites `result.json`, this README, `cli-transcript.txt`, and
regenerates `result.html`.

## Session checklist

Manual equivalent (if not using `record_session.py`):

```sh
./cli/automa vehicles workbench replay <source_dir> \
  --plugin-dir lab/plugins/perception \
  --plugin classical_regions \
  --pace realtime \
  --max-frames 1024 \
  --open
```

The source must be a real readable image directory. `--json` may corroborate
state; it is not the operator display.

### Environment

- [ ] Record UTC start, OS, browser name/version.
- [ ] Record exact `auto-driving` commit and clean/dirty state.
- [ ] Name the local image directory and plugin root.
- [ ] Record the printed loopback URL.

### Primary demonstration

- [ ] Page opened with source identity, plugin catalog, and declared next actions.
- [ ] Replay started with a ready plugin selection; capture, overlays, progress, and memory are visible on a processed frame.
- [ ] Paused; toggling ready plugins including empty raw-capture updates the held still from the server.
- [ ] Invalid plugin IDs are refused without changing the effective set.
- [ ] Resume or step: a running toggle, including empty, keeps the current still until the next processed frame.
- [ ] Reset isolated memory and start a second run without restarting the server.

### Failure, recovery, cleanup

- [ ] Empty, missing, or unsupported source shows a named failure and next action.
- [ ] Recovery uses an operator-chosen valid directory; no silent substitute.
- [ ] Cancel or reset; no vehicle, worker, simulator, Metrics operation, movement, or recording.
- [ ] Isolated mapper/memory state is reset.

### Final reconciliation

- [ ] Capture one cropped `browser-view.png` of the inspected still.
- [ ] Optional `cli-transcript.txt` of launch/help/status, with local prefixes redacted.
- [ ] Operator records `accepted`, `blocked`, or `incomplete` in `result.json` and this README.
- [ ] Regenerate `result.html` from the committed `result.json`:
      `python3 render_result.py`
- [ ] Refresh artifact SHA-256 digests in `result.json` and re-render HTML.

## Artifacts

| Path | Status |
| --- | --- |
| [result.json](result.json) | Present; `status=incomplete` |
| [result.html](result.html) | Derived from `result.json` |
| [render_result.py](render_result.py) | Regenerates `result.html` |
| [record_session.py](record_session.py) | Prompt-driven recorder |
| `browser-view.png` | Not captured |
| `cli-transcript.txt` | Not captured |

## Findings

None yet. Confirmed discrepancies use ids `M008-POC-###` with classification
`acceptance_blocker`, `enhancement_candidate`, or `environment_blocker`.
