# QCA M008 Backtest

- schema: `qca/m008-backtest-report/v1`
- analyzer: `0.2.0`
- states: 7
- revisions resolved: `True`
- one analyzer version: `True`

## Hypothesis

A small, interpretable vector of base-to-head observations will make implementation evolution easier to inspect than raw diff size alone.

## Provisional signal assessment

These are experiment findings, not workflow policy or quality grades.

| Signal | Status | Evidence |
| --- | --- | --- |
| `source-class attribution` | `useful` | Separates production, tests, tooling, evidence, and documentation in the M008 gradient. |
| `decision-burden delta` | `useful-context` | Highlights the +583 implementation transition and stays neutral on whether the increase is justified. |
| `changed callables and grouped review targets` | `actionable-with-grouping` | Points agents at changed owners while avoiding one prompt for every simple added callable. |
| `dependency/public-surface deltas` | `promising` | Provides concrete follow-up questions after standard-library imports and constants are de-emphasized. |
| `browser-visible behavior` | `not-covered` | The static Python-first pass cannot explain M008 visual interaction or playback findings. |

## Gradient readings

| State | Base | Head | Files | Core files | Added | Core churn | Decision burden Δ |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `m008-proposal` (medium) | `3e0730d1732e` | `09687f19acd6` | 5 | 1 | 712 | 48 | +0 |
| `plugin-selection-proposal` (medium) | `09687f19acd6` | `118900244780` | 8 | 1 | 1005 | 246 | +4 |
| `plugin-selection-amendment` (small) | `118900244780` | `5cf51585ac79` | 3 | 0 | 99 | 0 | +0 |
| `workbench-implementation` (large) | `b1e97ad8bd9c` | `27b3c343de31` | 24 | 11 | 7534 | 5281 | +583 |
| `poc-acceptance` (large) | `8dca162ee776` | `6c2f26a2ce34` | 10 | 1 | 2750 | 454 | +10 |
| `closeout` (small) | `05d3c3c9ee7c` | `9d3fa1d1334e` | 5 | 0 | 261 | 0 | +0 |
| `cumulative-merge` (small) | `9d3fa1d1334e` | `3fce449d1eb6` | 4 | 0 | 69 | 0 | +0 |

## Operator questions

### `m008-proposal` (medium)

Opening the bounded perception-memory workbench proposal.

Historical outcome (revealed after the reading): Proposal established the initial replay/workbench contract; no product implementation was present yet.

Changed source classes: docs/configuration (4 files, +664/-167); tests (1 files, +48/-0)
Core measured change: 1 files, +48/-0 (48 churn).

- What consumer-visible behavior or boundary does the changed test surface verify?
- Which contract, operator context, or evidence claim changed in the documentation surface?
- Does the spread across source classes match the intended change, or is any work incidental?
- Agent targets: 4 deterministic inspection target(s).

### `plugin-selection-proposal` (medium)

Adding the operator-selected plugin proposal.

Historical outcome (revealed after the reading): Plugin-selection requirements expanded proposal, tests, tooling, and documentation and led to a later live-selection amendment.

Changed source classes: docs/configuration (3 files, +395/-3); tests (1 files, +238/-8); tooling/scripts (4 files, +372/-6)
Core measured change: 1 files, +238/-8 (246 churn).

- What consumer-visible behavior or boundary does the changed test surface verify?
- Which tooling or workflow behavior does this changed script surface support?
- Which contract, operator context, or evidence claim changed in the documentation surface?
- What requirement or control-flow change explains the decision-burden delta (+4)?
- Does the spread across source classes match the intended change, or is any work incidental?
- Agent targets: 9 deterministic inspection target(s).

### `plugin-selection-amendment` (small)

Amending the accepted journey for live plugin selection.

Historical outcome (revealed after the reading): A narrow contract amendment made plugin toggles usable during playback.

Changed source classes: docs/configuration (3 files, +99/-5)
Core measured change: 0 files, +0/-0 (0 churn).

- Which contract, operator context, or evidence claim changed in the documentation surface?
- Agent targets: 3 deterministic inspection target(s).

### `workbench-implementation` (large)

Implementing the replay workbench and its selected seams.

Historical outcome (revealed after the reading): The workbench implementation added the replay UI, plugin selection, source/session handling, and associated tests and lab seams.

Changed source classes: docs/configuration (4 files, +2226/-5); experimental/lab (9 files, +93/-1); production (9 files, +3831/-66); tests (2 files, +1384/-0)
Core measured change: 11 files, +5215/-66 (5281 churn).

- Which changed production callable(s) account for the implementation change, and are they the intended owners?
- What consumer-visible behavior or boundary does the changed test surface verify?
- Which contract, operator context, or evidence claim changed in the documentation surface?
- Is the experimental/lab surface intentionally part of this transition?
- What requirement or control-flow change explains the decision-burden delta (+583)?
- Are the new dependency edges intentional and within the expected ownership direction?
- Which public-surface changes are required by the intended consumer contract?
- Does the spread across source classes match the intended change, or is any work incidental?
- Agent targets: 92 deterministic inspection target(s).

### `poc-acceptance` (large)

Recording the bounded Chrome replay acceptance evidence.

Historical outcome (revealed after the reading): Acceptance added browser-run evidence and test-side validation; most added surface was evidence/documentation rather than product code.

Changed source classes: docs/configuration (2 files, +17/-3); generated/runtime (5 files, +780/-0); tests (1 files, +454/-0); tooling/scripts (2 files, +1499/-0)
Core measured change: 1 files, +454/-0 (454 churn).

- What consumer-visible behavior or boundary does the changed test surface verify?
- Which tooling or workflow behavior does this changed script surface support?
- Which contract, operator context, or evidence claim changed in the documentation surface?
- Which generated or evidence artifacts are expected outputs of this transition?
- What requirement or control-flow change explains the decision-burden delta (+10)?
- Which public-surface changes are required by the intended consumer contract?
- Does the spread across source classes match the intended change, or is any work incidental?
- Agent targets: 11 deterministic inspection target(s).

### `closeout` (small)

Publishing the M008 closeout packet.

Historical outcome (revealed after the reading): Closeout published the milestone packet and durable evidence without changing product behavior.

Changed source classes: docs/configuration (5 files, +261/-11)
Core measured change: 0 files, +0/-0 (0 churn).

- Which contract, operator context, or evidence claim changed in the documentation surface?
- Agent targets: 5 deterministic inspection target(s).

### `cumulative-merge` (small)

Recording the post-closeout reconciliation included in the cumulative merge.

Historical outcome (revealed after the reading): The cumulative merge preserved the accepted work; this selected transition is paperwork/reconciliation, not a second product implementation.

Changed source classes: docs/configuration (4 files, +69/-82)
Core measured change: 0 files, +0/-0 (0 churn).

- Which contract, operator context, or evidence claim changed in the documentation surface?
- Agent targets: 4 deterministic inspection target(s).

The readings are observations; no quality grade or gate is emitted.
