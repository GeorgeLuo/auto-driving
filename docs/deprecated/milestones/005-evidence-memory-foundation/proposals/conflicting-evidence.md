# Proposal: Conflicting evidence semantics

Milestone: 005 Evidence Memory Foundation  
Frontier: Conflicting evidence semantics  
Proposal branch: `m005/conflicting-evidence-proposal`  
Implementation branch: `m005/conflicting-evidence` (pre-gate draft [#59](https://github.com/GeorgeLuo/auto-driving/pull/59) exists and is paused; see Handoff)  
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

### Extraction gates (pre-ledger)

Candidates reach the ledger only after the existing extraction gates. In
particular:

- Explicit `False` **signals are dropped at extraction** and never enter the
  ledger as candidates.
- Therefore `True → False` for a signal is **not** a conflicting scalar update.
  It is **missing evidence**: the retained affirmative signal is left in place
  until age expiry (affirmative-only policy).
- Non-JSON properties, oversize properties, and below-threshold confidence
  continue to drop candidates before slot comparison.

### Update order in one cycle

For each `update(context, observation)`:

1. Extract candidate records from the observation (existing gates apply).
2. Expire records older than `max_age_ms` using the cycle timestamp from
   `context.timestamp_ms` **before** comparing candidates to retained slots.
3. Group remaining candidates by `record_id`.
4. For each group, apply **same-observation resolution**, then **same-slot
   policy** against the post-expiry ledger (see below).
5. Enforce capacity (`max_records`, oldest-first) after conflict resolution.
6. Publish a detached snapshot with fixed conflict-policy metadata and counters.

### Recency ordering (cross-observation)

When deciding whether a **single** compatible candidate may replace retained
evidence, “newer” is defined by the **update invocation order** of
`BoundedEvidenceLedger.update`: each successful entry into step 3 of a later
`update` call is strictly newer than any candidate applied in an earlier
`update` call.

Within a single `update` call, candidates are **not** ordered by timestamp.
Same-observation multi-candidate groups use the equality / contradiction rules
below; they do not use recency to pick a winner.

`context.timestamp_ms` and `frame_index` are **not** the recency comparator for
replacement. They still drive **age expiry** (`max_age_ms` vs
`provenance.updated_at_ms` / cycle timestamp as today) and are stored on
admitted provenance.

| Timestamp case | Required behavior |
| --- | --- |
| Compatible candidate in a later `update` than retained | Replace retained (invocation-order newer). |
| Compatible candidate whose `context.timestamp_ms` is lower than retained provenance (regressing clock / replay) | Still replace if the candidate arrives in a later `update` invocation; do **not** invent a second clock-based invalidation. Callers must not expect timestamp regression alone to protect prior evidence. |
| Two successive `update` calls with **equal** `timestamp_ms` and compatible payloads | Invocation order still decides: second update replaces first. |
| Same `update` call with multiple candidates | No recency winner; use same-observation equality/contradiction only. |

### Same-observation resolution (before ledger apply)

For each `record_id` group in one observation after extraction:

1. Compute **payload equality** for every pair (definition below).
2. If the group contains **two or more candidates that are not payload-equal**,
   treat the group as a **same-observation contradiction**:
   - do not retain any candidate from the group;
   - if a retained slot for that key still exists post-expiry, **invalidate** it;
   - count **one** conflict for that slot in this update (not one per pair).
3. If every candidate in the group is payload-equal, collapse to **one**
   candidate (any member; they are equal) and continue to same-slot policy.
4. Group resolution is independent of tuple order.

### Payload equality (same-observation only)

Two candidates for the same slot are **payload-equal** only when their
**normalized payloads** are identical. Normalization is:

| Field | Included in equality? | Rule |
| --- | --- | --- |
| `record_id` | Yes (already same group) | Exact string |
| `kind` | Yes | Exact string |
| `label` | Yes | Exact string |
| `confidence` | Yes | Exact float after existing confidence normalization |
| `location` | Yes | Both `None`, or both present with equal `ViewLocation.to_dict()` after construction normalization |
| `properties` | Yes | Deep equality of JSON values after existing strict-JSON admission (see numeric rule and type rules below; strings exact; object keys exact; array order and length matter) |
| `provenance` | **No** | Excluded from equality so two identical claims with different observation/frame ids in one batch still collapse if all payload fields above match. |

Payload equality is **stricter** than structural compatibility. Equal structure
with different `label`, `confidence`, coordinates, or property **values** is
**not** payload-equal → same-observation contradiction.

#### Numeric value equality (payload equality only)

JSON numbers use one shape token (`"number"`), but value equality is still
defined explicitly:

- Booleans are never equal to numbers (`true` ≠ `1`, `false` ≠ `0`). Exclude
  booleans before any numeric comparison.
- Two admitted numbers `a` and `b` (Python `int` and/or `float` after
  strict-JSON admission) are equal iff `a == b` under ordinary Python equality
  **without** coercing either operand with `float(...)`.
  - This yields the intended mixed-width equalities `1 == 1.0` and
    `0 == -0.0`.
  - Distinct large integers remain distinct:
    `9007199254740992 != 9007199254740993` (unlike `float(a) == float(b)`,
    which collapses them).
  - Equality must not raise on large finite integers (do not call `float` on
    them during compare).
- Non-finite floats are not admitted by existing strict-JSON gates; if a value
  is not a finite number or boolean/string/null/array/object under those gates,
  it never reaches equality.

Do **not** use `float(a) == float(b)` (lossy / may `OverflowError`). Do **not**
use canonical JSON text/bytes for number equality.

### Structural compatibility (cross-observation only)

Used only when applying a **single** collapsed candidate to a **retained** slot
from a **previous** update (or empty slot).

A retained record and a candidate are **compatible** only when all of the
following hold:

1. **`kind`**: exact string equality.
2. **Location presence**: both `location is None` or both non-`None`.
3. **Location coordinate frame**: if both non-`None`, `location.frame` equal
   (exact string). `provenance.coordinate_frame` is not a separate
   compatibility axis beyond what is already reflected in `ViewLocation.frame`
   for located claims; for `location is None`, frame is not compared.
4. **Location family** (if both non-`None`): equal **geometry signature** as
   defined next. `zone` and actual numeric coordinates are **not**
   compatibility fields; they may change under the same family (compatible
   payload refresh).
5. **Property shape**: equal recursive JSON type shape of `properties` as
   defined next. Property **values** may change under the same shape.

**Not** compatibility fields (may change on a compatible refresh):

- `label`
- `confidence`
- `zone` (when location present)
- actual `bbox_xyxy_norm` / `polygon_xy_norm` coordinates
- property scalar/object **values** under an unchanged shape
- provenance timestamps / frame_id / observation_id

### Location family (executable against `ViewLocation`)

`ViewLocation` fields: `frame`, `zone`, optional `bbox_xyxy_norm`, optional
`polygon_xy_norm`. There is **no** point geometry type.

Define geometry signature as the pair:

```text
(has_bbox: bool, has_polygon: bool)
```

| Signature | Meaning |
| --- | --- |
| location absent | `location is None` (no signature pair; separate case) |
| `(False, False)` | location present, neither bbox nor polygon |
| `(True, False)` | bbox only |
| `(False, True)` | polygon only |
| `(True, True)` | bbox and polygon |

Compatible location requires the same absence/presence and, when present, the
same `(has_bbox, has_polygon)` signature. Changing any of those bits is
**incompatible** (including present↔absent).

### Property type shape (canonical algorithm)

Define `shape(value)` recursively on strict JSON admitted by existing gates:

| JSON value | Shape token |
| --- | --- |
| `null` | `"null"` |
| `true` / `false` | `"boolean"` |
| number (int or float; JSON number) | `"number"` — int and float share `"number"` |
| string | `"string"` |
| array | `["array", shape(elem0), shape(elem1), …]` — length and per-index shapes matter; empty array is `["array"]` |
| object | `["object", sorted list of (key, shape(value)) by key]` — key set and per-key shapes matter; key order in the source object does not |

Two property dicts have equal shape iff `shape(props_a) == shape(props_b)`.

Heterogeneous arrays are allowed; shape records each index. Changing array
length, index type, object key set, or a nested type is a shape change →
incompatible.

### Same-slot policy (after same-observation resolution)

Given one slot key after expiry and a resolved candidate (or none):

| Case | Required behavior |
| --- | --- |
| No candidate for slot | Missing evidence: leave retained slot until age expiry; no conflict. |
| No retained record + one candidate | Admit candidate (empty-slot write). |
| Retained + compatible candidate | Replace retained payload and provenance (invocation-order newer). Not identity. Conflict counters unchanged. |
| Retained + structurally incompatible candidate | Invalidate retained slot; **do not** retain the candidate. Conflict +1 for this slot. |
| Same-observation contradiction on this slot | Invalidate retained if present; retain no candidate. Conflict +1 for this slot. |

### Post-conflict lifecycle (no tombstones)

Conflict invalidation **removes** the slot. There is no tombstone or sticky
block:

- On a **later** `update`, a candidate for that key is treated as an empty-slot
  write if it passes extraction, even if it is “compatible-looking” relative to
  the invalidated claim.
- Invalidation lasts only for the update that counted the conflict (plus any
  simultaneous absence of a replacement). Implementers must not invent a
  multi-cycle ban.

### Telemetry

On every **successfully published** `BoundedEvidenceLedger` snapshot from
`update`, `reset`, or `snapshot` (implementation path only):

| Metadata key | Exact value / rule |
| --- | --- |
| `conflict_policy` | Exact string: `bounded_evidence_structural_v1` |
| `conflict_count` | Non-negative int; cumulative **slots invalidated** in the current epoch |
| `last_update_conflict_count` | Non-negative int; slots invalidated during the **most recent completed `update`** in this epoch |

Counting unit: **one per invalidated slot per update**, not per candidate pair.

`last_update_conflict_count` lifecycle:

- Set at the end of each `update` to the number of slots invalidated in that
  call (may be `0` for a conflict-free update).
- **`snapshot()` must not change it.** Pure reads republish the same count from
  the latest completed update (or `0` after reset / before any update in the
  epoch). Reading memory must not hide a prior conflict.
- `reset` sets `conflict_count = 0` and `last_update_conflict_count = 0`.
- Do **not** zero `last_update_conflict_count` merely because `snapshot()` ran.

Examples for `last_update_conflict_count` **within one update**:

| Situation in one update | Count |
| --- | --- |
| Three pairwise-unequal candidates for one slot | 1 |
| Two slots each with a same-observation contradiction | 2 |
| One slot with same-observation contradiction that is also incompatible with retained | 1 (single invalidation of that slot) |
| Two slots each receiving an incompatible single candidate | 2 |
| Compatible refresh only | 0 |

Required sequence:

1. An `update` that invalidates exactly one slot → published
   `last_update_conflict_count = 1`.
2. Any number of subsequent `snapshot()` calls (and controller reads of
   `_latest`) still report `last_update_conflict_count = 1` and the same
   `conflict_count`.
3. A later conflict-free `update` → `last_update_conflict_count = 0`
   (`conflict_count` unchanged unless that update also invalidates).

Rules:

- Capacity eviction does **not** increment conflict counters.
- Age expiry does **not** increment conflict counters.
- `reset` starts a new epoch with both conflict counters `0` (and capacity
  eviction counter reset as today).
- Do not retain conflicting payloads or a side conflict ledger.
- Framework-generated failure fallbacks from `ActivatedMemoryStage` that do not
  run the ledger implementation are **not** required to carry these keys; the
  max-age / operator proofs that need counters must use successful ledger
  publications.

### Operator / framework surface

- No new operator command is required.
- Replay and offline fixtures assert policy via records + metadata on successful
  ledger snapshots.
- Live Chase/Pi re-proof is out of scope.

## Ownership

| Concern | Owner |
| --- | --- |
| Same-slot compatibility, equality, invalidation, recency | `implementations/memory/bounded_evidence.py` (`BoundedEvidenceLedger.update`) |
| Snapshot metadata policy/counters | `BoundedEvidenceLedger` published `MemorySnapshot.metadata` |
| Framework stage isolation | Unchanged: `ActivatedMemoryStage` does not learn conflict policy |
| Deterministic proof | Files listed under File Impact |

## Affected Paths

- Success: compatible recency replace.
- Missing: no candidate (including `False` signal drop) → retain until age.
- Conflict: incompatible or same-observation contradiction → clear slot, count.
- Expiry: before compare; no conflict increment.
- Capacity: after conflict resolution; no conflict increment.
- Reset: empty epoch; counters zero.
- Serialization: detached snapshot; required metadata keys on ledger success.
- Error: extract gates drop before compare; framework fallbacks unchanged.

## Adversarial Matrix

| Case | Expected result |
| --- | --- |
| Compatible property scalar value change, same shape | Replace; counters unchanged |
| Compatible confidence-only change | Replace; counters unchanged |
| Compatible label-only change | Replace; counters unchanged |
| Compatible zone-only change (same location family) | Replace; counters unchanged |
| Compatible bbox coordinate change (bbox-only family) | Replace; counters unchanged |
| Kind change under same evidence id | Slot empty; conflict_count +1 |
| Location present ↔ absent | Slot empty; +1 |
| Location family change e.g. bbox-only ↔ polygon-only | Slot empty; +1 |
| Location family change e.g. bbox-only ↔ bbox+polygon | Slot empty; +1 |
| Property shape change (scalar ↔ object/array; key type change; array length) | Slot empty; +1 |
| Same-observation two candidates, equal structure, different `properties.score`, order A then B | Slot empty; +1 |
| Same pair, order B then A | Same as previous (order-independent) |
| Same-observation two candidates, equal structure, different confidence, both orders | Slot empty; +1 each run |
| Same-observation two candidates, equal structure, different bbox coords, both orders | Slot empty; +1 each run |
| Same-observation two payload-equal candidates (incl. equal props/label/confidence/location) | Collapse to one; then normal same-slot policy; no conflict from duplicate alone |
| Same-observation two candidates differing only by JSON number form `1` vs `1.0` on a property, both tuple orders | Payload-equal (`1 == 1.0`); collapse; no conflict from duplicate alone |
| Same-observation two candidates differing only by `0` vs `-0.0` on a property, both tuple orders | Payload-equal; collapse; no conflict |
| Same-observation two candidates differing only by integers `2**53` vs `2**53 + 1` on a property, both tuple orders | Not equal (exact int equality); contradiction; +1; no raise |
| Same-observation two candidates with the same large finite integer property value (e.g. `10**400` if admitted as int) | Payload-equal; collapse; equality path must not raise |
| Same-observation `true` vs number `1` on a property, both orders | Not equal (boolean ≠ number); contradiction; +1 |
| Same-observation three pairwise-unequal candidates for one slot | Slot empty; last_update_conflict_count = 1 |
| Two slots each with a same-observation contradiction in one update | last_update_conflict_count = 2 |
| Missing evidence while retained within max_age | Slot remains; no conflict |
| `True` signal retained, later observation has explicit `False` for same signal id | `False` dropped at extract; treated as missing; retain until age; no conflict |
| Retained older than max_age, then new candidate | Expiry first; empty-slot admit; no conflict |
| After conflict invalidation, next update admits a single compatible-looking candidate | Empty-slot write succeeds (no tombstone) |
| Two successive updates with equal `timestamp_ms`, compatible payload | Second replaces first by invocation order; no conflict |
| Later update with regressing `timestamp_ms` but compatible payload | Replace by invocation order; no conflict |
| Conflict update (+1) then two pure `snapshot()` calls | Both snapshots still report last_update_conflict_count = 1 |
| Then a conflict-free `update` | last_update_conflict_count = 0; conflict_count unchanged |
| Capacity pressure after a conflict invalidation | capacity_eviction_count only; conflict counters separate |
| Reset after conflicts | Empty; conflict counters 0; new epoch_id |
| Unrelated other slots | Unaffected |
| Cross-plugin same local id | Distinct slots |
| Caller mutates returned snapshot | Detached; ledger unchanged |

Table-driven unit coverage (in `test_bounded_evidence_conflicts.py`) must also
directly exercise the location geometry-signature table and the property-shape
algorithm (including empty array, heterogeneous array, and sorted object keys),
either as dedicated parametrized tests or by explicit subsumption notes linking
each signature/shape row to a named matrix case above.

## External Assumptions

- Perception plugins may emit unstable structure; the ledger must not invent
  semantic truth.
- Offline deterministic tests and replay fixtures close the M005-08 conflict
  gap; no live vehicle run is required for this unit.
- Existing namespaced record ids and extraction gates remain correct inputs.
- Callers (including offline replay) may supply non-monotonic timestamps;
  recency is invocation order, not the wall clock.

## Non-Goals

- Semantic fusion, voting, confidence aggregation, multi-source arbitration.
- Object identity, track continuity, or world models.
- Point geometry or new `ViewLocation` shapes.
- Changing capacity, max-age, or reset contracts except to keep telemetry
  disjoint from conflict counters.
- Sticky multi-cycle conflict bans / tombstones.
- Live Chase/Pi lifecycle re-proof or action/movement behavior.
- New CLI commands.
- Rewriting stable `MemorySnapshot` schema beyond the listed metadata keys.
- Closing milestone 005 or activating 006.
- Accepting pre-gate draft #59 as implementation evidence.

## File Impact

### Create

- `tests/implementations/memory/test_bounded_evidence_conflicts.py` — adversarial
  matrix coverage for compatibility, payload equality (including `1`/`1.0`,
  `2**53` boundary contradiction, large-int no-raise), same-observation order
  independence, counters including the conflict → repeated `snapshot()` →
  clean `update` sequence, post-conflict empty-slot re-admit, `False` signal
  missing behavior, and table-driven location/property-shape cases.
- `tests/cli/memory/fixtures/conflict_sequence.json` — offline **update**
  sequence for conflict invalidation and counter transitions across successive
  observation frames only. The current replay format has no snapshot operation;
  pure `snapshot()` preservation is **not** encoded here (owned by the
  implementation test module above).

### Modify

- `implementations/memory/bounded_evidence.py` — equality, compatibility,
  invalidation, metadata counters; keep capacity/age paths separate;
  `snapshot()` must not zero `last_update_conflict_count`.
- `tests/implementations/memory/test_bounded_evidence.py` — only as needed so
  existing recurrence tests still pass under the explicit compatibility
  definition.
- `tests/cli/memory/test_replay.py` — add focused cases (clearly named test
  methods) that load `conflict_sequence.json` and assert records +
  `conflict_policy` / counters across **update** frames. Do not invent a second
  fixture root or widen the replay schema for snapshot ops.
- Milestone plan/ledger only at implementation handoff (not in this proposal
  PR).

### Remove

- None.

## Validation Plan

Deterministic only:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.implementations.memory.test_bounded_evidence \
  tests.implementations.memory.test_bounded_evidence_conflicts \
  tests.cli.memory.test_replay \
  -v
```

Acceptance requires:

1. Every adversarial matrix row has a direct test or an explicit subsumption note
   in the implementation PR.
2. Conflict counters never move on pure age expiry or pure capacity eviction.
3. Reset clears conflict counters with the epoch.
4. `conflict_policy` is exactly `bounded_evidence_structural_v1` on successful
   ledger snapshots.
5. No live vehicle dependency in CI for this unit.

## Implementation Notes (non-binding)

Suggested helpers (names optional):

- `_payload_equal(a, b) -> bool`
- `_structurally_compatible(retained, candidate) -> bool`
- `_location_geometry_signature(location) -> tuple[bool, bool] | None`
- `_property_shape(value) -> object`
- `_invalidate_slot(record_id) -> None` (increments counters)
- Group candidates by `record_id` before apply

Do not add a second durable store of conflicting payloads.

## Handoff

After this proposal merges, execute **exactly** this sequence (the executable
gate rejects any remaining matching local/remote ref for the implementation
branch name):

1. Record acceptance (`ready_for_implementation`) with the proposal merge commit
   via:
   ```text
   python3 docs/milestones/workflow.py accept-proposal \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --pr <this-proposal-pr-number>
   ```
   Commit the resulting plan/HTML on the milestone branch.
2. **Retire the pre-gate implementation attempt (mandatory deletes):**
   - Close draft PR #59 without merge (`gh pr close 59`).
   - Remove any linked worktree that checks out `m005/conflicting-evidence`.
   - Delete local and remote branch refs:
     ```text
     git branch -D m005/conflicting-evidence   # if present locally
     git push origin --delete m005/conflicting-evidence
     git fetch --prune origin
     ```
   - Confirm no remaining ref:
     `git show-ref | grep m005/conflicting-evidence` must print nothing.
3. On the **post-acceptance milestone tip** with a clean worktree, run:
   ```text
   python3 docs/milestones/workflow.py start-implementation \
     --plan docs/milestones/005-evidence-memory-foundation/plan.md \
     --branch m005/conflicting-evidence
   ```
   This creates a **fresh** branch from that tip. Do **not** recreate history
   by merging or resetting onto the deleted pre-gate tip.
4. Implement **only** this accepted proposal on that fresh branch; open a **new**
   implementation PR. Pre-gate code may be used only as informal offline
   reference after re-validation against this contract—it carries **no**
   acceptance and must not be force-pushed back as the branch tip.
5. Do not widen into closeout or live re-proof.

Merely abandoning the old tip without deleting the remote/local branch refs is
**not** sufficient: `start-implementation` will still refuse the name.
