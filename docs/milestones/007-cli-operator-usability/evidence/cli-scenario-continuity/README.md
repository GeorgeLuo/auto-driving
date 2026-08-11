# Realistic CLI scenario continuity evidence

Status: **Interactive HITL complete at behavior head `37b7393`.**

Accepted contract: [cli-scenario-continuity.md](../../proposals/cli-scenario-continuity.md) (PR #99).

## Session

| Field | Value |
| --- | --- |
| Catalog | `m007-continuity` |
| Execution | `interactive_live` |
| Behavior head | `37b7393fe759f1597860a30d8c10ca5692f1c0cc` |
| Named operator | `GeorgeLuo` |
| Machine preflight | **pass** (6/6 required steps) |
| Continuity verdict | **pass** |
| Recorded-review adjunct | **pass** — source/processed/combined selection, scrubbing, and play/pause were exercised on the fresh four-frame recording |
| Live visual gate | **pass** — the named operator confirmed a nonblank, intelligible Automa perception view with the expected overlay/readiness cue |
| US-04 restore | **ok** (full staged snapshot/restore verification) |
| Finalizer | **ok** against the auto-driving tree and Metrics UI checkout |
| Metrics UI | `m002/04-passive-observation` at `722e070fdc9f4ee89d13f947bf3996e62dcb2783`, clean |
| Evidence | `runner-session/browser-view.png`, `result.json`, `human-notes.md`, and command transcripts |

## Integrated HITL adjunct

PR #103, originating from issue #101, adds local playback controls to recorded
perception-review artifacts. It is additive to the accepted frontier contract:
the original continuity scenarios remain valid without it, while the fresh
human run now exercises the implementation-discovered review need on the same
recorded frames. No live-view, safety, activation, report-schema, or command
contract changed.

The adjunct is confined to `README.md`,
`cli/automa_cli/perception_evaluation.py`, and
`tests/cli/perception/test_runs.py`. Its focused validation covers zero- and
one-frame reviews, missing source/processed images, untrusted label escaping,
no-JavaScript fallback content, and the absence of external runtime assets.

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound capture lineage, two exclusive applies, and named-human recorded-review playback |
| `continuity.live_config_swap` | **passed** | Named visual confirmation, stop cleanup, and staged-state restoration |
| `continuity.memory_lifecycle` | **passed** | Present/dropout/expiry/reset memory check PASS; movement never commanded |

## Validation and integrity

- 22 focused recorded-review tests passed.
- 69 continuity-contract tests and 43 session-runner tests passed.
- 112 milestone tests passed.
- The full deterministic suite ran 605 tests successfully with 2 expected skips.
- The fresh machine-only rehearsal passed all six required steps before HITL.
- The interactive session has zero findings, 55 verified inner digest entries,
  and a finalizer pass against both clean reviewed checkouts.

## Verdict

`pass` — machine-first continuity, the integrated recorded-review adjunct,
named-operator HITL, cleanup, restoration, and freshness finalization all passed
on the same product head.
