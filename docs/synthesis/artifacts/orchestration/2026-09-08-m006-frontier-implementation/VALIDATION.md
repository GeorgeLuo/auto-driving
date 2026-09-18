# Validation ledger

The preparation checks below were run on implementation branch
`m006/shadow-proposal-evidence` from base
`a8c4483dea577afdb78094caa36d650f47882a27`. A concurrent remote start commit
`af02f318010947612bf8ec9f25e42e01581288e1` was preserved; it changes only the
canonical plan/HTML state to `implementation_in_review`. The accepted proposal
artifact was not edited.

## Passed

- `PYTHONDONTWRITEBYTECODE=1 python3 verify_packet.py --check-html --self-test`
  — blocked packet accepted, derived HTML matched, fabricated readiness rejected.
- `PYTHONDONTWRITEBYTECODE=1 pytest -q tests/cli/decision/test_commands.py tests/cli/decision/test_shadow_decision_surfaces.py`
  — 33 passed.
- `PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py status --plan docs/milestones/006-decision-facing-perception-readiness/plan.md`
  — M006 active, frontier correct, state `implementation_in_review`, accepted
  proposal #201 recorded.
- `PYTHONDONTWRITEBYTECODE=1 python3 docs/milestones/workflow.py validate docs/milestones/006-decision-facing-perception-readiness/plan.md`
  — valid milestone plan.
- `python3 docs/render_markdown.py --check` — pass.
- `git diff --check` — clean.
- Preparatory public-door replay — existing `decision apply --json --record`
  returned deterministic `true`; both digest SHA-256 values were
  `63369a158af3198a44e145ed11aa71dbb62097e46a39e3e458909fe08a53e54b`.

## Broader-suite limitation

`PYTHONDONTWRITEBYTECODE=1 pytest -q` returned `1048 passed, 2 skipped, 4
failed`. The four failures are in pre-existing M007 coverage/audit tests, not
the changed docs/evidence paths. On an untouched detached M006 base, the three
PATH-sensitive tests passed when selected individually and the historical CLI
surface audit failure reproduced. The full suite is therefore not green; this
run does not claim ready-for-independent-review and the PR remains draft.

## Live/external checks intentionally not run

No simulator, Metrics UI session, Chase worker, PiRacer worker, vehicle, or
physical control operation was started. D1/D2 receipts are blocked, so no
canonical capture authorization was valid. The offline fixture is explicitly
preparatory and has no source image or host-authority interval.

## Reconciliation

The final branch preserves the concurrent implementation-start transition and
the local evidence preparation. Its changed paths are the accepted evidence
root, orchestration record, and the two canonical/generated plan files from
the remote start; no proposal, product, runtime, or test source path changed.
