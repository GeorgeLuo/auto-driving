# Milestone Planning Contract

This file defines the planning and delivery format for every active and future
milestone. Individual milestone plans contain their own goals, evidence, work,
and decisions; they should link here instead of restating these general rules.
Closed milestone plans are frozen historical records and are not required to be
retrofitted.

## Planning Model

A milestone has a stable objective and exit criteria, but not a fixed schedule
of pull requests. Keep the next review unit concrete and let evidence from each
merge determine the next one. This prevents early implementation assumptions
from becoming commitments while still making the direction and stopping
conditions explicit.

The plan must distinguish:

- **Observed state:** verified repository behavior, measurements, and gaps.
- **Current delivery:** implemented or actively changing work with one review
  question.
- **Queued delivery:** the one likely next review unit, defined but not started.
- **Preparation horizon:** ordered needs that remain provisional until promoted.
- **Completion usage:** the small, stable set of new human workflows the
  completed milestone must make possible.
- **Exit criteria:** fixed milestone outcomes that do not depend on a particular
  implementation path.

### Completion Usage Contract

Every active milestone enumerates the straightforward usage that should be
possible after closeout. Describe each workflow from the user's perspective:
the starting context, a proposed command, API, or UI execution path, and the
observable result that tells them it worked. The proposal must be concrete
enough for a reviewer to understand how the completed behavior will be run,
while remaining free of internal implementation steps. Clearly label commands
or interfaces that do not exist yet as proposed rather than observed behavior.

The workflow set is part of milestone scope and should not drift casually.
Adding or removing a workflow requires an explicit scope decision in the plan's
decision log. Exact command spelling, flags, schemas, limits, and presentation
may evolve during implementation as long as the original usage remains apparent
and executable. Update the proposal when those details settle. Every completion
workflow must be supported by exit criteria and closeout evidence; otherwise it
is aspiration rather than delivered usage.

Each completion workflow records:

- **Starting state:** what must already be available or selected.
- **Proposed execution:** the shortest expected human path through the public
  interface.
- **Success signal:** the concise output or state change that proves it worked.
- **Automation path, when needed:** structured output suitable for tests without
  making machine-oriented flags the default human experience.

## Common Plan Format

Each active milestone uses a standalone `plan.html` with the same high-level
shape as the [milestone 003 plan](003-test-architecture-and-operator-contracts/plan.html).
Keep it portable, readable without a server, responsive on narrow screens, and
free of external assets.

Use these sections in this order unless a section is genuinely irrelevant:

1. **Header:** milestone number, literal title, objective summary, status,
   start date, important operating constraints, and delivery model.
2. **High-Level Objective:** a small set of outcome cards and a concise statement
   of what success means.
3. **Completion Usage:** an implementation-agnostic enumeration of the new
   workflows a human can run after closeout, proposed public execution paths,
   and the result each workflow exposes.
4. **Baseline:** observed status, evidence, and remaining gap for each major area.
5. **Current Delivery Horizon:** current PR, next-after-review candidate,
   preparation horizon, and current delivery state.
6. **Milestone-Specific Contracts:** architecture, interfaces, output policies,
   target structures, or other rules needed to evaluate this milestone.
7. **Work Plan:** expandable work packages with `pending`, `active`, `blocked`,
   or `done` status and an accurate aggregate progress indicator.
8. **Scope Boundaries:** explicit in-scope and out-of-scope work.
9. **Risks And Controls:** likely failure modes paired with concrete controls.
10. **Exit Criteria:** observable conditions required before closeout.
11. **Decision Log:** dated decisions and reasons, including assumptions that
    changed during implementation.

Plans should support quick scanning. Use status pills, compact tables, cards for
distinct concepts, and expandable work packages when they improve navigation.
Do not add interactive elements that merely decorate the page. Plan text should
describe outcomes, evidence, and decisions rather than narrating every code edit.

### Shared Contract Visibility

Every active plan embeds the rendered version of this contract in a collapsed,
expandable section. The canonical content remains this Markdown file; do not
copy its rules into individual plans or edit `planning-contract.html` directly.

Refresh the generated rendering after changing this file:

```sh
python3 -m pip install -r docs/requirements.txt
python3 docs/render_markdown.py
python3 docs/render_markdown.py --check
```

The deterministic test suite verifies that the rendering identifies the current
Markdown source and that the active milestone embeds it. This keeps the rules
visible during planning without making every plan a second source of truth.

## Pull Request Delivery Contract

Every pull request is one complete, reviewable deliverable. Review size is a
logical-complexity budget, not a line-count target.

### Deep And Narrow

Introduce or settle one policy, abstraction, or behavioral contract in a small
number of owning files. The reviewer should be able to reason deeply about one
question without also auditing a repository-wide rollout.

### Broad And Mechanical

Apply an already-reviewed pattern across many files. These changes may be large
by file count, but must avoid new behavior, new abstractions, and unrelated
cleanup.

Every PR description identifies:

- one explicit review question;
- the review shape and any files requiring deeper attention;
- a concise file-impact list grouped as `Create`, `Modify`, and `Remove` where
  applicable;
- dependencies and explicit non-goals;
- validation performed and its result; and
- user, operator, or developer impact.

List file impacts at ownership granularity with one purpose per path. The list
should make the shape of the change inspectable before implementation without
becoming a line-by-line design. Reconcile meaningful deviations in the final PR
description.

Every PR leaves the repository in a complete state. Do not define a pattern and
roll it out broadly in the same PR. Land the pattern first, merge each deliverable
before branching the next by default, and split work when the primary review
question stops being singular.

## Rolling Delivery Horizon

Only the current PR is committed in detail. A milestone plan also names one
likely next review unit so the current work can prepare a clean boundary, but
that next unit remains unstarted until the current one is accepted. Everything
beyond it stays in the preparation horizon.

The current PR records:

- status and review shape;
- deliverable and review question;
- expected or actual file impacts;
- non-goals; and
- measured validation evidence.

The next-after-review entry records:

- the expected review shape and question;
- a bounded expected deliverable;
- proposed file impacts when they are known; and
- non-goals that prevent work from leaking forward.

After each merge:

1. Re-read the milestone objective and exit criteria.
2. Record what changed, what was learned, and which assumptions failed.
3. Update baseline evidence and work-package status.
4. Promote one preparation-horizon item into the next concrete PR.
5. Leave later work provisional rather than constructing a detailed schedule.

## Status And Evidence

Use `pending`, `active`, `blocked`, and `done` consistently. A work package is
`done` only when all of its acceptance conditions are met; completing one PR
inside it does not complete the whole package.

Evidence should be reproducible and appropriately scoped. Record test counts,
timings, artifacts, or live-system observations when they support a conclusion.
Do not present planned behavior as observed behavior, a skipped check as a pass,
or an unlabeled visual result as an accuracy claim.

## Milestone Lifecycle

1. Create one numbered directory and a `plan.html` following this contract.
2. Define completion usage, fixed exit criteria, a concrete first PR, and a
   preparation horizon.
3. Merge one deliverable at a time by default and update the plan after each
   accepted review unit.
4. At closeout, freeze the plan and write `closeout.md` with outcomes, decisions,
   validation, unresolved work, and links to durable reference material.
5. Append a concise entry to [completed.md](completed.md).
6. Make the next milestone active (or promote a pre-plan) and update the
   active-work link in [the documentation guide](../README.md).

Closeouts preserve durable context; they do not duplicate source-level details.
New architecture facts belong in `docs/reference/`, and future-facing research
belongs in `docs/synthesis/`.

## Immediate Deferred Work And Pre-Plans

Closeouts may leave residual work. That residual is not a free-form backlog.
Route it into exactly one of these places:

1. **Durable reference** (`docs/reference/`): settled current behavior.
2. **Synthesis** (`docs/synthesis/`): research evidence without commitment.
3. **At most one pre-plan** after the active milestone: the single most immediate
   next problem that is already known and would block a named later capability.

### What Counts As Immediate Deferred Work

Immediate deferred work is the smallest next milestone-shaped question that is
already forced by evidence—for example, “packaged perception is fit for memory
but not for non-idle decision.” It is not a wishlist of ideas, a multi-year
roadmap, or a growing list of “nice to haves.”

### Pre-Plan Rules

- A pre-plan lives at `milestones/<number>-<slug>/plan.html` with status such as
  `pre-plan - queued after NNN`.
- It has a fixed objective, explicit non-goals, a stop condition, and provisional
  packages—same honesty as an active plan, without competing for implementation
  attention.
- Pre-plans are **not active work**. Do not implement them while another
  milestone is active unless the active plan’s decision log explicitly allows a
  narrow parallel exception.
- Prefer **one** written pre-plan for “next after active.” Do not stack many
  future milestone drafts. When a newer pre-plan supersedes an older one, mark
  the old status superseded and link forward.
- Revise a pre-plan only when prerequisite closeout evidence changes the bounded
  question—not to absorb every new idea discovered during the active milestone.
- On activation, promote the pre-plan to active status, name the first review
  unit, and update the documentation guide. On abandon, record why and leave no
  dangling “maybe later” list.

### Stop Expanding

If a residual cannot be stated as a single milestone objective with a stop
condition, it is not ready for a pre-plan. Leave it in synthesis or omit it
until evidence forces a sharper question.
