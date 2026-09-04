# Replay workbench POC acceptance evidence

Status: **accepted**

Accepted contract:
[Replay workbench POC acceptance](../../proposals/replay-workbench-acceptance.md)
([PR #190](https://github.com/GeorgeLuo/auto-driving/pull/190)).

## Verdict

`accepted` — accepted

Operator: `gluo`

## Environment receipt

| Field | Value |
| --- | --- |
| Operator | `gluo` |
| Started (UTC) | `2026-09-03T23:53:17.227764Z` |
| Ended (UTC) | `2026-09-03T23:54:35.311055Z` |
| OS | `macOS-26.6.2-arm64-arm-64bit` |
| Browser | `Chrome 152.0.7977.76` |
| auto-driving commit | `ade36f37d02302d0d8d206cb3fd4efff1ec796fd` |
| Worktree | `dirty` |
| Image source (redacted) | `<home>/Projects/auto-driving/runtime/vehicles/chase-sim-chaser/bundle/runtime/automation/captures/chase-stream-decision-model-default-45s-20260901-230833` |
| Plugin root | `<repo>/lab/plugins/perception` |
| Loopback URL | `http://127.0.0.1:62908/` |
| Server identity | `workbench-743d40b45bc2` |
| Launch run id | `run-2e29263e3d26480aa47c0e10256914e5` |

## Session checklist

Recorded by `record_session.py`. The operator drove the page; the script
launched the CLI and wrote artifacts. Compact `/api/state` snapshots after each
step are corroboration only; they do not override the operator answers.

- [x] `page_open` — Page shows source identity, plugin catalog, and declared next actions.
- [x] `inspect_replay` — Ready-plugin replay shows capture, server overlays, progress, and memory on a processed frame.
- [x] `paused_toggle` — Paused toggle including empty raw-capture updates the held still from the server; invalid IDs are refused.
- [x] `running_toggle` — Running toggle including empty keeps the current still until the next processed frame.
- [x] `second_run` — Reset and a second run without restarting the server; prior run identity is not current success.
- [x] `source_failure` — Empty, missing, or unsupported source names the failure and next action; recovery is an operator-chosen directory.
- [x] `cleanup` — Cancel or reset with no worker, simulator, Metrics operation, movement, or recording; isolated state is reset.

Observation-only checks (operator `y`, `occurred: false`): vehicle, Automa
worker, simulator, Metrics operation, movement, recording.

## Findings

None recorded.

## Limitations

- Worktree `dirty` at record time is the in-progress evidence packet, not a
  product edit.
- `cli-transcript.txt` is the launch banner only (`phase: running`,
  `progress: 0/845`). The workbench process stays open after that.
- `browser-view.png` is the cropped live still: chase capture frame with
  `classical_regions` overlays, memory ledger, Evidence `ok`, progress 18/845.
- Compact `/api/state` snapshots after every prompted step share one completed
  run (`run-2e29263e3d26480aa47c0e10256914e5`, 845/845, `phase: completed`).
  They do not list distinct first, second, failed, and recovered run IDs. The
  operator still recorded `observed_pass` for those steps. Those snapshots were
  not used to change the verdict.

## Deterministic boundary citations

Invalid-ID and shared-runner claims stay on the existing tests, not this
session:

- `tests/cli/test_workbench.py::test_explicit_catalog_allows_raw_capture_and_live_replacement`
- `tests/cli/test_workbench.py::test_loopback_api_exposes_and_applies_plugin_selection`
- `tests/cli/test_workbench.py::test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv`
- `tests/cli/test_workbench.py::test_cli_replay_machine_readable_boundary`
- `tests/cli/test_workbench.py::test_cli_replay_accepts_realtime_pace`

## Artifacts

See `result.json` `artifacts` and derived [result.html](result.html).
Regenerate HTML with `python3 render_result.py`.
