# Live CLI Operator Acceptance Evidence

Status: **Pass — M007-05 live session complete.**

Accepted contract: [live CLI operator acceptance proposal](../../proposals/live-cli-acceptance.md)
([PR #86](https://github.com/GeorgeLuo/auto-driving/pull/86)), including the
[correlation amendment](../../proposals/live-cli-acceptance-correlation-amendment.md)
([PR #94](https://github.com/GeorgeLuo/auto-driving/pull/94)).

## Verdict

`pass` — interactive live acceptance against the pinned `m007-acceptance`
catalog with human visual confirmation, bound browser-view evidence, proven
bounded-stale view correlation, observation-only authority, no default
recording, and clean worker cleanup. **No acceptance findings** were recorded.
Earlier exploratory PR observations `M007-LIVE-001..005` are reconciled in the
non-gating [exploratory finding ledger](exploratory-findings.md) and do not
affect this verdict.

## Environment receipt

| Field | Value |
| --- | --- |
| Operator | `gluo` |
| Started (UTC) | `2026-08-05T23:47:54.250402Z` |
| Ended (UTC) | `2026-08-05T23:49:50.502903Z` |
| Local timezone | `PST/PDT` |
| OS | `macOS-26.5.1-arm64-arm-64bit` |
| Browser | `Chrome 150.0.7871.188` |
| Metrics UI origin | `http://localhost:5050` |
| Vehicle | `chase-sim-chaser` |
| auto-driving | `caf335797b71df1323736a2054934b7c211418b0` on `m007/live-cli-acceptance` (`clean`) |
| metrics-ui | `722e070fdc9f4ee89d13f947bf3996e62dcb2783` on `m002/04-passive-observation` (`clean`) |
| Catalog | pinned `m007-acceptance` (canonical) |
| Correlation mode | `bounded_stale` derived_lag=`15` bound=`24` |
| Execution mode | `interactive_live` |

## Session checklist

### Baseline

- [x] Record start time, timezone, OS, and browser version.
- [x] Record exact `auto-driving` and `metrics-ui` revisions and worktree state.
- [x] Record the Metrics UI origin and visible Chase game/vehicle state.
- [x] Stop any pre-existing Automa worker and record baseline run directories.

### Discoverability

- [x] Audit `./cli/automa help`.
- [x] Audit `./cli/automa vehicles help`.
- [x] Audit `./cli/automa vehicles automation help`.
- [x] Audit `./cli/automa vehicles automation run --help`.
- [x] Compare the discovered flow with
  [`docs/reference/cli-simulator-perception-journey.md`](../../../../reference/cli-simulator-perception-journey.md).

### Live Journey

- [x] `./cli/automa vehicles status --chase-url http://localhost:5050`
- [x] `./cli/automa vehicles update perception --id chase-sim-chaser --algorithm lightweight_observer`
- [x] `./cli/automa vehicles automation run --id chase-sim-chaser --observe-only --frames 0 --open-view`
- [x] `./cli/automa vehicles status --id chase-sim-chaser`
- [x] Inspect the Automa view and the still-running Metrics UI.
- [x] `./cli/automa vehicles automation stop --id chase-sim-chaser`
- [x] `./cli/automa vehicles status --id chase-sim-chaser`

### Final Reconciliation

- [x] Confirm scenario, playback, control source, and input were preserved.
- [x] Confirm observation-only authority and no applied control.
- [x] Confirm no default recording directory was created.
- [x] Confirm terminal worker and view cleanup.
- [x] Reconcile every PR observation into the ledger or mark it
  `not_reproduced` with the attempted recheck (acceptance findings empty;
  exploratory `M007-LIVE-001..005` in [exploratory-findings.md](exploratory-findings.md)).
- [x] Replace the incomplete result with one internally consistent `pass` or
  `findings` session and verify artifact digests.

## Gate results

| Gate | Status | Summary |
| --- | --- | --- |
| `help_discoverability` | `pass` | vehicles command is listed without implementation jargon |
| `initial_layers` | `pass` | initial_layers: initial layers healthy |
| `staging` | `pass` | staged_layers: staging left worker stopped with deployed perception |
| `startup` | `pass` | Ready for: inspect perception and stop automation; Browser opened |
| `running_layers` | `pass` | view_latest: mode=bounded_stale derived_lag=15 bound=24: correlation proven; running_layers: running layers healthy; authority: observe_only / not_applied / recording=false; view_correlation: mode=bounded_stale derived_lag=15 bound=24: correlation proven; preservation: protected session fields preserved (stable projection) |
| `human_view` | `pass` | Ready for: inspect perception and stop automation; Browser opened |
| `authority` | `pass` | view_latest: mode=bounded_stale derived_lag=15 bound=24: correlation proven; running_layers: running layers healthy; authority: observe_only / not_applied / recording=false; view_correlation: mode=bounded_stale derived_lag=15 bound=24: correlation proven; preservation: protected session fields preserved (stable projection) |
| `correlation` | `pass` | view_latest: mode=bounded_stale derived_lag=15 bound=24: correlation proven; running_layers: running layers healthy; authority: observe_only / not_applied / recording=false; view_correlation: mode=bounded_stale derived_lag=15 bound=24: correlation proven; preservation: protected session fields preserved (stable projection) |
| `default_recording` | `pass` | stopped_layers: stopped layers healthy; default_recording: no new automation run directories; preservation: protected session fields preserved (stable projection) |
| `cleanup` | `pass` | Automation stopped; Ready for: inspect stopped deployment |

## Frame correlation

Proven **bounded_stale** correlation under the accepted amendment bound:

- current camera frame id: `chase_frame_391992`
- perception source frame id: `chase_frame_391977`
- overlay status: `stale`
- derived lag: `15` (claimed `15`)
- bound: `24`
- summary: `mode=bounded_stale derived_lag=15 bound=24: correlation proven`

## Authority and recording

- action policy: `observe_only`
- control application: `not_applied`
- movement applied: `False`
- worker recording flag: `False`
- new automation run directories: `[]`

## Cleanup

- stop exit code: `0`
- worker stopped: `True`
- current-generation view unavailable after stop: `True`
- repository-owned process remaining: `False`
- observed PIDs and liveness: `{'47071': False, '47195': False}`

## Artifact ledger

| Artifact | State |
| --- | --- |
| `result.json` | Present; formal `m007_live_cli_acceptance_v1` **pass** |
| `help-transcript.txt` | Present; four audited help levels |
| `cli-transcript.txt` | Present; ordered CLI transcript |
| `initial-status.json` | Present |
| `running-status.json` | Present |
| `stopped-status.json` | Present |
| `view-publication.json` | Present |
| `browser-view.png` | Present; bound after view health floor |
| `browser-view-meta.json` | Present; import metadata and health floor |
| `runner-session/` | Full HITL runner session (`live_cli_session_result_v0`) with digests and step envelopes |
| `exploratory-findings.md` | Non-gating ledger reconciling PR exploratory observations `M007-LIVE-001..005` |

SHA-256 digests for these artifacts are listed in `result.json` under `artifacts`.

## Finding ledger

### Acceptance findings (M007-05 gate)

None. Formal `result.json` → `findings` is empty. The interactive acceptance
session recorded no acceptance blockers, usability defects on the frozen
journey, or environment blockers.

### Exploratory observations (non-gating)

Confirmed earlier on this PR and retained for later work — **outside** the
M007-05 pass gate:

| ID | Severity | One-line observation | Disposition |
| --- | --- | --- | --- |
| `M007-LIVE-001` | P2 | `perception apply` second-granularity run ids can collide | Confirmed; deferred |
| `M007-LIVE-002` | P2 | `perception candidates` ready ≠ compare model path | Confirmed; deferred |
| `M007-LIVE-003` | P3 | Failed compare dumps full JSON into human output | Confirmed; deferred |
| `M007-LIVE-004` | P3 | No consolidated multi-engine review / auto-open | Confirmed; deferred |
| `M007-LIVE-005` | P3 | `perception run --json` buries review path | Confirmed; deferred |

Full fields (classification, reproduction, owner, recheck): [
`exploratory-findings.md`](exploratory-findings.md). Source:
[PR comment](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199).

## Operator notes

All interactive visual checks were marked **pass** with no free-text notes.
Source human notes: [`runner-session/human-notes.md`](runner-session/human-notes.md).

## Limitations

- Session proves the accepted local Metrics UI + Chase observation-only journey
  at the recorded commits only.
- Correlation used proven bounded-stale lag
  (`15` ≤ `24`), not exact-current.
- No Pi, remote-view, applied-control, or non-idle decision path is claimed.

## How this evidence was produced

Interactive runner command (session artifacts then packaged into this directory):

```text
python3 docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py \
  --catalog docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/m007-acceptance.yaml \
  --metrics-ui-origin http://localhost:5050 \
  --session-dir /tmp/m007-acceptance-20260805-164754 \
  # plus operator/browser identity flags used for the live session
```

Runner result: `pass` with `interactive_human_confirmation=true`,
`browser_view.ok=true`, and all required gates green.
