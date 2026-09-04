# Replay workbench POC acceptance evidence

Status: **incomplete**

Accepted contract:
[Replay workbench POC acceptance](../../proposals/replay-workbench-acceptance.md)
([PR #190](https://github.com/GeorgeLuo/auto-driving/pull/190)).

## Verdict

`none` — PR #191 changes_requested: the prior session did not prompt for distinct first/second/failed/recovered run IDs, did not snapshot source failure before recovery, and asked for the screenshot only at the end. Packet reset pending a rerun with the repaired recorder.

Operator: `none`

## Environment receipt

| Field | Value |
| --- | --- |
| Operator | `none` |
| Started (UTC) | `none` |
| Ended (UTC) | `none` |
| OS | `none` |
| Browser | `none` |
| auto-driving commit | `none` |
| Worktree | `none` |
| Image source (redacted) | `none` |
| Plugin root | `none` |
| Loopback URL | `none` |
| Server identity | `none` |
| First run id | `none` |
| Second run id | `none` |
| Failed run id | `none` |
| Recovered run id | `none` |

## Session checklist

Recorded by `record_session.py`. The operator drove the page; the script
launched the CLI and wrote artifacts. Compact `/api/state` snapshots are
corroboration. The operator types run IDs; the script does not fill them.

- [ ] `page_open` — Page shows source identity, plugin catalog, and declared next actions.
- [ ] `inspect_replay` — Ready-plugin replay shows capture, server overlays, progress, and memory on a processed frame.
- [ ] `paused_toggle` — Paused toggle including empty raw-capture updates the held still from the server; invalid IDs are refused.
- [ ] `running_toggle` — Running toggle including empty keeps the current still until the next processed frame.
- [ ] `second_run` — Reset and a second run without restarting the server; prior run identity is not current success.
- [ ] `source_failure` — Empty, missing, or unsupported source names the failure and next action; recovery is an operator-chosen directory.
- [ ] `cleanup` — Cancel or reset with no worker, simulator, Metrics operation, movement, or recording; isolated state is reset.

Observation-only checks are in `result.json` `observation_only`.

Inspect screenshot asked during `inspect_replay` (not at session end):
captured=`False`, path_redaction=`None`.

## Findings

None recorded.

## Limitations

- The workbench page does not display `run_id`. After identity steps the
  recorder prints a compact `/api/state` snapshot and asks the operator to
  type the run id from that snapshot or `api/state`.
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
