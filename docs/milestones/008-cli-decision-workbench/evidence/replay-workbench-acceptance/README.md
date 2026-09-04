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
| Started (UTC) | `2026-09-04T00:52:44.452208Z` |
| Ended (UTC) | `2026-09-04T00:54:25.560331Z` |
| OS | `macOS-26.6.2-arm64-arm-64bit` |
| Browser | `Chrome 152.0.7977.76 headed` |
| auto-driving commit | `768458edda91082faa868128a9e1337f7d1d5562` |
| Worktree | `dirty` |
| Image source (redacted) | `<home>/Projects/auto-driving/runtime/vehicles/chase-sim-chaser/bundle/runtime/automation/captures/chase-stream-decision-model-default-45s-20260901-230833` |
| Plugin root | `<repo>/lab/plugins/perception` |
| Loopback URL | `http://127.0.0.1:50093/` |
| Server identity | `workbench-c5b968eb46d0` |
| First run id | `run-8c2b647c85954586b9b8e04ea7ef7c0c` |
| Second run id | `run-1e8262ef400e4a3f93269b1561692942` |
| Failed run id | `run-16266be033944c14a3988af516e19088` |
| Recovered run id | `run-9bfbad7f5b634756af7d3c5d216b78c1` |

## Session checklist

Recorded by `record_session.py` with Playwright Chrome driving the live page
at the operator's request. Compact `/api/state` snapshots are the run-id
source because the page does not display `run_id`. Copying those ids from the
printed snapshot is the accepted identity method for this packet.

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
  from that snapshot (or `http://127.0.0.1:50093/api/state`).
  Surfacing current run identity on the page would avoid that side channel;
  it is out of this evidence PR.
- `accepted` requires distinct first, second, failed, and recovered run IDs
  on one server identity, a failure payload while the invalid source is
  visible, a recovered run snapshot, and a cropped inspect screenshot whose
  local paths were confirmed excluded.
- Worktree `dirty` at record time is the in-progress evidence packet.

## Deterministic boundary citations

- `tests/cli/test_workbench.py::test_explicit_catalog_allows_raw_capture_and_live_replacement`
- `tests/cli/test_workbench.py::test_loopback_api_exposes_and_applies_plugin_selection`
- `tests/cli/test_workbench.py::test_loopback_api_persists_after_terminal_state_and_rejects_raw_argv`
- `tests/cli/test_workbench.py::test_cli_replay_machine_readable_boundary`
- `tests/cli/test_workbench.py::test_cli_replay_accepts_realtime_pace`

## Artifacts

See `result.json` `artifacts` and derived [result.html](result.html).
Regenerate HTML with `python3 render_result.py`.
