# Exploratory finding ledger (non-gating)

This appendix reconciles PR observations that are **outside** the M007-05
acceptance gate. It does not change the formal acceptance `result` in
`result.json`.

Source observation: [PR #88 exploratory live session report](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199)
(2026-08-03, auto-driving `27dd071955c5afa9adc51b63ffb69062cd056d01`,
metrics-ui `722e070fdc9f4ee89d13f947bf3996e62dcb2783`).

The later formal interactive acceptance session (2026-08-05) exercised only the
pinned six-command observation-only journey and recorded zero acceptance
findings. These exploratory items were not re-run as part of that formal
session; they remain confirmed from the earlier PR observation and are retained
for later coverage / leaf-audit / product proposals.

## Scope boundary

| Ledger | Affects M007-05 pass? | Contents |
| --- | --- | --- |
| Acceptance findings (`result.json` → `findings`) | Yes | Empty for this pass |
| This exploratory ledger | No | `M007-LIVE-001` … `M007-LIVE-005` |

**Gating is separate from classification.** Every item below has
`acceptance_blocker: false` for M007-05 because it is outside the frozen primary
journey. Classification still follows the accepted #86 taxonomy:

| Classification | Meaning here |
| --- | --- |
| `usability_defect` | Observed failure of an **existing** operator surface (misleading, lossy, or malformed output) |
| `enhancement_candidate` | Request for a **new** preference or capability beyond what the surface already provides |

None is an `acceptance_blocker` for M007-05.

## Summary

| ID | Classification | Severity | One-line | Gating |
| --- | --- | --- | --- | --- |
| `M007-LIVE-001` | `usability_defect` | P2 | Apply run-id collision overwrites sibling artifacts | non-gating |
| `M007-LIVE-002` | `usability_defect` | P2 | Candidates “ready” ≠ compare model path | non-gating |
| `M007-LIVE-003` | `usability_defect` | P3 | Failed compare dumps full JSON on human surface | non-gating |
| `M007-LIVE-004` | `enhancement_candidate` | P3 | Want consolidated multi-engine review / open-review | non-gating |
| `M007-LIVE-005` | `usability_defect` | P3 | `perception run --json` buries review path | non-gating |

## Reconciled items

### M007-LIVE-001

| Field | Value |
| --- | --- |
| Classification | `usability_defect` |
| Classification rationale | Existing apply artifact identity fails under sequential use: sibling run directories overwrite. This is a defect of the current surface, not a request for new capability. |
| Severity | P2 |
| Affected surface | `./cli/automa vehicles perception apply --record` |
| Procedure step | Exploratory multi-algorithm apply bake-off (not acceptance path) |
| Expected | Distinct durable run directories / ids for sequential applies |
| Observed | Two applies finishing in the same wall-clock second shared second-granularity run id `…-133051`; only one directory retained under `runtime/perception-applies/` for that second |
| Reproduction | Sequential `perception apply --record` for `sim_debug` then `visual_observer` within the same second on the 2026-08-03 exploratory session |
| Evidence | [PR comment B2](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199) |
| Owner | Automa CLI perception apply run-id / artifact naming |
| Operator impact | Multi-engine bake-offs can overwrite sibling apply artifacts |
| Disposition | **Confirmed; deferred** product repair. Not an M007-05 acceptance blocker (`acceptance_blocker: false`). |
| Required recheck | Separate product unit with unique run-id generation (sub-second or UUID suffix) |

### M007-LIVE-002

| Field | Value |
| --- | --- |
| Classification | `usability_defect` |
| Classification rationale | Existing readiness inventory contradicts execution: marks a candidate ready then fails on model path resolution. Misleading existing output. |
| Severity | P2 |
| Affected surface | `./cli/automa vehicles perception candidates` vs `perception compare` |
| Procedure step | Exploratory lab candidate compare (not acceptance path) |
| Expected | “Ready” means the same resolver used by execution can run the candidate |
| Observed | `fastsam` reported `model.ready: true` with plugin-local model present, but `compare` failed with `FileNotFoundError` resolving `models/FastSAM-s.pt` |
| Reproduction | `perception candidates` then `perception compare --record` on 2026-08-03 exploratory session |
| Evidence | [PR comment B3](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199) |
| Owner | Lab candidate readiness inventory vs compare path resolution |
| Operator impact | Misleading readiness; compare fails after inventory said ready |
| Disposition | **Confirmed; deferred** product repair. Not an M007-05 acceptance blocker (`acceptance_blocker: false`). |
| Required recheck | Align inventory readiness with the execution-time model path resolver |

### M007-LIVE-003

| Field | Value |
| --- | --- |
| Classification | `usability_defect` |
| Classification rationale | Existing human failure path dumps a full multi-frame JSON blob after the table. Malformed operator-facing failure presentation. |
| Severity | P3 |
| Affected surface | `./cli/automa vehicles perception compare` human output on candidate failure |
| Procedure step | Exploratory compare with FastSAM failure |
| Expected | One-line structured failure reason for a failed candidate in human mode |
| Observed | Human table was usable, then a full multi-frame JSON dump for the failure |
| Reproduction | Same 2026-08-03 compare run as M007-LIVE-002 |
| Evidence | [PR comment B3](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199) |
| Owner | Automa CLI compare human formatting |
| Operator impact | Noisy failure diagnosis |
| Disposition | **Confirmed; deferred** product repair. Non-blocking for M007-05 (`acceptance_blocker: false`). |
| Required recheck | Compress failed-candidate human output; keep full detail in JSON/verbose |

### M007-LIVE-004

| Field | Value |
| --- | --- |
| Classification | `enhancement_candidate` |
| Classification rationale | A consolidated multi-algorithm comparison page / auto-open review is an additive preference beyond per-run review.html already written. |
| Severity | P3 |
| Affected surface | Multi-algorithm `apply` / multi-candidate `compare` review surfaces |
| Procedure step | Exploratory apply + compare |
| Expected | Operator-facing openable comparison surface (or explicit open-review path) |
| Observed | Per-run `review.html` only; not auto-opened; no consolidated multi-algorithm page |
| Reproduction | 2026-08-03 exploratory B2/B3 |
| Evidence | [PR comment B2/B3](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199); related product discussion also notes consolidated comparison (e.g. #90) |
| Owner | Perception review artifact UX |
| Operator impact | Harder multi-engine judgment without a new consolidated surface |
| Disposition | **Confirmed; deferred** enhancement. Outside frozen acceptance journey (`acceptance_blocker: false`). |
| Required recheck | Product unit for consolidated review / `--open-review` |

### M007-LIVE-005

| Field | Value |
| --- | --- |
| Classification | `usability_defect` |
| Classification rationale | Existing --json perception-run surface buries the review path in a large dump, making the machine report operator-hostile as a scan surface (human non-JSON form exists separately). |
| Severity | P3 |
| Affected surface | `./cli/automa vehicles perception run --json` |
| Procedure step | Exploratory capture-once |
| Expected | Human-scannable default with prominent review path; JSON for machines with stable artifact fields |
| Observed | Large machine report; `review.html` path buried in payload (human form without `--json` exists) |
| Reproduction | `perception run --record` with JSON-oriented output on 2026-08-03 exploratory B1 |
| Evidence | [PR comment B1](https://github.com/GeorgeLuo/auto-driving/pull/88#issuecomment-5171399199) |
| Owner | Perception run default human vs JSON surfaces |
| Operator impact | Operator-hostile primary JSON surface |
| Disposition | **Confirmed; deferred** product repair. Not an M007-05 acceptance blocker (`acceptance_blocker: false`). |
| Required recheck | Keep compact human default; stable JSON with prominent artifact paths |

## Explicit non-findings from the same exploratory session

These were observed healthy and are **not** ledger items:

- Primary six-step CLI journey machine gates
- View publication correlation on that exploratory run
- Observation-only authority / no applied control
- Stop → non-current view
- Memory lifecycle check PASS (exploratory US-05 style)

## Acceptance session relationship

The formal M007-05 interactive session did **not** re-exercise perception
run/apply/compare/candidates. No acceptance recheck attempted to clear
`M007-LIVE-001..005`; they remain confirmed exploratory observations with
deferred disposition. The acceptance `result` stays `pass` because none is an
acceptance blocker for the frozen primary journey, **not** because they were
classified as enhancements.
