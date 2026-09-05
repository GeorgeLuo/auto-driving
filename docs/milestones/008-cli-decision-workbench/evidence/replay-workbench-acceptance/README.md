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
| Started (UTC) | `2026-09-04T01:16:22.238118Z` |
| Ended (UTC) | `2026-09-04T01:18:00.849056Z` |
| OS | `macOS-26.6.2-arm64-arm-64bit` |
| Browser | `Chrome 152.0.7977.76 headed` |
| auto-driving commit | `e3572d2c875d166efc2d6011384810169e3ce3cb` |
| Worktree | `clean` |
| Image source (redacted) | `<home>/Projects/auto-driving/runtime/vehicles/chase-sim-chaser/bundle/runtime/automation/captures/chase-stream-decision-model-default-45s-20260901-230833` |
| Plugin root | `<repo>/lab/plugins/perception` |
| Loopback URL | `http://127.0.0.1:53043/` |
| Server identity | `workbench-2d29d6df9d2f` |
| First run id | `run-af702ee8f0974eabb15bb5bdfa4fff4f` |
| Second run id | `run-3fa4314708804bacbafe9675fff24037` |
| Failed run id | `run-0969dd4fc26d4f3e9ccd04d217c4d156` |
| Recovered run id | `run-614187c4db7e445294420fa5fc4022f2` |

## Session checklist

Recorded by `record_session.py` from a clean checkout. Playwright Chrome drove
the live page at the operator's request. Paused and running steps each have
per-selection `/api/state` snapshots. Run ids come from those snapshots
because the page does not display `run_id`.

- [x] `page_open` — Page shows source identity, plugin catalog, and declared next actions.
- [x] `inspect_replay` — Ready-plugin replay shows capture, server overlays, progress, and memory on a processed frame.
- [x] `paused_toggle` — Paused toggle including empty raw-capture updates the held still from the server; invalid IDs are refused.
- [x] `running_toggle` — Running toggle including empty keeps the current still until the next processed frame.
- [x] `second_run` — Reset and a second run without restarting the server; prior run identity is not current success.
- [x] `source_failure` — Empty, missing, or unsupported source names the failure and next action; recovery is an operator-chosen directory.
- [x] `cleanup` — Cancel or reset with no worker, simulator, Metrics operation, movement, or recording; isolated state is reset.

Observation-only checks are in `result.json` `observation_only`.

Inspect screenshot asked during `inspect_replay` (not at session end):
captured=`True`, path_redaction=`observed_pass`.

## Findings

[
  {
    "id": "M008-POC-E-001",
    "step": "second_run",
    "classification": "enhancement_candidate",
    "observed": "run_id is not shown on the workbench page. Distinct first, second, failed, and recovered IDs were copied from the recorder /api/state snapshot. Displaying current run identity on the page would avoid a separate state URL. Out of this evidence PR."
  }
]

## Limitations

- The workbench page does not display `run_id`. That is an
  `enhancement_candidate` residual, not a blocker. After identity steps the
  recorder prints a compact `/api/state` snapshot and asks for the run id
  from that snapshot (or `http://127.0.0.1:53043/api/state`).
  Surfacing current run identity on the page would avoid that side channel;
  it is out of this evidence PR.
- `accepted` requires distinct first, second, failed, and recovered run IDs
  on one server identity, a failure payload while the invalid source is
  visible, a recovered run snapshot, and a cropped inspect screenshot whose
  local paths were confirmed excluded.

## Deterministic boundary citations

- `tests/cli/test_workbench.py::test_explicit_catalog_allows_raw_capture_and_live_replacement`
- `tests/cli/test_workbench.py::test_loopback_api_exposes_and_applies_plugin_selection`
- `tests/cli/test_workbench.py::test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv`
- `tests/cli/test_workbench.py::test_cli_replay_machine_readable_boundary`
- `tests/cli/test_workbench.py::test_cli_replay_accepts_realtime_pace`

## Artifacts

See `result.json` `artifacts` and derived [result.html](result.html).
Regenerate HTML with `python3 render_result.py`.
