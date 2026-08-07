# Proposal: Realistic CLI scenario continuity

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Realistic CLI scenario continuity |
| Proposal branch | `m007/scenario-continuity-proposal` |
| Implementation branch | `m007/scenario-continuity` |
| Exit criterion | M007-10 |

## Review Question

After the primary six-step journey is live-accepted, can the repository-owned
live CLI session runner declare and execute representative safe multi-command
sequences beyond that journey—anchored to the #88 candidate catalog families
(offline perception feedback / US-03-class; live configuration or plugin swap
with restoration / US-04-class; perception→memory lifecycle / US-05 and
US-08-class; and, when prerequisites allow, ablation, temporal backpressure,
and deterministic replay / US-06, US-07, US-09-class)—with machine-first
execution, one primary human-scannable confirmation per sequence, durable
findings with owners, and only the bounded product or operator-facing repairs
those sequences prove necessary?

This is a continuity unit after [PR #88](https://github.com/GeorgeLuo/auto-driving/pull/88)
and plan revision [#98](https://github.com/GeorgeLuo/auto-driving/pull/98). It
reuses the live CLI session runner. It does not reopen M007-05 or re-prove
US-01/US-02 as the acceptance gate.

## Proposed Contract

### Acceptance breadth (frozen families)

The implementation must declare runner catalogs (or catalog extensions) that
cover these **representative families** from the #88 prospective usage-sequence
catalog
([comment](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5169077892)):

| Family | #88 ids (class) | Minimum ownership |
| --- | --- | --- |
| Offline perception feedback | US-03-class | Capture-once then apply and/or compare on the same frames with a compact human comparison surface |
| Live configuration / plugin swap with restoration | US-04-class | Observation-only restage or plugin change, restart to a healthy view, restore prior activation; full transactional trial UX (#91) is not required |
| Perception → memory lifecycle | US-05-class and US-08-class | Memory check or equivalent concise PASS/FAIL; reset/repopulation with human-scannable key/epoch confirmation where applicable |
| Optional stress families | US-06, US-07, US-09-class | Ablation, temporal backpressure, deterministic replay — include when prerequisites are available; otherwise record `blocked` / `partial` with owner and reason |
| Out of gate | US-01, US-02 | Already owned by M007-05; not re-proven for M007-10 |
| Deferred | US-10-class | Physical-check qualification when labeled input is unavailable |

A thin help/status-only catalog that never exercises perception/memory feedback
loops **cannot** satisfy M007-10 even if it is multi-command and machine-green.

Partial or blocked family outcomes are allowed only when the proposal’s
evidence records an explicit owner, reason, and whether a product repair,
external dependency, or later feature unit owns the gap. Silent omission is a
failure.

### Confirmation standard (every declared sequence)

Each declared sequence must record:

- operator question and prerequisites;
- exact current public commands (human-first argv);
- safety class and side effects;
- unconditional cleanup / restoration steps;
- **one primary human-scannable confirmation**: either a concise CLI verdict
  (for example `PASS`/`FAIL`, `Ready for:`, `Deterministic: yes`,
  `Keys: N -> 0`) or a launched frontend / generated review surface;
- optional secondary cue;
- optional JSON or recorded artifacts as supporting evidence only;
- status: `passed`, `partial`, `blocked`, `finding`, or `incomplete`.

Operator-facing rules:

1. A person must not need to parse raw JSON to decide success.
2. A record path, digest, or raw payload is never the sole human success signal.
3. Live automation used for visual inspection includes `--open-view` when a
   perception/memory view is the primary visual surface.
4. The sequence names the exact cue before it is run.
5. Cleanup and restored staged configuration are part of the visible outcome
   when the sequence mutates staging or a worker.

### Execution procedure

1. **Declare** sequences in session-runner YAML/JSON catalogs under the existing
   live CLI session runner tools directory (new continuity catalog(s) and/or
   reviewed extensions; do not invent a second harness).
2. **Machine-first:** run each required sequence non-interactively / machine-only
   where the runner supports it, and record machine verdicts.
3. **HITL elevation:** only machine-green sequences that declare visual judgment
   require interactive human pass/fail on the primary confirmation surface.
4. **Findings:** confirmed discrepancies use stable `M007-LIVE-###` (or
   continuity-specific) ids with classification, severity, owner, disposition.
5. **Repairs (bounded):** product or CLI-output changes are allowed only for
   surfaces listed under **Bounded repair set** below, or for defects the
   declared sequences newly prove that remain within non-goals (no #90/#91
   feature delivery). Material defects outside the bound get durable disposition
   and, when they change the contract, a proposal amendment or separate unit.
6. **Evidence PR:** commit catalogs, session artifacts, digests, README ledger,
   and any repair diffs in the implementation unit. Incomplete environment →
   `incomplete`, not a false pass.

### Bounded repair set

The proposal authorizes implementation to repair or harden only what blocks
safe execution or human-scannable confirmation of the frozen families:

| Source | Surface | Intent |
| --- | --- | --- |
| #89 / M007-LIVE-001 | `perception apply` run-id / record directory identity | Collision-resistant exclusive record dirs |
| M007-LIVE-002 | `perception candidates` readiness vs compare model path | Ready means execution-time resolver can run |
| M007-LIVE-003 | `perception compare` human failure output | One-line structured failure; full detail in JSON/verbose |
| M007-LIVE-005 | `perception run` human vs `--json` surfaces | Compact human default; review path prominent |
| M007-LIVE-004 (partial) | review path discoverability | May launch or print a single clear review path; **must not** require a new consolidated multi-engine product (#90) |
| Sequence-forced CLI output | Concise verdict lines for memory check / reset / replay | Only as needed for the confirmation standard |

**Not authorized as required deliverables:** #90 same-frame experiment matrices,
#91 transactional live trials as a full feature, Metrics UI product redesign
(#93), movement/hardware leaves, coverage collector, full leaf inventory.

### Environment and safety

- Observation-only action policy; no applied vehicle movement.
- Metrics UI Chase already available; no hidden `simulators ensure` as part of
  a “pass.”
- Precondition stop of stray Automa workers before baseline.
- Record exact `auto-driving` and `metrics-ui` identities (clean or named diff).
- Redact local absolute paths, secrets, and unrelated browser content.

### Evidence artifacts (implementation-owned)

Tracked under a new evidence directory, for example:

`docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/`

Minimum contents:

| Path | Contract |
| --- | --- |
| `README.md` | Environment receipt, family/sequence ledger, confirmation results, repair list, findings, verdict, link to #88 catalog |
| `result.json` | Machine-readable continuity result: per-sequence status, machine vs human, digests, findings, repairs applied |
| Runner session dir(s) | Full session-runner outputs for machine-only and HITL runs |
| Catalogs as committed | The exact YAML/JSON catalogs executed (or digests + pinned path under `tools/live-cli-session-runner/catalogs/`) |
| Repair notes | Map of applied code changes to finding ids / issue numbers |

`result.json` records `pass`, `findings`, or `incomplete`. A pass requires every
**required** family to be `passed` or an explicitly allowed `partial` with
owner that does not leave the family unrepresented. A thin catalog pass is
forbidden.

### Relation to the session runner

- Keep `m007-acceptance` / pinned acceptance catalog immutable for M007-05
  posterity unless a separate amendment is reviewed.
- Continuity catalogs are a distinct track (`exploratory` or a new
  `continuity` track if the runner needs one) so they cannot claim M007-05.
- Prefer extending the existing runner over parallel tooling.

### Success / failure outcomes

| Outcome | Meaning |
| --- | --- |
| `pass` | Required families represented; machine-first complete; HITL complete where required; confirmation standard held; findings disposed; only bounded repairs applied; digests consistent |
| `findings` | Representative sequences ran; blocking product issues remain with ledger; M007-10 not Met until repaired and re-run or exceptional block reviewed |
| `incomplete` | Environment/identity/session abandoned; no Met claim |

## Ownership

| Boundary | Owner in this unit |
| --- | --- |
| Catalog declaration and runner procedure | Continuity implementation + session runner |
| Human visual / scannable confirmation | Named operator in the evidence receipt |
| Machine gates and digests | Session runner + result schema |
| Bounded product/CLI-output repairs | Automa CLI ownership for listed surfaces |
| Metrics UI product redesign | Out of unit (#93 external) |
| Large experiment features | Out of unit (#90/#91) |
| Milestone transition | Reviewed successful handoff template |

## Affected Paths

| Path / surface | Role |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/` | Catalogs, optional runner extensions for multi-sequence continuity |
| `docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/` | Tracked evidence |
| `cli/automa_cli/` (bounded) | Apply run-id, candidates readiness, compare/run human output as listed |
| `tests/` (focused) | Deterministic tests for catalog load, confirmation schema, and authorized repairs |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | Workflow transitions only |

## Adversarial Matrix

| Attempted bypass | Required response |
| --- | --- |
| Help/status-only multi-command catalog claims M007-10 | Fail closed: unrepresentative set |
| Machine-green only; skip HITL where visual primary confirmation is declared | Fail closed / incomplete |
| Primary confirmation is only a record path or JSON blob | Fail closed: confirmation standard violated |
| Implement #90/#91 as “required” under repair authority | Out of scope; separate proposal |
| Re-run only US-02 and claim continuity | Fail closed: US-01/02 are not the M007-10 gate |
| Omit US-03/04/05/08-class without blocked+owner record | Fail closed: silent omission |
| Product repair outside bounded set without amendment | Fail review; disposition + amendment if material |
| Dirty metrics-ui without named identity | `incomplete` |
| Skip cleanup / leave worker running | Acceptance blocker finding |
| Use movement or hardware leaves to “cover” families | Forbidden |

## External Assumptions

- M007-05 remains Met; primary six-step evidence stays authoritative for US-02.
- Live CLI session runner and pinned acceptance catalog remain available.
- Local Metrics UI can expose Chase for observation-only sequences that need a
  live view or memory map.
- Operator can run machine-only then interactive sessions on a developer
  machine with Chrome (or recorded browser identity).
- Lab candidate model paths and apply recording behave as exercised in #88
  unless repaired under the bounded set.

## Non-Goals

- Full CLI-leaf inventory (M007-08) or capability disposition (M007-09).
- Coverage collector / journey attribution (M007-07) — next frontier after this
  unit’s successful handoff.
- Reopening or re-running M007-05 as the continuity gate.
- Metrics UI redesign (#93) as a required deliverable.
- Same-frame experiment matrices (#90) or full transactional live trials (#91)
  as required deliverables.
- Movement, destructive, or hardware-dependent leaves.
- Numeric coverage gates or treating unexecuted code as dead.
- Satisfying M007-10 without human-scannable confirmations.

## File Impact

| Path | Change type |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/cli-scenario-continuity.md` | This proposal (proposal PR only) |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | Workflow transitions |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/*` | Continuity catalog(s) |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py` | Only if multi-sequence / confirmation fields need small extensions |
| `docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/**` | Evidence package |
| `cli/automa_cli/perception_runs.py` and related CLI surfaces | Bounded repairs (#89 / LIVE-001, etc.) |
| `tests/milestones/` or `tests/cli/` | Focused tests for catalogs and authorized repairs |

No product implementation lands in the **proposal** PR.

## Validation Plan

### Proposal PR

- Only this proposal artifact, canonical plan transition, and generated plan
  HTML change.
- `workflow.py validate` and proposal transition validation pass.
- `Expected Handoff` materializes against the current plan (M007-10 only).
- `git diff --check` clean.

### Implementation PR (after accept-proposal)

Deterministic:

- Continuity catalogs parse and are refused if they claim the pinned acceptance
  M007-05 identity.
- Result schema tests: confirmation fields present; path/JSON-only primary
  confirmation rejected.
- Focused tests for each applied bounded repair (e.g. unique apply run ids under
  frozen clock; readiness vs resolve path).
- Full default suite green.

External / live:

1. Machine-first run of every required family sequence.
2. HITL for sequences with visual primary confirmation.
3. Commit evidence directory with digests and family ledger.
4. Confirm no thin-catalog pass; cleanup unconditional.
5. List applied repairs mapped to finding/issue ids.

Reviewers verify representative families, confirmation standard, machine-first
ordering, bounded repairs only, and handoff readiness for coverage.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Realistic CLI scenario continuity in PR #{pr}: representative #88 catalog families declared on the live session runner, machine-first then conditional HITL with human-scannable confirmations, durable finding disposition, bounded product/CLI-output repairs only, and tracked evidence under docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/",
  "criterion_updates": {
    "M007-10": {
      "status": "Met",
      "evidence": "PR #{pr} declares and executes representative sequences beyond the primary six-step journey (US-03/04/05/08-class and allowed optional families), enforces machine-first and one human-scannable confirmation per sequence, disposes findings with owners, and applies only proposal-bounded repairs"
    }
  },
  "risk_remove": [
    "Jumping from primary-journey live acceptance to coverage without declared realistic sequences",
    "An unrepresentative or non-human-verifiable scenario set is accepted for M007-10"
  ],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "CLI journey coverage foundation is promoted after realistic scenario continuity declares the journeys coverage will attribute.",
    "revisit_when": "The coverage frontier implements reproducible per-command and multi-command journey attribution against the declared continuity and primary journeys before the full CLI-leaf audit."
  }
}
```

This template applies only to a `pass` result with no unresolved acceptance
blocker for required families. A conclusive findings unit uses an exceptional
block receipt and does not mark M007-10 Met or promote coverage. An incomplete
session has no handoff.

### Sequence after this proposal merges

1. Accept and merge this proposal PR into
   `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal`; verify `ready_for_implementation` and the
   exact proposal merge commit.
3. Start `m007/scenario-continuity` and implement catalogs, runner needs,
   bounded repairs, tests, and evidence scaffold.
4. Run machine-first then HITL; commit evidence; dispose findings.
5. Accept the implementation PR only as complete pass or conclusive findings.
6. On pass, complete the normal handoff and promote **CLI journey coverage
   foundation**. On findings, stop before that promotion.
