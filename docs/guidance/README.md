# Agent Guidance

This directory contains small, task-focused guides for repository work.

## Default path

| File | Load for |
| --- | --- |
| [agent-surface.md](agent-surface.md) | Every new or resumed work session |
| [roles/engineer.md](roles/engineer.md) | Making a requested repository change |
| [roles/reviewer.md](roles/reviewer.md) | Reviewing, diagnosing, assessing, or explaining |
| [orchestration/README.md](orchestration/README.md) | Only when the operator names a policy |

Read another guide only when the current request clearly needs it. Keep startup
guidance short and independent of any particular delivery history or branch
layout.

## Maintenance

- Keep each default guide narrow enough to load independently.
- Keep current task state out of reusable guidance.
- Keep automatic operation routing in `AGENTS.md` and `agent-surface.md`.
- Remove stale instructions instead of layering compatibility text onto them.

## Parked delivery guidance

These files remain in the tree so the older delivery process can be restored.
Do not load them for new work unless the operator asks to restore that process.

| File | Historical use |
| --- | --- |
| [roles/implementer.md](roles/implementer.md) | Proposal/implementation role |
| [roles/meta-manager.md](roles/meta-manager.md) | Review/closeout role |
| [proposal-vs-implementation.md](proposal-vs-implementation.md) | Phase split |
| [review-unit.md](review-unit.md) | PR-sized review contract |
| [repair-cycle.md](repair-cycle.md) | Review-repair ledger |
| [validation.md](validation.md) | Contracted validation sequence |
| [adversarial-matrix.md](adversarial-matrix.md) | Universal-claim matrices |
| [hitl-implementation-adjunct.md](hitl-implementation-adjunct.md) | Hands-on adjunct PRs |
