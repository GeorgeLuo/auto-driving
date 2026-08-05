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
3. review-sized proposal and implementation PR deliverables (review units);
4. implementation tasks inside an accepted proposal;
5. external evidence units;
6. milestone closeout;
7. Git branches.

Minimize manual synchronization after a merge. A normal accepted implementation
should require only a handful of milestone-plan edits:

1. add one accepted-review-unit ledger row;
2. update affected exit criteria;
3. update unresolved risks;
4. promote the next frontier;
5. optionally select one new next-frontier candidate with a **minimal
   pre-implementation acceptance contract**, or leave the next-candidate slot
   explicitly empty with a reason.

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
- one **next-frontier slot** containing at most one candidate;
- a **preparation horizon** for later provisional needs.

The frontier determines priority and readiness. It is not a detailed speculative
roadmap.

A **next-frontier candidate** is not a name stub. Selecting one defines a
minimal pre-implementation acceptance contract so promotion can open a proposal
branch against a frozen scope rather than inventing the unit during coding.
Full adversarial matrices, file impact, and exact validation are settled in the
independent proposal before implementation begins.

Promote work to the **current** frontier only when it is:

1. **contractable now** through one review question; and
2. **reviewable in one careful human pass**.

Select a **next-frontier candidate** only when those same readiness conditions
hold **and** the minimal acceptance fields below are filled. A title with a
vague likely question is not a candidate.

An empty next-frontier slot is honest when current evidence does not yet justify
another contract or when milestone closeout is already current. Record why it is
empty and what decision or evidence could fill it. Do not invent speculative
scope merely to keep the slot populated.

Human review attention is the throughput limit. Prefer fewer sequential units
that close a contractable edge over many named subdivisions that multiply
handoffs.

### Review Unit

A **review unit** is one complete pull-request deliverable. A frontier normally
has a proposal review unit followed by an implementation review unit.

It proposes, implements, or proves one frontier claim and answers **one primary
review question**.

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

A bounded live check may remain in an implementation review unit when it is
immediate, requires no additional implementation, and adds little independent
review burden. Split it into an evidence unit when it needs separate environment
preparation, repeatable operator procedure, tracked artifacts, or an acceptance
judgment that could fail while the implementation contract still passes.

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
| Detailed invariant and adversarial matrix | Accepted proposal document |
| Planned file impact and validation commands | Accepted proposal document |
| Actual file impact and validation results | Implementation PR |
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
├── proposals/       # independently reviewed frontier contracts
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
    ├── m<number>/<frontier>-proposal
    ├── m<number>/<frontier>
    └── ...
```

### `main`

Completed milestones and explicitly approved maintenance only.

### `milestone/<number>-<slug>`

All accepted work for one active milestone: accepted review units, plan updates,
evidence, reference updates, and closeout.

Open one long-lived **draft cumulative PR** from the milestone branch to `main`.

### Frontier branches

Each frontier has two independently reviewed branches:

- `m<number>/<frontier>-proposal` contains only the tracked proposal, canonical
  plan transition, and generated plan HTML;
- `m<number>/<frontier>` implements only the accepted proposal.

If evidence shows that an accepted proposal is materially wrong before its
implementation is accepted, an optional `m<number>/amend-<slug>` branch may add
a proposal amendment. It is a contract review unit, not a third implementation
branch.

Both branches:

- start from the updated milestone branch at their permitted workflow state;
- **targets the milestone branch**, never `main` directly;
- contains one primary review question;
- leaves the milestone branch coherent after merge.

Prefer squash-merging both PRs into the milestone branch. Proposal merge is an
approval receipt, not implementation acceptance. Merge the final cumulative
milestone PR into `main` with a **merge commit** so accepted frontier history
remains visible.

Do not create an implementation branch until its proposal PR has merged and the
workflow records `ready_for_implementation`. Do not begin the next frontier
before the current implementation PR merges unless the milestone decision log
records a narrow parallel exception.

If approved maintenance reaches `main` during an active milestone, merge updated
`main` into the milestone branch before starting another review unit. Do not
rebase or force-push a published milestone branch.

### Historical deviation note

Earlier 005 work often targeted `main` directly. New review units for active
milestones must use the milestone-branch topology above. Document any temporary
exception in the milestone decision log.

### Adopting This Contract Mid-Milestone

Do not pretend an already-active milestone always used this topology. Its plan
must record:

1. a **historical baseline** commit or accepted-review-unit summary for work
   merged before adoption;
2. a `Grandfathered PRs` header field naming every open grandfathered PR, with
   its existing target branch and whether it keeps a mixed review kind
   temporarily;
3. the exact **cutover point** after which review units use the milestone branch;
4. how conflicting hand-authored and generated planning files will resolve in
   favor of the canonical Markdown source; and
5. whether the first cumulative PR is a transitional closeout delta rather than
   a literal diff of all earlier milestone work.

Do not retarget or reconstruct a published historical PR merely for topology
purity when doing so adds risk without improving its review. Reconcile its
description and evidence to the new contract proportionately, then begin the new
branch model at the declared cutover.

## Compact Milestone Plan Structure

Active plans use these sections only.

### 1. Header

| Field | Value |
| --- | --- |
| Status | Active / Blocked / pre-plan / closed |
| Milestone branch | `milestone/<number>-<slug>` |
| Cumulative PR | `#…` or `TBD` |
| Current frontier | short name |
| Started | YYYY-MM-DD |
| Action policy | e.g. Idle / no movement |

### 2. Objective

One concise paragraph: what becomes possible, not how it is implemented.

### 3. Completion Usage

Stable human workflows after closeout:

| Workflow | Starting state | Execution | Success signal | Criteria |
| --- | --- | --- | --- | --- |

The first body row must be `Primary demonstration`. It states one bounded,
end-to-end feature outcome that a human can execute and recognize after
closeout. Keep it to one row and leave schemas, lifecycle matrices, edge cases,
and validation mechanics to the frontier proposal. Supporting workflow rows may
then cover setup, inspection, replay, or environment-specific execution without
creating another feature-goal section.

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

Exactly one current frontier while a milestone is active, plus one
next-frontier slot containing zero or one candidate.

**Current frontier** records: name, workflow state, separate proposal and
implementation branches, proposal path, review kind, one review question,
enforcement or acceptance owner, affected exit criteria, prerequisite, and
concise milestone-level non-goal. Record the accepted proposal PR and merge
commit before implementation starts. Record each accepted additive proposal
amendment with its artifact path, PR, and merge commit. Add the active PR only
for the phase currently under review.

When populated, the **next-frontier candidate** is a pre-implementation
acceptance contract. It is valid only when it records at least:

- **name;**
- **planned proposal branch;**
- **planned implementation branch;**
- **planned proposal path;**
- **expected review kind;**
- **one review question** stable enough that promotion would open proposal work
  against it;
- **enforcement or acceptance owner** (module, boundary, procedure surface, or
  closeout judgment surface);
- **affected exit criteria** (stable IDs);
- **prerequisite;**
- **concise non-goals** (what must not leak into that unit).

It is not started and must not yet have either branch or a PR. The acceptance
boundary—question, owner, non-goals, and affected exit criteria—is frozen before
the proposal branch opens. The proposal then defines implementation detail,
adversarial matrix, file impact, and validation plan in a tracked document.
That proposal is reviewed and merged independently, without implementation
changes. Only its accepted merge may move the frontier to
`ready_for_implementation` and permit the implementation branch to open.
Prerequisite status and residual evidence may update as the current frontier
lands; do not silently widen the candidate’s question or non-goals after
proposal work starts.

A name plus a vague “likely question” alone is not a candidate. Use an explicit
empty slot instead:

```markdown
### Next-Frontier Candidate

**None**

- Reason: <why another contract is not justified now>
- Revisit when: <named evidence, decision, or closeout result>
```

The empty slot opens no proposal or implementation branch. It is the required terminal state
while closeout is current, and it may also be used while a named blocker prevents
honest candidate selection.

**Frontier handoff:** closing the current frontier (accepting its review unit)
always updates Current Delivery so the plan still answers “what is active?”
and “what may be next?”. Promote the existing next-frontier candidate to current
(its pre-implementation contract becomes the current unit’s acceptance
boundary), then reset the next slot to explicit `None`. A later candidate enters
through the newly current review unit so it is visible in that PR before it can
be promoted; the mechanical handoff must not invent one. Do not leave Current
Delivery without a current frontier while the milestone is active: closeout
becomes current when it is the active review unit, and a bounded decision or
evidence unit becomes current when more evidence is required to choose
implementation work.

### 7. Workflow History

Append-only state-transition ledger:

| Frontier | State | Evidence |
| --- | --- | --- |

The latest row must match the current frontier and its machine-readable workflow
state. Preserve proposal acceptance and implementation acceptance as separate
events.

### 8. Accepted Review Units

Append-only one-row-per-merged-PR ledger:

| PR | Accepted review question | Result | Exit criteria | Durable evidence |
| --- | --- | --- | --- | --- |

### 9. Open Risks And Unverified Assumptions

Only unresolved items that affect milestone acceptance or frontier selection.
Remove resolved rows.

### 10. Milestone Decisions

Only decisions that change objective, usage, scope, exit criteria, review-unit
boundaries, external assumptions, or activation/closeout policy.

### 11. Closeout

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

### Proposal And Implementation Are Separate

Every frontier moves through these states in order, with an optional amendment
loop after proposal acceptance:

| Workflow state | Meaning | Permitted work |
| --- | --- | --- |
| `ready_for_proposal` | The bounded frontier is ready to hand to a proposal author | Start the proposal branch, or review a necessary pre-proposal plan revision |
| `proposal_in_review` | A proposal is being authored or reviewed | Proposal document and plan transition only |
| `ready_for_implementation` | The proposal PR and any amendments merged and their exact commits are recorded | Start the implementation branch, or start a bounded proposal amendment when established evidence requires one |
| `proposal_amendment_in_review` | New evidence requires a bounded correction to the accepted proposal | Additive amendment document and plan transition only; implementation remains blocked |
| `implementation_in_review` | Accepted scope is being implemented or reviewed | Product, test, and documentation changes described by the accepted proposal |

The expected collaboration is explicit:

1. the reviewer reports **ready for proposal** and stops;
2. the operator gives proposal work to the proposal author;
3. the reviewer reviews and finalizes that proposal without implementation;
4. proposal merge records acceptance and the reviewer reports **ready for
   implementation**;
5. the operator gives the accepted proposal to the implementer;
6. implementation review begins only after implementation is complete enough
   to answer the accepted review question.

The proposal author and implementer may be the same person or model, but they
must operate in separate branches and review phases. The reviewer must not
silently fill both roles in one change.

If the frozen frontier is found to be wrong before proposal work starts, revise
it in a separate plan-only review unit. Use a
`m<number>/plan-<slug>` branch, keep the workflow state
`ready_for_proposal`, and change only canonical `plan.md` plus generated
`plan.html`. Preserve accepted review-unit evidence and every existing `Met`
criterion, append one Workflow History row whose evidence begins
`Plan revision:`, and do not add a proposal, tests, or product code. The merged
revision returns to the normal `ready_for_proposal` handoff; it does not count
as proposal acceptance or authorize implementation.

If the accepted proposal is later shown to be materially insufficient, amend
it before implementation acceptance instead of rewriting history or knowingly
shipping the same gap into another frontier. Existing evidence of a
deterministic failure is sufficient to justify amendment review; do not require
a redundant live run merely to reproduce a condition already established. Use
`m<number>/amend-<slug>` and a new document under the frontier's `proposals/`
directory. The amendment PR may change only that new artifact, canonical
`plan.md`, and generated `plan.html`. It must preserve the original accepted
proposal, prior amendments, exit-criterion state, accepted ledger, risks, and
queued frontier.

An amendment document starts with `# Proposal Amendment:` and records Review
Question, Reason For Amendment, Contract Delta, Ownership, Affected Paths,
Adversarial Matrix, External Assumptions, Non-Goals, File Impact, and Validation
Plan. It narrows or corrects the implementation contract; it cannot replace the
proposal's reviewed Expected Handoff. After merge, record the amendment PR,
exact merge commit, and artifact path, then return the frontier to
`ready_for_implementation`. Amendments are cumulative and immutable. The
implementation PR must link and reconcile the original proposal plus every
accepted amendment, and CI rejects changes to any of those artifacts.

Each proposal lives at the current frontier’s declared `proposal path` and uses
`.github/PULL_REQUEST_TEMPLATE/proposal.md`. It records the review question,
proposed contract, owner, affected paths, adversarial matrix, assumptions,
non-goals, file impacts, validation plan, and the expected successful handoff.
It contains no product code, tests of unimplemented behavior, generated runtime
artifacts, or implementation repair.

`## Expected Handoff` contains exactly one `json` code block. It uses
`milestone_handoff_template_v1`, which is the normal handoff receipt without
`accepted_pr` or `accepted_merge_commit`. Those facts do not exist until merge.
The reviewed template may use `{pr}` and `{merge_commit}` inside strings; the
completion command substitutes them without changing any other judgment:

```json
{
  "schema": "milestone_handoff_template_v1",
  "outcome": "advance",
  "result": "Accepted",
  "durable_evidence": "Accepted implementation and focused tests in PR #{pr}",
  "criterion_updates": {
    "M000-01": {
      "status": "Met",
      "evidence": "Contract accepted in PR #{pr}"
    }
  },
  "risk_remove": [],
  "risk_upsert": [],
  "next_frontier": {
    "state": "none",
    "reason": "No later candidate is reviewed.",
    "revisit_when": "The promoted frontier determines what follows."
  }
}
```

Proposal validation simulates the later implementation handoff against the
frozen plan. It rejects templates that update unowned criteria, remove unknown
risks, promote closeout while other criteria remain unmet, or invent an
unreviewed next candidate.

Each implementation PR uses `.github/pull_request_template.md`, links the
accepted proposal PR and merge commit, and reconciles its actual diff to that
proposal. A changed proposal requires a new proposal review; implementation may
not rewrite its own acceptance boundary.

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

### Proposal PR Template

Use `.github/PULL_REQUEST_TEMPLATE/proposal.md`. The proposal document itself is
the durable contract; the PR body gives the reviewer its milestone context,
question, scope, and explicit confirmation that no implementation is present.

### Implementation PR Template

Use `.github/pull_request_template.md` (required headings):

- Milestone context
- Accepted proposal
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
- Scope reconciliation
- Validation
- Review notes

Detailed matrices, file impacts, and validation design originate in the
accepted proposal. The implementation PR reconciles them to observed work
without silently changing them.

### Invariant Closure (When Claiming Universals)

Words such as `bounded`, `detached`, `deterministic`, `exact`, `fail-closed`,
`fresh`, and `no movement` are universal guarantees, not positive-path
examples. Settle invariant, owner, affected paths, adversarial matrix, external
assumptions, and unverified limits in the proposal. The implementation PR
reports how the accepted contract was enforced and validated.

Before requesting review:

1. Test the failure class and adjacent paths, not only the first reproduction.
2. Enforce at the owning boundary.
3. Validate the final externally visible value after normalize/store/serialize.
4. Prove cross-system assumptions against the relevant live system before
   presenting them as observed.
5. Perform one fresh adversarial pass after a repair before re-review.

### Externally Owned Capability Gaps

Treat a separately owned repository as an available contract owner, not as an
unchangeable black box. Metrics UI is the primary simulator example for this
repository.

When an operator journey is blocked or made situational by an external
capability:

1. inspect the installed and documented interface and record concrete version,
   command, protocol, or response evidence;
2. identify whether the clean enforcement boundary belongs locally or in the
   external repository;
3. prefer the smallest owner-level capability, flag, query, or structured
   failure contract over UI automation, undocumented state scraping, implicit
   reconfiguration, duplicated protocol logic, or a permissive local fallback;
4. state the external gap, its consequence, and whether the current review
   question can still be accepted without it;
5. surface an external feature request as an explicit option instead of
   silently treating the dependency as fixed; and
6. link an authorized external issue from the relevant proposal, PR, evidence,
   or unresolved-risk record.

Creating or updating an issue changes external state. Do it only when the
operator explicitly authorizes the write or an accepted workflow step
specifically includes external issue creation. Read-only repository and issue
inspection may be used to resolve ownership and avoid duplicates.

An external request should be independently actionable. Include:

- the blocked user or operator journey;
- observed interface and version evidence;
- the minimum requested contract and acceptable equivalent outcomes;
- required safety and state-preservation behavior;
- structured unsupported or failure behavior;
- a bounded acceptance test; and
- links back to the consuming proposal or implementation.

If a small external flag or response field would remove a substantial local
workaround, say so directly. Do not conceal the option merely because the
dependency lives in another repository.

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

Before every review or re-review request, reconcile the PR description to the
current diff. Refresh the review question when its wording no longer matches,
the enforcement or acceptance owner, affected paths, adversarial matrix, file
impact, external assumptions, unverified limits, and exact validation results.
Summarize meaningful scope deepening and state whether it still closes the same
claim. Do not make the reviewer reconstruct the actual contract from commit
history.

### Cumulative Milestone PR

Use `.github/PULL_REQUEST_TEMPLATE/milestone.md`. Keep it compact: objective,
link to completion usage and exit criteria, list of accepted review units,
status, unresolved risks, final validation at closeout. Do not paste every
child PR matrix.

## Merge And Promotion Procedure

Inspect the current handoff before assigning work:

```sh
python3 docs/milestones/workflow.py status \
  --plan docs/milestones/<number>-<slug>/plan.md
```

When it reports `ready_for_proposal` but the scope itself needs review, create a
`m<number>/plan-<slug>` branch and open a plan-only PR to the milestone branch.
CI recognizes that reserved branch shape and rejects changes outside canonical
`plan.md` and generated `plan.html`. After that PR merges, inspect status again
and hand the revised frontier to the proposal author.

When it reports `ready_for_proposal`, create only the proposal branch:

```sh
python3 docs/milestones/workflow.py start-proposal \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --branch m<number>/<frontier>-proposal
```

The proposal author commits the proposal artifact, plan transition, and rendered
HTML, then opens a proposal PR to the milestone branch. After review and merge,
the maintainer updates the clean milestone branch and records acceptance:

```sh
python3 docs/milestones/workflow.py accept-proposal \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --pr <proposal-pr-number>
```

Inspect and commit the resulting plan and HTML transition. If known evidence
requires a bounded contract correction, start an additive amendment instead:

```sh
python3 docs/milestones/workflow.py start-proposal-amendment \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --branch m<number>/amend-<slug> \
  --path docs/milestones/<number>-<slug>/proposals/<slug>-amendment.md
```

After that contract-only PR merges, record its exact acceptance receipt:

```sh
python3 docs/milestones/workflow.py accept-proposal-amendment \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --pr <amendment-pr-number>
```

Only when status reports `ready_for_implementation` may the implementation
branch start:

```sh
python3 docs/milestones/workflow.py start-implementation \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --branch m<number>/<frontier>
```

After the implementation PR is accepted:

1. squash-merge it into the milestone branch;
2. from a clean local milestone branch, run the completion command below;
3. confirm its reported frontier and workflow state;
4. open the new current frontier’s proposal branch only after completion.

```sh
python3 docs/milestones/workflow.py complete-implementation \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --pr <implementation-pr-number>
```

`complete-implementation` fetches and fast-forwards the milestone branch,
confirms the implementation PR is merged from the planned branch, fills the
reviewed template with the PR number and merge SHA, applies the existing
handoff owner, verifies that only canonical `plan.md` and generated `plan.html`
changed, commits them, and pushes the milestone branch. It stops at
`ready_for_proposal`; it never starts the next proposal branch.

The lower-level `handoff --receipt <path>` command remains available for a
reviewed exceptional receipt or recovery, but normal successful completion
must not reconstruct acceptance judgment after merge.

The helper, rather than agent memory, enforces the local order. It refuses a
dirty worktree, the wrong branch, a branch/state mismatch, an unmerged proposal,
an implementation start without an accepted proposal, an implementation PR
from the wrong branch, or a merge commit that is not already an ancestor of the
milestone branch. Proposal acceptance asks GitHub to confirm the exact base,
head, merge commit, and changed-file allowlist. Implementation completion asks
GitHub to confirm the implementation PR and commit, then limits criterion
updates to the current frontier, prevents premature closeout, updates the
accepted ledger and risks, promotes the reviewed next candidate to
`ready_for_proposal`, records workflow history, and regenerates HTML.

CI runs `workflow.py validate-pr` on every PR targeting a milestone branch. A
proposal PR may change only its declared proposal document, canonical plan, and
generated plan HTML. A proposal amendment PR has the same contract-only
boundary and must add a new artifact without modifying accepted proposal
history. An implementation PR is rejected unless its base records an accepted
proposal; it may not modify that proposal, an accepted amendment, or the frozen
frontier.
`docs/render_markdown.py` invokes the same plan validator, so hand-edited state
that omits required fields or history is rejected.

The machine cannot prove which model authored a phase, that a reviewer
understood a proposal, or that approval was intellectually sound. The operator
owns those judgments. What the repository does guarantee is that the current
state and next handoff are visible, the accepted proposal is durable, proposal
and implementation diffs are separate, and implementation cannot pass CI
before proposal acceptance.

The handoff commit is a narrow exception to PR-only changes because it applies
mechanical post-merge facts that cannot truthfully exist in the merged review
unit beforehand. It must not introduce code, widen an acceptance contract,
invent an unreviewed candidate, or change milestone scope. If the handoff needs
judgment beyond the already reviewed plan state, use a plan-only review unit
instead.

Drafting or revising the next candidate on the current PR is optional. It gives
the reviewer visibility into the likely handoff, but it is not a second review
question and current-PR acceptance must not depend on accepting future scope.
After that candidate is promoted, its own review unit may introduce a later
candidate. When none is ready, leave the slot empty rather than forcing one.

At milestone closeout:

1. complete `closeout.md`;
2. update the completed-milestone ledger;
3. update `docs/README.md` navigation only;
4. mark the cumulative milestone PR ready;
5. review the milestone as a whole;
6. merge into `main` with a merge commit;
7. tag the mainline merge;
8. remove obsolete milestone, proposal, and implementation branches;
9. activate or revise the next pre-plan only after closeout
   (this is the cross-milestone handoff, distinct from in-milestone
   frontier handoff above).

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

## Selective Agent Operating Surface

`docs/guidance/` is a short, derived operating surface for selective agent
loading. It exists to reduce repeated context cost; this contract remains the
single source of truth.

For repository-aware agents, root `AGENTS.md` is the automatic entrypoint. It
routes each requested operation through `docs/guidance/agent-surface.md`, then
only the selected role- or task-specific guidance. Load this full contract when
a guidance file directs it, when workflow meaning is ambiguous, or when
changing the workflow itself.

Guidance files may summarize or route to this contract. They must not introduce
new process rules, carry current milestone state, or override this contract. If
the two conflict, this contract wins. Operation classification does not
authorize a workflow phase transition. Long-running conversations should
retain current work state and findings, not act as the durable store for
process rules.

## Non-Goals Of This Contract

This contract does not:

- redesign product architecture;
- create a detailed long-term roadmap;
- introduce many package sub-IDs;
- turn the milestone plan into a ticket backlog;
- copy proposal-level matrices into the plan;
- require one branch per implementation task;
- equate PR quality with line count;
- make generated HTML a second manually edited source of truth.
