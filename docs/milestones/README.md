# Milestone Planning And Delivery Contract

This file is the canonical planning and pull-request delivery contract for the
repository. Individual milestone plans contain their own objectives, usage,
status, and decisions. They link here instead of restating these rules.

Closed milestone plans are frozen historical records and are not required to be
retrofitted to this format.

## Goals

Separate:

1. feature-level milestone outcomes;
2. planning priority (frontier);
3. review-sized PR deliverables (review units);
4. implementation tasks inside a review unit;
5. external evidence units;
6. milestone closeout;
7. Git branches.

Minimize manual synchronization after a merge. A normal accepted review unit
should require only a handful of milestone-plan edits:

1. add one accepted-review-unit ledger row;
2. update affected exit criteria;
3. update unresolved risks;
4. promote the next frontier;
5. select at most one new next-frontier candidate.

Do not preserve redundant sections merely because they already exist.

## Work-Unit Model

### Milestone

A **milestone** is a feature-level user or operator outcome.

It defines:

- a stable objective;
- observable completion usage;
- fixed exit criteria;
- milestone-level scope boundaries;
- safety or operating constraints;
- final external proof where required;
- residual-risk and closeout expectations.

A milestone is **not** a predetermined sequence of pull requests.

A milestone answers:

> What new thing can a user or operator reliably do after this work is complete?

### Frontier

The **frontier** is a planning position, not a branch or task.

It identifies the next milestone claim ready for active attention.

The plan may contain:

- exactly one **current frontier**;
- at most one **next-frontier candidate**;
- a **preparation horizon** for later provisional needs.

The frontier determines priority and readiness. It is not a detailed speculative
roadmap.

Promote work to the frontier only when it is:

1. **contractable now** through one review question; and
2. **reviewable in one careful human pass**.

Human review attention is the throughput limit. Prefer fewer sequential units
that close a contractable edge over many named subdivisions that multiply
handoffs.

### Review Unit

A **review unit** is one complete pull-request deliverable.

It implements or proves one frontier claim and answers **one primary review
question**.

A review unit may be:

- deterministic invariant closure;
- a behavioral feature slice;
- a broad mechanical rollout;
- live or external evidence;
- a migration;
- a review repair;
- milestone closeout.

Do not call all review units “features.” Use the generic term **review unit**.

### Task

A **task** is a concrete implementation action inside a review unit.

Tasks normally do **not** receive separate branches or PRs. Group tasks only
when they support the same review question and acceptance boundary.

### Evidence Unit

When the review question changes from implementation correctness to real-system
proof, create a separate **evidence** review unit.

Examples: guided simulator validation, physical-device validation, benchmark,
operator acceptance procedure, tracked provenance artifact.

Do not combine deep deterministic contract review and substantial live-system
proof merely because they support the same milestone.

### Repair Cycle

A review finding remains in the existing PR when it still challenges that PR’s
stated contract.

A repair response should identify:

- root cause;
- owning enforcement boundary changed;
- adjacent paths audited;
- regression coverage added;
- assumptions still unverified.

Create a separate repair review unit only when a distinct PR is genuinely
necessary.

### Closeout

**Milestone closeout** is a separate review unit asking:

> Is the milestone complete as a whole?

It evaluates completion usage, every exit criterion, cumulative implementation,
external evidence, durable documentation, unresolved risks, and whether the next
milestone or pre-plan should be activated.

Closeout must not conceal unfinished implementation or validation.

## Information Ownership

| Information | Canonical location |
| --- | --- |
| Milestone objective | Milestone plan |
| Completion usage | Milestone plan |
| Exit criteria and status | Milestone plan |
| Current and next frontier | Milestone plan |
| Detailed invariant and adversarial matrix | Review-unit PR |
| File impact and exact validation commands | Review-unit PR |
| Review findings and repair history | Review-unit PR |
| Accepted result of a merged PR | One-row plan ledger |
| Current architecture behavior | `docs/reference/` |
| Repository navigation | `docs/README.md` |
| Final milestone judgment | `closeout.md` |
| Future research without commitment | `docs/synthesis/` |

Do not copy complete PR descriptions into milestone plans.

Do not copy architecture facts into milestone plans when they belong in durable
reference documentation.

Do not make `docs/README.md` a second source of detailed milestone status.

## Milestone Layout

Prefer:

```text
docs/milestones/<number>-<slug>/
├── plan.md          # canonical plan (active milestones)
├── plan.html        # generated; do not edit directly
├── closeout.md      # created at closeout
└── evidence/
```

`plan.md` is canonical for active milestones. `plan.html` is generated from it.

The shared contract lives in this file (`docs/milestones/README.md`). Its
browser rendering is `planning-contract.html`.

Refresh generated HTML after contract or plan Markdown changes:

```sh
python3 -m pip install -r docs/requirements.txt
python3 docs/render_markdown.py
python3 docs/render_markdown.py --check
```

Closed historical plans may remain hand-authored `plan.html` files and are not
required to gain a `plan.md`.

## Git Branch Model

Use one integration branch per active milestone.

```text
main
└── milestone/<number>-<slug>
    ├── m<number>/<review-unit>-<slug>
    ├── m<number>/<review-unit>-<slug>
    └── m<number>/closeout
```

### `main`

Completed milestones and explicitly approved maintenance only.

### `milestone/<number>-<slug>`

All accepted work for one active milestone: accepted review units, plan updates,
evidence, reference updates, and closeout.

Open one long-lived **draft cumulative PR** from the milestone branch to `main`.

### `m<number>/<review-unit>-<slug>`

One review-sized deliverable.

Every review-unit branch:

- starts from the updated milestone branch;
- **targets the milestone branch**, never `main` directly;
- contains one primary review question;
- leaves the milestone branch coherent after merge.

Prefer squash-merging review-unit PRs into the milestone branch. Merge the final
cumulative milestone PR into `main` with a **merge commit** so accepted review
units remain visible.

Do not begin the next implementation branch before the current review unit
merges unless the milestone decision log records a narrow parallel exception.

If approved maintenance reaches `main` during an active milestone, merge updated
`main` into the milestone branch before starting another review unit. Do not
rebase or force-push a published milestone branch.

### Historical deviation note

Earlier 005 work often targeted `main` directly. New review units for active
milestones must use the milestone-branch topology above. Document any temporary
exception in the milestone decision log.

## Compact Milestone Plan Structure

Active plans use these sections only.

### 1. Header

| Field | Value |
| --- | --- |
| Status | Active / pre-plan / closed |
| Milestone branch | `milestone/<number>-<slug>` |
| Cumulative PR | `#…` or `TBD` |
| Current frontier | short name — PR `#…` |
| Started | YYYY-MM-DD |
| Action policy | e.g. Idle / no movement |

### 2. Objective

One concise paragraph: what becomes possible, not how it is implemented.

### 3. Completion Usage

Stable human workflows after closeout:

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |

### 4. Scope Boundaries

One concise in-scope / out-of-scope table. Review-unit non-goals live in the PR.

### 5. Exit Criteria

Authoritative completion table:

| ID | Criterion | Status | Evidence / remaining gap |
| --- | --- | --- | --- |

Allowed statuses: `Unmet`, `Partial`, `Met`, `Blocked`.

Do **not** maintain separate remaining-for-closeout, remediation-order, package
progress, or completion-percentage sections. Those must be derivable from this
table.

### 6. Current Delivery

Exactly one current frontier and at most one next-frontier candidate.

**Current frontier** records: name, PR, branch, review kind, one review
question, affected exit criteria, prerequisite, concise milestone-level
non-goal. Link to the PR. Do **not** paste the PR matrix or file impact here.

**Next-frontier candidate** records: name, expected review kind, one likely
question, prerequisite, concise non-goal. Not started; scope may change.

**Frontier handoff:** closing the current frontier (accepting its review unit)
always updates Current Delivery so the plan still answers “what is active?”
and “what is likely next?”. Promote the existing next-frontier candidate to
current, then select at most one new next-frontier candidate—or record that
milestone closeout is next when no further in-milestone unit remains. Do not
leave Current Delivery without a current frontier while the milestone is active
(closeout becomes the current frontier when it is the active review unit).

### 7. Accepted Review Units

Append-only one-row-per-merged-PR ledger:

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

### 8. Open Risks And Unverified Assumptions

Only unresolved items that affect milestone acceptance or frontier selection.
Remove resolved rows.

### 9. Milestone Decisions

Only decisions that change objective, usage, scope, exit criteria, review-unit
boundaries, external assumptions, or activation/closeout policy.

### 10. Closeout

While active, keep minimal: blocked until every exit criterion is `Met`; list
closeout outputs. Write substantive closeout only when closeout is the current
review unit.

## Pull Request Delivery

### Attention Budget

Review size is a logical-complexity and human-attention budget, not a line-count
limit. A unit that cannot be reviewed carefully in one pass is too large.

### Singular Review Question Rule

A review question must represent one independently acceptable claim.

**Split** when:

- the question requires “and” to connect independently acceptable guarantees;
- it contains multiple primary enforcement boundaries;
- deterministic implementation and substantial live proof both require deep review;
- the reviewer must alternate between unrelated subsystems;
- one half could be accepted while the other remains false;
- repairs reveal the original abstraction cannot close the claimed class;
- closeout judgment is mixed with unfinished implementation.

**Do not split** merely because the diff is large, several files participate in
one contract, one invariant needs coordinated tasks, or a repair adds adjacent
paths and tests.

### Review Kinds

| Kind | Focus |
| --- | --- |
| Deterministic invariant closure | Universal guarantee, owner, bypasses, boundaries, final external values |
| Behavioral feature slice | User path, success/failure, contract compatibility |
| Broad mechanical rollout | Faithful application of an accepted pattern; link pattern PR |
| Live or external evidence | Procedure, artifacts, assumptions, non-claims; CI alone is insufficient |
| Review repair | Separate PR only when needed; root cause, owner, adjacent paths, regressions |
| Milestone closeout | Whole-milestone acceptance judgment |

### Review-Unit PR States

| State | Meaning |
| --- | --- |
| Draft | Question still changing, required behavior missing, validation incomplete, or adversarial pass incomplete |
| Ready for review | Singular stable question, complete for scope, validation recorded, limitations explicit, description matches diff |
| Changes requested | Stated question cannot yet be answered affirmatively |
| Approved | Reviewer accepts that this PR answers its stated question within its scope, assumptions, and non-goals |

Approval does **not** mean the milestone is complete, every improvement belongs
in this PR, the next frontier is automatically approved, or external
assumptions are proven.

### Review-Unit PR Template

Use `.github/pull_request_template.md` (required headings):

- Milestone context
- Review kind
- Review question
- User or operator impact
- Deliverable
- Invariant or acceptance contract
- Enforcement or acceptance owner
- Affected paths
- Adversarial matrix
- External assumptions
- Unverified limits
- Scope (in / out)
- File impact
- Validation
- Review notes

Detailed matrices and file impacts belong **only** in the PR, not the milestone plan.

### Invariant Closure (When Claiming Universals)

Words such as `bounded`, `detached`, `deterministic`, `exact`, `fail-closed`,
`fresh`, and `no movement` are universal guarantees, not positive-path
examples. Record invariant, owner, affected paths, adversarial matrix, external
assumptions, and unverified limits in the PR.

Before requesting review:

1. Test the failure class and adjacent paths, not only the first reproduction.
2. Enforce at the owning boundary.
3. Validate the final externally visible value after normalize/store/serialize.
4. Prove cross-system assumptions against the relevant live system before
   presenting them as observed.
5. Perform one fresh adversarial pass after a repair before re-review.

### Review Finding Format

```markdown
[P1] <Concise finding title>

**Violated contract**
<Invariant or acceptance condition that does not hold.>

**Bypass or failure class**
<How the implementation escapes the owning boundary.>

**Reproduction**
<Concrete input, state, command, or test.>

**Why this belongs in the current PR**
<Why it challenges the stated review question.>

**Required outcome**
<Observable result required for acceptance.>
```

Severities: `P0` unsafe/destructive; `P1` stated question materially false;
`P2` meaningful adjacent gap (normally fix before merge); `P3` nonblocking.

### Author Repair Response

```markdown
## Review Repair Summary

Revision: `<commit>`

### Finding 1 — <title>

- Root cause:
- Owning boundary changed:
- Adjacent paths audited:
- Regression coverage:
- Remaining assumption:

## Validation

<commands and results>

## Fresh Adversarial Pass

<Additional cases checked after repair>
```

One review-and-repair cycle is normal. After two substantial repair cycles for
the same invariant, reconsider abstraction, enforcement location, PR scope, and
whether the question is singular.

### Cumulative Milestone PR

Use `.github/PULL_REQUEST_TEMPLATE/milestone.md`. Keep it compact: objective,
link to completion usage and exit criteria, list of accepted review units,
status, unresolved risks, final validation at closeout. Do not paste every
child PR matrix.

## Merge And Promotion Procedure

After a review-unit PR is accepted (frontier handoff):

1. squash-merge into the milestone branch;
2. add one accepted-review-unit row to the plan;
3. update affected exit criteria;
4. update unresolved risks;
5. **promote** the next-frontier candidate to **current frontier** (or make
   milestone closeout the current frontier when that is the next unit);
6. **select at most one new next-frontier candidate**—or explicitly record that
   no further in-milestone candidate remains and closeout is next;
7. branch the new current frontier’s review unit from the updated milestone
   branch (unless closeout is deferred until remaining criteria are met).

Steps 5–6 are mandatory plan edits on every accepted review unit. Promoting
without naming the following candidate (or “closeout next”) leaves the plan
without a planning handoff.

Prefer drafting or revising the next-frontier candidate on the current review
unit’s PR so the same human pass can accept this unit and the likely next
scope. Final promotion still happens only after merge (steps 5–6).

At milestone closeout:

1. complete `closeout.md`;
2. update the completed-milestone ledger;
3. update `docs/README.md` navigation only;
4. mark the cumulative milestone PR ready;
5. review the milestone as a whole;
6. merge into `main` with a merge commit;
7. tag the mainline merge;
8. remove obsolete milestone and review-unit branches;
9. activate or revise the next pre-plan only after closeout
   (this is the cross-milestone handoff, distinct from in-milestone
   next-frontier selection in steps 5–6 above).

## Immediate Deferred Work And Pre-Plans

Closeouts may leave residual work. Route it into exactly one of:

1. **Durable reference** (`docs/reference/`): settled current behavior.
2. **Synthesis** (`docs/synthesis/`): research without commitment.
3. **At most one pre-plan** after the active milestone: the single most immediate
   next problem already forced by evidence.

Pre-plans are not active work. Do not implement them while another milestone is
active unless the decision log records an explicit parallel exception.

## Shared Contract Visibility

Active plans should link this contract (Markdown and/or rendered HTML). Do not
copy its rules into individual plans. Do not edit `planning-contract.html`
directly.

## Non-Goals Of This Contract

This contract does not:

- redesign product architecture;
- create a detailed long-term roadmap;
- introduce many package sub-IDs;
- turn the milestone plan into a ticket backlog;
- copy PR-level matrices into the plan;
- require one branch per implementation task;
- equate PR quality with line count;
- make generated HTML a second manually edited source of truth.
