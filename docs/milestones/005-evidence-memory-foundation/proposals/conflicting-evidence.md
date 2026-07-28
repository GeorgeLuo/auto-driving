# Proposal: Conflicting evidence semantics

Milestone: 005 Evidence Memory Foundation  
Frontier: Conflicting evidence semantics  
Proposal branch: `m005/conflicting-evidence-proposal`  
Implementation branch: `m005/conflicting-evidence` (blocked until this proposal is accepted)  
Exit criterion: M005-08  

## Review Question

Does the bounded evidence ledger handle contradictory attributed evidence,
same-slot recurrence, missing evidence, and structurally incompatible updates
deterministically without silently claiming semantic truth?

This proposal is ready for implementation only if an implementer can apply the
contract below without inventing policy during coding.

## Proposed Contract

### Slot key

Retain the existing namespaced ledger key:

- kind prefix (`thing` or `signal`);
- optional source plugin identity;
- local evidence id.

Two different plugins with the same local evidence id remain distinct slots.
This unit does **not** redefine namespacing.

### Update order in one cycle

For each `update(context, observation)`:

1. Extract candidate records from the observation (existing extraction and
   confidence / property / JSON gates still apply).
2. Expire records older than `max_age_ms` using the cycle timestamp
   **before** comparing candidates to retained slots.
3. Apply same-slot policy below for each candidate against the post-expiry ledger.
4. Enforce capacity (`max_records`, oldest-first) after conflict resolution.
5. Publish a detached snapshot whose metadata states the fixed conflict policy
   and bounded conflict telemetry.

### Same-slot policy (fail-closed, evidence-not-truth)

Given one slot key after expiry:

| Case | Required behavior |
| --- | --- |
| No retained record | Admit the candidate if it passes existing extract gates. |
| Compatible newer candidate | Replace retained value and provenance by recency. This remains bookkeeping, not object identity. |
| Missing evidence for an existing slot | Do not refresh and do not remove the slot before age expiry. |
| Structurally incompatible candidate | Invalidate the retained slot **and** do not retain the incoming candidate. |
| Two or more non-identical candidates for the same slot in one observation | Invalidate that slot for this update regardless of tuple order; retain neither candidate. |

“Identical” for same-observation duplicates means equal structural identity after
normalization used by compatibility (below), not Python object identity.

### Structural compatibility

A retained record and a candidate for the same slot are **compatible** only when
all of the following match:

1. **Record kind** (`kind` string).
2. **Coordinate frame** from provenance / location.
3. **Location representation family**: both absent, or both present with the
   same geometry family (for example both image-space boxes / both points as
   already modeled by `ViewLocation`). Do not invent new geometry types here.
4. **Property shape**: recursive JSON type shape of `properties` matches
   (object keys; array vs scalar vs object; nested types). Ordinary scalar
   **value** changes under the same shape are compatible recurrence.

Incompatible examples (non-exhaustive):

- kind `region` replaced by kind `obstacle` under the same evidence id;
- image-frame claim replaced by a different coordinate frame;
- bbox location replaced by a point (or by absence of location);
- `properties.score` number replaced by an object or array;
- required property key disappears or a new nested structure appears where a
  scalar lived.

Compatible examples:

- same kind/frame/location family/property shape with updated confidence or
  numeric score;
- same shape with refreshed provenance timestamps / frame_id.

The ledger must **not** interpret semantic agreement (for example whether two
labels “mean the same object”). Compatibility is structural only.

### Telemetry

Publish on every snapshot metadata (and only as bounded counters / policy
labels—not retained conflict payloads):

- `conflict_policy`: fixed identifier for this contract
  (for example `bounded_evidence_structural_v1`);
- `conflict_count`: cumulative invalidations in the current epoch
  (incompatible recurrence and same-observation non-identical duplicates);
- `last_update_conflict_count`: conflicts produced by the latest update only.

Rules:

- Capacity eviction must **not** increment conflict counters.
- Age expiry must **not** increment conflict counters.
- `reset` starts a new epoch with zero conflict counters (same as capacity
  eviction counter reset behavior).
- Do not retain historical conflicting records, payloads, or side ledgers.

### Operator / framework surface

- No new operator command is required.
- Replay and offline check fixtures must be able to assert the policy through
  snapshot records + metadata.
- Live Chase/Pi re-proof is out of scope for this unit.

## Ownership

| Concern | Owner |
| --- | --- |
| Same-slot compatibility and invalidation | `implementations/memory/bounded_evidence.py` (`BoundedEvidenceLedger.update`) |
| Snapshot metadata policy/counters | `BoundedEvidenceLedger` published `MemorySnapshot.metadata` |
| Framework stage isolation | Unchanged: `ActivatedMemoryStage` continues to accept/detach snapshots without learning conflict policy beyond published metadata |
| Deterministic proof | Focused unit tests + offline replay fixture under `tests/` |

## Affected Paths

- Success path: compatible recency update replaces retained evidence.
- Missing path: absent observation or absent slot key leaves retained evidence
  until age expiry.
- Conflict path: incompatible or same-observation contradictory candidates clear
  the slot and increment conflict telemetry.
- Expiry path: age removal before comparison; no conflict increment.
- Capacity path: oldest-first eviction after conflict resolution; no conflict
  increment.
- Reset path: empty epoch; conflict and capacity counters zero.
- Serialization path: detached snapshot; metadata keys present and bounded.
- Error path: existing extract gates (non-JSON properties, oversize properties,
  low confidence) continue to drop candidates before slot comparison.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| Compatible scalar value change, same shape | Slot retained; value/provenance refresh; conflict counters unchanged |
| Compatible confidence-only change | Same as above |
| Kind change under same evidence id | Slot empty after update; conflict_count +1 |
| Coordinate frame change | Slot empty; conflict_count +1 |
| Location family change (bbox ↔ point / present ↔ absent) | Slot empty; conflict_count +1 |
| Property shape change (scalar ↔ object/array; key type change) | Slot empty; conflict_count +1 |
| Two non-identical same-slot candidates in one observation, order A then B | Slot empty; conflict_count +1; independent of order |
| Two non-identical same-slot candidates, order B then A | Same result as previous |
| Two identical same-slot candidates in one observation | Treated as one compatible candidate (no false conflict) |
| Missing evidence while retained still within max_age | Slot remains; no refresh; no conflict |
| Retained older than max_age, then new candidate | Expiry first; candidate admits as empty-slot write; no conflict |
| Capacity pressure after a conflict invalidation | Eviction may remove other slots; capacity_eviction_count only; conflict counters separate |
| Reset after conflicts | Empty snapshot; conflict counters 0; new epoch_id |
| Unrelated other slots | Unaffected by a conflict on a different key |
| Cross-plugin same local id | Distinct slots; no cross-slot conflict |
| Caller mutates returned snapshot metadata/records | Detached; internal ledger unchanged |

## External Assumptions

- Perception plugins may emit unstable or contradictory structure over time; the
  memory stage must not repair that into semantic truth.
- Offline deterministic tests and replay fixtures are sufficient for this unit’s
  acceptance; no live vehicle run is required to close M005-08’s conflict gap.
- Existing namespaced record ids and extraction gates remain correct inputs.

## Non-Goals

- Semantic fusion, voting, confidence aggregation, or multi-source arbitration.
- Object identity, track continuity, or world models.
- Changing capacity, max-age, or reset contracts except to keep their telemetry
  disjoint from conflict counters.
- Live Chase/Pi lifecycle re-proof or action/movement behavior.
- New CLI commands or operator workflows.
- Rewriting the stable `MemorySnapshot` schema beyond additive metadata keys
  already used for bounded telemetry.
- Closing milestone 005 or activating 006.

## File Impact

### Create

- `tests/implementations/memory/test_bounded_evidence_conflicts.py` (or equivalent
  focused module) covering the adversarial matrix.
- Offline replay / sequence fixture under `tests/cli/memory/fixtures/` (or the
  established memory fixture location) that exercises conflict invalidation and
  counters.

### Modify

- `implementations/memory/bounded_evidence.py` — same-slot compatibility,
  invalidation, metadata counters; keep capacity/age paths separate.
- Existing bounded-evidence tests only as needed so recurrence tests still pass
  under the explicit compatibility definition.
- Milestone plan/ledger only at implementation handoff (not in this proposal PR).

### Remove

- None.

## Validation Plan

Deterministic only:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.implementations.memory.test_bounded_evidence \
  tests.implementations.memory.test_bounded_evidence_conflicts \
  -v
```

Plus any replay fixture test module added for the conflict sequence.

Acceptance requires:

1. Every adversarial matrix row above has a direct test or an explicit subsumption
   note in the implementation PR.
2. Conflict counters never move on pure age expiry or pure capacity eviction.
3. Reset clears conflict counters with the epoch.
4. No live vehicle dependency in CI for this unit.

## Implementation Notes (non-binding)

Suggested internal helpers (names optional):

- `_structurally_compatible(retained, candidate) -> bool`
- `_invalidate_slot(record_id, *, reason) -> None` updating counters
- Group candidates by `record_id` before apply to detect same-observation
  contradictions order-independently

Do not add a second durable store of conflicting payloads.

## Handoff

After this proposal merges:

1. Record acceptance (`ready_for_implementation`) with merge commit.
2. Open `m005/conflicting-evidence` from the updated milestone branch.
3. Implement only this contract; do not widen into closeout or live re-proof.
