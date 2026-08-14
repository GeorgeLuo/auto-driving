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

For a universal or deterministic implementation claim, the proposal chooses the
evidence topology before implementation starts. It states whether bounded proof
remains in the implementation review unit or whether capture and acceptance use
a later evidence review unit. Do not begin canonical live-artifact capture until
the proposal's stated capture-readiness conditions hold; repeated capture while
the artifact schema, authority mapping, semantic verifier, or adversarial
mutation cases are still changing is contract discovery, not acceptance proof.

### Repair Cycle

A review finding remains in the existing PR when it still challenges that PR’s
stated contract.

A **repair cycle** is one consolidated changes-requested verdict followed by an
author revision that addresses that verdict. Count the round once regardless of
how many findings, commits, or comments it contains. Repeated discussion against
the same repair revision is still the same cycle; a later consolidated verdict
against a newer revision followed by another repair is the next cycle.

The reviewer classifies the cycle in the verdict. It is **substantial** when
either the verdict contains a P0–P2 contract failure or the repair changes the
review question, contract, primary owner or abstraction, material scope or file
impact, external assumptions, or adversarial failure class. Editorial cleanup,
evidence formatting, and localized P3 corrections are **minor** only when none
of those conditions applies. A disputed or omitted classification is treated as
substantial until the reviewer resolves it.

A repair response should identify:

- root cause;
- owning enforcement boundary changed;
- adjacent paths audited;
- regression coverage added;
- assumptions still unverified.

Every review-unit PR body keeps a `Repair Cycle Ledger` with the verdict receipt,
classification, repair revision, and contract impact for each cycle. The count
belongs to the review unit and does not reset after force-push, reopen, or a
change of author.

After the **second substantial** cycle, stop before requesting re-review. A
human operator or meta-manager—not the repair author acting alone—must record a
durable escalation decision and select exactly one route:

- `replan-current-unit` when the accepted contract remains unchanged and the
  question is still singular, but the owner or implementation approach needs an
  explicit reset;
- `proposal-amendment` when the accepted contract must change;
- `split-or-replace-review-unit` when the question or scope is not singular; or
- `abandon-review-unit` when the claim should not proceed.

Record that receipt, route, and disposition in `Repair Escalation` before
re-review. A third substantial cycle cannot remain in the same review unit; it
must be split, replaced, amended through a replacement implementation review,
or abandoned. A replacement review unit starts its own count but links the
decision receipt and superseded PR so the reset is explicit rather than a way to
erase repair history.

Create a separate repair review unit only when a distinct PR is genuinely
necessary.

### HITL Implementation Adjunct

A **HITL implementation adjunct** is an exceptional child review unit for a
bounded change first requested by a human during hands-on testing after an
implementation review has started. It targets the canonical implementation
branch, not the milestone branch, and leaves the frontier in
`implementation_in_review`.

An adjunct is neither a repair nor a contract amendment. The parent’s accepted
contract must remain true without it, while the requested behavior is additive,
compatible, and useful to the same frontier and operator journey. Human
direction supplies the need and `implement-now` priority; it does not waive
contract compatibility, safety review, or evidence refresh.

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
| Detailed invariant, trust/authority model, evidence topology, and adversarial matrix | Accepted proposal document |
| Planned file impact and validation commands | Accepted proposal document |
| Actual file impact and validation results | Implementation PR |
| Human user-testing request and implement-now direction | Durable issue and adjunct PR |
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
    │   └── m<number>/<frontier>--adjunct-<slug>
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

Prefer squash-merging both PRs into the milestone branch. A proposal's
exact-head contract review and merge together form its approval receipt; merge
alone is not proposal acceptance or implementation acceptance. Merge the final
cumulative milestone PR into `main` with a **merge commit** so accepted
frontier history remains visible.

### HITL implementation adjunct branches

When a human explicitly requests an eligible additive change during hands-on
testing, branch `m<number>/<frontier>--adjunct-<slug>` from the current head of
the canonical `m<number>/<frontier>` implementation branch. The adjunct PR:

- targets that implementation branch, never the milestone branch or `main`;
- uses `.github/PULL_REQUEST_TEMPLATE/implementation-adjunct.md`;
- links the parent implementation PR and durable operator-request issue;
- records the HITL discovery context and explicit `implement-now` disposition;
- contains one bounded review question and compatibility assertion; and
- does not change the milestone plan, accepted proposal, or accepted amendment.

Do not base an adjunct on another adjunct. Keep it current with the parent
implementation branch, merge it back into that parent, then re-review the
parent PR in totality. The parent implementation remains the frontier’s sole
acceptance and ledger unit.

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

The current frontier and any populated next-frontier candidate must use one of
the supported values in [Review Kinds](#review-kinds). The value is the stable
review focus for that frontier across its proposal, any proposal amendments,
and its implementation.

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

The values below are the complete supported set for canonical milestone plans
and review-unit PR bodies. Use one value; do not invent a hybrid label.

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
| `ready_for_implementation` | The proposal PR and any amendments have accepted exact-head contract reviews, are merged, and have their reviewed heads and merge commits recorded | Start the implementation branch, resume a paused implementation after amendment, or start a bounded proposal amendment when established evidence requires one |
| `proposal_amendment_in_review` | New evidence requires a bounded correction to the accepted proposal | Additive amendment document and plan transition only; implementation remains blocked and any in-flight implementation PR must be paused or closed |
| `implementation_in_review` | Accepted scope is being implemented or reviewed | Product, test, and documentation changes described by the accepted proposal. A recorded `proposal-amendment` escalation may start a contract-only amendment after the implementation PR is paused or closed. |

The expected collaboration is explicit:

1. the reviewer reports **ready for proposal** and stops;
2. the operator gives proposal work to the proposal author;
3. the reviewer reviews and finalizes that proposal without implementation;
4. the reviewer records an accepted review on the proposal's exact final head;
   merge and the acceptance command then record both commits, and the reviewer
   reports **ready for implementation**;
5. the operator gives the accepted proposal to the implementer;
6. implementation review begins only after implementation is complete enough
   to answer the accepted review question.

The proposal author and implementer may be the same person or model, but they
must operate in separate branches and review phases. The reviewer must not
silently fill both roles in one change.

### Exact-Head Contract Review Receipts

A proposal or proposal amendment must have an accepted GitHub review attached
to the PR's final head commit before merge. The review is the contract judgment;
the subsequent merge establishes repository ancestry. They are separate facts,
and neither substitutes for the other. An authorized contract reviewer must
have current repository push authority and an `OWNER`, `MEMBER`, or
`COLLABORATOR` association when acceptance is recorded.

- An `APPROVED` review records `accepted`.
- A `CHANGES_REQUESTED` review records `changes_requested`.
- When GitHub prevents a reviewer from approving their own PR, a new, unedited
  formal `COMMENTED` review may contain only:

  ```text
  ## Contract Review Receipt

  - Outcome: `accepted`
  ```

  Use `changes_requested` instead when the contract is not acceptable.
- Only formal GitHub reviews count. PR conversation comments are not bound to a
  commit and never count as contract receipts.
- For each authorized reviewer, their latest decisive review on the exact head
  owns their outcome. Promotion requires at least one accepted outcome and no
  authorized reviewer with an outstanding `changes_requested` outcome.
- A later commit invalidates every receipt attached to an earlier head and
  requires another review. A review submitted or edited after merge cannot
  retroactively authorize promotion.

The proposal acceptance commands verify the complete review history within a
bounded 100-review window, fail closed if that window would truncate, compare
`headRefOid` with each review's commit, enforce reviewer authority and
pre-merge timing, and record the reviewer, authority, review time, reviewed
head, and merge commit in the canonical plan. A merged PR without that receipt
remains `proposal_in_review`; do not begin implementation.

Every proposal, proposal amendment, and implementation PR body must provide
exactly one completed `## Review Kind` section. Its value must be supported and
must match the current frontier's canonical plan value. This keeps the review
focus stable across the proposal and implementation phases; changing the kind
requires a reviewed plan revision before proposal work starts, not a PR-body
reclassification during delivery.

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
proposal's reviewed Expected Handoff. After exact-head contract review and
merge, record the amendment PR, reviewed head, exact merge commit, and artifact
path, then return the frontier to `ready_for_implementation`. Amendments are
cumulative and immutable. The implementation PR must link and reconcile the
original proposal plus every accepted amendment, and CI rejects changes to any
of those artifacts.

When an amendment's Review Question or Contract Delta introduces or changes a
universal invariant, its artifact also completes `## Trust And Authority Model`
and `## Evidence Topology And Capture Strategy` for that delta. An amendment
cannot bypass contractability requirements merely because the original proposal
has already been accepted.

### Human Discovery During Implementation

Classify a human request from hands-on testing before changing code:

| Discovery | Required route |
| --- | --- |
| The parent review question is false without the change | Repair the parent implementation PR; this is not adjunct scope |
| The accepted contract, exit criteria, safety authority, schema, external assumption, expected handoff, or explicit non-goal must change | Stop and use proposal-amendment or later-frontier review; never conceal the change in an adjunct |
| The parent contract remains true and the human explicitly wants a bounded additive change in the same journey now | Use a HITL implementation adjunct |
| The request has a different goal, journey, primary owner, or independently acceptable feature outcome | Queue and contract a later frontier |

An adjunct is eligible only when all of the following are true:

1. a durable issue records the human user-testing request, and the adjunct PR
   records the requester, discovery context, and `implement-now` direction;
2. the parent implementation is already in `implementation_in_review` and the
   request serves its current frontier and operator journey;
3. the change is additive or optional, and every parent contract claim remains
   true if the adjunct is omitted;
4. it changes no exit criterion, safety or enforcement authority, schema,
   external assumption, expected handoff, or explicit non-goal;
5. it changes no canonical plan, accepted proposal, accepted amendment, or
   workflow state;
6. it has one bounded acceptance owner and one review question; and
7. it declares which evidence remains valid, which evidence must be refreshed,
   and what parent-level integration check will be run.

The human request authorizes consideration and priority, not a compatibility
waiver. If any eligibility assertion is uncertain, do not start the adjunct;
route the request through repair, amendment, or frontier planning.

Open the child PR from
`m<number>/<frontier>--adjunct-<slug>` to `m<number>/<frontier>`. Prefer one
active adjunct at a time. After it is reviewed and merged, update the parent
implementation PR’s `Integrated HITL Adjuncts`, scope reconciliation, affected
paths, adversarial matrix, file impact, assumptions, and exact validation.
Refresh invalidated evidence and review the integrated parent in totality
before accepting it. If the child is rejected or abandoned, the parent
contract remains reviewable without it.

An adjunct creates no plan transition or accepted-review-unit ledger row. CI
recognizes the canonical implementation branch as its base, requires the child
branch shape and completed adjunct template, rejects stale parent ancestry, and
rejects milestone plan or proposal-artifact edits. The machine validates the
recorded topology and assertions; the reviewer owns whether the asserted
compatibility is actually true.

Each proposal lives at the current frontier’s declared `proposal path` and uses
`.github/PULL_REQUEST_TEMPLATE/proposal.md`. It records the review question,
proposed contract, owner, affected paths, adversarial matrix, assumptions,
non-goals, file impacts, validation plan, and the expected successful handoff.
When the review question or proposed contract claims a universal invariant, it
also records the trust and authority model plus the evidence topology and
capture strategy defined below. It contains no product code, tests of
unimplemented behavior, generated runtime artifacts, or implementation repair.

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

Every review-unit template includes two shared state receipts:

- `Repair Cycle Ledger`, whose cycle numbers are consecutive and whose review
  receipt, classification, repair revision, and contract impact are updated
  before re-review; and
- `Repair Escalation`, which stays `not-required` until escalation occurs and
  becomes `completed` with a durable human decision receipt, route, and
  disposition at the second substantial cycle.

The ledger contains one all-`None` row for an initial review. Do not delete a
prior row, combine separate verdict rounds, or downgrade a reviewer’s
classification in order to pass the gate.

### Proposal PR Template

Use `.github/PULL_REQUEST_TEMPLATE/proposal.md`. The proposal document itself is
the durable contract; the PR body gives the reviewer its milestone context,
review kind, question, scope, and explicit confirmation that no implementation
is present. Proposal amendments use the same canonical review kind.

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
- Repair cycle ledger
- Repair escalation
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

When either `## Review Question` or `## Proposed Contract` makes one of those
claims, the proposal artifact must complete both of these sections before it can
be accepted:

- `## Trust And Authority Model`: distinguish consistency, provenance, and
  authenticity guarantees; identify trusted and untrusted actors and inputs;
  map each externally visible claim to its source of authority; and state the
  covered and excluded adversaries, including whether same-user mutation is
  inside the model.
- `## Evidence Topology And Capture Strategy`: map each claim and explicit
  non-claim through authoritative raw evidence, derivation, and semantic
  verifier; choose bounded implementation evidence or a separate evidence review
  unit; and define capture readiness, freshness, reproducibility, invalidation,
  and retained-versus-derived artifact boundaries.

If the guarantee depends on process, library, or external-system behavior whose
ownership is uncertain, cite the smallest feasibility evidence that settles the
boundary. Otherwise narrow the guarantee and record the behavior as an
unverified limit; do not leave ownership discovery for implementation. For
canonical live capture, readiness must at least settle the artifact format,
authority mapping, semantic verifier, and coordinated mutation cases. A digest
or self-seal proves internal consistency only unless the trust model identifies
an independent authenticity root.

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
Cycle: `<consecutive integer>`
Classification: `<minor | substantial>`
Review receipt: `<durable link to the consolidated verdict>`

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

One review-and-repair cycle is normal. The second substantial cycle invokes the
hard escalation rule in [Repair Cycle](#repair-cycle); do not request another
review until its human decision receipt and disposition are recorded. A third
substantial cycle cannot remain in the same review unit.

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
HTML, then opens a proposal PR to the milestone branch. Its `Review Kind` must
match the canonical frontier. When the contract is acceptable, submit the
exact-head GitHub review receipt described above before merging. Any later
proposal commit requires another receipt. After merge, the maintainer updates
the clean milestone branch and records acceptance; the acceptance command
rechecks the merged PR body and exact-head review receipt before promotion:

```sh
python3 docs/milestones/workflow.py accept-proposal \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --pr <proposal-pr-number>
```

Inspect and commit the resulting plan and HTML transition. If known evidence
requires a bounded contract correction, start an additive amendment instead.
That command is legal from `ready_for_implementation` and from
`implementation_in_review`. When implementation has already started, pass the
durable `proposal-amendment` escalation receipt and identify the implementation
PR as `paused` (resume policy `reconcile`) or `closed` (resume policy
`replace`):

```sh
python3 docs/milestones/workflow.py start-proposal-amendment \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --branch m<number>/amend-<slug> \
  --path docs/milestones/<number>-<slug>/proposals/<slug>-amendment.md
```

```sh
python3 docs/milestones/workflow.py start-proposal-amendment \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --branch m<number>/amend-<slug> \
  --path docs/milestones/<number>-<slug>/proposals/<slug>-amendment.md \
  --implementation-pr <implementation-pr-number> \
  --implementation-url https://github.com/<owner>/<repo>/pull/<n> \
  --implementation-head <implementation-head-sha> \
  --implementation-disposition paused \
  --resume-policy reconcile \
  --escalation-receipt https://github.com/<owner>/<repo>/pull/<n>#pullrequestreview-<id>
```

The amendment PR remains contract-only. Concurrent implementation mutation is
forbidden while the frontier is `proposal_amendment_in_review`; validate-pr
rejects implementation heads in that state. Starting an implementation-source
amendment, validating that PR, accepting the amendment, and resuming
implementation all recheck authoritative GitHub metadata: the implementation
URL must be this repository's pull request, the head branch must be the
planned implementation branch, the base must be the milestone branch, and the
recorded 40-character head must still be the PR head. A `paused` PR must
remain open, unmerged, and converted to draft so it cannot merge. A `closed`
PR must be closed and unmerged. The escalation receipt must be a GitHub
`pullrequestreview` or `issuecomment` URL on that implementation PR from an
`OWNER`, `MEMBER`, or `COLLABORATOR` whose body selects `proposal-amendment`.
Drift fails closed.

After exact-head review and merge, `accept-proposal-amendment` returns the
frontier to `ready_for_implementation` and preserves any paused implementation
identity. Then `start-implementation` resumes only the exact recorded head and
merges the updated milestone branch into that published implementation branch
(`--no-ff`; do not rebase it). `reconcile` restores the paused PR and marks it
ready for review (`gh pr ready`); `replace` reopens the planned branch for a
new implementation PR. Abandoning a `paused`/`reconcile` amendment also marks
that PR ready for review.

To reject or abandon an in-review amendment without accepting it, run the
command on the unmerged amendment branch. It writes the restore onto the
milestone branch and does not merge the amendment artifact or copy amendment
state by hand:

```sh
python3 docs/milestones/workflow.py abandon-proposal-amendment \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --reason "<concrete reason>"
```

`paused`/`reconcile` restores `implementation_in_review` and the existing PR,
and marks that draft PR ready for review. `closed`/`replace` returns to
`ready_for_implementation` while keeping the pause receipt so
`start-implementation` can open the recorded replacement. A failed amendment
is not implementation acceptance and does not rewrite the original proposal.

Apply the same exact-head review rule to the contract-only amendment PR. After
it merges, record its reviewed head and exact merge acceptance receipt. The
amendment acceptance command also rechecks the canonical review kind:

```sh
python3 docs/milestones/workflow.py accept-proposal-amendment \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --pr <amendment-pr-number>
```

Only when status reports `ready_for_implementation` may the implementation
branch start or a paused implementation resume:

```sh
python3 docs/milestones/workflow.py start-implementation \
  --plan docs/milestones/<number>-<slug>/plan.md \
  --branch m<number>/<frontier>
```

If explicit human testing then produces an eligible implement-now request,
create its child from the published parent head without changing plan state:

```sh
git fetch origin m<number>/<frontier>
git switch -c m<number>/<frontier>--adjunct-<slug> \
  origin/m<number>/<frontier>
```

Open the child PR back to `m<number>/<frontier>` with the implementation-adjunct
template. After child acceptance, merge it into the parent branch, reconcile
the parent description, refresh affected evidence, and request one parent
totality re-review.

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
confirms the implementation PR is merged from the planned branch and its body
still matches the canonical review kind, fills the reviewed template with the
PR number and merge SHA, applies the existing handoff owner, verifies that only
canonical `plan.md` and generated `plan.html` changed, commits them, and pushes
the milestone branch. It stops at `ready_for_proposal`; it never starts the next
proposal branch.

The lower-level `handoff --receipt <path>` command remains available for a
reviewed exceptional receipt or recovery, but normal successful completion
must not reconstruct acceptance judgment after merge.

The helper, rather than agent memory, enforces the local order. It refuses a
dirty worktree, the wrong branch, a branch/state mismatch, an unmerged proposal,
an implementation start without an accepted proposal, an implementation PR
from the wrong branch, or a merge commit that is not already an ancestor of the
milestone branch. Proposal acceptance asks GitHub to confirm the exact base,
head, merge commit, changed-file allowlist, accepted authorized review receipt
attached to the exact proposal head, and matching review kind. Implementation
completion asks GitHub to confirm the implementation PR, commit, and matching
review kind, then limits criterion updates to the current frontier, prevents
premature closeout, updates the accepted ledger and risks, promotes the
reviewed next candidate to `ready_for_proposal`, records workflow history, and
regenerates HTML.

CI runs `workflow.py validate-pr` when a PR is opened, synchronized, reopened,
or its description is edited. It applies the frontier gate to PRs targeting a
milestone branch and the adjunct gate to reserved adjunct PRs targeting the
active plan's canonical implementation branch. Proposal, proposal-amendment,
and implementation PRs must provide the canonical review kind. A proposal PR
may change only its declared proposal document, canonical plan, and generated
plan HTML. A
proposal amendment PR has the same contract-only boundary and must add a new
artifact without modifying accepted proposal history. An implementation PR is
rejected unless its base records an accepted proposal; it may not modify that
proposal, an accepted amendment, or the frozen frontier. An adjunct PR must use
the reserved child branch, current parent head, completed HITL template, and
immutable milestone contract artifacts.
For each recognized milestone review-unit transition and adjunct, CI also
validates the PR body’s declared repair ledger and hard escalation receipt. The
pull-request workflow runs when that body is edited so a newly recorded
decision can satisfy the gate without an unrelated code commit. The same
sections remain required human-visible state in cumulative milestone and
separate repair templates even when those PR topologies are outside the
transition gate.
`docs/render_markdown.py` invokes the same plan validator, so hand-edited state
that omits required fields or history is rejected.

CI supplies `validate-pr` with the PR event payload. For an equivalent local
check, save the current PR description and pass it with
`--pr-body-file <path>`; a milestone proposal, amendment, or implementation
cannot receive a complete validation result without its PR body.

The machine cannot discover an unrecorded review round, decide whether a cycle
was intellectually substantial, prove that the named decision author had sound
judgment, prove which model authored a phase, prove that a reviewer understood a
proposal, or prove that approval was intellectually sound. The reviewer and
operator own those judgments. It can prove that a decisive review was submitted
on a specific proposal head and preserve that fact separately from merge
ancestry. The gate also guarantees that declared cycles are consecutive, the
second declared substantial cycle has an explicit decision receipt and route,
and a third declared substantial cycle cannot be merged as the same review unit.
The repository also guarantees that the current state and next handoff are
visible, the accepted proposal is durable, proposal and implementation diffs
are separate, and implementation cannot pass CI before proposal acceptance.

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
