# Documentation Guide

This directory separates current reference material, future-facing synthesis,
and milestone history.
Read this file first when starting work.

## Active Work

Milestone 005, [Evidence Memory Foundation](milestones/005-evidence-memory-foundation/plan.html),
is active. It should give the decision cycle bounded, inspectable continuity
across frames by retaining attributed observation evidence without claiming a
complete world model or granting movement authority.

**Where it stands:** **not ready to close.** Packages 1–4, Memory map,
operator reset, and offline **memory replay** are landed. Still required:
opt-in **record**, Chase + Pi **visual-provenance checks**, then package 6
**closeout**. Next: record / visual-provenance path. See the plan’s
[Remaining For Closeout](milestones/005-evidence-memory-foundation/plan.html)
and exit-criteria status table. Do not start 006 while those remain open.

## Immediate Pre-Plan (Not Active)

Milestone 006, [Decision-Facing Perception Readiness](milestones/006-decision-facing-perception-readiness/plan.html),
is a **pre-plan** queued after 005. It records the immediate deferred question
from physical parity: whether packaged floor-boundary evidence is fit for a
first constrained non-idle decision path, or whether exactly one bounded
upgrade should be attempted under the same contract. It is not a multi-candidate
backlog and must not absorb open-ended CV research.

See the [immediate deferred work and pre-plan rules](milestones/README.md#immediate-deferred-work-and-pre-plans)
in the planning contract.

## Recently Closed

Milestone 004, [Physical Perception Parity](milestones/004-physical-perception-parity/plan.html),
is closed. The Pi runs always-on manual-mode observation, publishes an exact
latest snapshot, and retains the packaged floor-plane observer after rejecting
floor-continuity. Residual quality limits are deferred to the 006 pre-plan, not
an expanding list. Closeout:
[004 closeout](milestones/004-physical-perception-parity/closeout.md).

## Reading Order

1. Read the shared [`milestones/README.md`](milestones/README.md) planning and
   pull-request delivery contract, or use its
   [rendered view](milestones/planning-contract.html).
2. Read the active milestone plan when one is listed above. If none is active,
   formalize the next goal before implementation. Read a listed pre-plan only
   as the bounded next question—not as current work.
3. Read [`milestones/completed.md`](milestones/completed.md) for durable context
   from closed work.
4. Read only the relevant documents under `reference/` for current system
   behavior.
5. Consult `synthesis/` when evaluating future work or looking for previously
   researched approaches. Synthesis notes are evidence, not backlog commitments.

Do not treat closed milestone plans as current architecture. They are frozen
records that explain why prior work was shaped the way it was.

## Structure

- `reference/` is the living source of truth for architecture diagrams and
  contracts.
- `synthesis/` relates external research and repository evidence to bounded
  candidate work without making it part of the active milestone.
- `milestones/README.md` is the shared milestone format, rolling-horizon, and
  pull-request delivery contract.
- `milestones/planning-contract.html` is its generated browser rendering; active
  plans expose it in an expandable section.
- `milestones/<number>-<slug>/plan.html` is the detailed plan and status record
  for one milestone.
- `milestones/<number>-<slug>/closeout.md` is the compressed durable summary
  created when that milestone closes.
- `milestones/completed.md` is the append-only high-level ledger of closed
  milestones.

The shared milestone contract owns lifecycle, plan-format, PR-review, rolling
horizon, and evidence rules. Keep this guide limited to documentation discovery
so those development instructions have one authoritative location.
