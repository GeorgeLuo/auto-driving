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

## Session separation

The package contains two independent, finalized runner sessions. The
machine-only rehearsal is not relabeled or derived from the interactive run.

| Session | Manifest | Result |
| --- | --- | --- |
| Machine-only rehearsal | `machine-only-session/result.json` | **incomplete by design**; 6/6 machine steps green, US-04 restore/cleanup ok, finalizer ok, HITL not attempted |
| Named HITL run | `runner-session/result.json` | **pass**; named operator `GeorgeLuo`, visual gate and recorded-review adjunct passed |

The retained machine-only session ran from `18:16:19Z` to `18:17:03Z`,
immediately before the named HITL session. It was recorded against behavior
head `37b7393`; its separate identity, cleanup, restore, and finalizer records
are retained under `machine-only-session/`.

## Integrated HITL adjunct

PR #103, originating from issue #101, adds local playback controls to recorded
perception-review artifacts. It is additive to the accepted frontier contract:
the original continuity scenarios remain valid without it, while the fresh
human run now exercises the implementation-discovered review need on the same
recorded frames. No live-view, safety, activation, report-schema, or command
contract changed.

The adjunct is confined to `README.md`,
`cli/automa_cli/perception_evaluation.py`, and
`tests/cli/perception/test_runs.py`. Its direct renderer coverage is the
two-frame selector/escaping case and the zero-frame no-motion case. The
22-test command/run suite does not directly assert missing source/processed
images, no-JavaScript fallback, or external asset absence; those rows are not
claimed as deterministic test coverage here.
When the selected view has no representation, the renderer shows an explicit
empty-view message; it does not fall back to another representation.

## Acceptance anchor and repair ledger

Acceptance breadth remains anchored to the [#88 prospective usage-sequence
catalog and confirmation standard](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5169077892).

| Finding / issue | Disposition | Evidence or implementation |
| --- | --- | --- |
| US-04 restore-integrity P1 from [review 4899909647](https://github.com/GeorgeLuo/auto-driving/pull/100#pullrequestreview-4899909647) | Repaired at the managed-entry owner | [cfd1a3d](https://github.com/GeorgeLuo/auto-driving/commit/cfd1a3d); lstat identity, safe absence cleanup, receipt comparison, and regressions |
| [Issue #101](https://github.com/GeorgeLuo/auto-driving/issues/101) / [PR #103](https://github.com/GeorgeLuo/auto-driving/pull/103) recorded-review playback | Bounded additive adjunct integrated; no live-view or continuity-contract change | [37b7393](https://github.com/GeorgeLuo/auto-driving/commit/37b7393); named-HITL playback exercise and 22-test command/run suite |
| Latest review evidence P1/P2 from [review 4909716246](https://github.com/GeorgeLuo/auto-driving/pull/100#pullrequestreview-4909716246) | Evidence-only reconciliation; no new product defect | Separate machine-only session, narrowed claims, #88 anchor, and this repair ledger |

## Family ledger

| Family | Aggregate | Notes |
| --- | --- | --- |
| `continuity.offline_perception` | **passed** | Content-bound capture lineage, two exclusive applies, and named-human recorded-review playback |
| `continuity.live_config_swap` | **passed** | Named visual confirmation, stop cleanup, and staged-state restoration |
| `continuity.memory_lifecycle` | **passed** | Present/dropout/expiry/reset memory check PASS; movement never commanded |

## Validation and integrity

- 22 focused perception command/run tests passed; 2 directly exercise the new
  recorded-review renderer.
- 69 continuity-contract tests and 43 session-runner tests passed.
- 112 milestone tests passed.
- The full deterministic suite ran 605 tests successfully with 2 expected skips.
- The separate machine-only rehearsal passed all six required steps, restored
  and cleaned up successfully, and finalized independently before HITL.
- The interactive session has zero findings, 55 verified inner digest entries,
  and a finalizer pass against both clean reviewed checkouts; the machine-only
  session has 54 verified inner digest entries and its own finalizer pass.

## Verdict

`pass` — the independent machine-first rehearsal, integrated recorded-review
adjunct, named-operator HITL, cleanup, restoration, and freshness finalization
all passed on the same product head.
