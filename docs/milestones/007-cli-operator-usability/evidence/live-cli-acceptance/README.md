# Live CLI Operator Acceptance Evidence

Status: **Draft — session not started.** This scaffold makes no M007-05
acceptance claim.

Accepted contract: [live CLI operator acceptance proposal](../../proposals/live-cli-acceptance.md)
([PR #86](https://github.com/GeorgeLuo/auto-driving/pull/86)).

## How We Will Use This PR

The operator uses the public CLI and browser surfaces normally and reports each
observation in the PR as it occurs. The evidence recorder captures command
output, machine state, and bounded browser evidence, then reconciles confirmed
observations into the finding ledger below. The operator does not need to
format evidence or fill this document manually.

Product fixes do not enter this PR. A confirmed defect remains evidence and is
routed to its owning repository for separately reviewed repair.

## Session Checklist

### Baseline

- [ ] Record start time, timezone, OS, and browser version.
- [ ] Record exact `auto-driving` and `metrics-ui` revisions and worktree state.
- [ ] Record the Metrics UI origin and visible Chase game/vehicle state.
- [ ] Stop any pre-existing Automa worker and record baseline run directories.

### Discoverability

- [ ] Audit `./cli/automa help`.
- [ ] Audit `./cli/automa vehicles help`.
- [ ] Audit `./cli/automa vehicles automation help`.
- [ ] Audit `./cli/automa vehicles automation run --help`.
- [ ] Compare the discovered flow with
  [`docs/reference/cli-simulator-perception-journey.md`](../../../../reference/cli-simulator-perception-journey.md).

### Live Journey

Run and assess these commands one at a time:

- [ ] `./cli/automa vehicles status --chase-url http://localhost:5050`
- [ ] `./cli/automa vehicles update perception --id chase-sim-chaser --algorithm lightweight_observer`
- [ ] `./cli/automa vehicles automation run --id chase-sim-chaser --observe-only --frames 0 --open-view`
- [ ] `./cli/automa vehicles status --id chase-sim-chaser`
- [ ] Inspect the Automa view and the still-running Metrics UI.
- [ ] `./cli/automa vehicles automation stop --id chase-sim-chaser`
- [ ] `./cli/automa vehicles status --id chase-sim-chaser`

The recorder also captures the targeted JSON status documents and the healthy
view's `/api/latest` response required by the accepted proposal.

### Final Reconciliation

- [ ] Confirm scenario, playback, control source, and input were preserved.
- [ ] Confirm observation-only authority and no applied control.
- [ ] Confirm no default recording directory was created.
- [ ] Confirm terminal worker and view cleanup.
- [ ] Reconcile every PR observation into the ledger or mark it
  `not_reproduced` with the attempted recheck.
- [ ] Replace the incomplete result with one internally consistent `pass` or
  `findings` session and verify artifact digests.

## Artifact Ledger

| Artifact | State |
| --- | --- |
| `result.json` | Present; explicitly incomplete until the session finishes |
| `help-transcript.txt` | Pending session |
| `cli-transcript.txt` | Pending session |
| `initial-status.json` | Pending session |
| `running-status.json` | Pending session |
| `stopped-status.json` | Pending session |
| `view-publication.json` | Pending session |
| `browser-view.png` | Pending successful live view |
| `finding-<id>.png` | Optional; only when needed for a visual finding |

## Finding Ledger

No findings have been recorded; the session has not started.

Confirmed findings will use stable `M007-LIVE-###` identifiers and record the
classification, severity, affected surface, command or procedure step,
expected and observed behavior, reproduction, evidence, owner, operator impact,
disposition, and required recheck.

## Verdict

`incomplete` — the tracked operator session has not started.
