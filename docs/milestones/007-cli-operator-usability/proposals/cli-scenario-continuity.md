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

The implementation must declare runner catalogs that cover these families from
the #88 prospective usage-sequence catalog
([comment](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5169077892)).

**Stable family IDs** (required in every catalog step/sequence mapping and in
`result.json`):

| Family ID | #88 class | Required? | Minimum ownership |
| --- | --- | --- | --- |
| `continuity.offline_perception` | US-03-class | **Required** | Capture-once then apply and/or compare on the **same** captured input with a compact human comparison surface and preserved source lineage |
| `continuity.live_config_swap` | US-04-class | **Required** | Observation-only restage or plugin change, restart to a healthy view, then **transactional restore** of the prior activation (see below). Full #91 product UX is not required |
| `continuity.memory_lifecycle` | US-05 and US-08-class | **Required** | Memory check or equivalent concise PASS/FAIL; reset/repopulation with human-scannable key/epoch confirmation where the sequence mutates memory |
| `continuity.plugin_ablation` | US-06-class | Optional | Include when prerequisites allow; else `blocked`/`partial` with owner |
| `continuity.temporal_backpressure` | US-07-class | Optional | Include when prerequisites allow; else `blocked`/`partial` with owner |
| `continuity.memory_replay` | US-09-class | Optional | Include when prerequisites allow; else `blocked`/`partial` with owner |
| — | US-01, US-02 | Out of gate | Already owned by M007-05; not re-proven for M007-10 |
| — | US-10-class | Deferred | Physical-check qualification when labeled input is unavailable |

**Required-family set** for a `pass`:

```text
continuity.offline_perception
continuity.live_config_swap
continuity.memory_lifecycle
```

A catalog or result that omits a required family ID, duplicates/aliases a family
ID onto help/status-only steps, or labels a thin help/status multi-command set
as a required family **fails closed** before or at machine-first evaluation.
Silent omission is a failure.

### Continuity-track command safety (fail closed before execution)

The current runner’s general schema check and catalog-declared `safety` labels
are **not** sufficient for this track. Continuity catalogs may not trust free-form
argv plus a self-asserted safety enum.

Implementation must add an **executable preflight owner** for track
`continuity` (name may match the catalog track field; must not be the pinned
`acceptance` / M007-05 identity) that runs **before any CLI command,
precondition stop, or live mutation**:

1. Parse every command argv against the real public CLI parser (or equivalent
   structural allowlist derived from the registered parser).
2. **Derive** safety class from argv/flags (not solely from the catalog’s
   `safety` field). If the catalog label disagrees with the derived class,
   reject the entire catalog.
3. Allow only an **explicit continuity allowlist** of command paths and flag
   combinations needed for the frozen families (status, update perception,
   automation run/stop with observation-only constraints, perception
   run/apply/compare/candidates as used by the families, memory check/reset/
   replay as used by the families, and similar read-only discovery already
   public). Exact allowlist entries are implementation detail; the contract is
   fail-closed membership.
4. **Reject the entire catalog** (no partial run) if any step includes:
   - movement or operation pulses (for example `operation startup-check`
     without an exclusive dry-run that never sends pulses—if not clearly
     dry-only, reject);
   - hardware / Pi deploy leaves (`update core`, `update autonomy`, …);
   - destructive or environment-mutating leaves outside the allowlist;
   - `simulators ensure` or other simulator reconfiguration;
   - unknown commands or unregistered flags;
   - flags that apply control, take non-observe-only authority, or enable
     default history recording when the family does not explicitly allow
     `--record` for offline perception artifacts.
5. Do **not** execute an unsafe live reproduction to “prove” rejection;
   deterministic adversarial tests own that proof.

### US-04 restoration as a transaction

For `continuity.live_config_swap`, “unconditional cleanup/restoration” means a
**transaction**, not only “stop the worker in `finally`”:

1. **Snapshot restorable state before mutation.** Capture the full prior staged
   activation / plugin configuration as **recoverable bytes** (or an immutable
   recoverable source identity that can recreate those exact bytes), plus a
   verification hash of that payload, plus whether a worker was running. A
   hash-only snapshot **without** restorable bytes/source is rejected: do not
   mutate. Snapshot failure prevents any trial mutation.
2. Apply the trial restage/plugin change only under observation-only authority.
3. Start (or restart) and reach the family’s **complete** primary confirmation
   (healthy view / readiness cue as declared for the family minimum contract).
4. **Always restore** from the restorable snapshot after success, command
   failure, timeout, or interruption (operator abort / runner signal), then stop
   any trial worker generation.
5. **Verify** the restored activation matches the snapshot verification hash
   (and/or byte equality) and that no repository-owned automation worker remains
   running for the vehicle.
6. A restore or verification failure is an **acceptance blocker**: the sequence
   family aggregate and overall continuity result cannot be `pass`.

Focused tests must cover success restore, command-failure restore, timeout/
interruption restore, restore-failure → non-pass, and
**hash-only/non-restorable snapshot rejection** (no mutation). The runner’s
existing worker-stop cleanup alone does not satisfy this contract.

### Confirmation standard (every declared sequence)

Each declared sequence must record:

- stable `family_id` from the table above;
- operator question and prerequisites;
- exact current public commands (human-first argv);
- derived safety class and side effects;
- unconditional cleanup / restoration steps (transactional for
  `continuity.live_config_swap`);
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

### Source lineage (offline perception family)

For `continuity.offline_perception`:

- Capture (or select) one immutable ordered input set and record its identity
  (path + content digest / provenance).
- Apply and compare must consume **that same** input identity.
- Evidence must preserve lineage so a pass cannot mix frames from different
  captures or untracked directories.

### Execution procedure

1. **Declare** sequences in session-runner YAML/JSON catalogs under the existing
   live CLI session runner tools directory (new `continuity` track catalog(s);
   do not invent a second harness). Catalogs must not use the pinned
   `m007-acceptance` / M007-05 identity.
2. **Preflight:** run continuity-track safety allowlist validation; on failure,
   write a refused result and stop—no CLI execution.
3. **Family validation:** ensure required family IDs are present, not
   help/status-only mislabeled, and commands are parser-valid.
4. **Machine-first:** run each required sequence non-interactively / machine-only
   where the runner supports it, and record machine verdicts.
5. **HITL elevation:** only machine-green sequences that declare visual judgment
   require interactive human pass/fail on the primary confirmation surface.
6. **Findings:** confirmed discrepancies use stable ids with classification,
   severity, owner, disposition.
7. **Repairs:** only surfaces in **Bounded repair set** (strict). See below.
8. **Evidence binding + finalizer:** see **Evidence freshness finalizer** below.
   Commit catalogs, session artifacts, digests, README ledger, and any repair
   diffs only after the finalizer accepts the binding (or after a required
   rerun).

Incomplete environment → `incomplete`, not a false pass.

### Required-family aggregation (overall `pass`)

Per-sequence statuses roll up to a **family aggregate** per stable family ID.

| Rule | Contract |
| --- | --- |
| Overall `pass` | Every **required** family aggregate is exactly `passed` (not `partial`, not `blocked`, not missing). Safety preflight passed. Evidence finalizer passed. |
| Family aggregate `passed` | At least one sequence for that family completed the **entire minimum ownership contract** for the family (including US-04 restore verification when applicable), with machine-first green and HITL green when visual confirmation is declared. |
| Sequence `partial` | Allowed only as an intermediate or failed attempt. It does **not** make the family aggregate `passed`. Another sequence for the same family must fully satisfy the minimum contract, or the family aggregate is not `passed`. |
| Overall outcome if any required family aggregate is not `passed` | `findings` (sequences ran, family incomplete or blocked with ledger) or `incomplete` (refused/abandoned)—never overall `pass`. |

“Does not leave the family unrepresented” is **not** an executable pass rule and
is not used. A US-04 sequence that stops before healthy-view confirmation or
without verified restore cannot contribute a `passed` family aggregate.

### Evidence freshness finalizer (mechanically fail closed)

Implementation must provide a **deterministic evidence finalizer/validator**
that runs before any overall `pass` claim is accepted (in the runner and/or a
committed validator used by review):

1. Read recorded identities from the session/result: auto-driving product commit
   and/or owned product tree digest covering behavioral CLI/runtime surfaces
   touched by the catalogs; session-runner source digest; continuity catalog
   content digests; and, when any sequence used visual confirmation, the exact
   Metrics UI identity (commit and clean/dirty + named diff or linked PR when
   dirty).
2. Recompute the same identities from the **final implementation tree** under
   review (the PR head to be merged).
3. **Refuse `pass`** if any recorded identity/digest mismatches the final tree,
   or if required identity fields are missing.
4. A mismatch on behavior-bearing surfaces (product CLI/runtime, runner,
   catalog, or Metrics UI identity used for visual confirmation) requires a
   full machine-first rerun and applicable HITL for affected sequences—not an
   advisory note.
5. Packaging-only edits inside the evidence directory (redaction, path
   normalize) may be re-finalized without rerun only when they do not change
   verdict, family aggregates, digests of product/runner/catalog, or confirmation
   outcomes; the finalizer still rechecks those immutable fields.

A concrete bypass—run machine/HITL, then change `session_runner.py`, a bounded
CLI repair, or a catalog, then package the old passing result—must fail the
finalizer and a dedicated **stale-result regression test**.

### Bounded repair set (strict)

The proposal authorizes implementation to repair or harden **only** the
surfaces and intents in this table:

| Source | Surface | Intent |
| --- | --- | --- |
| #89 / M007-LIVE-001 | `perception apply` run-id / record directory identity | Collision-resistant exclusive record dirs |
| M007-LIVE-002 | `perception candidates` readiness vs compare model path | Ready means execution-time resolver can run |
| M007-LIVE-003 | `perception compare` human failure output | One-line structured failure; full detail in JSON/verbose |
| M007-LIVE-005 | `perception run` human vs `--json` surfaces | Compact human default; review path prominent |
| M007-LIVE-004 (partial) | review path discoverability | May launch or print a single clear review path; **must not** require a new consolidated multi-engine product (#90) |
| Sequence-forced CLI output | Concise verdict lines for memory check / reset / replay | Only as needed for the confirmation standard on allowlisted memory leaves |

There is **no** open-ended authority for “defects sequences newly prove.” Newly
discovered defects outside this table:

1. receive durable disposition in the evidence ledger (owner, classification);
2. if repair is desired **in this unit**, require a **reviewed proposal
   amendment** that adds the surface to this table;
3. otherwise remain deferred to a separate review unit.

**Not authorized as required deliverables:** #90 same-frame experiment matrices,
#91 transactional live trials as a full feature, Metrics UI product redesign
(#93), movement/hardware leaves, coverage collector, full leaf inventory.

### Environment and baseline

- Observation-only action policy; no applied vehicle movement.
- Metrics UI Chase already available; no hidden `simulators ensure` as part of
  a “pass.”
- Precondition stop of stray Automa workers before baseline (after continuity
  safety preflight has accepted the catalog).
- Record exact `auto-driving` and `metrics-ui` identities (clean or named diff).
- Redact local absolute paths, secrets, and unrelated browser content.

### Evidence artifacts (implementation-owned)

Tracked under:

`docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/`

Minimum contents:

| Path | Contract |
| --- | --- |
| `README.md` | Environment receipt, family/sequence ledger by stable family ID, confirmation results, repair list, findings, product/runner/catalog digests, verdict, link to #88 catalog |
| `result.json` | Machine-readable continuity result: required-family set, per-sequence `family_id` and status, **per-family aggregates**, machine vs human, source lineage, safety preflight receipt, US-04 restorable snapshot metadata + restore verification, recorded product/runner/catalog/Metrics UI identities, finalizer receipt, digests, findings, repairs applied (only bounded set) |
| Runner session dir(s) | Full session-runner outputs for machine-only and HITL runs |
| Catalogs as committed | Exact YAML/JSON catalogs executed with content digests under `tools/live-cli-session-runner/catalogs/` |
| Repair notes | Map of applied code changes to finding ids / issue numbers; empty or N/A if no repairs |

`result.json` records `pass`, `findings`, or `incomplete`. An overall `pass`
requires: successful safety preflight; every **required family aggregate**
exactly `passed` (see aggregation rules); successful US-04 restore verification
when that family ran; and a **passing evidence freshness finalizer** against the
final implementation tree. A thin catalog pass or partial-only required family
is forbidden.

### Relation to the session runner

- Keep `m007-acceptance` / pinned acceptance catalog immutable for M007-05
  posterity unless a separate amendment is reviewed.
- Continuity catalogs use a distinct track (recommended name: `continuity`) so
  they cannot claim M007-05 and so safety preflight can target them.
- Prefer extending the existing runner over parallel tooling.

### Success / failure outcomes

| Outcome | Meaning |
| --- | --- |
| `pass` | Safety preflight passed; every required family aggregate is `passed`; machine-first and required HITL complete; confirmation standard held; US-04 restorable snapshot + restore verified when applicable; findings disposed; only bounded-table repairs applied; evidence freshness finalizer passes against final product/runner/catalog/Metrics UI identities |
| `findings` | Sequences ran under safety preflight but a required family aggregate is not `passed`, or blocking product issues remain; M007-10 not Met until repaired (bounded set or amendment) and re-run, or exceptional block reviewed |
| `incomplete` | Environment/identity/session abandoned, catalog refused at safety preflight, finalizer refuse without a completed family proof, or similar; no Met claim |

## Ownership

| Boundary | Owner in this unit |
| --- | --- |
| Catalog declaration, safety preflight, family validation | Continuity implementation + session runner |
| Human visual / scannable confirmation | Named operator in the evidence receipt |
| Machine gates, digests, evidence freshness binding | Session runner + result schema |
| Bounded product/CLI-output repairs | Automa CLI ownership for **listed table surfaces only** |
| Metrics UI product redesign | Out of unit (#93 external) |
| Large experiment features | Out of unit (#90/#91) |
| Milestone transition | Reviewed successful handoff template |

## Affected Paths

| Path / surface | Role |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/` | Continuity catalogs; safety preflight; family/confirmation/restore hooks |
| `docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/` | Tracked evidence |
| `cli/automa_cli/` (bounded table only) | Apply run-id, candidates readiness, compare/run human output, memory verdict lines as listed |
| `tests/` (focused) | Safety preflight adversarial tests; family validation; restore transaction; confirmation schema; authorized repairs only |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | Workflow transitions only |

## Adversarial Matrix

| Attempted bypass | Required response |
| --- | --- |
| Help/status-only multi-command catalog claims M007-10 | Fail closed: unrepresentative set / missing required family IDs |
| Required family ID missing, duplicated, or aliased onto help/status steps | Fail closed at family validation |
| Catalog `safety: read` but argv is movement, hardware, or mutation | Fail closed at safety preflight before any command |
| Movement / hardware leaf, `simulators ensure`, unknown command, unsafe flag | Fail closed at safety preflight; covered by deterministic tests—not live unsafe runs |
| Machine-green only; skip HITL where visual primary confirmation is declared | Fail closed / incomplete |
| Primary confirmation is only a record path or JSON blob | Fail closed: confirmation standard violated |
| US-04 “cleanup” is only worker stop without activation restore/verify | Fail closed: restore transaction not satisfied |
| US-04 snapshot is hash-only / non-restorable | Fail closed before mutation; covered by focused tests |
| Restore fails but result claims `pass` | Fail closed: acceptance blocker |
| Required family aggregate is only `partial` but overall claims `pass` | Fail closed: aggregation rule |
| Offline apply/compare uses different capture than recorded lineage | Fail closed: source lineage broken |
| Claim `pass` after product/runner/catalog/Metrics UI identity change without rerun | Fail closed: evidence finalizer mismatch + stale-result regression test |
| Finalizer is advisory / “where practical” only | Fail review: finalizer must refuse `pass` on mismatch |
| Implement #90/#91 as “required” under repair authority | Out of scope; separate proposal |
| Repair outside bounded table without proposal amendment | Fail review; disposition only, or amendment first |
| Re-run only US-02 and claim continuity | Fail closed: US-01/02 are not the M007-10 gate |
| Dirty metrics-ui without named identity | `incomplete` |
| Skip cleanup / leave worker running | Acceptance blocker finding |

## External Assumptions

- M007-05 remains Met; primary six-step evidence stays authoritative for US-02.
- Live CLI session runner and pinned acceptance catalog remain available.
- Local Metrics UI can expose Chase for observation-only sequences that need a
  live view or memory map.
- Operator can run machine-only then interactive sessions on a developer
  machine with Chrome (or recorded browser identity).
- Lab candidate model paths and apply recording behave as exercised in #88
  unless repaired under the bounded table.

## Non-Goals

- Full CLI-leaf inventory (M007-08) or capability disposition (M007-09).
- Coverage collector / journey attribution (M007-07) — next frontier after this
  unit’s successful handoff.
- Reopening or re-running M007-05 as the continuity gate.
- Metrics UI redesign (#93) as a required deliverable.
- Same-frame experiment matrices (#90) or full transactional live trials (#91)
  as required deliverables.
- Movement, destructive, or hardware-dependent leaves (including as
  “exploratory” catalog content).
- Open-ended product repair beyond the bounded table without amendment.
- Numeric coverage gates or treating unexecuted code as dead.
- Satisfying M007-10 without human-scannable confirmations or without required
  family IDs.

## File Impact

| Path | Change type |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/cli-scenario-continuity.md` | This proposal (proposal PR only) |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | Workflow transitions |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/catalogs/*` | Continuity catalog(s) with family IDs |
| `docs/milestones/007-cli-operator-usability/tools/live-cli-session-runner/session_runner.py` | Continuity safety preflight, family validation, restore hooks, evidence binding |
| `docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/**` | Evidence package |
| `cli/automa_cli/…` | **Only** bounded-table surfaces |
| `tests/milestones/` or `tests/cli/` | Safety/family/restore/repair tests listed in Validation Plan |

No product implementation lands in the **proposal** PR.

## Validation Plan

### Proposal PR

- Only this proposal artifact, canonical plan transition, and generated plan
  HTML change.
- `workflow.py validate` and proposal transition validation pass.
- `Expected Handoff` materializes against the current plan (M007-10 only).
- `git diff --check` clean.

### Implementation PR (after accept-proposal)

Deterministic (required):

- Continuity catalogs parse; refuse pinned acceptance / M007-05 identity.
- **Safety preflight:** reject entire catalog before any command for
  safety-label mismatch, movement/hardware leaves, `simulators ensure`,
  disallowed update/mutation leaves, unsafe flags, and unknown commands
  (adversarial unit tests; no live unsafe execution).
- **Family validation:** required family IDs present; reject missing, duplicate,
  or help/status-only mislabeled families; commands parser-valid.
- **US-04 restore:** success, failure, timeout/interruption, restore-failure →
  non-pass, and **hash-only/non-restorable snapshot rejection** (no mutation).
- **Confirmation schema:** path/JSON-only primary confirmation rejected.
- **Family aggregation:** overall `pass` requires every required family
  aggregate `passed`; a lone `partial` sequence cannot yield overall `pass`.
- **Evidence freshness finalizer:** deterministic comparison of recorded
  product/runner/catalog digests and Metrics UI identity (when visual) to the
  final tree; refuse `pass` on mismatch; **stale-result regression test**
  (session artifacts from commit A, tree at commit B with runner/CLI/catalog
  change → finalizer fails).
- Focused tests for each **applied** bounded-table repair only.
- Full default suite green.

External / live:

1. Safety preflight accepts the committed continuity catalog.
2. Machine-first run of every required family sequence.
3. HITL for sequences with visual primary confirmation.
4. Verify US-04 restorable snapshot + restore (activation match + worker stopped).
5. Run evidence freshness finalizer against the final implementation head.
6. Commit evidence with family aggregates, lineage, identities, and finalizer
   receipt.
7. Confirm no thin-catalog or partial-only required-family pass; list repairs
   only from the bounded table.

Reviewers verify required family aggregates, safety preflight, confirmation
standard, US-04 restorable transaction, finalizer refuse-on-mismatch, bounded
repairs only, and handoff readiness for coverage.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Realistic CLI scenario continuity in PR #{pr}: required family aggregates continuity.offline_perception, continuity.live_config_swap, and continuity.memory_lifecycle each passed on the live session runner with fail-closed safety preflight; machine-first then conditional HITL with human-scannable confirmations; US-04 restorable snapshot and restore verified; evidence freshness finalizer matched final product/runner/catalog/Metrics UI identities; durable finding disposition; repairs limited to the proposal bounded table; tracked under docs/milestones/007-cli-operator-usability/evidence/cli-scenario-continuity/",
  "criterion_updates": {
    "M007-10": {
      "status": "Met",
      "evidence": "PR #{pr} passes required family aggregates with safety preflight, machine-first/HITL confirmations, US-04 restorable restore verification, offline source lineage, deterministic evidence freshness finalizer, and only bounded-table repairs"
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
3. Start `m007/scenario-continuity` and implement catalogs, safety preflight,
   family validation, US-04 restore, evidence binding, bounded-table repairs,
   tests, and evidence scaffold.
4. Run machine-first then HITL; commit evidence; dispose findings.
5. Accept the implementation PR only as complete pass or conclusive findings.
6. On pass, complete the normal handoff and promote **CLI journey coverage
   foundation**. On findings, stop before that promotion.
