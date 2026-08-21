# Proposal: Capability disposition outside CLI journeys

| Field | Value |
| --- | --- |
| Milestone | 007 CLI Operator Usability |
| Frontier | Capability disposition outside CLI journeys |
| Proposal branch | `m007/capability-disposition-proposal` |
| Implementation branch | `m007/capability-disposition` |
| Exit criterion | M007-09 |
| Review kind | Deterministic invariant closure |

## Review Question

Can owned production code not reached by the declared CLI journey set be
grouped by capability and reconciled with tests, other entrypoints, dynamic or
platform paths, and ownership so every group is flagged to expose through CLI,
retain with an explicit owner and reason, or remove through separately reviewed
work, without authorizing feature or deletion solely by a coverage percentage?

This unit is **accountable disposition of unreached owned code**, not product
change. The milestone walks away knowing every such group has an owned
`expose`, `retain`, or `remove` candidate. It does not implement those
candidates.

## Glossary (contract terms)

| Term | Meaning |
| --- | --- |
| **Owned production code** | Tracked Python under the #107 owned roots (the same roots the journey-coverage collector measures). Tests, docs, generated runtime, and lab candidates are not this set. |
| **Declared CLI journey set** | The command/journey contexts sealed in the accepted M007-07 `report.json` (primary journey plus the three required continuity families). |
| **Sealed source universe** | The sorted `.py` paths in `subject.source_identity.relevant.files` that are under `inputs.owned_source_roots`, with the per-path SHA-256 values in `inputs.relevant_file_sha256`. This is larger than `report.files`; `report.files` is execution evidence, not the universe. |
| **Source member** | One owned source path plus its sealed source SHA and exact unreached statement/arc sets. A wholly absent path is still a member, even when coverage reports no executable region for it. |
| **Statement region** | A canonical `(path, line)` pair in the executable-statement set produced by the sealed coverage.py source analysis. |
| **Arc region** | A canonical `(path, from_line, to_line)` pair in the possible branch-arc set produced by that analysis. Negative entry/exit endpoints are retained as coverage.py reports them. |
| **Reached** | A statement or arc present in the union of `executed_lines` or `executed_arcs` across the report's declared-journey contexts for that path. A file path is reached only when it occurs in `report.files`. |
| **Unreached** | A source member whose path is absent from `report.files`, or whose possible statement/arc sets contain a region absent from the corresponding executed union. |
| **Capability group** | A named cluster of unreached regions that share one product capability and one owner. Not a per-line dump. |
| **Disposition** | Exactly one of `expose`, `retain`, or `remove`. `remove` is a candidate for later review, not a delete in this unit. |
| **Reachability authority** | The sealed #107 report bytes. Percentages in that report are informational and never Met. |

## Proposed Contract

### Sealed input identity

This proposal is bound to the current sealed M007-07 report, not to whatever
source happens to be checked out when implementation starts:

| Input | Frozen value or rule |
| --- | --- |
| Report | `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json`, `integrity.report_sha256 = 51801c7686b247055114109e7462d13cb6702a1c8dcd8990a168f68357015789` |
| Source revision | `subject.source_identity.commit = 7931fa9a995af5626fabef818f9e28b98c73e299`; relevant-file tree `e9e708b083bd203e1ca6b058404869e838ea5ad8dc1e7c9466302b9ab873bbe0` |
| Coverage analysis | `subject.coverage_version = 7.15.2`, with the sealed `.coveragerc` settings `branch = True`, `relative_files = True`, the three declared source roots, and `omit = */__init__.py` |
| File universe | Exact sorted paths from `subject.source_identity.relevant.files` whose normalized path is a `.py` file equal to or below one of `inputs.owned_source_roots`; each path's SHA must match `inputs.relevant_file_sha256` |

The implementation records the report path, report digest, source commit, and
relevant-file tree digest and fails closed if any of them, the source hashes,
the coverage version, or the owned roots differ. The sealed report currently
contains 96 owned Python paths while `report.files` contains 63 paths; the 33
owned paths absent from `report.files` are intentionally part of the source
universe and must not disappear from the capability record. `report.files` is
used only to obtain per-context execution evidence.

For each source-universe path, implementation obtains the possible statement
and branch-arc sets by analyzing the source at the frozen commit with the
sealed coverage.py/configuration identity. For a path absent from
`report.files`, the executed line and arc sets are empty. Otherwise they are
the unions of that path's context-level `executed_lines` and `executed_arcs`.
The derived unreached sets are therefore deterministic even for an entirely
unrepresented file, a partially reached file, or a file with a missing branch
arc.

### Acceptance statement

An implementation answers the review question only when **all** of the
following hold:

1. **Unreached set is derived, not invented.** Membership is computed from the
   frozen source universe and the sealed report. A human overlay cannot add a
   path outside that universe or drop a path the source/reachability derivation
   marks as unreached. `report.files` is never used as the source universe.
2. **Every unreached region belongs to exactly one capability group.** Each
   capability-record member is a source path with its sealed SHA,
   `unreached_statements`, and `unreached_arcs`. The member-path set must equal
   the derived candidate-path set exactly; each path occurs once; and each
   statement/arc set must equal possible regions minus the report's executed
   union. A path absent from `report.files` remains a member even if its
   possible-region sets are empty. Omission, partial-file loss, and
   branch-arc loss fail Met. Empty "nothing unreached" is allowed only when
   the derived candidate set is empty.
3. **Every group is reconciled.** Each group has separate, required
   `tests`, `non_cli_entrypoints`, `dynamic_paths`, and `platform_paths`
   reconciliation objects. Each object uses only `present` or
   `not_applicable`: `present` requires one or more stable references and no
   empty reason; `not_applicable` requires an empty reference list and a
   non-empty reason. Unknown statuses, missing objects, blank references, and
   missing `not_applicable` reasons fail. The owner is a structured object
   whose `kind` is `repo_path` or `m007_08_owner`; a `repo_path` must be an
   existing sealed source path/directory containing a member, and an
   `m007_08_owner` must exactly match an owner value in the read-only M007-08
   inventory/registry. An arbitrary non-empty string is not an owner.
4. **Every group has one disposition and a mechanically decidable reason.**
   `expose` = candidate to add or surface through CLI. `retain` = keep with
   owner and why CLI journeys need not reach it. `remove` = candidate for a
   later deletion review. The reason is a closed `code` plus a stable
   `reference` and non-empty `detail`; `code` must be `cli_gap` for `expose`,
   `non_cli_entrypoint`, `dynamic_path`, or `platform_path` for `retain`, and
   `separate_removal_review` for `remove`. The detail is normalized with
   Unicode NFKC and case-folding and is rejected if it contains `%`, a
   percentage/ratio expression, a numeric line/branch/statement/arc count,
   `coverage`, `unexecuted`, `unreached`, `untested`, `not covered`, or
   `never executed`. Unknown reason keys and a free-text reason scalar are
   rejected. Thus surrounding prose cannot launder a metric into causal
   authorization; the same negative corpus is exercised for every disposition.
5. **This unit does not perform the product work.** No CLI feature, no
   deletion, no move of production code to satisfy a disposition. Those are
   later review units.
6. **Validators and focused tests** enforce derivation, grouping completeness,
   required reconcile fields, and the percentage ban, including omission and
   "percent-as-reason" negative fixtures. A rollup that looks complete is not
   Met without those tests.

### Artifact shape

| Artifact | Authority | Contents |
| --- | --- | --- |
| **Reachability input** | Sealed M007-07 `report.json` | Owned roots, per-file executed/unexecuted attribution for the declared journey set |
| **Leaf/sequence context** | M007-08 inventory and registry | CLI-facing names and owners used when grouping; read-only |
| **Capability record** | This unit | Groups, members, reconcile fields, disposition, owner, reason |
| **Pass report / rollup** | This unit | Derived unreached counts, group list, residuals, explicit non-claims |
| **Derived HTML** | Same bytes as the record | Human view of groups and dispositions; not authority; layout is not Met |

### Capability record schema

Every group uses the following closed shape; implementations may add no
alternate free-text fields that influence Met:

```json
{
  "reconcile": {
    "tests": {"status": "present", "refs": ["tests/..."], "reason": ""},
    "non_cli_entrypoints": {"status": "not_applicable", "refs": [], "reason": "..."},
    "dynamic_paths": {"status": "present", "refs": ["autonomy/..."], "reason": ""},
    "platform_paths": {"status": "not_applicable", "refs": [], "reason": "..."}
  },
  "owner": {"kind": "repo_path", "ref": "implementations/..."},
  "disposition": "retain",
  "reason": {
    "code": "dynamic_path",
    "reference": "autonomy/...",
    "detail": "Loaded through the runtime plugin boundary and owned there."
  }
}
```

`reconcile` has exactly the four named dimensions. A `present` object has a
non-empty `refs` list of stable repository-relative paths or declared
entrypoint/artifact identifiers and has no meaningful `reason` (the serialized
value is the empty string). A `not_applicable` object has `refs: []` and a
non-empty explanation in `reason`. The two statuses are the complete vocabulary;
`unknown`, `pending`, and blank values fail. The validator rejects missing or
extra dimension keys and duplicate references after normalization.

`owner.kind = repo_path` means `owner.ref` is an existing sealed source file or
directory within the owned roots and contains at least one group member.
`owner.kind = m007_08_owner` means `owner.ref` is an exact owner or
`ledger_owner` value in the read-only M007-08 sequence registry or audit
report. These are the only owner forms, so a placeholder such as `x`, `team`,
or `unknown` cannot satisfy ownership by being non-empty.

The `reason` object is the only disposition rationale. Its closed code/reference
pair is the causal reason; `detail` provides human context but cannot override
the code. `reference` must resolve to a stable ref in the same group's
reconciliation data, the sealed source universe, or the M007-08 inventory.
After NFKC normalization and case-folding, the validator rejects `detail` when
it contains a percent sign, a numeric percentage or ratio, a numeric
line/branch/statement/arc count, or any of `coverage`, `unexecuted`,
`unreached`, `untested`, `not covered`, or `never executed`. The implementation
test matrix runs each forbidden form against `expose`, `retain`, and `remove`.

Exact repository paths and schema version ids are fixed in implementation under:

```text
docs/milestones/007-cli-operator-usability/tools/capability-disposition/
docs/milestones/007-cli-operator-usability/evidence/capability-disposition/
```

`docs/milestones/007-cli-operator-usability/evidence/capability-disposition/`
is this frontier's declared per-frontier evidence directory. Implementation
commits the sealed capability record, pass report, residual rollup, and
derived HTML of those records in that directory. Derived HTML: yes. The
record stays authority; layout is not Met.

### Disposition rules

| Disposition | Meaning in this unit | Forbidden here |
| --- | --- | --- |
| `expose` | Named CLI gap; later unit may add a leaf | Adding the leaf now |
| `retain` | Keep; journeys need not reach it; owner+reason required | Using "untested" as the only reason |
| `remove` | Candidate for a later deletion review | Deleting or quarantining the code now |

M007-06 closeout may cite this record. It may not treat a `remove` candidate as
already deleted.

## Trust And Authority Model

This unit's universal language applies to **complete grouping and fail-closed
disposition of unreached owned production code**. It does not claim that
unreached code is dead, that reached code is correct, or that a percentage is
a product decision.

| Guarantee class | What this unit claims | What it does not claim |
| --- | --- | --- |
| **Consistency** | The member-path set is the frozen source universe's unreached complement, with exact statement and arc subtraction; every member is in one group; every group has the four closed reconciliation objects and a legal reason object | That #107 attribution remains true after later product commits without a new capture |
| **Provenance** | The record stores the report digest, source commit/tree, per-member source SHA, and coverage identity used for derivation; owner and reason references resolve to named repository/M007-08 boundaries | That the owner field proves who should implement a later expose/remove unit |
| **Authenticity** | Validators authenticate source/member/region equality, closed reconciliation statuses and owner forms, and the metric-resistant reason grammar against the sealed inputs | That `retain` or `remove` is the right product call beyond the recorded reason; review still owns judgment quality |

**Trusted inputs:** sealed M007-07 report; #107 owned-root list; M007-08 leaf
inventory and sequence registry as CLI-context labels; this unit's schemas.

**Untrusted / non-authoritative for Met:** coverage percentages; chat claims
that "everyone knows this is lab-only"; test-run coverage as a substitute for
the declared journey set; a later HEAD that no longer matches the sealed
report.

**Claim → authority map:**

| Claim | Authority |
| --- | --- |
| File is owned production | Frozen source-universe paths and per-path SHA values |
| File is reached | Path presence in `report.files` |
| Statement/arc is reached | Union of context-level `executed_lines` / `executed_arcs` |
| File/region is unreached | Possible source regions minus the corresponding executed union |
| Group membership complete | Derived member rows and exact region sets equal the union of group members with no overlap |
| Tests / entrypoints / platform | The four separate `reconcile` objects and their stable refs |
| Explicit owner | Closed `repo_path` or `m007_08_owner` object |
| Disposition | Closed group field plus code/reference/detail reason object; metric grammar rejects authorization laundering |
| Product expose/delete done | Out of scope; later units |

**Adversaries covered:** omitting an unreached owned file; inventing members
outside the sealed source universe; assigning a file or region to two groups;
dropping a partial-file statement or branch arc; using test execution as
journey reachability; accepting a source/hash/report mismatch; authorizing a
disposition from a percentage, ratio, line/branch count, or `unexecuted`
clause; collapsing reconciliation into one free-text field; shipping a rollup
with blank or placeholder owner/reason; performing the product change in this
PR.

**Adversaries excluded / residual:** same-user later mutation of product code
that does not refresh #107 (record stays historical); subjective quality of a
`retain` reason beyond required fields; whether a later unit actually lands.

## Evidence Topology And Capture Strategy

| Claim / non-claim | Authoritative raw evidence | Derivation | Semantic verifier |
| --- | --- | --- | --- |
| Unreached membership | Sealed #107 report + frozen source inventory/config | Possible statements/arcs minus executed unions, with file-absence handling | Exact path/SHA/member-region equality |
| Group completeness | Capability record | Union of member rows and exact region sets | No remainder, no extra, no overlap, including partial/branch mutations |
| Reconcile fields present | Closed group schema | Four dimension objects plus owner object | Omission / unknown status / empty / missing-reason mutation tests |
| Reason is not a metric authorization | Structured reason object | Closed code/reference plus normalized detail grammar | Percentage, ratio, line/branch count, and `unexecuted` negatives for every disposition |
| CLI context labels | M007-08 inventory/registry | Optional join by path/owner | Unknown leaf ids fail if cited |
| Non-claim: dead code | — | — | Explicit rollup non-claim |
| Non-claim: HEAD still matches #107 | — | — | Record report digest; drift is residual |

**Capture strategy:**

- **Bounded implementation evidence** only: deterministic derivation, schema,
  and adversarial fixtures. No new live CLI or coverage recapture is required
  for Met.
- **#107 recapture** is out of scope unless the sealed report cannot be read.
  Then stop and amend; do not invent reachability.
- **Freshness:** store the #107 report path and digest. Product HEAD drift
  after that digest is residual, not silent Met.
- **Retained artifacts:** capability record, pass report, rollup, derived
  HTML of that record, test fixtures. Derived CI logs are not sole authority.

Canonical live recapture of journeys is **explicitly unnecessary** for Met.

## Ownership

| Concern | Owner |
| --- | --- |
| Unreached membership | Derivation from sealed #107 report and owned roots |
| Capability grouping and reasons | Human review; schema-enforced required fields |
| Percentage ban and completeness | This unit's validators |
| CLI leaf/sequence labels | Read-only M007-08 artifacts |
| Implementing `expose` / `remove` | **Out of scope** — later review units |
| Re-measuring journeys | **Out of scope** — M007-07 remains sealed |
| Re-opening leaf inventory | **Out of scope** — M007-08 |
| Milestone closeout | **Out of scope** — M007-06 |

The capability-disposition validator/record suite is the single Met owner. A
coverage percentage, a leaf dump, or an informal "we will delete this later"
note cannot independently mark M007-09 Met.

## Affected Paths

- `#107` evidence
  `docs/milestones/007-cli-operator-usability/evidence/cli-journey-coverage/report.json`
  is a **read input**. This unit does not rewrite it.
- M007-08
  `docs/milestones/007-cli-operator-usability/tools/cli-surface-audit/`
  inventory and registry are **read inputs** for CLI-facing labels.
- New
  `docs/milestones/007-cli-operator-usability/tools/capability-disposition/`
  owns derivation, schema, validators, rollup, README.
- New
  `docs/milestones/007-cli-operator-usability/evidence/capability-disposition/`
  owns the capability record, pass report, and residual rollup.
- `tests/milestones/` owns deterministic derivation, completeness, and
  percentage-ban tests.
- `autonomy/`, `implementations/`, and `cli/automa_cli/` are **read inputs**
  for path identity only. No product behavior change.

## Adversarial Matrix

| Case | Required result |
| --- | --- |
| Owned path is present in the sealed source inventory but absent from `report.files` and in no group | Met fails; the path must have a member row even if its possible-region sets are empty |
| Group member path is outside the frozen source universe or has the wrong source SHA | Reject |
| Same unreached path appears in two groups | Reject |
| Derived unreached set is non-empty but record says none | Met fails |
| Reached statement/arc is listed as unreached, or an unreached statement/arc is omitted | Met fails |
| Partial file loses one possible statement from `unreached_statements` | Reject |
| Missing branch arc is absent from `unreached_arcs` | Reject |
| Sealed report digest, source commit/tree, coverage identity, or per-file SHA does not match | Met fails |
| Test-only execution used to mark a file journey-reached | Reject; tests reconcile `retain`, they are not the journey set |
| Group omits one of `tests`, `non_cli_entrypoints`, `dynamic_paths`, or `platform_paths` | Met fails |
| Reconciliation uses an unknown status, blank ref, duplicate ref, or `present` with no ref | Met fails |
| `not_applicable` reconciliation field has a ref, no reason, or a blank reason | Met fails |
| Owner is a free string, placeholder, unknown M007-08 label, or unrelated repo path | Reject |
| Disposition/reason code pair is invalid, reason keys are unknown, or reference does not resolve | Reject |
| Reason detail contains a percentage, numeric ratio, line/branch/statement/arc count, or `unexecuted` clause | Reject for `expose`, `retain`, and `remove` |
| `remove` group accompanied by deleting the production file | Out of scope; fail the independence of this unit |
| `expose` group accompanied by a new CLI leaf | Out of scope |
| Rollup hides groups with `remove` | Met fails |
| Implementation rewrites #107 report to shrink unreached set | Forbidden |
| M007-08 inventory rewritten to make grouping easier | Out of scope; amend or separate unit |
| Issues #89–#108 treated as this unit's Met | Out of scope; later wants |

## External Assumptions

- The sealed M007-07 report remains readable and is the reachability authority
  for this unit. If it is missing or unverifiable, stop; do not recapture as a
  side quest.
- #107 owned roots still name the production set this milestone cares about.
- M007-08 leaf inventory and sequence registry remain the CLI-facing labels.
- Non-CLI entrypoints (tests, Pi deploy, lab plugins, Metrics UI) exist and
  may justify `retain`; they do not expand the declared journey set.
- Dynamic import and platform-only modules may be unreached for honest
  reasons; they still need a group.

## Non-Goals

- Executing expose, retain-as-refactor, or remove in production code.
- Numeric coverage gates or expanding #107 measurement.
- Reopening M007-08 accounting or M007-07 capture.
- Product repair of LIVE defects or issues #89–#108.
- Milestone closeout (M007-06).
- Claiming unreached code is dead.
- A second coverage collector.

## File Impact

### Proposal PR only

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/proposals/capability-disposition.md` | This contract |
| `docs/milestones/007-cli-operator-usability/plan.md` / `plan.html` | `proposal_in_review` transition |

### Expected implementation PR

| Path | Change |
| --- | --- |
| `docs/milestones/007-cli-operator-usability/tools/capability-disposition/` | Derivation, schema, validators, rollup, README |
| `docs/milestones/007-cli-operator-usability/evidence/capability-disposition/` | Capability record, pass report, residual rollup, derived HTML of that record |
| `tests/milestones/` | Completeness, overlap, digest, percentage-ban fixtures |
| Plan handoff on success | M007-09 Met; next-frontier remains empty toward closeout |

No planned product changes under `autonomy/`, `implementations/`, or
`cli/automa_cli/`.

## Validation Plan

### Proposal PR

```sh
python3 docs/milestones/workflow.py validate \
  docs/milestones/007-cli-operator-usability/plan.md
python3 docs/render_markdown.py --check
python3 -m unittest \
  tests.docs.test_milestone_proposal_workflow \
  tests.docs.test_milestone_planning
python3 docs/milestones/workflow.py validate-pr \
  --base-ref milestone/007-cli-operator-usability \
  --head-ref m007/capability-disposition-proposal \
  --base-sha <merge-base> \
  --head-sha <head> \
  --pr-body-file <path-to-pr-body>
git diff --check
```

Reviewers confirm proposal-only paths, review kind **deterministic invariant
closure**, Trust/Evidence sections, and no implementation payload.

### Implementation PR (after acceptance)

Deterministic:

- frozen source universe and per-file hashes match the sealed report;
- unreached member paths ≡ source-universe paths absent from `report.files` or
  containing a missing possible statement/arc;
- every member path appears in exactly one group with exact missing statement
  and arc sets, including wholly absent files, partial files, and branch arcs;
- the four reconciliation dimensions have closed statuses and stable refs;
- owner form and owner reference validate against the sealed source/M007-08
  inputs;
- the closed reason code/reference/detail grammar rejects metric laundering for
  every disposition;
- digest of sealed #107 report matches the record;
- rollup lists every group including `remove`;
- derived HTML regenerates from the sealed record (layout is not Met);
- no production path diffs.

No live recapture.

## Expected Handoff

Post-merge successful implementation template:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Capability disposition outside CLI journeys in PR #{pr}: unreached owned production code derived from sealed M007-07 report; every region in exactly one capability group; tests/entrypoints/platform/owner reconciled; expose/retain/remove candidates with non-percentage reasons; validators reject omission and percentage-as-authorization; derived HTML of that record; tracked evidence under docs/milestones/007-cli-operator-usability/evidence/capability-disposition/",
  "criterion_updates": {
    "M007-09": {
      "status": "Met",
      "evidence": "PR #{pr} groups unreached owned production code from the sealed journey-coverage report, reconciles tests/entrypoints/dynamic-or-platform paths and ownership, and records an owned expose, retain, or remove candidate for every group without using a coverage percentage as authorization"
    }
  },
  "risk_remove": [
    "Coverage absence is not proof that code is dead"
  ],
  "risk_upsert": [
    {
      "risk": "Capability dispositions are historical to the sealed M007-07 report",
      "consequence": "Later product commits can change what is unreached without updating the candidate record",
      "resolution": "Closeout cites the record digest; a later unit recaptures #107 if reachability must be refreshed"
    }
  ],
  "next_frontier": {
    "state": "none",
    "reason": "M007-09 leaves owned expose/retain/remove candidates. Closeout (M007-06) is the remaining milestone unit when the operator is ready; it does not execute those candidates.",
    "revisit_when": "Operator starts milestone closeout, or a later unit implements a named expose or remove candidate."
  }
}
```

### Sequence after this proposal merges

1. Merge this proposal into `milestone/007-cli-operator-usability`.
2. Run `workflow.py accept-proposal` with exact-head review receipt.
3. Start `m007/capability-disposition` and implement only this contract.
4. Pass deterministic validation. Do not recapture journeys.
5. On complete Met, accept implementation with an empty next-frontier toward
   closeout. Otherwise stop without promotion.

## Review Kind

**Deterministic invariant closure** — complete grouping of unreached owned
production code and fail-closed rejection of percentage-as-authorization.
Met is an owned candidate record, not product expose/retain/remove.
